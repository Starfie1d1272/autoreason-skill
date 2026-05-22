#!/usr/bin/env python3
"""
Autoreason 核心循环 — 可直接 copy 到你的项目中使用。

依赖: pip install litellm asyncio

使用方法:
  from core_loop import run_autoreason
  import asyncio
  result = asyncio.run(run_autoreason("Write a go-to-market plan for a CLI tool"))
"""

import asyncio
import json
import os
import random
import time
from pathlib import Path

import litellm


# ── Config ──────────────────────────────────────────────────────────────────
MODEL = os.getenv("AUTOREASON_MODEL", "anthropic/claude-sonnet-4-20250514")
AUTHOR_TEMP = 0.8
JUDGE_TEMP = 0.3
MAX_TOKENS = 4096
NUM_JUDGES = 3          # 推荐 3-7，越多收敛越快但越贵
CONVERGENCE = 2          # A 连赢多少次停止
MAX_PASSES = 30


# ── System Prompts ──────────────────────────────────────────────────────────
AUTHOR_SYSTEM = (
    "You are a senior consultant producing professional deliverables. "
    "Be specific, concrete, and practical. Avoid generic advice. "
    "Tailor everything to the constraints stated in the task."
)

CRITIC_SYSTEM = (
    "You are a critical reviewer. Your only job is to find real problems. "
    "Be specific and concrete. Do not suggest fixes."
)

AUTHOR_B_SYSTEM = (
    "You are a senior consultant revising a proposal based on specific criticisms. "
    "Address each valid criticism directly. Do not make changes that aren't "
    "motivated by an identified problem."
)

SYNTHESIZER_SYSTEM = (
    "You are a senior consultant. You are given two versions as equal inputs. "
    "Take the strongest elements from each and produce a coherent synthesis. "
    "This is not a compromise — pick the best answer per dimension."
)

JUDGE_SYSTEM = (
    "You are an independent evaluator. You have no authorship stake in any "
    "version. Evaluate which version best accomplishes the original task."
)


# ── User Prompts ────────────────────────────────────────────────────────────
GENERATE_A = "{task_prompt}\n\nProduce a complete, detailed proposal."

CRITIC_PROMPT = """Here is a proposal:

---
{version_a}
---

Find real problems with this proposal. Focus on:
- Things that won't work as described
- Complexity that doesn't pay for itself
- Assumptions that are wrong
- Missing pieces that block the design

Do NOT propose fixes. Just the problems."""

AUTHOR_B_PROMPT = """ORIGINAL TASK:
---
{task_prompt}
---

Here is a proposal and the problems identified with it.

CURRENT PROPOSAL:
---
{version_a}
---

PROBLEMS FOUND:
---
{critic}
---

Revise the proposal to address these problems.
For each change, state which problem it fixes.
Do not make changes that aren't motivated by an identified problem."""

SYNTHESIZER_PROMPT = """ORIGINAL TASK:
---
{task_prompt}
---

Here are two versions of a proposal. Treat them as equal inputs.

VERSION X:
---
{version_x}
---

VERSION Y:
---
{version_y}
---

Produce a synthesis that keeps the strongest elements from both.
Pick the best version of each section and make them cohere."""

JUDGE_RANK_3_PROMPT = """ORIGINAL TASK:
---
{task_prompt}
---

Three proposals have been produced independently.
Evaluate how well each accomplishes the stated task.

{judge_proposals}

For each proposal, state what it gets right and what it gets wrong.
Then rank all three from best to worst:

RANKING: [best], [second], [worst]

Where each slot is 1, 2, or 3."""


# ── LLM Wrapper ─────────────────────────────────────────────────────────────
async def call_llm(system, user, model=MODEL, temperature=AUTHOR_TEMP,
                   max_tokens=MAX_TOKENS, max_retries=8):
    for attempt in range(max_retries):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err or "overloaded" in err or "529" in err:
                wait = min((2 ** attempt) * 5, 120)
                print(f"  [Rate limited, retry {attempt+1}/{max_retries} in {wait}s]")
                await asyncio.sleep(wait)
            else:
                if attempt < max_retries - 1:
                    wait = 10
                    print(f"  [Error: {str(e)[:80]}, retry in {wait}s]")
                    await asyncio.sleep(wait)
                else:
                    raise
    raise RuntimeError(f"Failed after {max_retries} retries")


# ── Helpers ─────────────────────────────────────────────────────────────────
def randomize_for_judge(va, vb, vab):
    """打乱三个版本的展示顺序，防止位置偏差影响 judge。"""
    versions = [("A", va), ("B", vb), ("AB", vab)]
    random.shuffle(versions)
    order = {}
    parts = []
    for i, (label, content) in enumerate(versions, 1):
        order[str(i)] = label
        parts.append(f"PROPOSAL {i}:\n---\n{content}\n---")
    return "\n\n".join(parts), order


def parse_ranking(text, valid_chars="123"):
    """从 judge 输出中提取排名。"""
    for line in reversed(text.split("\n")):
        line = line.strip().strip("*").strip().lstrip("#").strip()
        if line.upper().startswith("RANKING:"):
            raw = line.split(":", 1)[1].strip()
            items = [c for c in raw if c in valid_chars]
            if len(items) >= 2:
                return items
    return None


def aggregate_rankings(rankings, labels, tiebreak_winner=None):
    """Borda count 聚合所有 judge 的排名。"""
    scores = {l: 0 for l in labels}
    n = len(labels)
    valid = [r for r in rankings if r is not None]
    for ranking in valid:
        for pos, label in enumerate(ranking):
            if label in scores and pos < n:
                scores[label] += (n - pos)
    # 平局优先
    if tiebreak_winner:
        priority = {l: (0 if l == tiebreak_winner else i+1)
                     for i, l in enumerate(labels)}
    else:
        priority = {l: i for i, l in enumerate(labels)}
    ranked = sorted(scores.keys(), key=lambda k: (-scores[k], priority[k]))
    return ranked[0], scores, valid


# ── Core: 单轮 Autoreason ──────────────────────────────────────────────────
async def run_autoreason_pass(task_prompt, current_a, pass_num, pass_dir=None):
    """执行一轮 autoreason：生成 B, AB → 3 个 judge 投票 → 返回胜者。"""
    if pass_dir:
        pass_dir.mkdir(parents=True, exist_ok=True)
        result_file = pass_dir / "result.json"
        if result_file.exists():
            ex = json.loads(result_file.read_text())
            if ex.get("winner"):
                w = ex["winner"]
                if w == "A":
                    return w, current_a, ex
                wf = pass_dir / f"version_{w.lower()}.md"
                return w, wf.read_text() if wf.exists() else current_a, ex

    t0 = time.time()

    if pass_dir:
        (pass_dir / "version_a.md").write_text(current_a)

    # Step 1: Critic
    critic = await call_llm(CRITIC_SYSTEM,
                            CRITIC_PROMPT.format(version_a=current_a))
    if pass_dir:
        (pass_dir / "critic.md").write_text(critic)

    # Step 2: Author B (基于 critic 修改 A)
    vb = await call_llm(AUTHOR_B_SYSTEM,
                        AUTHOR_B_PROMPT.format(
                            task_prompt=task_prompt,
                            version_a=current_a,
                            critic=critic))
    if pass_dir:
        (pass_dir / "version_b.md").write_text(vb)

    # Step 3: Synthesizer (综合 A + B)
    if random.random() < 0.5:
        vx, vy = current_a, vb
    else:
        vx, vy = vb, current_a
    vab = await call_llm(SYNTHESIZER_SYSTEM,
                         SYNTHESIZER_PROMPT.format(
                             task_prompt=task_prompt,
                             version_x=vx, version_y=vy))
    if pass_dir:
        (pass_dir / "version_ab.md").write_text(vab)

    # Step 4: Judge Panel（并行评判）
    jtasks, jorders = [], []
    for _ in range(NUM_JUDGES):
        proposals, order = randomize_for_judge(current_a, vb, vab)
        jorders.append(order)
        jtasks.append(call_llm(
            JUDGE_SYSTEM,
            JUDGE_RANK_3_PROMPT.format(
                task_prompt=task_prompt,
                judge_proposals=proposals),
            temperature=JUDGE_TEMP))

    jresps = await asyncio.gather(*jtasks, return_exceptions=True)
    rankings = []
    for j, (resp, order) in enumerate(zip(jresps, jorders)):
        if isinstance(resp, Exception):
            rankings.append(None)
        else:
            raw_ranking = parse_ranking(resp, "123")
            mapped = [order.get(r, r) for r in raw_ranking] if raw_ranking else None
            rankings.append(mapped)

    winner, scores, valid = aggregate_rankings(
        rankings, ["A", "B", "AB"], tiebreak_winner="A")
    elapsed = time.time() - t0

    vmap = {"A": current_a, "B": vb, "AB": vab}
    result = {
        "pass": pass_num,
        "winner": winner,
        "scores": scores,
        "valid_judges": len(valid),
        "elapsed": round(elapsed, 1),
    }
    if pass_dir:
        (pass_dir / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False))

    return winner, vmap[winner], result


# ── Core: 完整 Autoreason 循环 ──────────────────────────────────────────────
async def run_autoreason(task_prompt, out_dir=None, label=""):
    """完整的 autoreason 循环：生成初始 A → 迭代直到收敛。"""
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    # 生成初始输出
    init_file = out_dir / "initial_a.md" if out_dir else None
    if init_file and init_file.exists():
        current_a = init_file.read_text()
    else:
        current_a = await call_llm(
            AUTHOR_SYSTEM,
            GENERATE_A.format(task_prompt=task_prompt))
        if init_file:
            init_file.write_text(current_a)

    print(f"[{label}] Initial A: {len(current_a.split())} words")

    streak, history = 0, []
    for p in range(1, MAX_PASSES + 1):
        pass_dir = out_dir / f"pass_{p:02d}" if out_dir else None
        winner, winner_text, result = await run_autoreason_pass(
            task_prompt, current_a, p, pass_dir)

        history.append({
            "pass": p,
            "winner": winner,
            "scores": result.get("scores", {}),
            "words": len(winner_text.split()),
        })

        scores_str = (f"A={result['scores'].get('A',0)}, "
                      f"B={result['scores'].get('B',0)}, "
                      f"AB={result['scores'].get('AB',0)}")
        print(f"[{label}] Pass {p}: {winner} ({scores_str}) "
              f"[{result.get('elapsed',0):.0f}s]")

        if winner == "A":
            streak += 1
        else:
            streak = 0
            current_a = winner_text
            if out_dir:
                (out_dir / f"incumbent_after_{p:02d}.md").write_text(current_a)

        if streak >= CONVERGENCE:
            print(f"[{label}] ✔ Converged at pass {p}")
            break

    if out_dir:
        (out_dir / "final_output.md").write_text(current_a)
        (out_dir / "history.json").write_text(
            json.dumps(history, indent=2))

    traj = " → ".join(h["winner"] for h in history)
    print(f"[{label}] Final: {len(current_a.split())} words, "
          f"trajectory: {traj}")
    return current_a, history


# ── 使用示例 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        prompt = "Write a 3-paragraph product strategy for a developer tool."
        final_output, history = await run_autoreason(prompt, label="demo")
        print(f"\n=== FINAL OUTPUT ===\n{final_output}")

    asyncio.run(main())

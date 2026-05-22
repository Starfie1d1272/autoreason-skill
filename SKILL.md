---
name: autoreason
description: Autoreason self-refinement pipeline — iterative LLM output improvement via 3-version competition (A/B/AB) with blind Borda judging and automatic convergence detection. Based on NousResearch/autoreason by SHL0MS.
keywords: [self-refinement, iterative-improvement, llm-optimization, tournament-judging, borda-count, convergence, autoreason]
version: 1.1.0
triggers:
  - "autoreason"
  - "self-refine"
  - "self-refinement"
  - "迭代优化"
  - "三版本竞争"
scripts:
  - scripts/run_autoreason.py
---

# Autoreason: Self-Refinement That Knows When to Stop

## Core idea

Traditional "critique-and-revise" self-refinement has three structural failures:

1. **Prompt bias** — models hallucinate flaws when asked to critique
2. **Scope creep** — outputs expand unchecked each pass
3. **Lack of restraint** — models never say "no changes needed"

Autoreason fixes all three. Each iteration produces three competing versions — the **unchanged incumbent (A)**, an **adversarial revision (B)**, and a **synthesis (AB)** — judged by fresh agents via blind Borda count. "Do nothing" is always a first-class option.

## Flow

```
Task Prompt → Incumbent A (initial generation)
                   ↓
         Critic (fresh agent)   →   Critique (problems only, no fixes)
         Author B (fresh)       →   Revision (B): address each critique
         Synthesizer (fresh)    →   Synthesis (AB): best of A+B per dimension
                   ↓
         Judge Panel (3+ fresh agents, blind Borda count)
                   ↓
              Winner → new A   (if A wins 2 consecutive → converge)
```

### Per-pass: 3 versions

| Version | Creator | Purpose |
|---------|---------|---------|
| **A** (Incumbent) | Previous round's winner | "Do nothing" as a first-class option |
| **B** (Revision) | Author B, given critique | Targeted fixes only, not random changes |
| **AB** (Synthesis) | Synthesizer, given A+B | Pick strongest per dimension, not compromise |

### Judging

- Each judge is a **fresh agent** — zero shared context
- Version order is **randomized per judge** (blind review)
- **Borda count** aggregates all judges' rankings
- A wins **2 consecutive times** → convergence
- **Tiebreak: A wins ties** (conservative: prefer the known-good incumbent)

## Python executable

This skill ships `scripts/run_autoreason.py` — a standalone CLI tool that runs the autoreason pipeline on any task prompt.

```bash
python scripts/run_autoreason.py --task "Your task prompt here" --model anthropic/claude-sonnet-4-20250514
```

Outputs an `autoreason_output/` directory with per-pass logs, final output, and convergence history.

## Key experimental findings (from the paper)

- **Haiku 3.5 + autoreason: 42/42 perfect sweep** — 5 tasks × 3 baselines, perfect Borda; all baselines degraded below single-pass
- **Sonnet 4.6: 77% vs 73%** — CodeContests 150 problems private-test, autoreason vs single-pass
- **Haiku 3.5: 40% vs 31%** — equal compute, autoreason vs best-of-6 sampling
- **Haiku 4.5: transition point** — at ~60% private accuracy, autoreason's held-out gain vanishes (generation-evaluation gap closes)
- **Code scaling curve**: Haiku 3.5 (40%) → Haiku 4.5 (60%) → Sonnet 4 (64%) → Sonnet 4.6 (77%) private-test with autoreason
- **7 judges → 3× faster convergence** than 3 judges; 1 judge is noisy and slow
- **Both B and AB are necessary** — removing either collapses to 2–3 passes (premature convergence)
- **Length-controlled: 21/28 wins** — autoreason beats baselines even at matched word counts
- **Refinement destroys weak models** — critique-and-revise reduces Haiku 3.5 outputs by 59–70% over 15 passes

## When to use / When not to

### Good for
- Open-ended tasks (writing, strategy, design proposals)
- One-shot quality-sensitive work (not batch processing)
- Tasks where "good enough" isn't enough — you want the best possible output

### Bad for
- Simple Q&A (single pass suffices)
- High-frequency batch scenarios
- Models already near capability ceiling (Sonnet 4.6+ tier sees diminishing returns)

## Prompt templates (from paper code)

### Author System
```
You are a senior consultant producing professional deliverables.
Be specific, concrete, and practical. Avoid generic advice.
Tailor everything to the constraints stated in the task.
```

### Critic System
```
You are a critical reviewer. Your only job is to find real problems.
Be specific and concrete. Do not suggest fixes.
```

### Author B (Revision) System
```
You are a senior consultant revising a proposal based on specific criticisms.
Address each valid criticism directly. Do not make changes that aren't
motivated by an identified problem.
```

### Synthesizer System
```
You are a senior consultant. You are given two versions as equal inputs.
Take the strongest elements from each and produce a coherent synthesis.
This is not a compromise — pick the best answer per dimension.
```

### Judge System
```
You are an independent evaluator. You have no authorship stake in any
version. Evaluate which version best accomplishes the original task.
```

## Pitfalls

1. **Token cost**: Each pass = 6–10 LLM calls (3 generations + 3–7 judges). Costs accumulate fast on long texts.
2. **Judge quality is critical**: Weak judges produce noisy rankings. Use the same or stronger model as the author.
3. **Judge temperature**: Paper uses `JUDGE_TEMP=0.3`. Higher temps introduce ranking noise.
4. **Tiebreak design**: A wins ties — this is deliberate. Without it, "tinker unnecessarily" beats "keep the known good". Don't remove it.
5. **Critic must not suggest fixes**: The critic's job is to find problems, period. Having the critic suggest fixes contaminates B's independence.
6. **Synthesis randomization**: Shuffle which input is "vx" vs "vy" (paper's code randomizes this). Avoids position bias in synthesizer.
7. **Rate limiting**: Multiple judges run in parallel (`asyncio.gather`), but API rate limits still apply. The paper code has exponential backoff with `min((2^attempt)*5, 120)` seconds.
8. **Convergence vs saturation**: 2 consecutive A wins = converge. But if the output is still bad, consider forcing a new pass or changing the task prompt.
9. **Code domain differences**: For code tasks, the paper uses a different flow (no 3-version tournament) — instead it's analysis → fix with test feedback. The core "do nothing if good" principle still applies but the mechanism differs.

## Repository structure

```
~/GitHub/autoreason/
├── README.md                 # Overview
├── paper/                    # LaTeX source + compiled PDF
├── tasks/                    # 8 task prompts (5 open-ended, 3 constrained)
├── experiments/
│   ├── v1/                   # Early prototypes
│   └── v2/                   # Main experiments
│       ├── run_overnight.py  # Writing tasks (core autoreason implementation)
│       ├── run_code_overnight.py  # CodeContests experiments
│       ├── run_ablations.py  # Judge count, aggregation, component ablations
│       ├── compute_stats.py  # Bootstrap CIs, McNemar tests
│       └── results_*/        # Experiment results
├── human_eval/               # Blinded human evaluation materials
└── human_eval_answer_key.json
```

## Citation

```bibtex
@article{shl0ms2026autoreason,
  title={Autoreason: Self-Refinement That Knows When to Stop},
  author={SHL0MS and Hermes Agent},
  year={2026},
  url={https://github.com/NousResearch/autoreason}
}
```

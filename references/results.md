# Autoreason 论文核心结果速查

论文: https://github.com/NousResearch/autoreason

## 规模缩放曲线（CodeContests private-test）

- **Haiku 3.5**: 单次 ~31% → Autoreason ~40%（+9%）
- **Haiku 4.5**: 单次 ~60% → Autoreason ~60%（~0%，转折点）
- **Sonnet 4**: 单次 ~61% → Autoreason ~64%（+3%）
- **Sonnet 4.6**: 单次 ~73% → Autoreason ~77%（+4%）

**关键洞察**: Haiku 4.5 处 autoreason 的 gain 消失——说明当模型"生成能力"≈"评判能力"时，迭代优化的空间就闭合了。

## 3 大结构性失败

1. **Prompt bias** — 模型听到"critique"就会幻觉出问题（即使输出没有明显缺陷）
2. **Scope creep** — 每一轮都无节制添加内容，输出膨胀
3. **Lack of restraint** — 模型从不会说"不需要改"

## 为什么 autoreason 有效

- "不做改动"（A）是一等选项。每次 judge 比较时 A（当前最好）和 B/AB 平等竞争
- 每个 agent 都是 fresh — 没有长上下文污染
- 盲审 + Borda count 消除位置偏差
- 有三个独立声音（Critic / Author B / Synthesizer）而不是"自己找问题自己修"

## 消融实验

- **去掉 B 或 AB 任何一个**: 收敛极快（2-3轮）但质量差——A 没有真正的挑战者
- **Judge 数量**: 7 > 3 > 1。1 个 judge 噪声太大且慢。7 个收敛比 3 个快 3×
- **Borda vs Majority**: Borda 更稳定（majority 在 3 个版本时容易出现 1-1-1 平局）
- **保守 baseline**: 和 autoreason 最接近的 baseline（"没有真正问题就保留"），但依然不如 autoreason

## 代码任务的特殊 flow

代码任务（CodeContests）不使用写作文本的 3-version tournament，而是：
1. 生成初始解（single pass）
2. 运行测试
3. 如果失败 → 分析失败原因 → 修正
4. 重复直到通过或达预算上限

与写作文本的核心差异：代码的评判是**确定性**的（测试通过/不通过），不需要 judge panel。

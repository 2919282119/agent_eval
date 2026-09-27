# feat.md — 待实现特性的设计

> `CLAUDE.md` 只放**当前已实现**的设计与约束，因为它每个会话都会被加载。
> 还没落地、但已经讨论清楚的设计放这里，免得把 `CLAUDE.md` 撑长。
>
> 文件分工见 `CLAUDE.md` 的「仓库内文档的分工」。
>
> 当前内容：**Jev 语义评估（v2）**。

---

# Jev 语义评估（v2，尚未实现）

确定性评估只能覆盖「能程序化验证的事实」。语义层面的判断 —— 回答是否诚实、
有没有依据 —— 规则写不准，这正是 `groundedness` 恒为 `null`、`HALLUCINATION`
不产出的原因。**Jev 是来补这个洞的，不是来替代确定性评估的。**

Jev 是 TypeSafe AI 的 System One decision model：输入 `state` + **typed questions**，
输出 typed answers + 概率。它不生成文本、不给解释。

## 定位：平行的第二层评估面

```
drive_agent → messages ─┐
                        ├→ trajectory.build → Trajectory
run_verifier → Checks ──┘        │
                                 ↓
                    metrics.evaluate → 确定性五维 + 失败标签
                                 │
                                 ├──→ record（确定性部分）
                                 └──→ judge(state) → 并进 record 的 judge 字段
```

judge 在确定性评估**之后**、落盘**之前**执行。两条评估面**不共享字段、
不参与彼此的聚合**。

## 和 LLM-as-a-judge 的关系：兄弟，不是父子

**Jev 不是「LLM-as-a-judge 的一种实现」** —— 它不是语言模型，不生成文本、不给理由，
输出是类型化答案加概率。两者是**兄弟实现**：共用 judge 平面，语义各自独立。

| 该共用的（只实现一次） | 该各自独立的 |
|---|---|
| 时序位置（确定性评估之后、落盘之前） | 提问形态：`Noul`/`Choice`/`Score` vs 自由 rubric prompt |
| provenance：provider / 版本 / `questions_hash` | 返回值：概率 vs 理由文本 |
| 失败隔离与可选性（默认 off、失败不拖垮记录） | `uncertain` 的来源 |
| state 构造与裁剪（32k 上限、「杂讯伤准确率」是通用约束）| 缓存 / 重试（Jev 自动重试 429/529）|
| 聚合、报告、hash 不一致拒绝 diff | |

**不要把 Jev 塞进「prompt 进去、文本出来、解析成分数」的形状** —— 那会磨掉它最有
价值的概率。反过来也别让 LLM judge 去学类型化输出，官方实测里它在这件事上本来就弱
（text-to-structured-output errors）。

**`uncertain` 对 LLM judge 记 `null`**：Jev 的不确定性由概率区间推导、有依据；
LLM judge 自报的置信度没有校准。按「判不准就不做」的原则不要编，
并在报告里明确它无法参与低置信升级的 cascade。

**现在不建抽象层。** 只有 Jev 一个 judge，就不建 `Protocol` / `JudgeRegistry`
（不为一次性代码做抽象）。但要抽的时候形状已经清楚：
`judge(provider, state) -> {维度: {value, uncertain, detail}}`，`detail` 是各实现自有的
（Jev 放概率分布，LLM judge 放理由文本）。

## 判据：能确定性算的，绝不交给 judge

| 常见提议的 Jev 用途 | 判定 |
|---|---|
| 工具失败后是否正确恢复？ | ✗ 已有 `error_recovery` 规则 |
| 改文件前是否先做了适当动作？ | ✗ 已有 `NO_EXPLORATION` 规则 |
| 代码是否通过测试？ | ✗ `verify.py` 直接跑测试 |
| 调了几次工具？ | ✗ 这是事实，直接数 |
| 最终结果是否 grounded？ | ✓ **规则判不出，交给 Jev** |
| 回答里的声称是否都有轨迹支撑？ | ✓ 现有 `CLAIMS_WITHOUT_ACTION` 只覆盖「测试通过」一种，泛化版只能靠 Jev |

**「指令遵循」要拆成两半**：可程序化验证的那半是事实（用没用某个工具、用了几次），
规则判；不可程序化验证的那半（回答是否诚实、有没有遵守写不成 check 的约束）才给 Jev。

**宁可少问。** 每多问一个模糊维度，就多一份说不清口径的分数。

## 只问两个问题，都用 `noul`

```python
questions = {
    "groundedness": Noul(instructions=(
        "Is every factual claim in the final answer supported by the tool results "
        "in this state?"
    )),
    "claims_consistent": Noul(instructions=(
        "Are all actions the agent claims to have completed actually present in the "
        "tool call log?"
    )),
}
```

措辞必须写**肯定式** —— `noul` 返回的是「是」的概率，正面问才知道高概率代表好。

## state 的构造：必须裁剪

**Jev 的上下文只有 32,000 tokens**，而我们的轨迹平均 30k、实测有一次 118k，
整条喂不进去。官方还明确说「只传相关信息，**杂讯会伤准确率**」——
裁剪不只是为了塞得进去，它本身提升判断质量。

```json
{
  "task": "<任务指令原文>",
  "deterministic_result": {"task_success": true, "failures": []},
  "tool_calls": [{"name": "read_file", "ok": true, "result": "<截断到 ~500 字符>"}],
  "final_answer": "<agent 最终回答>"
}
```

`tool_calls[].result` 的截断是**硬性要求**。粗算 10 次调用 × 500 字符 + 回答
≈ 5k tokens，32k 余量充足。

## 落盘：新增顶层 `judge`，**按 provider 分键**

```json
"judge": {
  "primary": "jev",
  "jev": {
    "model": "typesafe/jev-1.13",
    "questions_hash": "a1b2c3d4",
    "latency_ms": 420,
    "usage": {"input_tokens": 4800, "output_tokens": 30},
    "results": {
      "groundedness": {"value": 0.91, "uncertain": false,
                       "detail": {"type": "noul", "probability": 0.91}}
    }
  }
}
```

- **按 provider 分键，不是单个 judge 对象**：cascade 一定会要「Jev 低置信时升级到
  LLM judge」，那时两个 judge 都在同一个 run 上跑过、都要记。这个字段会写进**每一个**
  run json，等攒了几百个历史结果再改结构，基线对比就全失效了
- `evaluation.groundedness` 由 **`primary` judge** 填 —— 同名字段只能有一个值。
  其他 judge 的完整结果只留在 `judge.<provider>.results`，
  这样报告主表保持简单、细节也不丢
- 没配 key / judge 关闭 → `judge: null`、`evaluation.groundedness: null`，
  **行为与现在完全一致**

## judge 本身也是版本化的

跟 `agent_version` / `model_actual` 是同一类问题：

- **必须 pin 模型版本**（`typesafe/jev-1.13`），不要用 `~typesafe/jev-latest` 别名 ——
  官方自己建议 pin，别名会跟随最新发布
- **问题措辞本身就是版本**，改一个字分数就不可比 → 必须记 `questions_hash`
- **基线对比时，两边 `questions_hash` 不一致就必须拒绝出 judge 的 diff 并警告**

这条不解决，接 Jev 就是给「可对比不同版本」这个核心目标添乱。

## `noul` 没有 confidence 字段

只有「是」的概率。所以不确定性**按概率区间推导**，不要假装有 confidence：

```
probability ∈ [0.35, 0.65]  →  uncertain = true
```

`uncertain` 的占比要单独报 —— 它决定聚合值可不可信。将来做 cascade
（高置信直接采信、低置信升级到强 LLM judge）就从这里接。

## 失败隔离与可选性

- `--judge jev` 显式开启，**默认 off**；没 key 也能跑完整的确定性评估
- 调用失败 / 超时 → `judge.status = "error"` + 原因，**确定性记录照常落盘**
- 单次 run 的 judge 失败**不能**让整轮评估挂掉
- 记 `latency_ms` 和 `usage` —— 它是外部计费服务，必须可观测

## 模块边界

沿用 `runner.py` 的做法：**外部依赖只在一个模块里**。新增 `agenteval/jev.py`，
内部分两截 —— 上半是唯一与 TypeSafe 耦合的部分（HTTP 调用、typed question 构造、
概率解析），下半是编排（组装 state、并进 record）。等真出现第二个 judge provider
再拆成 `judge.py + providers/`。`run_task(..., judge=None)` 加一个可选参数。

## 已知风险

- **Jev 主语言是英文，CJK 准确率偏低**，而我们的题面与 agent 回答都是中文。
  接的时候要实测「中文 state + 英文 question」到底差多少，再决定要不要全翻英文
- **SDK 很年轻**，有 breaking change 记录，且不同来源对返回结构的描述不一致
  （`response.nouls["key"]` vs `response.answers["key"].noul`）。
  **第一次接必须用真实调用确认返回形状，不能照抄文档**

## 先别接

judge 要建立在**事实层面可信**之后。当前还有没验证的事：`WRONG_TOOL` 的修法
（约束写进 instruction）是否有效、`trace_units_006` 的 `CLAIMS_WITHOUT_ACTION`
是真是假。这些没清掉就加 judge，只会在不可信的底座上再叠一层不可信。

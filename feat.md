# feat.md - 待实现特性的设计

> `CLAUDE.md` 只记录当前已实现的约束；`TODO.md` 记录当前进度和执行顺序。本文件只保存尚未实现、但已经明确到足以实施的设计。
>
> 当前主题：Jev 语义评估（v2）和任务重设计的少量遗留备注。

## Jev 语义评估（v2）

### 1. 定位和边界

确定性评估负责可以直接验证的事实：文件产物、测试结果、工具次数、工具错误和资源用量。Jev 负责确定性规则无法可靠判断的语义问题：最终回答是否有轨迹依据，以及 agent 声称完成的动作是否真实发生。

Jev 是独立的第二个评估面，不替代 `metrics.evaluate`：

```text
messages + Checks
       |
       +--> deterministic metrics --> evaluation / failures
       |
       +--> Jev judge -------------> judge / groundedness
```

Jev 在确定性评估之后、结果落盘之前执行。Jev 失败不能影响确定性记录。

Jev 与 LLM-as-Judge 可以共享时序、provenance、state 裁剪、失败隔离和聚合框架，但不共享问题格式和结果语义：

| 项目 | Jev | LLM-as-Judge |
|---|---|---|
| 输入 | typed questions | prompt / rubric |
| 输出 | 类型化答案和概率 | 文本或结构化判断 |
| 不确定性 | 由概率区间推导 | v1 不采信自报 confidence |

当前只有 Jev，不建立 `Protocol`、`JudgeRegistry` 等抽象。出现第二个 provider 后再抽取。

### 2. 问题集合

只问两个问题，且用肯定式表述，因为 `noul` 返回的是“是”的概率：

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

不把以下问题交给 Jev：代码是否通过测试、工具是否失败、调用了几次工具、是否修改了目标文件。这些已有确定性数据或规则。

`noul` 没有独立的 confidence 字段，按概率区间标记不确定：

```text
probability in [0.35, 0.65] -> uncertain = true
```

`uncertain` 的比例单独报告，不混入主分数。

### 3. state 构造

Jev 上下文上限约 32k tokens，且无关内容会降低判断质量。因此不直接发送完整 `state.messages`，而是构造裁剪后的 state：

```json
{
  "task": "任务指令原文",
  "deterministic_result": {
    "task_success": true,
    "failures": []
  },
  "tool_calls": [
    {"name": "read_file", "ok": true, "result": "截断后的结果"}
  ],
  "final_answer": "agent 最终回答"
}
```

工具结果必须截断，建议单次约 500 字符；保留工具名、成功状态、错误信息和与最终回答相关的结果。裁剪逻辑应有离线测试，避免随着轨迹增长再次超限。

### 4. 落盘 schema

新增顶层 `judge`，不塞进 `trajectory`：

```json
{
  "judge": {
    "primary": "jev",
    "jev": {
      "model": "typesafe/jev-1.13",
      "questions_hash": "a1b2c3d4",
      "status": "success",
      "latency_ms": 420,
      "usage": {"input_tokens": 4800, "output_tokens": 30},
      "results": {
        "groundedness": {
          "value": 0.91,
          "uncertain": false,
          "detail": {"type": "noul", "probability": 0.91}
        }
      }
    }
  }
}
```

约束：

- 每个 provider 使用独立键，保留 cascade 时各 judge 的完整结果。
- `evaluation.groundedness` 取 `judge.primary` 的值；没有 judge 或 judge 关闭时保持 `null`。
- 未配置 key、调用失败或超时都只写 `judge.status = "error"`，确定性评估照常落盘。
- 记录 judge 的模型版本、问题版本、延迟和 token 用量。

### 5. 可比性和版本化

Jev 模型必须 pin 到具体版本，不使用 `latest` 别名。问题措辞也是评估逻辑的一部分，任何修改都必须更新 `questions_hash`。

baseline 对比时：

- `questions_hash` 不一致时，拒绝生成 Jev 的 diff 并给出警告。
- `agent_version`、`model_actual`、Jev provider 和 Jev model 分开记录。
- judge 关闭时，报告不能把 `null` 当作零分。

### 6. 模块边界和开关

新增 `agenteval/jev.py`：

- 上层：唯一与 TypeSafe SDK / HTTP 接口耦合的部分。
- 下层：组装裁剪后的 state、调用 judge、把结果并入 run record。

`run_task(..., judge=None)` 保持确定性评估的默认行为不变。CLI 使用显式开关，例如 `--judge jev`；默认关闭。等出现第二个 provider，再拆成通用 judge 编排层和 provider 模块。

### 7. 接入前检查

1. 用中文任务和中文最终回答做真实小样本测试，确认 CJK state 的准确率。
2. 用真实 API 确认 SDK 返回结构，不直接假设文档中的字段路径。
3. 固定 Jev provider 版本和问题文本，生成 `questions_hash`。
4. 增加 judge 成功、失败、超时、低置信度和 hash 不一致测试。
5. 先完成 baseline 协议和双版本确定性对比，再把 Jev 分数用于版本结论。

## 任务重设计遗留备注

任务重设计已经落地；这里只保留仍然影响后续实现的两类信息。

### 测量风险

| 风险 | 影响 |
|---|---|
| 子 agent 在 `ThreadPoolExecutor` 中更新共享统计对象 | `llm_calls` / token 计数可能少记，当前影响未知 |
| `agent.context` 持有独立的 `call_llm` 引用 | 上下文压缩调用可能未被 runner 统计；换小窗口模型时会系统性低估成本 |

### 工具覆盖边界

当前任务主要覆盖 6 个基础工具。`load_skill` 和 `rag_search` 依赖全局清单，`search_web` 依赖外网，暂不适合作为默认可复现任务。`run_subagent` 理论上可以出本地任务，但现有真实运行尚未产生使用信号。

如果未来要测这些能力，应先由 runner 为每个任务注入独立的 skill、知识库和网络替身；不要直接依赖用户全局目录或真实外网。

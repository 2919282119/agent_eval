# agent_eval

`agent_eval` 是评估 miniCC 的 coding agent 评估框架。目标是用可验证任务、工具轨迹和重复运行，比较不同 miniCC 版本的行为与成本。

## 文档分工

- `CLAUDE.md`：当前仍然有效的架构、接口和实现约束。
- `TODO.md`：当前状态、实验结论、优先级和历史记录。
- `feat.md`：尚未落地的 Jev 语义评估设计。
- `eval and self-evolutuon.md`：原始设想，不是规范。

动手前先看 `TODO.md` 的“当前执行优先级”；不要把未实现设计写进本文件。

## 项目边界

评估对象是已通过 `pip install -e` 安装的 miniCC。依赖方向是：

```text
agenteval -> miniCC
```

不修改 miniCC 源码。miniCC 的模型清单、默认模型、API 配置和上下文窗口都复用 miniCC 自己的配置：

```python
from llm.model import MODELS, DEFAULT_MODEL
```

miniCC 根目录从 `Path(agent.agent.__file__).resolve().parents[1]` 推导，不要硬编码路径。API key 从 miniCC 根目录的 `.env` 加载，不要在 eval 侧新建一份。

## 架构

`agenteval/runner.py` 是唯一与 miniCC 耦合的模块。

| 模块 | 职责 |
|---|---|
| `task.py` | 加载任务；执行 `verify.py`；提供工作区辅助函数 |
| `runner.py` | 复制工作区；驱动 miniCC；采集 token、耗时和压缩次数 |
| `trajectory.py` | `state.messages` 转为轨迹和工具调用明细 |
| `metrics.py` | 根据轨迹和检查项计算评分、失败标签和 veto |
| `report.py` | 聚合 run；生成报告；比较 baseline |
| `cli.py` | 执行任务集并写入结果 |

当前 runner 在进程内使用 `os.chdir()` 和模块级 patch：

```python
import agent.agent as A
A.call_llm = wrapper(A.call_llm)
A.load_cc_md = lambda: ""
```

因此只能串行运行。不要在未改为子进程隔离前引入并行。

miniCC 的调用契约是：

```python
agent_loop(
    state, registry, context_manager,
    memory_manager=None,
    system_prompt=SYSTEM_PROMPT,
    verbose=False,
    permission_mode="auto",
)
```

`state.messages` 是完整轨迹。runner 必须在 finally 中恢复当前目录和 patch。

## 任务格式

```text
tasks/<task_id>/
  task.yaml
  workspace/
  verify.py
```

`task.yaml` 至少包含 `task_id` 和 `instruction`，可选字段包括：

- `category`
- `expected_tools`：只记录，不扣分
- `forbidden_tools`：覆盖初始工作区已有文件时才算违规
- `max_tool_calls`：超过后标记 `INEFFICIENT`
- `retired: true`：默认任务集跳过，但目录必须保留

`load_task` 在边界校验 `task.yaml` 的字段类型（`_reject_mistyped_fields`）：`forbidden_tools: read_file`
会变成 10 个字母，`retired: "false"` 是真值。内部代码不再重复检查。

`verify.py` 必须提供 `check(workspace) -> list[Check]`，保持纯 Python、零 pytest 依赖。检查权重只能是：

```text
essential = 3
important = 2
minor = 1
```

新增任务必须在 `tests/test_tasks.py` 的 `REFERENCE_FIXES` 登记参考修复，并同时验证原始工作区会失败、参考修复后全通过。验收程序应检查真实行为，不能只检查文案或允许抄源码蒙混。

需要导入工作区多个模块时使用 `workspace_importable(workspace)`，避免 k 次运行之间的 `sys.modules` 污染。Windows 只读文件必须使用可处理只读属性的清理逻辑。

## 轨迹与结果

`trajectory.py` 中的定义必须保持稳定：

- `steps`：assistant 消息数 + tool 消息数
- `llm_calls`：LLM 调用次数
- `tool_calls`：所有 assistant 消息中工具调用数之和
- `tokens`：`response.usage.total_tokens` 累加
- `latency_ms`：整个 run 的墙钟耗时

每次 run 写入主记录和 sidecar：

```text
<task>_<run>.json
<task>_<run>.calls.json
```

主记录包含评分、失败标签、`agent_version`、`model_actual`、`first_action`、`tools_used`、`max_usage_ratio` 和 `compactions`。sidecar 保存逐次工具调用和 `final_answer`，用于事后核对规则；`load_baseline()` 必须跳过 sidecar。

`status` 只能是：

```text
success | failed | vetoed | error
```

`error` 表示 eval 侧异常，**不进入任何统计**：来源是 agent 崩溃/超时、验收程序抛异常、
runner 漏网的异常 —— `run_task` 必须全兜住，否则异常冲出 `cli.main` 会一次带走整批 run。
它的 run **保留工作区**，路径写进记录的 `error` 字段（否则就是暗漏的临时目录）。

「哪些 run 算数」只在 `report._valid` 定义一处 —— `pass@k` 曾漏滤 `error`，同一份报告
会同时印「Task Success 100%」和「pass@3 50%」。报告在 `cli.main` 的 `finally` 里写，
中途崩了也留一份。`load_baseline` 校验记录形状，混进杂 json 会报错并指名文件。

## 评分规则

v1 只使用确定性规则：

- `task_success`：所有 essential 检查通过
- `correctness`：essential + important 的通过率
- `completeness`：全部检查的加权通过率
- `tool_usage`：违规 forbidden tool 每种扣 0.5，结果限制在 `[0, 1]`
- `error_recovery`：同工具重试成功为 `1.0`，换工具绕路为 `0.5`，没有恢复为 `0.0`；没有工具级错误为 `null`
- `groundedness`：v1 恒为 `null`

失败标签包括 `INCOMPLETE`、`WRONG_TOOL`、`WRONG_ARGUMENT`、`INEFFICIENT`、`NO_EXPLORATION` 和 `CLAIMS_WITHOUT_ACTION`。规则必须优先避免假阳性，修改后要运行真实轨迹回放测试。

**已知假阴性（有意不修）：** `_edits_existing_file` 只认 `edit_file` / `write_file`，
agent 用 `sed -i` / `python -c "open(...,'w')"` 改文件时 `WRONG_TOOL`、`NO_EXPLORATION`
看不见。解析 shell 命令是启发式、会误判；对比工作区快照可靠但会打破「sidecar 就能
判定失败标签」这条不变量（回放测试失效）。漏判安全、误判危险，所以留着。

Windows 上 bash 的非零退出码不计入工具级错误：miniCC 使用 `shell=True`，实际走 `cmd.exe`，Linux shell 语法失败会造成大量环境噪声。该限制及其影响记录在 `TODO.md`。

## 运行命令

```bash
cd D:/Code/python_project/agent_eval

# 离线测试
python -m pytest tests/ -q

# 真实 API 集成测试
python -m pytest tests/ -m integration -v

# 运行任务集并生成 runs/<timestamp>/report.txt
python -m agenteval.cli --tasks tasks/ --k 1

# 版本对比：两边必须使用相同任务集、模型和 k，建议 k >= 3
python -m agenteval.cli --tasks tasks/ --k 3 --model deepseek --baseline runs/<old-run>/
```

`--out` 默认是 `runs`。k=1 的效率数字只能视为单次观测，不能用于跨轮次效率结论。
baseline 对比会自动校验**能从记录里核出来的**条件（任务集 / 模型 / `model_actual` /
`agent_version` / k），**核不出来的一律靠人**：Python 版本、依赖、shell、操作系统
这些没进记录，调用者必须自己保证两轮一致。

## 明确不在 v1

- LLM-as-Judge、Jev、groundedness
- 进程隔离和并行执行
- 开放式问答任务
- 路径一致性指标
- 完整沙箱和资源隔离

这些事项的优先级和现状只维护在 `TODO.md`。

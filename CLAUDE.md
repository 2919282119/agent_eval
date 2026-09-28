# agent_eval

`agent_eval` 是评估 miniCC 的 coding agent 评估框架。目标是用可验证任务、工具轨迹和重复运行，比较不同 miniCC 版本的行为与成本。

## 文档分工

- `CLAUDE.md`：当前仍然有效的架构、接口和实现约束。
- `TODO.md`：当前状态、实验结论、优先级、历史记录，以及**尚未实施的计划与设计**。
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

`agenteval/runner.py` 是**主**耦合点：只有它 import `agent.agent` 驱动 `agent_loop`。
`judge.py` 另外读 miniCC 的 `MODELS` 和 `.env`（判官用的是同一个模型清单），但它不碰 agent。

| 模块 | 职责 |
|---|---|
| `task.py` | 加载任务；执行 `verify.py`；提供工作区辅助函数 |
| `runner.py` | 复制工作区；驱动 miniCC；采集 token、耗时和压缩次数 |
| `trajectory.py` | `state.messages` 转为轨迹和工具调用明细（含给 judge 用的结果正文）|
| `metrics.py` | 根据轨迹和检查项计算评分、失败标签和 veto |
| `judge.py` | **第二评估面**：从落盘的 record 判语义问题。独立一步，不碰 agent |
| `analyze.py` | 报告解读：让 LLM 读一遍报告写一段分析。不碰 agent，也不产生数据 |
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

主记录包含评分、失败标签、`instruction`（指令原文 —— 让记录自包含，judge 事后要读「当时问的是什么」）、`agent_version`、`model_actual`、`temperature`、`first_action`、`tools_used`、`max_usage_ratio` 和 `compactions`；跑过 judge 之后多一个顶层 `judge`。sidecar 保存逐次工具调用（含**截断后的结果正文**，约 500 字符 —— judge 判 `groundedness` 靠它）和 `final_answer`；`load_baseline()` 必须跳过 sidecar。

`temperature` 是**实验条件**：eval 不设置它（`counting_call_llm` 原样转发），值来自
`call_llm` 签名的默认值，由 `runner.temperature_default()` 现读 —— 不硬编码，否则
miniCC 改了默认值记录里会留下一个错的温度。签名里没有这个参数时记 `null`。

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
- `groundedness`：`null`，**除非跑过 judge** —— 那时取 judge 的值（见「语义面」）。没判过就是 `null`，不能当成 0 分

失败标签包括 `INCOMPLETE`、`WRONG_TOOL`、`WRONG_ARGUMENT`、`INEFFICIENT`、`NO_EXPLORATION` 和 `CLAIMS_WITHOUT_ACTION`。规则必须优先避免假阳性，修改后要运行真实轨迹回放测试。

**已知假阴性（有意不修）：** `_edits_existing_file` 只认 `edit_file` / `write_file`，
agent 用 `sed -i` / `python -c "open(...,'w')"` 改文件时 `WRONG_TOOL`、`NO_EXPLORATION`
看不见。解析 shell 命令是启发式、会误判；对比工作区快照可靠但会打破「sidecar 就能
判定失败标签」这条不变量（回放测试失效）。漏判安全、误判危险，所以留着。

Windows 上 bash 的非零退出码不计入工具级错误：miniCC 使用 `shell=True`，实际走 `cmd.exe`，Linux shell 语法失败会造成大量环境噪声。该限制及其影响记录在 `TODO.md`。

## 语义面（judge）

`judge.py` 是**第二评估面**，默认不跑。两种跑法：`cli --judge <模型>` 在批次跑完后顺手
判一次；`python -m agenteval.judge <目录> --model <模型>` 单独判已落盘的一批（换 prompt /
换判官模型重判时用）。后者才是本体，前者只是替你调了它。

判这一步**只读已经落盘的记录，不碰 agent** —— 跑一次 agent 几十分钟，判一次很便宜，
拆开之后重判不用重跑。问两个问题：`groundedness`（最终回答里的每个事实声称有没有工具
结果支撑）和 `claims_consistent`（声称完成的动作是否真在调用日志里）。判官模型**必须
显式指定**，且不该用被测模型 —— 自己判自己有偏。

- 结果写进顶层 `judge`（按 provider 分键，`primary` 指向生效的那个），
  `evaluation.groundedness` 取 primary 的值
- 单条失败只写 `judge.status = "error"`，不中断整批；确定性记录一个字段都不动
- `status = "error"` 的 run 不判：没有可判的轨迹，而且它本来就不进任何统计
- 提问措辞是**评估逻辑的一部分**，`prompt_hash` 落进记录。baseline 对比时 prompt 版本
  或判官模型不同 → **不出语义数字**，只说明原因
- 不采信 LLM 自报的 confidence，只要 0~1 的判定值

## 报告解读（analyze）

`analyze.py` 在报告末尾追加一段 LLM 写的分析：`cli --analyze <模型>`，或单独跑
`python -m agenteval.analyze <目录> --model <模型>`（不重跑 agent）。

**它的风险和 judge 相反。** judge 产出数据，解读产出文字 —— 而报告里最显眼的数字
（工具调用 / token / 延迟）恰好全是噪声量级的，一个自由的 LLM 几乎必然写出「B 略快」。
所以 `_SYSTEM` 里把规矩写死了，**改它等于改评估逻辑**：

1. 必须引用具体数字，不许只给形容词
2. **delta 小于噪声就必须写「看不出差别」** —— 靠报告里那行 `Noise`
3. 没判过不等于 0 分
4. 只对**类别信号**（成功率 / 首动作 / 稳定失败）下强结论；还要说清数据**不支持**什么

建议部分是「推测」，必须跟数据结论分开写。失败只返回一句 `（分析失败：…）`，不连累报告。

报告里的 `Noise` 行由 `report._noise` 算：**同题 k 次重复的组内变异系数**，跨题取中位数
（只在 success 的 run 上算）。k<2 时算不出来，报告直说。它是判断 delta 有没有意义的
唯一依据 —— 没有它，上面第 2 条约束就是空话。

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

# 跑完顺手判语义面（可选）
python -m agenteval.cli --tasks tasks/ --k 3 --judge kimi

# 跑完让模型读一遍报告，写一段分析（可选）
python -m agenteval.cli --tasks tasks/ --k 3 --analyze kimi

# 单独跑已落盘的一批 —— 换 prompt / 换模型重来，不重跑 agent
python -m agenteval.judge runs/<timestamp>/ --model kimi
python -m agenteval.analyze runs/<timestamp>/ --model kimi
```

`--out` 默认是 `runs`。k=1 的效率数字只能视为单次观测，不能用于跨轮次效率结论。
baseline 对比会自动校验**能从记录里核出来的**条件（任务集 / 模型 / `model_actual` /
`agent_version` / `temperature` / k），**核不出来的一律靠人**：Python 版本、依赖、shell、
操作系统这些没进记录，调用者必须自己保证两轮一致。

只有**任务集**不一致会拒绝出 diff —— 它是秤不是变量，两组不同题的平均相减没有意义。
`model` / `temperature` / `model_actual` / `k` 的差异一律只警告（`agent_version` 不同是
版本对比的常态，不提示；相同才提示一句）。理由：这个框架的瓶颈是**噪声**（逐题工具调用
CV 中位数 29%），不是变量太多，把实验条件的变化列清楚比拦下来有用；而且能核的只有进了
记录的字段，没记录的本就拦不住。

## 明确不在 v1

- 进程隔离和并行执行
- 开放式问答任务
- 路径一致性指标
- 完整沙箱和资源隔离

这些事项的优先级和现状只维护在 `TODO.md`。语义面（`judge.py`）已经落地，但它是
**可选的一步**，不跑时报告照旧。

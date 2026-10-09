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

主记录包含评分、失败标签、`instruction`（指令原文 —— 让记录自包含，judge 事后要读「当时问的是什么」）、`agent_version`、`model_actual`、`temperature`、`first_action`、`tools_used`、`max_usage_ratio`、`compactions`，以及三个**数据质量**字段（见下）；跑过 judge 之后多一个顶层 `judge`。sidecar 保存逐次工具调用（含**截断后的结果正文**，约 500 字符 —— judge 判 `groundedness` 靠它）和 `final_answer`；`load_baseline()` 必须跳过 sidecar。

三个数据质量字段回答的是「上面那些数字可不可信」，不是「agent 干得怎么样」：

- `truncated` —— 没跑完。agent 撞上 miniCC 的 `MAX_LOOP_CNT` 就 `break` 出来，最终回答
  是空的，但产物可能照样过验收，于是它跟正常跑完的 run 在记录里**长得一模一样**。
  判定要两个条件同时满足（调用次数到顶 **且** 最后一条 assistant 消息还带工具调用）；
  少了后者会把「恰好用满上限、正常收尾」的 run 误标（`agent_loop` 正常收尾是
  「没有工具调用」→ `return`）。这类 run 不判语义面，报告单列一段。
- `missing_initial_files` —— 初始工作区里有、跑完不见了的源文件（`__pycache__` 不算，
  agent 清理它是正常的）。空列表是常态；非空时报告提示这条 run 的失败标签可能来自
  环境，不是 agent。
- `posix_separator_calls` —— bash 命令含 `;` 的次数。**只是底数，不是标志**：实测 36 个
  run 里 27 个都含 `;`，当标志会把 3/4 的 run 全标上。它给上面那件事提供解释 ——
  Windows 上 `shell=True` 走 cmd.exe、`;` 不是分隔符，`rm a; cat b` 会把 b 一起删掉。

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

`INEFFICIENT` 在**报告里**按整道题归一化：工具调用数的上限在温度不为 0 时卡在分布
尾部（实测同题三次 11 / 21 / 32，上限 20 —— 「多一次就翻」），k≥2 时只有该题每次
都命中才算这道题的性质，只中一两次的挪到「长尾」单列、不进失败分布；k=1 照旧逐 run
判。判定本身仍在 `metrics`、逐 run 记进 `failures` —— 改的只是报告怎么读它。

**已知假阴性（有意不修）：** `_edits_existing_file` 只认 `edit_file` / `write_file`，
agent 用 `sed -i` / `python -c "open(...,'w')"` 改文件时 `WRONG_TOOL`、`NO_EXPLORATION`
看不见。解析 shell 命令是启发式、会误判；对比工作区快照可靠但会打破「sidecar 就能
判定失败标签」这条不变量（回放测试失效）。漏判安全、误判危险，所以留着。

Windows 上 bash 的非零退出码不计入工具级错误：miniCC 使用 `shell=True`，实际走 `cmd.exe`，Linux shell 语法失败会造成大量环境噪声。该限制及其影响记录在 `TODO.md`。

## 语义面（judge）

`judge.py`，默认不跑。两种跑法：`cli --judge <模型>`（批次跑完顺手判）、
`python -m agenteval.judge <目录> --model <模型>`（单独判已落盘的一批，重判用这个）。
问 `groundedness`（回答里的事实声称有没有工具结果支撑）和 `claims_consistent`
（声称完成的动作是否真在调用日志里）。

- 判官模型**必须显式指定**，且不该用被测模型 —— 自己判自己有偏
- 只读已落盘的记录：跑一次 agent 几十分钟、判一次很便宜，所以重判绝不能要求重跑 agent
- 结果写进顶层 `judge`（按 provider 分键），`evaluation.groundedness` 取 `primary` 的值
- 单条失败只写 `judge.status = "error"`，不中断整批；确定性记录一个字段都不动
- `status = "error"` 的 run 不判（没有可判的轨迹，且它本来就不进任何统计）
- 最终回答为空时是 `status = "skipped"`，**不给分** —— 让模型「拿不准就给中间值」会
  凭空编一个数混进平均值（实测 4 条把 Groundedness 从 92% 拖到 88%）。跳过和失败都在
  报告里点名，并印出对不上的分母
- 解析不出来时带一句「上次为什么没解析出来」重试一次（`ATTEMPTS = 2`）：judge 的
  temperature 是 0，原样重问拿回的是同一个坏回答
- 提问措辞是**评估逻辑的一部分**，`prompt_hash` 落进记录；baseline 对比时 prompt 版本
  或判官模型不同 → **不出语义数字**
- 不采信 LLM 自报的 confidence

## 报告解读（analyze）

`analyze.py`，默认不跑：`cli --analyze <模型>` 或 `python -m agenteval.analyze <目录>
--model <模型>`。往报告末尾追加一段 LLM 写的解读。

- `_SYSTEM` 的四条硬约束，**改它等于改评估逻辑**：引用具体数字 / delta 小于噪声必须写
  「看不出差别」/ 没判过不等于 0 分 / 只对类别信号下强结论、并说清数据**不支持**什么
- 建议部分必须标成「推测」，跟数据结论分开；失败只返回一句 `（分析失败：…）`
- 依赖 `Noise` 行（`report._noise`：同题 k 次重复的**组内**变异系数取中位数，只在
  success 的 run 上算，k<2 算不出来）—— 没有它上面第 2 条就是空话

## 运行命令

```bash
cd D:/Code/python_project/agent_eval

# 离线测试 / 真实 API 集成测试
python -m pytest tests/ -q
python -m pytest tests/ -m integration -v

# 运行任务集 → runs/<timestamp>/report.txt
python -m agenteval.cli --tasks tasks/ --k 1

# 版本对比：两边必须用相同任务集、模型和 k，建议 k >= 3
python -m agenteval.cli --tasks tasks/ --k 3 --model deepseek --baseline runs/<old-run>/

# 跑完顺手判语义面 + 解读（都可选，见各自一节）
python -m agenteval.cli --tasks tasks/ --k 3 --judge kimi --analyze kimi
```

`--out` 默认是 `runs`。k=1 的效率数字只能视为单次观测，不能用于跨轮次效率结论。
baseline 对比会自动校验**能从记录里核出来的**条件（任务集 / 模型 / `model_actual` /
`agent_version` / `temperature` / k），**核不出来的一律靠人**：Python 版本、依赖、shell、
操作系统这些没进记录，调用者必须自己保证两轮一致。

只有**任务集**不一致会拒绝出 diff —— 它是秤不是变量，两组不同题的平均相减没有意义。
`model` / `temperature` / `model_actual` / `k` 的差异一律只警告（`agent_version` 不同是
版本对比的常态，不提示；相同才提示一句）。理由：这个框架的瓶颈是**噪声**（见报告里那行
`Noise`），不是变量太多，把实验条件的变化列清楚比拦下来有用；而且能核的只有进了记录的
字段，没记录的本就拦不住。

## 明确不在 v1

- 进程隔离和并行执行
- 开放式问答任务
- 路径一致性指标
- 完整沙箱和资源隔离

这些事项的优先级和现状只维护在 `TODO.md`。语义面（`judge.py`）已经落地，但它是
**可选的一步**，不跑时报告照旧。

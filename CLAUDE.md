# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个项目是什么

`agent_eval` 是一个 agent 评估框架，评估对象是 **miniCC**
（`D:\Code\python_project\miniCC`，用户自制的 mini Claude Code）。
以「多维度 + 可复现 + 可对比不同版本」为目标，衡量 agent 在真实 coding 任务上的表现。

### 仓库内文档的分工

| 文件 | 内容 | 变更频率 |
|---|---|---|
| `CLAUDE.md`（本文件） | **已实现**的设计框架、约束、约定 | 低 |
| `TODO.md` | 实施步骤表与进度（`步骤 / 验证方式 / 状态`）| **推进时持续更新** |
| `feat.md` | **待实现**特性的设计：Jev 语义评估（v2）| 落定前反复改 |
| `feat-task-redesign.md` | **待实现**特性的设计：任务方向重设计（`TODO.md` 的「问题 1」）| 落定前反复改 |
| `eval and self-evolutuon.md` | 用户最初的设想，需求来源，**不是规范** | 不改 |

分工是刻意的：
- 设计读本文件，**动手前先看 `TODO.md` 确认当前进度和下一步**
- 还没落地的东西写 `feat.md`，不写本文件 —— 本文件每个会话都会被加载，
  塞进未实现的设计只会让上下文变贵

## 与 miniCC 的接口

miniCC 已 `pip install -e`，包名是**顶层**的 `agent` / `llm` / `tools` / `cli` / `rag`，
因此在任何目录下都能 `import agent.agent`。本框架的包名定为 `agenteval`，避开这些通用名。

依赖方向是**单向**的：`agenteval` → miniCC。**不改 miniCC 的代码**，
所有需要的能力靠 eval 侧 patch 获得。

### LLM 配置一律复用 miniCC 的，eval 侧不另立一份

| 要什么 | 从哪拿 |
|---|---|
| 模型清单 / 默认模型 | `from llm.model import MODELS, DEFAULT_MODEL` |
| API key / base_url | `load_dotenv(MINI_CC_ROOT / ".env")` —— miniCC 的 `.env` |
| miniCC 仓库位置 | `Path(agent.agent.__file__).resolve().parents[1]`，不硬编码路径 |
| 上下文窗口 | `MODELS[model].context_window` |

**不要新建 `.env`、不要另写一份模型配置**。eval 侧一旦有自己的副本，
两边就会漂移（比如你在 miniCC 里加了新模型，eval 却看不见）。
将来做 LLM-as-Judge 需要模型时，同样从这里取。

### miniCC 的调用契约

```python
from agent.agent import agent_loop          # 入口
agent_loop(state, registry, context_manager, memory_manager=None,
           system_prompt=SYSTEM_PROMPT, verbose=True, permission_mode="interactive")
```

- `permission_mode="auto"` → 跳过权限询问，是无人值守的前提
- `verbose=False` → 关掉工具调用日志
- `memory_manager=None` → 跳过 memory 注入
- `state.messages` 就是完整的 trajectory（assistant 轮 + tool 结果轮交替）

## 关键约束（实测确认，改动前务必先看）

这些是踩过的坑，不是猜测：

| 约束 | 后果 |
|---|---|
| `bash` 工具用 `shell=True` 且**不传 `cwd=`** | 继承**进程** cwd，`state.cwd` 对 bash 无效。必须 `os.chdir(workspace)` |
| `shell=True` 在 Windows 上走的是 **`cmd.exe`** | 而 agent 是按 Linux 训练的、写的是 bash 语法 → `pwd; ls`、`find ... 2>/dev/null`、`which` 通通报错。实测一轮 30 次失败调用里 24 次是这个原因。它让 `error_recovery` 失真（已排除），也让**效率数字里混着不少「跟 shell 搏斗」的开销** —— 跨版本比较不受影响（各版本一样），但绝对值的含义要打折 |
| `load_cc_md()` 读 `Path.cwd()/CC.md` 和 `~/.miniCC/CC.md` | 全局 CC.md 会让历史结果不可复现，每次 run 必须屏蔽 |
| `read_file` 接受任意路径、**无沙箱** | workspace 只是约定，不是安全边界。评估自己信得过的 agent 可接受 |
| miniCC 内写法是 `from llm.call_llm import call_llm` | 名字绑定在 `agent.agent` 命名空间。**patch `llm.call_llm` 无效，必须 patch `agent.agent.call_llm`** |
| `load_dotenv()` 是 **cwd 敏感**的 | 从本仓库目录 import 时，miniCC 找不到自己的 `.env`，四个 API key 全部 MISSING。`runner.load_mini_cc_env()` 必须显式 `load_dotenv(MINI_CC_ROOT / ".env")` |
| builtin 工具失败时**返回纯字符串**，不是 error dict | `read_file` 返回 `"文件不存在: x"`、`edit_file` 返回 `"未找到要替换的内容: x"`。只有 `bash` 用 `returncode`、未知工具 / 权限拒绝 / 执行异常用 `error` 字段。失败检测必须靠**前缀匹配**（见 `trajectory._TOOL_FAILURE_PREFIXES`），是启发式规则 |
| `response.usage` 无人读取 | token 全链路无采集，需 eval 侧包一层 |
| `verify.py` 必须保持零依赖 | 任务验收程序只用纯 python 模块，**不引入 pytest**（框架自身的测试才用 pytest）。它跑在 agent 干完活的产物上，多一个依赖就多一个「环境不对、验收跑不起来」的失败面 |

## 架构

**`agenteval/runner.py` 是唯一与 miniCC 耦合的模块**，这是刻意的设计。
换 miniCC 接口、或从进程内 `chdir` 升级到子进程隔离，都只动这一个文件，其余模块不受影响。

| 模块 | 职责 | 依赖 miniCC |
|---|---|---|
| `agenteval/task.py` | 加载 Task；跑 `verify.py` 拿 `list[Check]` | 否 |
| `agenteval/runner.py` | `run_task(task, run_idx) -> RunResult`（record + calls 明细）| **是** |
| `agenteval/trajectory.py` | `state.messages` → 轨迹结构与效率指标 | 否 |
| `agenteval/metrics.py` | `Trajectory + Checks → 五维分 + 失败分类` | 否 |
| `agenteval/report.py` | 聚合 N 个 run → 文本报告 + json；与基线对比出 diff | 否 |
| `agenteval/cli.py` | 入口 | 否 |

`runner.py` 内的两个 patch：

```python
import agent.agent as A
A.call_llm  = _counting_wrapper(A.call_llm)   # 采 token / latency
A.load_cc_md = lambda: ""                     # 屏蔽全局 CC.md，保证可复现
```

**两者都是模块级全局状态 → 串行跑安全，并行跑不安全。** 当前设计串行执行；
将来做并行必须连同一并改掉（见 `TODO.md` 的 v2 待办）。

## 数据模型

任务 = 一个目录，三部分对应「初始状态 / 任务描述 / 评估方法」：

```
tasks/<task_id>/
  task.yaml        # 任务描述 + tool_usage 规则
  workspace/       # 初始状态，每个 run 复制一份临时工作区
  verify.py        # def check(workspace) -> list[Check]
```

```yaml
# task.yaml
task_id: fix_offbyone_001
category: correctness            # 仅用于报告聚合
instruction: |
  calc.py 的 average() 在空列表时崩溃。请修复，不要改变非空时的行为。
expected_tools: [read_file, edit_file]   # 可选，只记录用于分析，不影响分数
forbidden_tools: [write_file]            # 可选。写文件类工具只看有没有覆盖已存在的文件
max_tool_calls: 12                       # 可选，超出 → INEFFICIENT
retired: false                           # 可选。true = 从默认任务集拿掉，但**目录留着**
                                         # —— 真实轨迹回放测试要从活的 tasks/ 读配置
```

```python
# verify.py —— 纯 python，不用 pytest
from agenteval.task import Check

def check(workspace):
    return [
        Check("空列表返回 None", _empty_ok(workspace), weight="essential"),
        Check("非空列表结果不变", _normal_ok(workspace), weight="important"),
        Check("未引入副作用", _no_side_effect(workspace), weight="minor"),
    ]
```

`Check(name, passed, weight, detail=None)`，`weight ∈ {essential, important, minor}`，
分值 3 / 2 / 1 —— 天然给出部分分，这是不依赖 pytest 也能算「完整性」的原因。
`workspace` 是复制后的临时工作区路径，`verify.py` 只读它、不改它。

### 任务设计约定

- **新增任务必须在 `tests/test_tasks.py` 的 `REFERENCE_FIXES` 里登记参考修复**，
  它会正反两面都测：未修复时必须挂掉至少一条 `essential`（否则这道题没区分度），
  套用参考修复后必须全过（否则验收程序过严，会冤枉做对的 agent）。
  这两条不调 LLM、秒级，加完题立刻跑。
- **`verify.py` 要堵住捷径。** 反例：`trace_units_006` 的症状是「正常读数误报过热」，
  如果只检查告警文案，agent 把 `OVERHEAT_C` 阈值改大就能蒙过去 —— 所以必须同时检查
  **显示出来的温度值本身**是对的。另一个反例：要求 agent「写文档描述接口」这类产出
  验收不可靠（把源码整段抄进去就蒙过去了），所以 `module_contracts_012` 让 agent 产出
  `contracts.py` 这个**字典**，验收侧用 `inspect` 现算真值来比对。
- **真值尽量现算，别写死。** `module_contracts_012` 的期望值是从模块本身
  `inspect` 出来的，不是常量表 —— 工作区一改，验收自动跟上。
- **`load_workspace_module` 加载失败是返回 `None`，不会抛。** 而
  `inspect.getmembers(None, ...)` 会安静地给出空列表 —— 于是「真值」变成 `{}`，
  检查**静默通过**。凡是拿它算期望值的 `verify.py`，都要显式判 `None` 并返回一条
  失败的 essential（`module_contracts_012` 的做法）。
- **陷阱靠文件系统元数据的话，要配一条「陷阱还在吗」的测试。** `readonly_config_011`
  靠只读属性，而打包 / 压缩解压 / 编辑器另存都可能把它抹掉，抹掉了这道题就退化成
  普通题 —— 静默失效比报错危险。`test_tasks.py::test_readonly_trap_is_still_armed` 盯着它。
  同理，`REFERENCE_FIXES` 的应用要能处理只读文件（`apply_reference_fix` 会先解开属性）。
- 需要 import 工作区里多个模块的 `verify.py`，用 `task.workspace_importable(workspace)`
  上下文管理器（它会清理 `sys.modules`，否则 k>1 时第二次 run 会拿到上一次的旧模块）。
- 跑子进程时**不要按文本捕获输出**（Windows 上父子默认编码不一致会 `UnicodeDecodeError`），
  只看 `returncode` 就够。
- 需要 import 工作区里多个模块、且**模块之间互相 import** 的 `verify.py`，
  用 `task.workspace_importable(workspace)`（`extract_helper_013` 就靠它 ——
  6 个调用点改成 `from duration import ...` 之后，站内 import 需要工作区在 `sys.path` 上）。
  Windows 上 `shutil.rmtree` **删不掉只读文件**，所以 `runner` 的清理用了
  `onerror=_remove_even_if_readonly`（注意 `ignore_errors=True` 会覆盖 `onerror`，只能二选一）。
- **约束必须写进 `instruction`**，不能只写在 `task.yaml` 里。`forbidden_tools`
  只写在 yaml 里的话，agent 根本看不到，那条标签测的就成了「有没有猜中我们的隐藏偏好」
  （实测踩过：某轮 `WRONG_TOOL` 报 62%，全是这个原因）。措辞要**精确陈述约束本身**，
  别顺手写出规避办法 —— 比如写「不要用 `write_file` 覆盖已有的源文件」就够了，
  多写一句「写临时脚本没问题」会主动引导 agent 去写验证脚本，
  反而污染 `CLAIMS_WITHOUT_ACTION` 的测量。
- **新增规则后要检查它会不会跟别的规则打架**。`WRONG_TOOL` 惩罚「用 write_file」，
  而 `CLAIMS_WITHOUT_ACTION` 奖励「写脚本自查」—— 同一个行为被两条规则反向评价，
  这是设计缺陷，不是 agent 的问题。
- `max_tool_calls` 用来让低效能被 `INEFFICIENT` 抓到 —— 便宜的题卡紧一点。

每次 run 落一个 json，**schema 与原设想文档一致**，v1 额外增加的字段一律放**顶层**
（都在 `trajectory` 之外，以免破坏那 5 个字段的 schema）：

```json
{
  "task_id": "fix_offbyone_001",
  "run_id": "run_002",
  "agent_version": "<miniCC git rev>",
  "model": "kimi",
  "model_actual": "<response.model，API 实际返回的模型标识>",
  "first_action": "explore",
  "tools_used": ["edit_file", "grep", "read_file"],
  "max_usage_ratio": 0.02,
  "compactions": 0,
  "status": "failed",
  "trajectory": { "steps": 8, "tool_calls": 5, "llm_calls": 3,
                  "tokens": 6200, "latency_ms": 12400 },
  "evaluation": { "task_success": false, "correctness": 0.5,
                  "completeness": 0.4, "tool_usage": 0.5,
                  "groundedness": null, "error_recovery": 0 },
  "failures": ["WRONG_TOOL", "INCOMPLETE"]
}
```

`status ∈ {success, failed, vetoed, error}`，其中 `error` 表示 runner 自身异常
（agent 崩溃 / 超时），**不要混进失败分布统计**。`groundedness` 恒为 `null`（v1 不产出）。

三个「不是五维分、但在顶层」的字段：

| 字段 | 含义 |
|---|---|
| `first_action` | 首个工具调用是探索类还是修改类（`explore` / `edit` / `other` / `null`）|
| `tools_used` | 这次用到的工具名（**去重后排序**）。`first_action` 的一般化形式 —— 有了它，报告判断「用没用 `run_subagent`」不必去读 sidecar |
| `max_usage_ratio` | 峰值 `prompt_tokens ÷ context_window`。**负载指标，不是评估信号** —— 用来回答「这批题离压缩线（0.8）还有多远」 |
| `compactions` | 这次 run 真正压缩了几次。在 `ContextManager` 实例上挂计数 wrapper 得到（`auto_compact` 走的是 `self.compact`，见 `runner.drive_agent`），**不靠扫 messages** —— 重复压缩会把旧摘要再摘一次，扫出来会少数 |

`max_usage_ratio` / `compactions` 存在的理由：`deepseek` 的窗口是 100 万，
压缩阈值 0.8 → 要 80 万 token 才触发。这两个数一摆出来，报告就能说明
「上下文压缩这条线为什么没测」是**碰不着**，而不是忘了测。

### trajectory 各字段的精确定义

（`trajectory.py` 的唯一职责，必须无歧义）

| 字段 | 定义 |
|---|---|
| `steps` | `len(assistant 消息) + len(tool 消息)` —— agent 在 messages 里产生的所有条目数 |
| `llm_calls` | patch 计数器，等于 assistant 消息数（每次 `call_llm` 产出一条 assistant 消息）|
| `tool_calls` | 所有 assistant 消息里 `tool_calls` 数组的长度之和 |
| `tokens` | 每次 `response.usage.total_tokens` 累加 |
| `latency_ms` | 整个 run 的墙钟耗时（含工具执行时间），非纯 LLM 耗时 |

除上述五项外，`trajectory.py` 还产出**逐次工具调用明细** `calls: [{name, arguments,
ok, error}]` —— 失败分类全部依赖它。它**不落进 run json**（保持 schema 与原设想文档一致），
而是由 cli 单独写成 `<task_id>_<run_id>.calls.json` 这个 **sidecar**。

sidecar 存在的唯一理由：**失败标签判得对不对，事后要能核对**。
曾经因为没有它，一个 `CLAIMS_WITHOUT_ACTION` 假阳性只能靠重跑一次 API 才查出来。
`load_baseline()` 会跳过 `*.calls.json`，别让 sidecar 混进 run 记录统计。

### 「可复现」的准确含义

本框架的「可复现」指**可重复执行、可对比**，**不指结果一致**。两个原因：

- `llm/call_llm.py` 硬编码了 `temperature: 1`，方差拉满
- `MODELS` 里是 `kimi-k2.6` / `deepseek-flash` 这类服务端标识，provider 静默升级模型版本时，
  同一个 `agent_version` 会跑出不同结果

所以 `RunRecord` 必须记录 API 实际返回的 `response.model`（字段名 `model_actual`），
它比 `MODELS` 里的配置名可信得多。`agent_version`（miniCC 的 git rev）记录代码版本，
`model_actual` 记录模型版本，**两者缺一不可** —— 只有代码版本时，报告里的分数变化无法归因。

## 评分与失败分类

五维分（v1 只用确定性规则，**不含 LLM-as-Judge**）：

| 维度 | 算法 |
|---|---|
| `task_success` | 所有 `essential` check 通过 |
| `correctness` | `essential` + `important` checks 的通过率。**不能只算 essential** —— 那样 `task_success` 恒等于 `correctness == 1.0`，两个维度退化成同一条曲线，并排列在报告里会让人误以为交叉验证了两次 |
| `completeness` | 全部 checks 加权通过率（3/2/1）|
| `tool_usage` | 基线 1.0；每个违规用到的 `forbidden_tool` 扣 0.5；clip [0,1]。**写文件类工具带路径判断**：只有动了初始工作区里已有的文件才算违规（理由见下）。`expected_tools` **只记录、不扣分** |
| `error_recovery` | 无**工具级**错误 → `null`。有错误时分三档：错误后**同一工具重试成功** = 真恢复 `1.0`；换别的工具绕过去 = 绕路 `0.5`；始终无成功动作 = `0.0`。只判「有无成功动作」区分不了真恢复和碰巧下一步对了。**`bash` 的退出码非 0 不算工具级错误** —— 见下 |
| `groundedness` | v1 恒 `null`；v2 由 Jev 填（`noul` 的概率即期望值），设计见 `feat.md` |

### 失败标签（全部走规则，无需 LLM）

| 标签 | 判定 |
|---|---|
| `INCOMPLETE` | 有 `essential` / `important` check 失败 |
| `WRONG_TOOL` | 违规使用 `forbidden_tools`。**写文件类工具带路径判断** —— 只有覆盖初始工作区里已存在的文件才算。**不含 `expected_tools` 未命中**，后者只记录不判负 |
| `WRONG_ARGUMENT` | 有工具返回参数类错误 |
| `INEFFICIENT` | `tool_calls > max_tool_calls`，或同一 `(name, arguments)` **连续重复 ≥3 次**。**中间改过文件就归零** —— `读 → 改 → 读（确认）` 是正当工作流，实测被判成盲目重复过 |
| `NO_EXPLORATION` | 改了**初始工作区里已存在的文件**，却从没用过任何探索类工具（`read_file`/`list_dir`/`glob`/`grep`）|
| `CLAIMS_WITHOUT_ACTION` | agent 声称「测试通过」，但整条轨迹里**一行代码都没执行过** |
| ~~`MISSING_CONTEXT`~~ | 原设想是「声称找不到 X 但 X 存在」，**判不准已废弃**。从自然语言里抠出 X 再回查工作区不可靠，理由同 `HALLUCINATION`。现由 `NO_EXPLORATION` 承担 Failure Pattern #1 |
| ~~`HALLUCINATION`~~ | v1 不产出 —— 规则判不准，硬做比不做更误导。v2 由 Jev 的 `groundedness` 间接覆盖 |

上表里有 **3 条从未在真实 run 上触发过** —— `NO_EXPLORATION`、`WRONG_ARGUMENT`、安全 veto，
22 次真实运行里一次都没命中，只在单元测试里验过。按「假阳性主要靠真实数据才暴露」的规律，
它们**第一次在真实数据上生效时大概率也是错的**。所以报告里把前两条固定标注成「未验证」、
**别当结论用**（`report.UNVERIFIED_RULES`）—— **veto 不在其中**，它不是信号规则而是安全网，
理由见本节末的 Veto 段。

「怎么脱掉标注」也分两种情况：`NO_EXPLORATION` 能靠出题**定向触发**（设计见
`feat-task-redesign.md` 的探针题 `blind_edit_014`）；`WRONG_ARGUMENT` **逼不出来** ——
它只在模型自己出错时才亮（吐坏 JSON、或猜错参数名），那是模型行为不是 miniCC 机制，
只能靠意外慢慢攒证据。

`NO_EXPLORATION` 与 `CLAIMS_WITHOUT_ACTION` 是本框架**最有价值的信号**，对应原设想文档
点名的 Top Failure Patterns（「不去检查仓库结构」「声称测试通过但没执行」），
且可纯程序化检测。**但 `NO_EXPLORATION` 至今没在真实 run 上触发过**（见上），
它的价值目前是设计上的判断，不是实测结论。

这几条规则都反复收窄过，教训完全一致 —— **误报会持续污染失败分布**，
而且代价很大：某一轮 8 个 run 报出的 7 条标签，**事后用 sidecar 核对发现 6 条是假的**。
所以规则宁窄勿宽，判不准的一律不做（`MISSING_CONTEXT` / `HALLUCINATION` 直接废弃）。

收窄记录（全是实测踩出来的）：

- `NO_EXPLORATION` 必须是「**已存在**的文件」—— 创建新文件本来就不需要先探索，
  否则「建一个 hello.txt」这种任务会被无脑误报
- `WRONG_TOOL` 对写文件类工具必须**带路径判断** —— agent 习惯用 `write_file`
  写一次性验证脚本（`_check_roman.py`、`test_rate_limiter.py`）来自查，
  按工具名一刀切会把它判成违规。**而「主动写脚本验证」正是本框架想鼓励的行为**
  （`CLAIMS_WITHOUT_ACTION` 就在奖励它），两条规则不能互相打架
- `CLAIMS_WITHOUT_ACTION` 收窄了三次，最后退到最粗的口径：只在
  **一行代码都没执行过**时触发。前两版试图判断「这次算不算测试」，先漏了
  `python test_x.py`、后漏了 `python3 -c "assert ..."` ——
  **两次都是 agent 明明真验证过却被判撒谎**
- `CLAIMS_WITHOUT_ACTION` 的另一半「已修复」**不做** —— agent 可以用 `bash` 的
  `sed -i` 改文件，没有 `edit_file` 调用不代表没改过
- `error_recovery` 把 **`bash` 的非 0 退出码排除**出去（`trajectory.is_command_failure`）。
  实测：一轮 12 个 run 共 30 次「失败调用」，**24 次（80%）是这一类**，而它们主要是
  Windows 上 `shell=True` 走 `cmd.exe`、agent 写的却是 bash 语法造成的 —— 和 agent
  能力无关，而且几乎必然「恢复」（换个写法重试）。算进来的后果是 `error_recovery`
  恒等于 1.0，这个维度等于没测。改完之后有取值的从 10/12 降到 4/12，且**全部由设计好的
  陷阱驱动**，含义才干净
- `INEFFICIENT` 的重复判定要**排除「中间有改动」的情形**。实测：`encoding_trap_010`
  那轮 `read_file pricing.py` 出现 3 次，分别在「初次探索」「改完确认」「整个重写之后
  再确认」之后 —— 规则自己的注释就写着「阈值 3 是为了不误伤合法的重复读取」，
  判它 INEFFICIENT 是跟自己的意图打架

**一个 run 可同时命中多个标签**，所以报告里的分布计数必须**按 run 归一化**
（`出现次数 / run 数`）并注明，否则「INEFFICIENT 14」会被误读成「14 个 run 失败」。

**真实轨迹回放测试 = 规则的回归护栏。** 改规则时靠单元测试不够（写用例时脑子里想的
场景和 agent 实际会做的事不一样），必须拿真实数据验：

```
tests/fixtures/runs/<轮次>/<task>_<run>.calls.json   真实 run 的 sidecar 快照
tests/fixtures/expected.json                          人工逐条核对过的「应该报哪些失败」
tests/test_rules_on_real_trajectories.py              回放验算
```

回放**不用重跑 API** —— sidecar 里的 `calls`（含 `ok`/`error`）+ `final_answer`
就是判定失败标签所需的全部输入，直接从它重建 `Trajectory` 喂给 `metrics.evaluate`。
`expected.json` 里的期望值是**逐条看 sidecar 判出来的**，不是照抄 run json
（那批记录本身就是错的：6 条假阳性）。任务配置从活的 `tasks/<id>/` 读，所以题目配置
一改、测试也会报错 —— 那同样是要的信号。

**安全 Veto（最小版）**：bash 命令过危险正则（`rm -rf /`、`format`、改 `USERPROFILE` 等），
命中则整个 run 标记 `vetoed`、总分归零。这是明确的最小版，不是完整的安全评估。

它 **不在 `UNVERIFIED_RULES` 里**，尽管 22 次真实运行一次都没触发过 —— 因为它是**安全网**，
和上面那两条信号规则不是一类东西：它不产生失败标签、不进失败分布。安全网要问的是
「该拦的拦住了吗」，单元测试就能答（`test_veto_zeroes_everything` /
`test_veto_not_triggered_by_benign_rm`）；它万一假阳性，表现是某一轮莫名 0 分，一眼可见。
而且**它无法靠出题「定向触发」** —— 要亮得 agent 真跑出 `rm -rf /`，没有正当任务会诱导它，
故意引诱就是在设计钓鱼测试。

### 效率与行为指标（聚合时算，不参与五维分）

| 指标 | 定义 | 为什么 |
|---|---|---|
| **条件效率** | 只在 `status == "success"` 的 run 上统计 tool_calls / tokens / latency | 无条件统计会把「失败得快」算成高效。效率只在成功的前提下才有意义 |
| **首动作类型** | 首个工具调用是探索类（`read_file` / `list_dir` / `glob` / `grep`）还是修改类（`write_file` / `edit_file`） | 把 Top Failure Pattern #1「不去检查仓库结构」变成数字。单点、高信号 |

多次重复跑时，`pass@k`（k 次至少一次成功）与 `pass^k`（k 次全成功）**独立保留，不做加权求和**。

### 报告格式

每次 run 落一个 json 到 `runs/<timestamp>/<task_id>_<run_idx>.json`，sidecar 落
`<...>.calls.json`；k 次跑完聚合出文本报告 —— **报告也落盘成
`runs/<timestamp>/report.txt`**（`cli.write_report`）。报告本来就能从那批 run 记录
重新生成，但 `render` 的格式和聚合逻辑以后会改，存下来的才是当天那份原件。
文件名固定 `report.txt`：`load_baseline` 只读 `*.json`，不会把它当 run 记录误读。

```
Agent Evaluation Report        tasks: 5   k: 3   model: kimi
────────────────────────────
Task Success       82%  (pass@3 100%  pass^3 67%)
Correctness        88%
Completeness       76%
Tool Usage         91%
Error Recovery     71%
Groundedness       n/a (needs judge)

Efficiency  (仅统计 success 的 run)
  Avg tool calls   8.2   Avg LLM calls 4.1   Avg tokens 7.8k   Avg latency 14.2s
  First action     explore 80%   edit 20%
  Context          max usage 2%   compactions 0

Failure Distribution  (按 run 归一化，一个 run 可命中多个标签)
  INEFFICIENT 0.62   WRONG_TOOL 0.53   INCOMPLETE 0.40
  未验证（从未在真实 run 上触发过，假阳性率未知，别当结论用）  NO_EXPLORATION   WRONG_ARGUMENT
```

`--k` 默认 1，k=1 时 `pass@k` / `pass^k` 不显示。

`Context` 那行是**负载指标、不是评估信号**：`max usage` 取峰值（不取均值 —— 要回答的是
「最接近压缩到什么程度」），`compactions` 取总数。

**k=1 时报告必须自己声明效率数字不可比。** `call_llm` 硬编码 `temperature=1`，
方差拉满 —— 同一道题跑两次能差 3 倍工具调用、13 倍 token。所以 k=1 时
Efficiency 段会钉一句「只有单次观测，勿跨轮次比较」，`--baseline` 的效率 delta
在任一侧 k=1 时**直接跳过**：两次噪声相减等于把随机波动说成趋势。

`--baseline runs/<旧目录>/` 追加逐任务的「本次 vs 基线」diff（哪些任务翻盘、效率涨跌）——
**这是「可对比不同版本」这个核心目标的直接支撑**，只有绝对值的报告回答不了「v1.2 比 v1.1 好在哪」。

**逐任务的 diff 必须比效率和行为，不能只比成功率。** 题集饱和时成功率永远是
`100% → 100%`，只比它就等于什么都不报 —— 那段会**一直空着**（而这正是「比较版本」
要用的地方）。所以 `Per task` 除成功率外还比 `first_action` / `tool_calls` / `tokens` /
子 agent 使用率；`Overall` 段另出一行 `First action` 比两边的首动作分布。

**`first_action` 是类别不是数字，所以它跟 `success` 一个待遇 —— k=1 也照比。**
任一侧 k=1 时 `tool_calls` / `tokens` / `subagent` 这些数值要跳过（只报成功率翻转），
否则一边说「需 k≥2」、一边把 13 倍的 token 噪声印出来，等于自己打自己脸。
但首动作没有这个毛病：单次观测下「这次是先探索还是直接改」是**确定的**，
而正确性饱和之后，它往往是报告里唯一还能报出差异的地方。**不过要拿到可靠的
效率 / 成本对比，k 仍然必须 ≥2。**

`Per task` 的 `first_action` 取**众数**（k=3 且三次不一致时看多数），分布由
`Overall` 那行承担 —— 逐任务列分布会把那段撑爆。平票时取字典序第一个，保证可复现。

## 待实现特性的设计

还没落地、但已经讨论清楚的设计放在 **`feat.md`**，不写在这里 —— 本文件每个会话都会
被加载，塞进未实现的东西只会让上下文变贵。`feat.md` 当前的内容是
**Jev 语义评估（v2）**。

## 怎么跑起来

### 前置条件

| 要什么 | 说明 |
|---|---|
| **miniCC 已 `pip install -e`** | `import agent.agent` 在任何目录下都可用。本框架靠它工作 |
| **API key** | **复用 miniCC 的 `.env`**，eval 侧不另立一份（理由见上文「LLM 配置一律复用 miniCC 的」）。跑评估会真调 API |
| **`pytest`** | 只有跑框架自身的测试才需要 —— 任务验收程序 `verify.py` 不用它 |
| **在项目根目录下运行** | 本仓库**没有 `pyproject.toml`、不需要 `pip install`**，但必须先 `cd D:\Code\python_project\agent_eval` —— `agenteval` 是相对当前目录被 import 的 |

实测环境：Python 3.11.9 / pytest 9.1.1。

### 跑评估（会真调 API）

```bash
cd D:/Code/python_project/agent_eval
python -m agenteval.cli --tasks tasks/ --k 1        # 12 题，实测约 10.5 分钟
```

| 参数 | 作用 |
|---|---|
| `--tasks tasks/` | 任务集目录，每个子目录含 `task.yaml`（`retired: true` 的会被跳过）|
| `--k 1` | 每个任务跑几次。**要比较版本必须 ≥2** —— k=1 的效率数字报告会自己标成「不可比」 |
| `--model deepseek` | 被测模型，可选 `deepseek` / `kimi`（默认 `deepseek`）|
| `--out runs` | 结果输出根目录 |
| `--baseline runs/<旧目录>/` | 追加逐任务的「本次 vs 基线」对比 —— **这就是「比较两个 miniCC 版本」的用法**：版本 A 跑一轮存成目录，版本 B 跑的时候拿它当 baseline |

### 结果落在哪

```
runs/<时间戳>/
  <task_id>_<run_id>.json         主记录：五维分 + 失败标签 + 用量
  <task_id>_<run_id>.calls.json   sidecar：逐次工具调用，事后核对失败标签靠它
  report.txt                      汇总报告（终端也打印一份）
```

⚠️ **报告不会重算标签，只读 run 时落盘的那一份。** 所以改完规则之后，历史目录里的
标签还是旧的 —— **想让新规则生效必须重跑**。想拿回某次的报告文本，读那个目录里的
`report.txt` 就行（或 `render(load_baseline('<目录>'))` 重新生成，但格式会是**现在**的）。

### 跑测试

```bash
python -m pytest tests/ -q                                    # 全套，离线、秒级
python -m pytest tests/test_rules_on_real_trajectories.py -v  # 只跑真实轨迹回放
python -m pytest tests/test_metrics.py::test_veto_zeroes_everything -v   # 单个用例
python -m pytest tests/ -m integration -v                     # 真调 API 的用例，默认被排除
```

`pytest.ini` 里用 `addopts = -m "not integration"` 把真调 API 的用例挡在默认套件外，
保证 `pytest tests/` 是**离线、秒级**的。需要真实模型行为的测试（runner / 端到端）
打 `@pytest.mark.integration`。

任务验收程序 `verify.py` **不用 pytest**，保持零依赖 —— 不要给它引入测试框架。

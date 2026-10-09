# agent_eval

给 **miniCC**（自制的迷你 Claude Code）做的 agent 评估框架。

跑真实 coding 任务 → 采轨迹 → 用确定性规则打分 →（可选）LLM 语义面 → 出报告，
并支持跟历史轮次对比。**目的只有一个：回答「miniCC 的新版本比旧版本好在哪」。**

| 文档 | 内容 |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 已实现的设计框架、约束、踩过的坑（**读代码前先读它**）|
| [`TODO.md`](TODO.md) | 实施进度、当前结论，以及尚未实施的计划与设计 |

## 它怎么工作

```
python -m agenteval.cli --tasks tasks/ --k 3 --baseline runs/<旧目录>/
```

| 模块 | 职责 | 依赖 miniCC |
|---|---|---|
| `agenteval/task.py` | 加载任务；跑 `verify.py` 拿验收结果 | 否 |
| `agenteval/runner.py` | **主耦合点** —— 复制临时工作区、`chdir`、patch `call_llm` 采用量、驱动 `agent_loop` | **是** |
| `agenteval/trajectory.py` | `state.messages` → 轨迹结构、效率指标、逐次工具调用明细（含截断结果，给 judge 用）| 否 |
| `agenteval/metrics.py` | 轨迹 + 验收结果 → 五维分 + 失败标签 | 否 |
| `agenteval/judge.py` | **第二评估面**：读落盘的 run，用 LLM 判语义问题。独立一步，不碰 agent | 读 `MODELS` / `.env` |
| `agenteval/analyze.py` | 报告解读：让 LLM 读一遍报告写一段分析。不碰 agent，也不产生数据 | 读 `MODELS` / `.env` |
| `agenteval/report.py` | 聚合 N 个 run → 文本报告 + 基线 diff | 否 |
| `agenteval/cli.py` | 入口 | 否 |

换 miniCC 的 agent 接口、或将来做子进程隔离，只动 `runner.py` 就够了。

## 快速开始

前置条件：

- **miniCC 已 `pip install -e`** —— `import agent.agent` 在任何目录下可用
- **API key** —— 复用 miniCC 的 `.env`，本仓库**不另存一份**（避免两边漂移）
- **`pytest`** —— 只有跑框架自身的测试才要；任务的验收程序 `verify.py` 是零依赖的
- **必须在仓库根目录运行** —— 没有 `pyproject.toml`、不需要 `pip install`

```bash
cd D:/Code/python_project/agent_eval

python -m pytest tests/ -q                     # 319 个用例，离线约 15 秒
python -m pytest tests/ -m integration -v      # 真调 API 的用例，默认被排除

python -m agenteval.cli --tasks tasks/ --k 1   # 12 次运行，约 12 分钟
python -m agenteval.cli --tasks tasks/ --k 3   # 36 次运行，约 40 分钟（实测）

# 跑完顺手判语义面 + 让模型读一遍报告写段分析（都可选）
python -m agenteval.cli --tasks tasks/ --k 3 --judge kimi --analyze kimi
```

| 参数 | 作用 |
|---|---|
| `--tasks tasks/` | 任务集目录，每个子目录含 `task.yaml`（`retired: true` 的会被跳过）|
| `--k 3` | 每个任务跑几次。**要比较版本必须 ≥2**，理由见下 |
| `--model deepseek` | 被测模型，可选 `deepseek` / `kimi` |
| `--out runs` | 结果输出根目录 |
| `--baseline runs/<旧目录>/` | 追加逐任务的「本次 vs 基线」对比 —— **这就是「比较两个 miniCC 版本」的用法** |
| `--judge kimi` | 跑完顺手判一次语义面。**别填被测模型**（自己判自己有偏）；换 prompt 重判用 `agenteval.judge` |
| `--analyze kimi` | 跑完让模型读一遍报告，写一段解读追加到报告末尾 |

结果落在 `runs/<时间戳>/`：主记录 `<task>_<run>.json`、逐次工具调用的 sidecar
`<task>_<run>.calls.json`（事后核对失败标签靠它，里面也存了工具结果的截断版 ——
judge 判 `groundedness` 要用）、汇总 `report.txt`。

主记录里另有三个**数据质量**字段：`truncated`（撞上 miniCC 的循环上限，没跑完）、
`missing_initial_files`（跑完时初始工作区少了文件）、`posix_separator_calls`（bash 命令
含 `;` 的次数）。报告里的 `Truncated` / `Environment` 两段读的就是它们 —— 详见 `CLAUDE.md`。

## 一个任务长什么样

```
tasks/<task_id>/
  task.yaml        # 任务描述 + 约束
  workspace/       # 初始状态，每次 run 复制一份临时工作区
  verify.py        # def check(workspace) -> list[Check]
```

```yaml
task_id: fix_offbyone_001
instruction: |
  calc.py 的 average() 在空列表时崩溃。请修复，不要改变非空时的行为。
forbidden_tools: [write_file]   # 可选
max_tool_calls: 12              # 可选，超出 → INEFFICIENT
retired: false                  # 可选，true = 从默认任务集拿掉但目录留着
```

```python
# verify.py —— 纯 python，零依赖，不用 pytest
def check(workspace):
    return [
        Check("空列表返回 None", _empty_ok(workspace), weight="essential"),
        Check("非空列表结果不变", _normal_ok(workspace), weight="important"),
    ]
```

`weight ∈ {essential, important, minor}` 对应 3/2/1 分，天然给出部分分。

## 评分

| 维度 | 算法 |
|---|---|
| `task_success` | 所有 `essential` check 通过 |
| `correctness` | `essential` + `important` 的通过率 |
| `completeness` | 全部 check 的加权通过率 |
| `tool_usage` | 基线 1.0，每个违规用到的 `forbidden_tool` 扣 0.5 |
| `error_recovery` | 无工具级错误 → 不适用；有则分三档：同工具重试成功 1.0 / 换工具绕路 0.5 / 没恢复 0.0 |
| `groundedness` | 由语义面（`judge.py`）填。**没跑过 judge 就是空**，不是 0 分 |

前五个维度全部是确定性规则，只看轨迹和产物。`groundedness` 和 `claims_consistent` 是
**第二评估面**：跑 `agenteval.judge` 才有，详见下面「语义面」。

`error_recovery` 只统计**工具级**错误。「命令跑完了、只是退出码非 0」不算 —— Windows 上
`shell=True` 走 cmd.exe，而被测 agent 写的是 Linux shell 语法，这类失败大面积出现且几乎
必然恢复，算进去这个维度会恒等于 1.0（实现见 `trajectory.is_command_failure`）。

失败标签（全部走确定性规则，不用 LLM）：

| 标签 | 判定 |
|---|---|
| `INCOMPLETE` | 有 `essential` / `important` check 失败 |
| `WRONG_TOOL` | 覆盖了初始工作区里已有的文件，而该工具在 `forbidden_tools` 里 |
| `WRONG_ARGUMENT` | 工具参数出错（坏 JSON、猜错参数名）。**从未在真实 run 上触发过**，报告里标「未验证」 |
| `INEFFICIENT` | 工具调用超上限，或同一调用盲目重复。**报告里按整道题归一化** —— 见下 |
| `NO_EXPLORATION` | 改了已有文件却从没探索过。同样**从未触发过** |
| `CLAIMS_WITHOUT_ACTION` | 声称「测试通过」但一行代码都没执行过 |

这些规则都反复收窄过 —— **误报会持续污染失败分布**，判不准的一律不做。收窄记录见 `CLAUDE.md`。

`INEFFICIENT` 的阈值在温度不为 0 时卡在分布尾部（实测同题三次 11 / 21 / 32，上限 20 ——
「多一次就翻」）。所以报告里 k≥2 时只有该题**每一次**都超限才算这道题的性质，只中一两次
的挪到「长尾」单列、不进失败分布；k=1 无从判断，照旧逐 run 判。

## 报告长什么样

```
Agent Evaluation Report        tasks: 12   k: 3   model: deepseek   temp: 0.1
────────────────────────────
Task Success      97%  (pass@3 100%  pass^3 92%)
Correctness       97%
Completeness      97%
Tool Usage        99%
Error Recovery    83%  (n=12/36)
Groundedness      92%  (n=31/36)

Judge        model deepseek   prompt 318aef70   judged 31/36（4 条跳过（最终回答为空）；1 条判失败：roman_008/run_001，都不计入平均值）
  Claims consistent  99%

Efficiency  (仅统计 success 的 run)
  Avg tool calls 14.6   Avg LLM calls 13.3   Avg tokens 53.3k   Avg latency 67.5s
  First action     explore 100%
  Noise            tool calls ±17%   tokens ±25%   latency ±37%   （同题重复跑的组内散布，中位数）
  Context          max usage 1%   compactions 0   （没触发过压缩，这条线没信号）
  Subagent         0/36 用了 run_subagent   （一次没用，这条线没信号）

Failure Distribution  (按 run 归一化，一个 run 可命中多个标签)
  INCOMPLETE 0.03   WRONG_TOOL 0.03
  长尾  4 次 INEFFICIENT 只出现在题内部分 run 上 —— 上限卡在噪声里，不算这道题的性质，不计入上面的分布
    follow_spec_003/run_002  top_words_005/run_003  trace_units_006/run_001  trace_units_006/run_003
  未验证（从未在真实 run 上触发过，假阳性率未知，别当结论用）  NO_EXPLORATION   WRONG_ARGUMENT

Truncated  4 个 run 撞到 miniCC 的循环上限（MAX_LOOP_CNT），没跑完：没有最终回答，也不判语义面
  follow_spec_003/run_002  module_contracts_012/run_001  module_contracts_012/run_002  trace_units_006/run_003

Environment  （跑完时工作区被破坏的 run —— 它们的失败标签可能来自环境，不是 agent）
  top_words_005/run_003   跑完时少了 wordcount.py
  成因线索：这批 27/36 个 run 的 bash 命令含 `;`（共 143 次）……（Windows 上 cmd.exe 不认 `;`）
```

几处刻意的写法：

- **`(n=12/36)`** —— `Error Recovery` 只在**出过工具错误**的 run 上有值、`Groundedness`
  只在判过的 run 上有值。分母不等于 run 总数的维度必须印 n，否则 83% 会被读成「83% 的 run」。
- **`Truncated`** —— agent 撞上 miniCC 的 `MAX_LOOP_CNT` 就退出来了，最终回答是空的，
  但产物照样过验收：不单列的话它跟正常跑完的 run 在报告里长得一样。
- **`Environment`** —— 这条 run 的 `WRONG_TOOL` 是 Windows 的 `;` 在 cmd.exe 下把工作区
  文件删掉造成的（agent 随后只能用 `write_file` 重写）。标签没说错，但成因是环境。
- **「这条线没信号」** —— 整批没触发的指标要说出来，不能被读成「测出来是 0」。
- **`Judge model deepseek`** —— 上面这份是真报告，判官就是被测模型（自己判自己有偏，
  见下面「语义面」和「现状」）。**报告不会替你检查这件事**，跑的时候自己盯住。

`Noise` 那一行是判断 delta 有没有意义的唯一依据：**它比 delta 大，读出来的就是噪声**。
算法是同题 k 次重复的组内变异系数、跨题取中位数 —— k<2 时算不出来，报告会直说。

拿 `--baseline` 跑时追加 `Baseline Diff` 段，逐任务比成功率、首动作、工具调用数、
token、子 agent 使用率。

## 语义面（judge）

确定性规则判不准的问题（「回答里的话有没有工具结果支撑」「声称做了的事是不是真做了」）
交给一个 LLM 判。**默认不跑**，两种跑法：

```bash
# 跑完顺手判（常用）
python -m agenteval.cli --tasks tasks/ --k 3 --judge kimi

# 单独判一个已有目录 —— 换 prompt / 换判官模型重判时用这个
python -m agenteval.judge runs/2026-09-28_120000/ --model kimi
```

判这一步读的是**已经落盘的记录，不碰 agent** —— 跑一次 agent 几十分钟，判一次很便宜，
所以两条其实是同一条逻辑：`--judge` 只是批次跑完之后替你调了第二条。

| 判什么 | 落到哪 |
|---|---|
| `groundedness` —— 最终回答里的每个事实声称，有没有工具结果支撑 | 报告里的 `Groundedness` 维度 |
| `claims_consistent` —— 声称完成的动作，是否真出现在调用日志里 | 报告里的 `Claims consistent` 行 |

判官模型**必须显式给**，而且**别用被测模型** —— 自己判自己有偏。

结果写回那条 run json 的顶层 `judge` 键（按 provider 分键），`evaluation.groundedness`
取生效的那个。单条失败只写 `judge.status = "error"`，不中断整批，确定性记录一个字段都不动。
对比两个版本时，prompt 版本或判官模型不同就**不出语义数字** —— 那是换了一把尺子。

**最终回答为空时是 `skipped`，不给分**（多半是 agent 撞了 miniCC 的循环上限，见
`Truncated` 那段）。让模型「拿不准就给中间值」会凭空编一个数混进平均值 —— 实测 4 条
把 `Groundedness` 从 92% 拖到 88%。解析不出来时会换句话重试一次，失败和跳过都在报告里点名。

## 报告解读（AI 分析）

`--analyze kimi` 会在报告末尾追加一段 LLM 写的解读（也可单独跑
`python -m agenteval.analyze runs/<目录>/ --model kimi`，不重跑 agent）：

```
────────────────────────────
AI 分析（由 kimi 生成，prompt 6b25e64d —— 是对上面报告的一种解读，不是数据本身）
────────────────────────────

能支持：成功率 97%、pass^3 92% —— 类别信号，噪声淹不掉。
不能支持：平均工具调用从 16.1 降到 14.6，但噪声是 ±17%，**看不出差别**。
建议（推测）：……
```

**为什么需要单独一层**：报告里最显眼的数字全在噪声量级上，一个自由的 LLM 几乎必然写出
「B 略快、C 更省 token」。所以 prompt 里把四条规矩写死了 —— 引用具体数字、**delta 小于
噪声必须写「看不出差别」**、没判过不等于 0 分、只对类别信号下强结论。建议部分必须标成推测。

## 现状与已知局限

诚实版（详细推演见 [`TODO.md`](TODO.md)）：

- **正确性维度天花板低。** 12 道题里只有 1 道能稳定挂，而且**现在只剩 2/3** ——
  temp=1 那轮它 3/3 全挂，温度降到 0.1 就只挂一次。整批唯一的区分度来源本身不稳。
- **有 1/9 的 run 没跑完。** miniCC 的循环上限是 30 次 LLM 调用，12 题 × k=3 里有 **4 条
  撞上**（`module_contracts_012` 两次、`follow_spec_003` 一次、`trace_units_006` 一次）。
  它们的产物照样过验收，所以既算 `success` 又没跑完 —— 报告里单列 `Truncated` 段，
  语义面跳过它们。**这个上限对成本高的题目偏紧，是「测 miniCC 机制」时最该盯的信号之一。**
- **语义面那批数是自判自的。** 判官和被测模型都是 deepseek，35 个 `groundedness` 里
  31 个落在 0.85–1.0、15 个恰好 0.9 —— 分布几乎没有分辨力。重判换判官即可
  （`python -m agenteval.judge <目录> --model kimi`），不用重跑 agent。
- **测量精度只够一半。** 同一版本、同一道题跑 3 次，工具调用数的**组内散布**
  （报告里的 `Noise` 行）：temp=1 时代约 ±29%，temp=0.1 下约 ±17%。逐题的效率差异
  要跟这个量级相当才看得见 —— 所以「整体变好没有」勉强答得出来，「好在哪道题」答不出来。
  k=1 时连噪声都估不出来，报告会直接声明数字不可比。
- **类别型信号不受这个限制** —— 成功率、首动作是「是/否」，噪声淹不掉，是目前最干净的东西。
- **四条设计线只工作了一条。** 错误恢复线 ✓；子 agent 线 ✗（模型 36 次里没用过一次
  `run_subagent`）；行为探针 ✗（`NO_EXPLORATION` 从未触发）；上下文压缩线测不了
  （deepseek 窗口 100 万，实测峰值只用掉 1%）。
- **还没能真的比较过两个版本** —— 手上只有一个 miniCC 版本。框架本身跑通了，
  但「哪个版本好」这个问题尚未被回答过。

## 关于「可复现」

指**可重复执行、可对比**，**不指结果一致**。原因有两个：上游 `call_llm` 的采样温度不为 0
（现在是参数、默认 0.1 —— 改了默认值整条链路的行为都会变），随机性被放大；
`MODELS` 里是服务端模型标识，provider 静默升级会让同一个 `agent_version` 跑出不同结果。
所以每条记录都同时存 `agent_version`（代码版本）、`model_actual`（API 实际返回的标识）
和 `temperature`，三者缺一不可。

## 不做的事（v1 范围）

进程隔离 / 并行执行、开放式问答类任务、路径一致性指标、Rubric 体系。
语义面（`judge.py`）已经落地，但它是独立的一步、默认不跑。理由见 `CLAUDE.md`。

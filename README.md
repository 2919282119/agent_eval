# agent_eval

给 **miniCC**（自制的迷你 Claude Code）做的 agent 评估框架。

跑真实 coding 任务 → 采轨迹 → 用确定性规则打分 → 出报告，并支持跟历史轮次对比。
**目的只有一个：回答「miniCC 的新版本比旧版本好在哪」。**

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

python -m pytest tests/ -q                     # 全套，离线，秒级
python -m pytest tests/ -m integration -v      # 真调 API 的用例，默认被排除

python -m agenteval.cli --tasks tasks/ --k 1   # 12 题，约 10 分钟
python -m agenteval.cli --tasks tasks/ --k 3   # 12 题，约 30 分钟

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

失败标签（全部走确定性规则，不用 LLM）：

| 标签 | 判定 |
|---|---|
| `INCOMPLETE` | 有 `essential` / `important` check 失败 |
| `WRONG_TOOL` | 覆盖了初始工作区里已有的文件，而该工具在 `forbidden_tools` 里 |
| `INEFFICIENT` | 工具调用超上限，或同一调用盲目重复 |
| `NO_EXPLORATION` | 改了已有文件却从没探索过 |
| `CLAIMS_WITHOUT_ACTION` | 声称「测试通过」但一行代码都没执行过 |

这些规则都反复收窄过 —— **误报会持续污染失败分布**，判不准的一律不做。收窄记录见 `CLAUDE.md`。

## 报告长什么样

```
Agent Evaluation Report        tasks: 12   k: 3   model: deepseek   temp: 0.1
────────────────────────────
Task Success      92%  (pass@3 92%  pass^3 92%)
Correctness       92%      Tool Usage        100%
Completeness      92%      Error Recovery    80%
Groundedness      n/a (needs judge)

Judge        （没判过。跑 python -m agenteval.judge <结果目录> --model <判官模型> 补上）

Efficiency  (仅统计 success 的 run)
  Avg tool calls 12.7   Avg LLM calls 10.9   Avg tokens 42.3k   Avg latency 51.5s
  First action     explore 92%   other 8%
  Noise            tool calls ±17%   tokens ±25%   latency ±37%   （同题重复跑的组内散布）
  Context          max usage 1%   compactions 0

Failure Distribution  (按 run 归一化，一个 run 可命中多个标签)
  INCOMPLETE 0.08   INEFFICIENT 0.08
```

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

- **正确性维度天花板低。** 12 道题里只有 1 道能稳定挂（k=3 三次全挂）。
  如果新版本在这道题上做同样选择，报告会重新变成一条直线。
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

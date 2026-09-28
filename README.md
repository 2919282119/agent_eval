# agent_eval

给 **miniCC**（自制的迷你 Claude Code）做的 agent 评估框架。

跑真实 coding 任务 → 采轨迹 → 用确定性规则打分 → 出报告，并支持跟历史轮次对比。
**目的只有一个：回答「miniCC 的新版本比旧版本好在哪」。**

| 文档 | 内容 |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 已实现的设计框架、约束、踩过的坑（**读代码前先读它**）|
| [`TODO.md`](TODO.md) | 实施进度与当前结论 |
| [`feat.md`](feat.md) | 还没落地的设计（Jev 语义评估、任务重设计的遗留结论）|

## 它怎么工作

```
python -m agenteval.cli --tasks tasks/ --k 3 --baseline runs/<旧目录>/
```

| 模块 | 职责 | 依赖 miniCC |
|---|---|---|
| `agenteval/task.py` | 加载任务；跑 `verify.py` 拿验收结果 | 否 |
| `agenteval/runner.py` | **唯一与 miniCC 耦合的模块** —— 复制临时工作区、`chdir`、patch `call_llm` 采用量、驱动 `agent_loop` | **是** |
| `agenteval/trajectory.py` | `state.messages` → 轨迹结构、效率指标、逐次工具调用明细 | 否 |
| `agenteval/metrics.py` | 轨迹 + 验收结果 → 五维分 + 失败标签 | 否 |
| `agenteval/report.py` | 聚合 N 个 run → 文本报告 + 基线 diff | 否 |
| `agenteval/cli.py` | 入口 | 否 |

换 miniCC 接口、或将来做子进程隔离，只动 `runner.py` 一个文件就够了。

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
```

| 参数 | 作用 |
|---|---|
| `--tasks tasks/` | 任务集目录，每个子目录含 `task.yaml`（`retired: true` 的会被跳过）|
| `--k 3` | 每个任务跑几次。**要比较版本必须 ≥2**，理由见下 |
| `--model deepseek` | 被测模型，可选 `deepseek` / `kimi` |
| `--out runs` | 结果输出根目录 |
| `--baseline runs/<旧目录>/` | 追加逐任务的「本次 vs 基线」对比 —— **这就是「比较两个 miniCC 版本」的用法** |

结果落在 `runs/<时间戳>/`：主记录 `<task>_<run>.json`、逐次工具调用的 sidecar
`<task>_<run>.calls.json`（事后核对失败标签靠它）、汇总 `report.txt`。

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
| `groundedness` | 待接入语义评估，v1 恒为空 |

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
Agent Evaluation Report        tasks: 12   k: 3   model: deepseek
────────────────────────────
Task Success      92%  (pass@3 92%  pass^3 92%)
Correctness       92%      Tool Usage        100%
Completeness      92%      Error Recovery    80%
Groundedness      n/a (needs judge)

Efficiency  (仅统计 success 的 run)
  Avg tool calls 12.7   Avg LLM calls 10.9   Avg tokens 42.3k   Avg latency 51.5s
  First action     explore 92%   other 8%
  Context          max usage 1%   compactions 0

Failure Distribution  (按 run 归一化，一个 run 可命中多个标签)
  INCOMPLETE 0.08   INEFFICIENT 0.08
```

拿 `--baseline` 跑时追加 `Baseline Diff` 段，逐任务比成功率、首动作、工具调用数、
token、子 agent 使用率。

## 现状与已知局限

诚实版（详细推演见 [`TODO.md`](TODO.md)）：

- **正确性维度天花板低。** 12 道题里只有 1 道能稳定挂（k=3 三次全挂）。
  如果新版本在这道题上做同样选择，报告会重新变成一条直线。
- **测量精度只够一半。** 同一版本、同一道题跑 3 次，工具调用数的变异系数中位数
  是 34%。换算成分辨率：k=3 时**总均值**能分辨约 ±8% 的差异，但**逐题**要 ±27%
  —— 也就是说「整体变好没有」答得出来，「好在哪道题」答不出来。k=1 更差（±14% / ±47%），
  所以报告在 k=1 时会自己声明数字不可比。
- **类别型信号不受这个限制** —— 成功率、首动作是「是/否」，噪声淹不掉，是目前最干净的东西。
- **四条设计线只工作了一条。** 错误恢复线 ✓；子 agent 线 ✗（模型 36 次里没用过一次
  `run_subagent`）；行为探针 ✗（`NO_EXPLORATION` 从未触发）；上下文压缩线测不了
  （deepseek 窗口 100 万，实测峰值只用掉 1%）。
- **还没能真的比较过两个版本** —— 手上只有一个 miniCC 版本。框架本身跑通了，
  但「哪个版本好」这个问题尚未被回答过。

## 关于「可复现」

指**可重复执行、可对比**，**不指结果一致**。原因有两个：上游 `call_llm` 把
`temperature` 写死成 1，方差拉满；`MODELS` 里是服务端模型标识，provider 静默升级
会让同一个 `agent_version` 跑出不同结果。所以每条记录都同时存 `agent_version`
（代码版本）和 `model_actual`（API 实际返回的模型标识），两者缺一不可。

## 不做的事（v1 范围）

LLM-as-Judge 打分、语义评估维度、进程隔离 / 并行执行、开放式问答类任务、
路径一致性指标。理由见 `CLAUDE.md`。

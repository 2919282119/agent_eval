更新了待办事项列表

`agenteval` 下面目前主要有这些 Python 文件：

```text
agenteval/
├── __init__.py
├── task.py
├── runner.py
├── trajectory.py
├── metrics.py
├── report.py
└── cli.py
```

整体数据流是：

```text
cli.py
  ↓
task.py 加载任务
  ↓
runner.py 驱动 miniCC
  ↓
trajectory.py 解析轨迹
  ↓
task.py 执行 verify.py
  ↓
metrics.py 评分
  ↓
report.py 生成报告
```

## `__init__.py`

包初始化文件。

目前基本为空，作用是让 `agenteval` 被 Python 识别为一个包，从而可以使用：

```python
from agenteval import trajectory
```

## `task.py`

负责**任务定义和任务验收**。

主要内容：

- `Check`：表示一条验收检查
- `Task`：表示一道任务
- `load_task()`：读取 `task.yaml`
- `run_verifier()`：执行任务的 `verify.py`
- `load_workspace_module()`：动态加载被测 Python 文件
- `workspace_importable()`：临时允许导入工作区中的多个模块
- `workspace_files()`：获取初始工作区中已有的文件

它不依赖 miniCC，属于任务和验收层。

## `runner.py`

负责**真正驱动 miniCC 跑任务**，是唯一直接依赖 miniCC 的模块。

主要内容：

- 复制任务工作区到临时目录
- 加载 miniCC 的 `.env`
- patch `agent.agent.call_llm`，统计 token 和 LLM 调用次数
- patch `load_cc_md()`，屏蔽外部 `CC.md`
- 创建 `AgentState`
- 调用 `agent_loop()`
- 统计耗时和上下文压缩次数
- 执行验收和评分
- 清理临时工作区
- 生成 `RunResult`

核心函数：

```python
drive_agent()
```

只负责驱动 agent。

```python
run_task()
```

负责完成一次任务的完整流程：

```text
复制 workspace
→ 运行 agent
→ 解析 trajectory
→ 执行 verify.py
→ 计算 metrics
→ 生成 RunResult
→ 清理临时目录
```

## `trajectory.py`

负责把 miniCC 的：

```python
state.messages
```

转换成统一的轨迹对象。

主要内容：

- `ToolCall`：一次工具调用
- `LlmStats`：LLM 调用统计
- `Trajectory`：完整轨迹
- `build()`：构建轨迹
- `final_answer()`：提取最终回答
- `_extract_calls()`：提取工具调用
- `_result_error()`：判断工具是否失败
- `_first_action()`：判断首个动作是探索、修改还是其他
- `is_command_failure()`：识别 bash 非零退出码

它负责回答：

- agent 调用了哪些工具？
- 工具参数是什么？
- 哪些工具调用失败？
- agent 第一动作是什么？
- 调用了多少次 LLM？
- 消耗了多少 token？
- 最终回答是什么？

它不负责判断任务是否完成。

## `metrics.py`

负责**评分和失败标签分类**。

主要计算：

```text
task_success
correctness
completeness
tool_usage
error_recovery
groundedness
```

主要规则包括：

- `INCOMPLETE`
- `WRONG_TOOL`
- `WRONG_ARGUMENT`
- `INEFFICIENT`
- `NO_EXPLORATION`
- `CLAIMS_WITHOUT_ACTION`
- 安全 veto

核心函数：

```python
evaluate(traj, checks, task, initial_files)
```

输入：

- agent 的 `Trajectory`
- `verify.py` 返回的 `Check` 列表
- 当前任务配置
- 初始工作区文件列表

输出：

```python
EvalResult(
    evaluation=...,
    failures=...,
    vetoed=...,
)
```

它不依赖 miniCC，只处理已经采集好的数据。

## `report.py`

负责**聚合多个 run 并生成文本报告**。

主要内容：

- `summarize()`：计算总体统计
- `render()`：生成报告文本
- `load_baseline()`：读取历史结果
- baseline diff：比较当前结果和历史结果

报告包括：

- 任务成功率
- 正确性
- 完整性
- 工具使用
- 错误恢复
- groundedness
- 平均工具调用和 token
- 首动作分布
- 失败标签分布
- baseline 对比

它只读取 run 记录，不重新运行 agent。

## `cli.py`

负责**命令行入口和流程编排**。

执行：

```bash
python -m agenteval.cli --tasks tasks/ --k 3
```

时，就是从这里开始。

主要内容：

- `find_tasks()`：查找任务目录
- `parse_args()`：解析命令行参数
- `main()`：执行完整评估流程
- `write_result()`：保存主 JSON 和 sidecar
- `write_json()`：写 JSON 文件
- `write_report()`：保存 `report.txt`
- `summarize_progress()`：打印单次运行进度

它本身不负责具体评分，而是把其他模块串起来：

```text
读取参数
→ 找到任务
→ 调用 runner.run_task()
→ 保存结果
→ 调用 report.render()
→ 输出和保存报告
```

## 简单类比

可以把它们看成一条流水线：

| 文件 | 类比 |
|---|---|
| `task.py` | 题目和阅卷标准 |
| `runner.py` | 让 agent 做题 |
| `trajectory.py` | 记录 agent 的答题过程 |
| `metrics.py` | 按标准打分和贴失败标签 |
| `report.py` | 汇总所有成绩 |
| `cli.py` | 总调度入口 |
| `__init__.py` | 声明这是一个 Python 包 |

最关键的边界是：

- `runner.py` 负责和 miniCC 交互
- `task.py` 负责验收任务
- `trajectory.py` 负责解析过程
- `metrics.py` 负责评分
- `report.py` 负责统计
- `cli.py` 负责串联流程
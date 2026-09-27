多维度评估：

+ 结果正确性
+ 规划是否合理
+ 工具是否正确
+ 推理/行为过程  (trajectory，tool call/llm call次数，time，tokens) ->效率与成本
+ 安全(Veto)



report应该类似于这样：

```c
Agent Evaluation Report
────────────────────────────

Task Success       82%
Correctness        88%
Completeness       76%
Tool Usage         91%
Groundedness       95%
Error Recovery     71%

Efficiency
  Avg tool calls   8.2
  Avg LLM calls    4.1
  Avg tokens       7.8k
  Avg latency      14.2s

Failure Distribution
────────────────────────────
MISSING_CONTEXT       12
WRONG_TOOL             8
WRONG_ARGUMENT         5
HALLUCINATION          3
INCOMPLETE             9
INEFFICIENT            14

Top Failure Patterns
────────────────────────────
1. Agent fails to inspect repository structure
2. Agent stops after first tool failure
3. Agent claims tests passed without execution
4. Agent misses secondary requirements
```

![](https://cdn.nlark.com/yuque/0/2026/png/42476437/1789972268273-6c6509b6-ba17-4076-a2db-e6c6c9cf5759.png)



#### 思考：
Agent=LLM+harness

harness=上下文+工具+约束+验证



针对不同的测试对象可以设定不同的任务，每个任务都需要有初始状态，任务描述，测试方法， 每次执行 Task 产生一个独立的 Trajectory  



多次测试，pass@k和pass^k都要保留，不是加权求和



评估方法分为两种，对于coding问题或者说用程序很好验证的问题，直接编写测试程序来测试（Verifier），而对于那种开放性没有准确答案的问题，可以使用 LLM-as-a-Judge  的方法，而rubric是llm-as-a-judge的一个评分标准



Rubric可以根据不同测试方向有所调整，当然可以先定义一个通用的Rubric，然后再根据不同测试方向（比如：system_prompt，Tool，context，skill等）设置不同的Rubric

Rubric举例：

```markdown
rubric:
  dimensions:
    - name: 事实正确性
      weight: essential        # 必要项
      scoring:
        4_优秀: "准确回答 Dr. Chen，且关联到女儿 Lily"
        3_良好: "准确回答 Dr. Chen，但未提及是 Lily 的医生"
        2_及格: "给出了正确医生但附带不确定的额外信息"
        1_不及格: "给出错误医生名，或回答不知道"

    - name: 信息完整性
      weight: important        # 重要项
      scoring:
        4_优秀: "主动补充相关信息（如上次就诊时间、诊断结果）"
        3_良好: "回答了核心问题，无遗漏"
        2_及格: "回答了核心问题，但遗漏了可用的关联信息"
        1_不及格: "关键信息缺失"

    - name: 思考正确性
      weight: important
      scoring:
        4_优秀: "正确关联'女儿=Lily'和'Lily的医生=Dr. Chen'两条跨会话信息"
        3_良好: "关联正确但思考路径不够清晰"
        2_及格: "部分关联正确"
        1_不及格: "错误关联（如把用户自己的医生当成女儿的医生）"

    - name: 幻觉检测
      weight: veto             # 一票否决项：一旦触发，总分归零
      scoring:
        pass: "所有信息均可溯源到历史对话记录"
        fail: "编造了对话中不存在的信息（如虚构就诊日期、诊断结果）"

  edge_cases:
    - "如果用户有多个女儿且分别看不同的医生，应追问是哪个女儿"
    - "如果记忆中同时存在'Dr. Chen'和'陈医生'，应识别为同一人"
```



<font style="color:rgb(31, 35, 40);">初步评估框架：</font>

```markdown
                    Evaluation Dataset
                           │
                           ↓
                         Task
              ┌────────────┼────────────┐
              ↓            ↓            ↓
        Initial State  Instruction   Evaluation
              │                         │
              │                    ┌────┴────┐
              │                    ↓         ↓
              │                Verifier   LLM Judge
              │                              │
              │                           Rubric
              │
              ↓
          Run Agent
              │
              ↓
          Trajectory
              │
              ↓
         Evaluation Result
              │
              ↓
       ┌──────┴──────┐
       ↓             ↓
    pass@k         pass^k
```

一次run之后的结果应该类似于：

```markdown
{
  "task_id": "tool_001",
  "run_id": "run_001",
  "agent_version": "miniCC-v1.2",
  "model": "kimi",
  "status": "failed",

  "trajectory": {
    "steps": 8,
    "tool_calls": 5,
    "llm_calls": 3,
    "tokens": 6200,
    "latency_ms": 12400
  },

  "evaluation": {
    "task_success": false,
    "correctness": 1,
    "completeness": 0,
    "tool_usage": 0,
    "groundedness": 1,
    "error_recovery": 0
  },

  "failures": [
    "WRONG_TOOL",
    "INCOMPLETE"
  ]
}
```


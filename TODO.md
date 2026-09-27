# agent_eval — 实施进度

评估对象：`D:\Code\python_project\miniCC`（已 `pip install -e`，`import agent.agent` 全局可用）

> **设计与约束全部在 `CLAUDE.md`，本文件只维护「要做什么 + 做到哪了」。**

## 状态：全链路跑通，12 道题已就位；报告第一次有了区分度（k=3 已试跑）

```bash
cd D:/Code/python_project/agent_eval
python -m pytest tests/ -q                       # 197 passed，离线约 7 秒
python -m pytest tests/ -m integration -v        # 2 个真调 API 的用例
python -m agenteval.cli --tasks tasks/ --k 1     # 12 题，实测约 10.5 分钟
```

**当前评测效果的结论**（详见「k=3 试跑」一节）：框架自己已经能用、标签可信、
`error_recovery` 这个维度活了；但**核心目的「比较两个 miniCC 版本」仍未达成**，
因为手上只有一个版本（见文末「卡在用户这边的事」）。

### 本轮已修（2026-09-25）：问题 2 / 3 已修完，问题 4 的代码部分已修

| 问题 | 改了什么 | 验证方式 |
|---|---|---|
| **2** | k=1 时 Efficiency 段钉一句「只有单次观测，勿跨轮次比较」；`--baseline` 的效率 delta 在任一侧 k=1 时**直接跳过** | 4 个新用例，含反方向：k=3 时不告警、k=3 时仍出 delta |
| **3** | `tests/fixtures/runs/` 存 8 条真实 sidecar 快照 + `expected.json` 存人工逐条核对过的期望标签 + 回放测试 | 17 个新用例。**把 `_forbidden_hits` 临时退回「按工具名一刀切」的旧行为，4 条 `WRONG_TOOL` 假阳性立刻被抓住** —— 护栏确实有牙齿 |
| **4** | 报告固定标注「未验证（从未在真实 run 上触发过）」：`NO_EXPLORATION` / `WRONG_ARGUMENT` / veto | `test_report.py` 新用例 |

回放能成立的关键：sidecar 的 `calls`（含 `ok`/`error`）+ `final_answer`
**就是判定失败标签所需的全部输入**，不用重跑 API。
上一轮写的一次性回放脚本已固化成 `tests/test_rules_on_real_trajectories.py`。

⚠️ **`runs/` 里的历史标签是修复前算出来的**（那 6 条假阳性仍冻在 json 里），
看旧报告时要知道 —— `Tool Usage 75%`、`WRONG_TOOL 0.50` 都是这个原因。
回放测试用同一批轨迹证明了**新规则不再报它们**。

### ⚠️ S8 的核心结论：**区分度在效率维度，不在正确性维度**

8 道题（5 易 + 3 难）× k=1 的完整评估：**8 道全部 success（100%）**。

```
Task Success      100%      Tool Usage  69%
Correctness       100%      Error Recovery 100%
Completeness      100%      Groundedness  n/a
Efficiency  Avg tool calls 12.1   Avg LLM calls 10.1   Avg tokens 39.5k   Avg latency 181.9s
Failure Distribution  WRONG_TOOL 0.62   INEFFICIENT 0.25   CLAIMS_WITHOUT_ACTION 0.12
```

**加难度没有让 task_success 拉开差距，难度以「成本」的形式体现了出来**：
`rate_limit_007`（滑动窗口）花了 **24 次工具调用 / 118k tokens / 372 秒**才做对，
`update_call_sites_004` 22 次调用 / 293 秒。对这个模型来说，
**正确性维度已经饱和，行为与效率维度才有信号**。

代价：一轮 k=1 从 6 分钟涨到 **23 分钟**。k=3 会到 70 分钟量级。

这对后续有两个直接影响：
- 想靠加难度让 `task_success` 有区分度，得换**质**的难度（跨文件重构、模糊需求、
  需要反向工程数据格式），而不是量的堆积
- 或者接受现状：**把行为/效率指标提为主要信号**（它们已经在出信号了）

### ⚠️ 失败标签大面积假阳性：7 条里 6 条是假的

第二版评估（8 题 × k=1，约 17 分钟）报出 7 条失败标签。
**用 sidecar 逐条核对真实轨迹后，6 条是假阳性**：

| 标签 | 报出的 run | 核对结果 |
|---|---|---|
| `WRONG_TOOL` ×4 | rate_limit_007 / roman_008 / top_words_005 / trace_units_006 | **假** —— agent 在写**一次性验证脚本**（`_check_roman.py`、`test_rate_limiter.py`、`_t.py`、`_test_alerts.py`），不是覆盖源文件 |
| `CLAIMS_WITHOUT_ACTION` ×2 | fix_offbyone_001 / follow_spec_003 | **假** —— agent 跑了内联断言（`python3 -c "assert ..."`）并拿到 `ALL TESTS PASSED` |
| `INEFFICIENT` ×1 | update_call_sites_004 | **真** —— 36 次工具调用，超了上限 |

已用 sidecar 对真实数据**回放验算**：修完之后 6 条全部消失、唯一的真阳性保留。
（回放脚本的做法：从 sidecar 重建 `Trajectory`，喂给 `metrics.evaluate`。
sidecar 存 `calls` 的价值就在这里 —— 不用重跑 API 就能验算法。）

**两个根因，都是设计缺陷不是 agent 的问题：**

1. **`WRONG_TOOL` 按工具名一刀切** —— 分不清「覆盖源文件」和「新建临时脚本」。
   更糟的是**「主动写脚本自查」正是本框架想奖励的行为**
   （`CLAIMS_WITHOUT_ACTION` 就在奖励它），两条规则互相打架。
   修法：写文件类工具**带路径判断**，只有动了初始工作区里已有的文件才算违规。

2. **`CLAIMS_WITHOUT_ACTION` 想判断「这次算不算测试」** —— 收窄了三次，
   先漏 `python test_x.py`、后漏 `python3 -c "assert ..."`，
   两次都是 agent 明明真验证过却被判撒谎。
   修法：退到最粗但不可能误报的口径 —— **一行代码都没执行过**才判；
   中间情形（跑了 `ls` 就声称测试通过）判不准，留给 v2 的 Jev。

另外：**约束必须写进 `instruction`**。`forbidden_tools` 只写在 `task.yaml` 里
agent 根本看不到，那条标签测的就成了「有没有猜中我们的隐藏偏好」。
7 道题的指令已改成「不要用 `write_file` 覆盖**已有的源文件**」。

教训都写进了 `CLAUDE.md` 的「失败标签」和「任务设计约定」两节。

## 已知的问题

22 次运行（4 轮）实测出来的，不是猜测。**按严重程度排。**
问题 2 / 3 已修完，问题 4 的代码部分已修（见上面「本轮已修」），**问题 1 没动**。

> 一句话总结：**现在这个框架还回答不了「miniCC 新版本比旧版本好在哪」**。
> 下面是拦在这条路上的四件事。

### 问题 1（**已缓解，未解决**）：所有题都 100% 通过，分不出好坏

> **进展**：已按「压 miniCC 机制」重出题目（A/B/D/D3 步，见下）。报告从一条 100% 直线
> 变成 **k=1 与 k=3 都是 92%**，且失败是**稳定**的（k=3 三次全挂）。
> 但「分不出哪个版本好」这个**根本问题仍未解决** —— 手上只有一个版本，没法对比。
> 下面的内容是问题刚被发现时的记录，保留作背景。

22 次运行**全部成功**。报告主表（成功率 / 正确性 / 完整性）永远是一条 100% 的直线。

意思是：改了 miniCC 跑一遍得 100 分，不改也是 100 分 —— **报告分不出哪个版本好**，
而「比较不同版本」正是做这个框架的目的。

| 维度 | 22 次运行出现过的取值 |
|---|---|
| 成功率 `task_success` | 只有 `True` |
| 正确性 `correctness` | 只有 `1.0` |
| 完整性 `completeness` | 只有 `1.0` |
| 工具使用 `tool_usage` | `0.5` 和 `1.0` —— **但那些 0.5 全是误报**，修完就恒为 1.0 |
| 错误恢复 `error_recovery` | `1.0` 或「不适用」，从没低过 |
| `groundedness` | 空着（等 Jev）|

原因：**题目太简单，deepseek-flash 全都能做对**。上次特意加了 3 道难题，
结果还是全对 —— 只是多花了一倍时间和 token。

**怎么修：换出题方向。别再出「更难的脑力题」，改出「专门折腾 miniCC 自己的题」。**

道理：这个框架要评的是 **miniCC 这个工具**，模型是固定的外部依赖。
拿自造小题去考模型聪不聪明必然撞天花板。应该去压 miniCC 的**机制**：

| 该压的机制 | 现状 |
|---|---|
| **上下文管理** —— 出「要读很多文件才能做完」的任务，逼出它的自动压缩 | 完全没测，这是 miniCC 最核心的机制之一 |
| **独有能力** —— skill / subagent / 知识库检索 / 联网搜索 | 11 个工具里只压了 6 个基础工具 |
| **上下文组装** —— CC.md / memory 注入的影响 | 被我们屏蔽了（为了可复现），但「比较 harness 版本」恰恰要测这个 |
| **必然失败的工具调用** —— 才能测出「出错后会不会恢复」| 现在 `error_recovery` 永远满分，等于没测 |

这样题一样难，但测出来的是「miniCC 不同版本的差别」，而不是「deepseek 聪不聪明」。

### 问题 2（已修）：只跑一次的数字不可信，但报告照样在出结论

同一任务、同一模型，跑两次可以差很多：

| 任务 | 工具调用次数 | token 数 |
|---|---|---|
| `rate_limit_007` | 24 → 8（**3 倍**）| 11.8 万 → 3.2 万 |
| `roman_008` | 7 → 11 | 2.5 万 → **31.8 万（13 倍）** |
| `update_call_sites_004` | 22 → 22 → 36 | 3.9 万 → 16.4 万 |

差这么多，说明数字里主要是**随机波动**。所以我之前报的「平均延迟从 67 秒涨到 182 秒」
很可能不是真的变慢，只是碰巧。

根因：miniCC 的 `call_llm` 把 `temperature` 写死成 1，本来就是故意放大随机性的。

**怎么修（小改动，纯代码）：** 只跑一次时（k=1），报告里不输出效率均值，
或加一句显眼的「只跑了一次，和别的轮次不可比」。`--baseline` 的效率对比同理。

**已修：** 保留数字但钉一句不可比的声明（单次观测本身是有效数据，藏掉反而丢信息），
`--baseline` 的效率 delta 在任一侧 k=1 时直接跳过 —— delta 才是把噪声说成趋势的那个。

### 问题 3（已修）：失败标签大部分是错的，而且错得很隐蔽

报告会列「这次 agent 犯了什么错」。拿真实日志核对：**报了 7 条，6 条是错的。**

两个真实例子：

- 报告说「用了被禁止的工具」→ 实际是 agent 写了个临时测试脚本（`_check.py`）
  来自查。**这明明是好事**，但规则只看工具名字，没看它动的是哪个文件。
- 报告说「声称测试通过但根本没跑」→ 实际 agent 真跑了，写法是
  `python3 -c "assert ..."`，规则的正则没认出来。

**也就是说那份失败统计 85% 是噪音。** 幸好存了原始日志才回头发现 ——
不核对就当结论用了。

**怎么修：** 把真实运行的日志固化成**测试数据**。以后一改规则，如果它在真实日志上
开始误报，测试立刻报错，不用再靠手工核对。

```
tests/fixtures/runs/          真实 run 的 sidecar 快照
tests/fixtures/expected.json  每个快照人工核对过的「应该报哪些失败」
tests/test_rules_on_real_trajectories.py   回放验算
```

（这次能查出来靠的就是「从 sidecar 重建轨迹重算一遍」，不用重跑 API。
把它从临时脚本变成固定资产。）

**已修。** 已落地成上面这个结构，8 条真实轨迹（`runs/2026-09-24_154404` 那轮）。
`expected.json` 的期望值是**逐条看 sidecar 判出来的**，不是照抄 run json ——
那批记录本身就是错的，照抄等于把假阳性固化成「正确」。

### 问题 4（一半已修）：有三条规则从没生效过，不知道准不准

「没检查就改文件」（`NO_EXPLORATION`）、「参数用错」（`WRONG_ARGUMENT`）、
「危险命令」（veto）—— 22 次运行里**一次都没触发过**，只在单元测试里验过。
按问题 3 的规律，它们第一次在真实数据上生效时大概率也是错的。

**怎么修（分两步）：**

1. ~~报告里把这三条标注成「未验证」，不当结论用~~ —— **已修**
   （`report.UNVERIFIED_RULES`，报告里固定钉一行）
2. 出能触发它们的任务，跑一次拿真实轨迹，固化成 fixture，才能把标注去掉。
   **逐条查过可行性后收窄了：**
   - `NO_EXPLORATION` **能靠出题触发** → 已排进问题 1 的批次（探针题 `blind_edit_014`）
   - `WRONG_ARGUMENT` **逼不出来** —— 只有模型自己出错时才亮（吐坏 JSON、或猜错参数名），
     那是模型行为不是 miniCC 机制。它只能靠意外慢慢攒证据
   - veto **不该靠出题触发**（要亮得 agent 真跑出 `rm -rf /`，没有正当任务会诱导，
     故意引诱就是在设计钓鱼测试）。而且它是安全网、不进失败分布，单元测试验它已经够了
     → **从 `UNVERIFIED_RULES` 里删掉**

### 下一步（建议顺序）

先解决「测出来的数可不可信」（问题 2/3/4），再解决「测出来的东西有没有意义」（问题 1）。
理由：问题 1 会产出新数据，如果那时规则还是错的，新数据又会被污染一遍。

| # | 做什么 | 量级 | 状态 |
|---|---|---|---|
| 1 | 问题 2：k=1 时报告不输出效率结论 / 加警告 | 小，纯代码 + 测试 | **[x]** |
| 2 | 问题 3：把 `runs/` 整理成 fixture + 回放测试 | 中 | **[x]** |
| 3 | 问题 4a：把规则标注「未验证」 | 小，纯代码 + 测试 | **[x]** |
| 4 | 问题 1：按「压 miniCC 机制」重新设计题目（**先设计再出题**）| 大 | **设计已完成 → `feat-task-redesign.md`** |
| 5 | 问题 1 的 A 步：改 harness（见下）| 中，纯代码 + 测试 | **[x]** |
| 5b | 问题 1 的 B 步：出 6 道新题（见下）| 大，离线可验 | **[x]** |
| 6 | 第一段跑 k=1 全量 → 手工核对标签 → 固化成 fixture | 中，要跑 API | **[x]** 2026-09-26，约 10.5 分钟 |
| 6b | k=3 试跑（**单版本**，只取重复跑的散布，不做版本对比）| 中，要跑 API | **[x]** 2026-09-26，见「k=3 试跑」 |
| 7 | 把 Jev 并入 `CLAUDE.md` 的设计：schema、`groundedness` 填充规则、`questions_hash` 的 diff 保护；`feat.md` 相应清空或标记完成 | 中 | **[ ]** |
| 8 | 接入 Jev（等 TypeSafe key）—— 它填的 `groundedness` 是唯一空着的维度 | 等 key | **[ ]** |
| 9 | 第二段跑 k≥3 做版本对比（12 题 ≈ 36 run，70–100 分钟）| 大，要两个版本 | **[ ] 等用户准备第二个 miniCC 版本** |

问题 4b（定向触发任务）**已并入第 5 项** —— 只有 `NO_EXPLORATION` 值得为它出题，
已排成探针题 `blind_edit_014`；另两条查实后确认逼不出来，理由见上文「问题 4」。

> **卡在用户这边的事**：第 9 项（版本对比）需要**第二个 miniCC 版本**。
> 2026-09-26 确认暂时没有，用户会自己准备。**这之前不要催**，
> 推进不依赖它的部分：第 8 项（灵敏度体检）、`max_tool_calls` 标定、Jev 接入。
> 版本到手后怎么跑（两版各 k≥3 + `--baseline`）见下文的「分两段跑」。

## 问题 1 落地计划

设计全文在 `feat-task-redesign.md`。执行顺序（A/B 可以只跑离线测试，C 之后才要 API）：

### A. 改 harness（纯代码 + 测试，离线可验）—— **已完成 2026-09-25**

| # | 改什么 | 文件 | 状态 |
|---|---|---|---|
| A1 | `compactions`：在 `ContextManager` 实例上挂计数 wrapper（`auto_compact` 走 `self.compact`，比扫 messages 准）| `runner.py` | **[x]** |
| A2 | 采集峰值 `prompt_tokens` → 顶层 `max_usage_ratio`、`tools_used`、`compactions` | `runner.py` + `trajectory.py`(`LlmStats`) | **[x]** |
| A3 | Efficiency 段下加 `Context  max usage .. compactions ..` 一行 | `report.py` | **[x]** |
| A4 | `_render_diff` 的 `Per task` 扩到比 `tool_calls`/`tokens`/子 agent 使用率（**原来对饱和题集永远空白**）| `report.py` | **[x]** |
| A5 | `UNVERIFIED_RULES` 删掉 veto（它是安全网，不进失败分布）| `report.py` | **[x]** |
| A6 | 支持 `task.yaml` 的 `retired: true`；给 `rate_limit_007`/`update_call_sites_004` 打上 | `task.py` + `cli.py` | **[x]** |
| A7 | schema 说明加三个新顶层字段；veto 改成「安全网，靠单元测试验证」 | `CLAUDE.md` | **[x]** |

测试 **137 → 147 passed**（新增 `tests/test_cli.py`；`test_report.py` 加 Context 行、
Per task diff、k=1 抑制、veto 不在未验证名单等 8 个用例）。

**A4 自审时发现并修掉一个自相矛盾**：`Per task` 一开始把 `tool calls 8.0 → 20.0`、
`tokens 24.7k → 317.9k` 这种 k=1 噪声直接印出来了 —— 而 Overall 段刚说过
「k=1 数字含大随机波动，跳过」。现在任一侧 k=1 时 `Per task` 也只报成功率翻转，
并显式说明原因。**结论：真要分版本，k 必须 ≥2。**

### B. 出 6 道新题 —— **已完成 2026-09-25**

| task_id | 线 | 陷阱 / 形态 |
|---|---|---|
| `blind_edit_014` | 探针 | 指令把改法写死，给 `NO_EXPLORATION` 提供触发机会。**不是难度题** |
| `stale_path_009` | 错误线 | 指令说改 `legacy/report_v2.py`，实际已改名到 `report.py`（`CHANGELOG.md` 里有记录）|
| `encoding_trap_010` | 错误线 | `prices.csv` 是 GBK 字节，`read_file` 会报「文件不是 UTF-8」 |
| `readonly_config_011` | 错误线 | `config.json` 带只读属性，`edit_file` 会报「没有权限修改文件」 |
| `module_contracts_012` | 子 agent 线 | 5 个模块要整理成 `contracts.py` 字典，验收用 `inspect` 现算真值比对 |
| `extract_helper_013` | 子 agent 线 | 6 个调用点抽公共函数，验收查输出一位不差 + 参数名没改 + 源码真接上了 |

默认任务集现在 **12 道**（8 − 2 退役 + 6 新），`tasks/` 目录 14 个。测试 **147 → 166 passed**。

出题时踩到并处理掉的两个坑（教训已写进 `CLAUDE.md` 的「任务设计约定」）：

- **E3 的只读属性会在搬运中静默丢失** → 加 `test_readonly_trap_is_still_armed` 盯着；
  且 `shutil.rmtree` 在 Windows 上删不掉只读文件，`runner` 的清理改用 `onerror=...`
  （原来的 `ignore_errors=True` 会**静默漏临时目录**）
- **`load_workspace_module` 失败返回 `None`，`inspect.getmembers(None)` 给空列表** →
  验收的「真值」会静默变成 `{}`、检查空过。S1 的 `verify.py` 显式拦了这一层

**S1 原设计改了一处**：原计划产出 `CONTRACTS.md`，但 md 没法可靠验收（抄源码就能蒙过），
改成产出 `contracts.py` 字典。

### D. 第一段跑 k=1 + 手工核对 —— **已完成 2026-09-26**

```bash
python -m agenteval.cli --tasks tasks/ --k 1     # 12 题，约 10.5 分钟（退役了最贵的两道，比原来还快）
```

**报告第一次不是一条 100% 的直线**：Task Success / Correctness / Completeness 都是 **92%**。

```
Task Success      92%      Error Recovery    90% → 修完规则是 75%
Correctness       92%      First action      explore 100%
Completeness      92%      Context           max usage 1%   compactions 0
Tool Usage        100%     Failure Distribution  INCOMPLETE 0.08   INEFFICIENT 0.08
```

**三道错误陷阱全部按设计触发** —— 题目有效。**唯一那道失败是 `readonly_config_011`**：
agent 正确诊断出「文件是只读的」并提出修法（`chmod u+w`），但**没自己解，转去问用户**。
判 `INCOMPLETE` + `error_recovery=0.0` 都对。

手工核对揪出**第二条假阳性**，并顺带发现一个更严重的问题：

| 发现 | 处理 |
|---|---|
| `encoding_trap_010` 的 `INEFFICIENT` 是假的 —— `read_file pricing.py` 出现 3 次，但中间改过两次 | `_has_blind_repeats`：**中间改过文件就归零** |
| **`shell=True` 在 Windows 上走 `cmd.exe`**，agent 写的是 bash 语法 → 一轮 30 次失败调用里 **24 次（80%）**是这一类 | `error_recovery` **排除 bash 退出码**（`trajectory.is_command_failure`）。有取值的从 10/12 降到 4/12，且全部由设计好的陷阱驱动 |

改完这一轮**只剩 1 条真标签**。新的一轮（12 条）已固化成 fixture：
`tests/fixtures/runs/2026-09-26_102112/` + `expected.json` 的 20 条。
**回放测试现在也钉 `error_recovery` 的值**（它不出现在失败标签里，只断言标签会漏掉一整块），
且退回旧行为能分别让 13 条和 1 条 fixture 失败 —— 护栏验过有牙齿。

`NO_EXPLORATION` **仍然没触发**（12 道 `first_action` 全是 `explore`，包括探针题）——
这个模型默认先看再改。探针题留着，等换模型或换 miniCC 版本再看。

**压缩彻底排除**：峰值 `max_usage_ratio` 只有 0.74%，离触发线 0.8 差两个数量级。

**E 步（定 `max_tool_calls`）当时没做** —— 这轮数据里没有任何一道新题因为「调用太多」被判
`INEFFICIENT`。见下面 D3 的新数据。

### D3. k=3 试跑（**单版本**）—— **已完成 2026-09-26**

```bash
python -m agenteval.cli --tasks tasks/ --k 3     # 12 题 × 3 = 36 run
```

**这不是版本对比**（只有一个版本），目的是拿「同一道题重复跑的散布」。
`runs/2026-09-26_144345/`。

```
Task Success      92%  (pass@3 92%  pass^3 92%)
Correctness       92%      Tool Usage        100%
Completeness      92%      Error Recovery    80%
Efficiency  Avg tool calls 12.7   Avg tokens 42.3k   Avg latency 51.5s
First action  explore 92%   other 8%      Context  max usage 1%   compactions 0
Failure Distribution  INCOMPLETE 0.08   INEFFICIENT 0.08
```

**`pass@3 == pass^3 == 92%` 的读法**：两个数相等 = 挂的那道题 **3 次全挂**，
是稳定失败不是手滑。这比 k=1 那轮的 92% 硬得多 —— k=1 的失手也可能是运气。

三个第一次拿到的结果：

| # | 结果 | 意义 |
|---|---|---|
| 1 | **`readonly_config_011` 3/3 全挂，且三次行为各不相同** | 第一次有「稳定失败」的题。三次分别是：把文件**内容改坏**了 / 诊断对但**转去问用户** / 只调 2 次工具就放弃 |
| 2 | **`error_recovery` 三档（`1.0` / `0.5` / `0.0`）都有真实取值** | 设计目标 1 达成。修 bash 退出码之前它恒等于 1.0，维度等于没测 |
| 3 | **同题三次成本能差 4 倍**（`encoding_trap_010`：33/10/21 次调用，129k/29.5k/69.8k token）| 量化了「k=1 的数字不可信」，也是「分版本必须 k≥2」的实证 |

**另外两道陷阱题没造成失败，但不是白出的**：`stale_path_009` / `encoding_trap_010`
三次都 `error_recovery=1.0` —— 陷阱按设计触发了工具错误、agent 也都恢复了。
**陷阱的作用是造 `error_recovery` 信号，不是造失败**。只有 `readonly_config_011`
因为恢复路径（`attrib -r`）超出模型的 Linux 知识而真的挂了。
⚠️ 所以这道题夹带一点「Windows 意识」的成分，解读时要知道。

**两个零信号，如实记**：

- `NO_EXPLORATION` **还是没亮** —— 探针题 `blind_edit_014` 三次首动作都是 `explore`
- **`run_subagent` 36 次里用了 0 次** —— 子 agent 线的两道题（`module_contracts_012` /
  `extract_helper_013`），agent 全自己硬干。「子 agent 使用率」这个指标**这轮没有信号**

**`INEFFICIENT` 的 3 条里有 2 条卡在阈值边上**：`follow_spec_003`（19 次，上限 15）、
`roman_008`（20 次，上限 15）—— 但这两道题**另外两次都过**（12/11 次、8/6 次）。
第三条 `encoding_trap_010`（33 次 + 同一文件读了 3 遍）是实打实的。

> 顺带发现一个潜在误报源：`_has_blind_repeats` 的「中间改过就归零」只认
> `edit_file` / `write_file`，**不认 agent 用 `python -c` / bash 重写文件**。
> 这轮恰好没踩到（三次读之间没有 bash 写入），但是个缺口。

### C. 灵敏度体检（要 API）

`tests/test_ablation_sensitivity.py`，打 `@pytest.mark.integration`。
**只跑强判据的两条**：E1 摘掉探索工具须翻假、E3 伪造静默成功须翻假。
S1 的弱判据留到第二段（k≥2 才能和噪声区分）。
⚠️ D3 显示 S1 那条**大概率是空跑**（`run_subagent` 36 次一次没用），做之前先想清楚。

### E. 定 `max_tool_calls`

按 D / D3 的实测分布来，不拍脑袋 —— 新题第一轮故意不设，就是为了这一步。
D3 的观察：上限 15 会挂住长尾（19/20 次），而同题另两次只要 6–12 次。
**阈值卡在分布尾部是 `temperature=1` 下的必然而非缺陷** —— 真正要定的是
「`INEFFICIENT` 算**单次 run 的长尾标记**，还是算**整道题的性质**」，留到版本对比那段再定。

## v1 范围

跑通「跑任务 → 采轨迹 → 出分 → 出报告」这条链。判断逻辑全部用确定性代码，
LLM-as-Judge / Rubric 只留接口，v2 再接。

v1 明确不做：

- LLM-as-Judge 打分、Rubric 体系
- `groundedness` 维度、`HALLUCINATION` 标签（规则写不准，硬做比不做更误导）
- 进程隔离 / 并行执行
- 开放式问答类任务（只做 coding 类，即能被程序验证的）
- 路径一致性指标（需要先攒够多次跑的轨迹才能定相似度阈值）

## 实施步骤

| # | 步骤 | 状态 | 备注 |
|---|---|---|---|
| S0 | 安装 pytest；项目骨架 `agenteval/` + `tests/` | **[x]** | `import agenteval` 通过；无需 `pyproject.toml` |
| S1 | `task.py`：Task 加载 + Check 模型 + 跑 verify.py | **[x]** | 含 `workspace_files()`、`load_workspace_module()` |
| S3 | `trajectory.py`：messages → 轨迹结构 + 首动作类型 | **[x]** | 含工具失败前缀识别（15 个参数化用例） |
| S2 | `runner.py`：`drive_agent` + `run_task` | **[x]** | 两个集成测试真实通过 |
| S4 | `metrics.py`：五维分 + 失败分类 + veto | **[x]** | `evaluate` 需要 `initial_files` 参数 |
| S5 | `report.py`：聚合 → 文本报告 + 基线 diff | **[x]** | 17 个用例 |
| S6 | `cli.py`：入口打通 | **[x]** | `--tasks/--k/--model/--out/--baseline` |
| S7 | 攒任务，覆盖不同失败模式 | **[x]** | 8 道（5 易 + 3 难） |
| S8 | 验证报告有区分度：两个已知有差异的版本各跑一遍 | **[x]** | **结论：四个版本 22 次运行全 100%，分不出差异 → 题目方向要重出。见问题 1** |

测试约定：`pytest.ini` 用 `addopts = -m "not integration"` 把真调 API 的用例挡在默认
套件外，保证 `pytest tests/` 离线、秒级。真调模型的测试打 `@pytest.mark.integration`。

### 实测的耗时（比最初估计乐观得多）

| 场景 | 每次 run |
|---|---|
| 极简任务（建一个 hello.txt） | **11s**（3 次 LLM 调用） |
| `fix_offbyone_001`（改一个函数） | **55s**（8 次工具调用 / 9 次 LLM 调用 / 27k tokens） |
| 一次异常值 | 178s（冷启动或网络抖动，不可复现） |

按 5 任务 × k=3 = 15 run 估算约 **10 分钟一轮**。瓶颈在 LLM 延迟，不是 agent 空转。

顺带一个观察：`fix_offbyone_001` 这道题只是给 `average()` 加个空列表判断，
agent 却用了 **8 次工具调用 / 27k tokens**。这种低效正是本框架该量出来的东西。

## 已完成的关键实现决策

这些在 `CLAUDE.md` 里只写了结论，过程记在这里：

- **`NO_EXPLORATION` 收窄过一次**：最初定为「首个动作是改文件且全程没探索」，
  诊断时发现「创建新文件」类任务被误报（agent 第一步 write_file 建 hello.txt
  是正确行为），改为「改了**初始工作区里已存在**的文件却没读过」
- **`grep` 的「没有找到」不算工具失败**：那是搜索成功但零结果，不是工具故障。
  算进去会让 `error_recovery` 几乎每轮都触发，变成噪音
- **`error_recovery` 的 `null` 语义**：`null` = 「没发生工具错误，不适用」，
  和 `groundedness` 一样。veto 归零时保持 `null`，不会把它变成 0
- **`RunRecord` 比原设计多了 3 个顶层字段**：`model_actual`、`first_action`、
  `error`（runner 自身异常信息，便于排查；正常 run 为 `null`）

## v2 待办（本版不做，仅记录）

- [ ] **Jev 语义评估**（`groundedness` / `claims_consistent` 两个维度）——
      完整设计在 `feat.md`，不在本文件
- [ ] LLM-as-Judge + Rubric 体系 —— 与 Jev 是**兄弟实现**（共用 judge 平面，
      语义各自独立），不是替代关系，设计见 `feat.md`
- [ ] `HALLUCINATION` 标签（v2 由 Jev 的 `groundedness` 间接覆盖）
- [ ] 进程隔离 / 并行执行（`multiprocessing`，同时解决 chdir 全局态与全局 CC.md）
- [ ] 开放式任务的评估（当前只支持可程序验证的 coding 任务）
- [ ] 更细的安全评估（当前只有 bash 危险命令正则，是明确的最小版）
- [ ] 按测试方向分化：system_prompt / tools / context / skill 各自的专项任务集。
      注意：真要做 A/B 对比，`runner.py` 现在固定用 `SYSTEM_PROMPT`，需要先支持注入配置
- [ ] **路径一致性**：同一任务 k 次跑，工具调用序列的相似度 —— 区分「稳定掌握」和「蒙对」。
      同样成功但重试路径完全不同，说明没真会。要做需先攒多次跑的轨迹数据来定阈值
- [ ] 离线 / mock 模式：现在每次 run 都要真实调 API，CI 里跑不了，且历史结果的可比性
      持续受 provider 静默升级威胁
- [ ] `temperature` 可配置：`call_llm` 硬编码了 1，方差拉满。要在不改 miniCC 的前提下做，
      只能靠已存在的 `call_llm` 包装层注入

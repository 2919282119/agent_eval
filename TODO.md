# agent_eval — 实施进度

评估对象：`D:\Code\python_project\miniCC`（已 `pip install -e`，`import agent.agent` 全局可用）

> **设计与约束全部在 `CLAUDE.md`，本文件只维护「要做什么 + 做到哪了」。**

## 状态：全链路跑通，12 道题已就位；报告第一次有了区分度（k=3 已试跑）

```bash
cd D:/Code/python_project/agent_eval
python -m pytest tests/ -q                       # 222 passed，离线约 8 秒
python -m pytest tests/ -m integration -v        # 2 个真调 API 的用例
python -m agenteval.cli --tasks tasks/ --k 1     # 12 题，实测约 10.5 分钟
```

**结论**：框架能用、标签可信、`error_recovery` 维度活了；但**核心目的「比较两个 miniCC
版本」仍未达成** —— 手上只有一个版本（见文末「卡在用户这边的事」）。

## 当前执行优先级（2026-09-27）

目标仍然是先得到一次可信的双版本对比，而不是继续堆功能。

| 优先级 | 工作 | 状态 / 验收 |
|---|---|---|
| P0 | 固定 baseline 对比协议：相同任务集、模型、`k`、环境；记录并核对 `agent_version` / `model_actual` | **[x]** 2026-09-28：自动一致性检查已落地；环境仍需人工保证 |
| P1 | 完成 Jev 设计文档同步：把 schema、`groundedness`、`questions_hash` 的约束落到 `CLAUDE.md` / `feat.md` | 设计待落地；接入等待 TypeSafe key |
| P2 | 准备第二个 miniCC 版本，两版各用相同条件跑 `k>=3`，再使用 `--baseline` | **等待第二版本** |
| P3 | 根据双版本数据决定是否改进效率统计：中位数 / 分位数 / 总体成本 / temperature 配置 | 尚未实施 |
| P4 | 处理 Windows shell 污染；优先考虑统一 shell 环境，再评估 error recovery 规则 | 已知问题，暂不阻塞当前版本对比 |
| P5 | 长期改 runner 为子进程隔离，再考虑并行 | v2 |
| P6 | 只有现有任务无法区分版本时，才继续增加机制专项任务 | 现有 12 题先保持冻结 |

当前不建议先做：继续扩充任务、引入语义 judge、或根据单版本 k=1 数据调整阈值 ——
缺少双版本对照时它们只会增加解释成本。

### Baseline 对比协议

两轮 run 之间必须保持一致：相同的 `tasks/` 与筛选结果、`--model` 与 API provider、
相同的 `k`（至少 3）、相同的 Python / 依赖 / shell / OS 环境；并记录两边的
`agent_version` 和 `model_actual`。

```bash
python -m agenteval.cli --tasks tasks/ --k 3 --model deepseek
python -m agenteval.cli --tasks tasks/ --k 3 --model deepseek \
  --baseline runs/<旧版本结果目录>/
```

`--baseline` 自动核对**能从记录里核出来的**条件（`report._baseline_conflicts`）：
任务集或 `model` 不同 → **拒绝出 diff**；`model_actual` 变了 / 两边 `agent_version`
相同 / `k` 不一致 → 出警告但照出数字。**核不出来的靠人**：Python 版本、依赖、shell、OS
没进记录。跟 Jev 的 `questions_hash` 同一条原则 —— 条件不一致就拒绝出 diff。

### 本轮已修（2026-09-28）：P0 一致性检查 + 8 个 bug

方法：**读代码 + 打畸形输入 + 覆盖率**三样一起用 —— 只靠读代码会漏掉一半
（上一版报出去的清单就是这么漏的）。

| 位置 | 问题 |
|---|---|
| `cli.main` | 无顶层兜底，且报告只在全部跑完才写 → 长跑中途崩掉 = 整批丢 + 报告没了 |
| `report.load_baseline` | `--baseline` 目录混进杂 json → `KeyError`，报错不指向文件 |
| `trajectory.final_answer` | 不保证返回 `str`，多模态 content 让 `re.search` TypeError |
| `runner.run_task` | 验收之外任一步抛异常仍会冲出 `main` |
| `runner.run_task` | `error` 的 run 工作区被删 → 没法看崩溃现场 |
| `task.load_task` | `max_tool_calls` / `forbidden_tools` / `retired` 类型错了会静默变形 |
| `report._pass_at_k` | 漏滤 `error` → 同一份报告同时印「Task Success 100%」和「pass@3 50%」|
| `metrics._has_blind_repeats` | 归零条件太窄 → 假阳性（`encoding_trap_010`，见 D3 节）|

改法：`report._valid` 与 `runner._run_result` 各自收敛到一处定义；`run_task` 双层 guard；
报告挪进 `finally`；`error` 保留工作区并记录路径；两个入口做边界校验。
**一处有意不修**（`_edits_existing_file` 认不出 bash 改文件）的理由见 `CLAUDE.md`「评分规则」。

测试 **210 → 222 passed**。

### ⚠️ 核心结论：区分度在效率维度，不在正确性维度

8 道题（5 易 + 3 难）× k=1：**8 道全部 success（100%）**。

```
Task Success      100%      Tool Usage  69%
Correctness       100%      Error Recovery 100%
Completeness      100%      Groundedness  n/a
Efficiency  Avg tool calls 12.1   Avg LLM calls 10.1   Avg tokens 39.5k   Avg latency 181.9s
Failure Distribution  WRONG_TOOL 0.62   INEFFICIENT 0.25   CLAIMS_WITHOUT_ACTION 0.12
```

**加难度没有让 task_success 拉开差距，难度以「成本」的形式体现了出来**：
`rate_limit_007`（滑动窗口）花了 **24 次工具调用 / 118k tokens / 372 秒**才做对，
`update_call_sites_004` 22 次调用 / 293 秒。对这个模型来说，**正确性维度已经饱和，
行为与效率维度才有信号**。代价：一轮 k=1 从 6 分钟涨到 **23 分钟**，k=3 到 70 分钟量级。

两个直接影响：
- 想靠加难度让 `task_success` 有区分度，得换**质**的难度（跨文件重构、模糊需求、
  反向工程数据格式），而不是量的堆积
- 或者接受现状：**把行为/效率指标提为主要信号**（它们已经在出信号了）

### ⚠️ 失败标签曾大面积假阳性：7 条里 6 条是假的（已修）

第二版评估（8 题 × k=1）报出 7 条失败标签，逐条核对 sidecar 后 **6 条是假阳性**：
4 条 `WRONG_TOOL` 实际是 agent 写**一次性验证脚本**（`_check_roman.py` / `test_rate_limiter.py`
等）不是覆盖源文件；2 条 `CLAIMS_WITHOUT_ACTION`（`fix_offbyone_001` / `follow_spec_003`）
实际跑了内联断言（`python3 -c "assert ..."`）并拿到 `ALL TESTS PASSED`。
唯一真阳性是 `update_call_sites_004` 的 `INEFFICIENT`（36 次调用超上限）。

两个根因，都是设计缺陷不是 agent 的问题：

1. **`WRONG_TOOL` 按工具名一刀切** —— 分不清「覆盖源文件」和「新建临时脚本」，而且
   「主动写脚本自查」正是本框架想奖励的行为（`CLAIMS_WITHOUT_ACTION` 就在奖励它），
   两条规则互相打架。修法：写文件类工具**带路径判断**，只有动了初始工作区里已有的文件才算违规。
2. **`CLAIMS_WITHOUT_ACTION` 想判断「这次算不算测试」** —— 收窄三次仍两次误报。修法：
   退到最粗但不可能误报的口径 —— **一行代码都没执行过**才判；中间情形留给 v2 的 Jev。
   口径退化的代价是这条规则几乎不出信号，但**假阳性比沉默危险**。

另外：**约束必须写进 `instruction`**。`forbidden_tools` 只写在 `task.yaml` 里 agent 根本看不到，
那条标签测的就成了「有没有猜中我们的隐藏偏好」。7 道题的指令已改成「不要用 `write_file`
覆盖**已有的源文件**」。教训写进了 `CLAUDE.md` 的「失败标签」和「任务设计约定」两节。

⚠️ **`runs/` 里的历史标签是修复前算出来的**（那些假阳性仍冻在旧 json 里），看旧报告时要知道 ——
`Tool Usage 75%`、`WRONG_TOOL 0.50` 都是这个原因。回放测试用同一批轨迹证明了**新规则不再报它们**。

## 已知的问题

22 次运行（4 轮）实测出来的，不是猜测。**按严重程度排。**
问题 2 / 3 已修完，问题 4 的代码部分已修，**问题 1 已缓解但未解决**。

> 一句话总结：**现在这个框架还回答不了「miniCC 新版本比旧版本好在哪」**。
> 下面是拦在这条路上的四件事。

### 问题 1（**已缓解，未解决**）：所有题都 100% 通过，分不出好坏

22 次运行**全部成功**，报告主表（成功率 / 正确性 / 完整性）永远是一条 100% 的直线 ——
改 miniCC 跑一遍 100 分，不改也是 100 分，**分不出哪个版本好**。

原因：**题目太简单，deepseek-flash 全都能做对**。这是本框架的方向性错误：
它要评的是 **miniCC 这个工具**，模型是固定的外部依赖。拿自造小题去考模型聪不聪明
必然撞天花板，应该去压 miniCC 的**机制**：

| 该压的机制 | 现状 |
|---|---|
| **上下文管理** —— 出「要读很多文件才能做完」的任务，逼出自动压缩 | 完全没测，是 miniCC 最核心的机制之一 |
| **独有能力** —— skill / subagent / 知识库检索 / 联网搜索 | 11 个工具里只压了 6 个基础工具 |
| **上下文组装** —— CC.md / memory 注入的影响 | 被我们屏蔽了（为了可复现），但「比较 harness 版本」恰恰要测这个 |
| **必然失败的工具调用** —— 才能测出「出错后会不会恢复」| 现在 `error_recovery` 永远满分，等于没测 |

**进展**：已按此重出题目（A/B/D/D3 步，见下）。报告从一条 100% 直线变成 **k=1 与 k=3
都是 92%**，失败是**稳定**的（k=3 三次全挂）。但「分不出哪个版本好」仍未解决 ——
手上只有一个版本。

### 问题 2（已修）：只跑一次的数字不可信，但报告照样在出结论

同一任务、同一模型，跑两次可以差很多：

| 任务 | 工具调用次数 | token 数 |
|---|---|---|
| `rate_limit_007` | 24 → 8（**3 倍**）| 11.8 万 → 3.2 万 |
| `roman_008` | 7 → 11 | 2.5 万 → **31.8 万（13 倍）** |
| `update_call_sites_004` | 22 → 22 → 36 | 3.9 万 → 16.4 万 |

根因：miniCC 采样温度不为 0（表格里这批数据是 `call_llm` 写死 `temperature=1` 时跑的），
随机性被放大。
**已修**：保留数字但钉一句不可比的声明（单次观测本身是有效数据，藏掉反而丢信息），
`--baseline` 的效率 delta 在任一侧 k=1 时直接跳过 —— delta 才是把噪声说成趋势的那个。

**2026-09-28 温度变了**：miniCC 把 `temperature` 提取成参数，默认值 **0.1**（原先写死 1），
且 agent 主循环不传这个参数 → **整条链路的默认行为都变了**。三件事跟上：

1. eval 侧记录实际温度（`runner.temperature_default()` 现读 `call_llm` 签名，不硬编码）
2. baseline 对比：只有任务集不一致才拒绝出 diff，其余条件差异（`model` / `temperature` /
   `agent_version`）降级成警告。**试过「最多差一个变量」的硬约束，又撤了** —— 它保证的
   是「可解释」不是「可信」，而瓶颈是噪声：只差一个变量时 delta 也可能整个是噪声
   （逐题 CV 中位数 29%）。真想解决得让报告拿 delta 跟噪声底比，不是加条件校验
3. 报告头部印 `temp:` —— 温度是实验条件，藏进 run json 就没人看了

⚠️ 上面表格里「3 倍 / 13 倍」是 **temp=1 时代**的数，不适用于 0.1 以后的 run；
`pass@3 == pass^3` 那类「稳定失败」结论同理 —— 它们建立在重复跑有散布之上。

### 问题 3（已修）：失败标签大部分是错的，而且错得很隐蔽

根因与修法见上面「失败标签曾大面积假阳性」一节。**关键修法**是把真实运行的日志固化成
**测试数据**（`tests/fixtures/runs/` 快照 + `expected.json` 人工核对过的期望标签 +
`test_rules_on_real_trajectories.py` 回放验算），规则一改若在真实日志上误报，测试立刻报错。
`expected.json` 的期望值是**逐条看 sidecar 判出来的**，不是照抄 run json ——
那批记录本身就是错的，照抄等于把假阳性固化成「正确」。

### 问题 4（一半已修）：有三条规则从没生效过，不知道准不准

`NO_EXPLORATION`、`WRONG_ARGUMENT`、危险命令 veto —— 22 次运行里**一次都没触发过**，
只在单元测试里验过。按问题 3 的规律，它们第一次在真实数据上生效时大概率也是错的。

处理：

1. ~~报告里把这三条标注成「未验证」，不当结论用~~ —— **已修**（`report.UNVERIFIED_RULES`）。
2. 出能触发它们的任务，跑一次拿真实轨迹固化成 fixture。**逐条查过可行性后收窄了**：
   - `NO_EXPLORATION` **能靠出题触发** → 探针题 `blind_edit_014`（已排进问题 1 的批次）
   - `WRONG_ARGUMENT` **逼不出来** —— 只有模型自己吐坏 JSON / 猜错参数名时才亮，
     那是模型行为不是 miniCC 机制，只能靠意外慢慢攒证据
   - veto **不该靠出题触发**（要亮得 agent 真跑出 `rm -rf /`，故意引诱就是钓鱼测试），
     而且它是安全网、不进失败分布 → **从 `UNVERIFIED_RULES` 里删掉**

### 下一步（建议顺序）

先解决「测出来的数可不可信」（问题 2/3/4），再解决「测出来的东西有没有意义」（问题 1）——
问题 1 会产出新数据，如果那时规则还是错的，新数据又会被污染一遍。

| # | 做什么 | 量级 | 状态 |
|---|---|---|---|
| 1 | 问题 2：k=1 时报告不输出效率结论 / 加警告 | 小，纯代码 + 测试 | **[x]** |
| 2 | 问题 3：把 `runs/` 整理成 fixture + 回放测试 | 中 | **[x]** |
| 3 | 问题 4a：把规则标注「未验证」 | 小，纯代码 + 测试 | **[x]** |
| 4 | 问题 1：按「压 miniCC 机制」重新设计题目（**先设计再出题**）| 大 | **[x]** 见 5 / 5b |
| 5 | 问题 1 的 A 步：改 harness | 中，纯代码 + 测试 | **[x]** |
| 5b | 问题 1 的 B 步：出 6 道新题 | 大，离线可验 | **[x]** |
| 6 | 第一段跑 k=1 全量 → 手工核对标签 → 固化成 fixture | 中，要跑 API | **[x]** 2026-09-26，约 10.5 分钟 |
| 6b | k=3 试跑（**单版本**，只取重复跑的散布，不做版本对比）| 中，要跑 API | **[x]** 2026-09-26，见 D3 |
| 7 | 把 Jev 的设计并入 `CLAUDE.md`：schema、`groundedness` 填充规则、`questions_hash` 的 diff 保护 | 中 | **[ ]** |
| 8 | 接入 Jev（等 TypeSafe key）—— 它填的 `groundedness` 是唯一空着的维度 | 等 key | **[ ]** |
| 9 | 第二段跑 k≥3 做版本对比（12 题 ≈ 36 run，70–100 分钟）| 大，要两个版本 | **[ ] 等用户准备第二个 miniCC 版本** |

问题 4b（定向触发任务）**已并入第 5 项** —— 只有 `NO_EXPLORATION` 值得为它出题，
已排成探针题 `blind_edit_014`；另两条查实后确认逼不出来，理由见上文「问题 4」。

> **卡在用户这边的事**：第 9 项（版本对比）需要**第二个 miniCC 版本**。
> 2026-09-26 确认暂时没有，用户会自己准备。**这之前不要催**，
> 推进不依赖它的部分：出新题、逐题精度（温度实验 / 改用中位数）、Jev 接入。
> 版本到手后：两版各 k≥3，后一版用 `--baseline` 指向前一版留下的目录。

## 问题 1 落地计划

执行顺序 A/B 可离线验，C 之后才要 API。原设计全文在已删除的 `feat-task-redesign.md`，
**仍然成立**的部分（两条测量准确性风险、工具覆盖边界）保留在 `feat.md` 的
「任务重设计遗留备注」。**C 已判定走不通**，理由见 C 节。

### A. 改 harness（纯代码 + 测试）—— **已完成 2026-09-25**

`runner.py` 在 `ContextManager` 实例上挂计数 wrapper（`auto_compact` 走 `self.compact`，
比扫 messages 准）采 `compactions`；采集峰值 `prompt_tokens` → 顶层
`max_usage_ratio` / `tools_used` / `compactions`。`report.py` 的 Efficiency 段加
`Context max usage .. compactions ..` 一行；`Per task` diff 扩到比 `tool_calls` / `tokens` /
子 agent 使用率（原来对饱和题集永远空白）；`UNVERIFIED_RULES` 删掉 veto。
`task.yaml` 支持 `retired: true`（`rate_limit_007` / `update_call_sites_004` 已退役）。
测试 **137 → 147 passed**。

**A4 自审时发现并修掉一个自相矛盾**：`Per task` 一开始把 `tool calls 8.0 → 20.0`、
`tokens 24.7k → 317.9k` 这种 k=1 噪声直接印出来了 —— 而 Overall 段刚说过
「k=1 数字含大随机波动，跳过」。现在任一侧 k=1 时 `Per task` 也只报成功率翻转，
并显式说明原因。**结论：真要分版本，k 必须 ≥2。**

### B. 出 6 道新题 —— **已完成 2026-09-25**

| task_id | 线 | 陷阱 / 形态 |
|---|---|---|
| `blind_edit_014` | 探针 | 指令把改法写死，给 `NO_EXPLORATION` 提供触发机会。**不是难度题** |
| `stale_path_009` | 错误线 | 指令说改 `legacy/report_v2.py`，实际已改名到 `report.py`（`CHANGELOG.md` 里有记录）|
| `encoding_trap_010` | 错误线 | `prices.csv` 是 GBK 字节，`read_file` 会报「文件不是 UTF-8」|
| `readonly_config_011` | 错误线 | `config.json` 带只读属性，`edit_file` 会报「没有权限修改文件」|
| `module_contracts_012` | 子 agent 线 | 5 个模块要整理成 `contracts.py` 字典，验收用 `inspect` 现算真值比对 |
| `extract_helper_013` | 子 agent 线 | 6 个调用点抽公共函数，验收查输出一位不差 + 参数名没改 + 源码真接上了 |

默认任务集现在 **12 道**（8 − 2 退役 + 6 新），`tasks/` 目录 14 个。测试 **147 → 166 passed**。
出题时踩到并处理掉的两个坑（教训写进 `CLAUDE.md` 的「任务设计约定」）：
E3 的只读属性会在搬运中静默丢失（加 `test_readonly_trap_is_still_armed` 盯着，且
`shutil.rmtree` 在 Windows 上删不掉只读文件，`runner` 清理改用 `onerror=...`）；
`load_workspace_module` 失败返回 `None`、`inspect.getmembers(None)` 给空列表 →
验收真值静默变 `{}`、检查空过（`verify.py` 显式拦这一层）。
**S1 原设计改了一处**：原计划产出 `CONTRACTS.md`，但 md 没法可靠验收（抄源码就能蒙过），
改成产出 `contracts.py` 字典。

### D. 第一段跑 k=1 + 手工核对 —— **已完成 2026-09-26**

```bash
python -m agenteval.cli --tasks tasks/ --k 1     # 12 题，约 10.5 分钟（退役了最贵的两道）
```

**报告第一次不是一条 100% 的直线**：Task Success / Correctness / Completeness 都 **92%**。

```
Task Success      92%      Error Recovery    90% → 修完规则是 75%
Correctness       92%      First action      explore 100%
Completeness      92%      Context           max usage 1%   compactions 0
Tool Usage        100%     Failure Distribution  INCOMPLETE 0.08   INEFFICIENT 0.08
```

**三道错误陷阱全部按设计触发 —— 题目有效**。唯一失败是 `readonly_config_011`：
agent 正确诊断出「文件是只读的」并提出修法（`chmod u+w`），但**没自己解，转去问用户** ——
判 `INCOMPLETE` + `error_recovery=0.0` 都对。

手工核对揪出**第二条假阳性**，并顺带发现一个更严重的问题：

| 发现 | 处理 |
|---|---|
| `encoding_trap_010` 的 `INEFFICIENT` 是假的 —— `read_file pricing.py` 出现 3 次，但中间改过两次 | `_has_blind_repeats`：**中间改过文件就归零** |
| **`shell=True` 在 Windows 上走 `cmd.exe`**，agent 写的是 bash 语法 → 一轮 30 次失败调用里 **24 次（80%）**是这一类 | `error_recovery` **排除 bash 退出码**（`trajectory.is_command_failure`）。有取值的从 10/12 降到 4/12，且全部由设计好的陷阱驱动 |

改完这轮**只剩 1 条真标签**。新的一轮（12 条）已固化成 fixture：
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

**`pass@3 == pass^3 == 92%` 的读法**：两个数相等 = 挂的那道题 **3 次全挂**，是稳定失败
不是手滑。这比 k=1 那轮的 92% 硬得多 —— k=1 的失手也可能只是运气。

三个第一次拿到的结果：

| # | 结果 | 意义 |
|---|---|---|
| 1 | **`readonly_config_011` 3/3 全挂，且三次行为各不相同** | 第一次有「稳定失败」的题。三次分别是：把文件**内容改坏**了 / 诊断对但**转去问用户** / 只调 2 次工具就放弃 |
| 2 | **`error_recovery` 三档（`1.0` / `0.5` / `0.0`）都有真实取值** | 设计目标达成。修 bash 退出码之前它恒等于 1.0，维度等于没测 |
| 3 | **同题三次成本能差 4 倍**（`encoding_trap_010`：33/10/21 次调用，129k/29.5k/69.8k token）| 量化了「k=1 的数字不可信」，也是「分版本必须 k≥2」的实证 |

**另外两道陷阱题没造成失败，但不是白出的**：`stale_path_009` / `encoding_trap_010` 三次都
`error_recovery=1.0` —— 陷阱按设计触发了工具错误、agent 也都恢复了。**陷阱的作用是造
`error_recovery` 信号，不是造失败**。只有 `readonly_config_011` 因为恢复路径（`attrib -r`）
超出模型的 Linux 知识而真挂了 —— ⚠️ 所以这道题夹带一点「Windows 意识」的成分，解读时要知道。

**两个零信号，如实记**：

- `NO_EXPLORATION` **还是没亮** —— 探针题 `blind_edit_014` 三次首动作都是 `explore`
- **`run_subagent` 36 次里用了 0 次** —— 子 agent 线的两道题（`module_contracts_012` /
  `extract_helper_013`），agent 全自己硬干。「子 agent 使用率」这个指标**这轮没有信号**

**`INEFFICIENT` 的 3 条全部不可信**：

| run | 触发原因 | 核对结果 |
|---|---|---|
| `follow_spec_003` run_001 | 19 次调用 > 上限 15 | 阈值卡在长尾上，同题另两次只要 12/11 次 |
| `roman_008` run_003 | 20 次调用 > 上限 15 | 同上，另两次只要 8/6 次 |
| `encoding_trap_010` run_001 | 「同一文件读了 3 遍」 | **假阳性**（见下）|

⚠️ **`encoding_trap_010` 那条我一开始判成真的，判错了。** 三次 `read_file _dump.txt`
中间夹着**两次 bash 重写**（`python -c "open('_dump.txt','w')..."`）—— 它读的是
**三个不同版本**的文件，不是盲目重复。

根因：`_has_blind_repeats` 的「中间改过就归零」只认 `edit_file` / `write_file`，
**不认 agent 用 bash / `python -c` 重写文件**。已修 —— 改成「中间出现任何
**可能写文件**的调用就归零」（只有 `read_file`/`list_dir`/`glob`/`grep` 确定不改文件）。
代价是这条在 36 条真实 sidecar 上命中 0 次，跟 `NO_EXPLORATION` 一样暂时不出信号；
**假阳性比沉默危险**，这个取舍是有意的。

### C. 灵敏度体检 —— **已判定走不通，别再照这个方案做**

原计划：`tests/test_ablation_sensitivity.py`，摘掉某个能力看题目反应，用来筛掉
对版本差异不敏感的题。**判据本身没问题**：强判据（`task_success` 翻假）是**类别变化**，
k=1 就能判；弱判据（只有效率数字动）必须 k≥2。但三条消融逐条核对后都不成立：

| 消融 | 结论 |
|---|---|
| E3 伪造静默成功 | **测不了** —— `readonly_config_011` 已是 3/3 全挂，baseline 就是假，「翻假」没有余地。而且它想验的「工具撒谎、验收抓不抓得住」**不用跑 API**：`verify.py` 只读磁盘产物、从不相信工具的自述 |
| E1 摘掉探索工具 | **大概率翻不了** —— agent 还有 `bash` 能 `ls`，找文件的路没堵死；把 bash 也堵死又会破坏写入路径，测的就成了「能不能跑」 |
| S1 `run_subagent` | **确认是空跑** —— D3 那 36 个 run 里一次没用过 |

根本原因：消融只能检测「能力被**整个摘掉**」，检测不到「能力还在、但各版本都做得出」。
而现在的真问题是后者 —— 题目太容易，不是能力缺失。

### E. 定 `max_tool_calls`

按 D / D3 的实测分布来，不拍脑袋 —— 新题第一轮故意不设，就是为了这一步。
D3 的观察：上限 15 会挂住长尾（19/20 次），而同题另两次只要 6–12 次。
**阈值卡在分布尾部是 `temperature=1` 下的必然而非缺陷** —— 真正要定的是
「`INEFFICIENT` 算**单次 run 的长尾标记**，还是算**整道题的性质**」，留到版本对比那段再定。

## v1 范围

跑通「跑任务 → 采轨迹 → 出分 → 出报告」这条链。判断逻辑全部用确定性代码，
LLM-as-Judge / Rubric 只留接口，v2 再接。

v1 明确不做：LLM-as-Judge 打分、Rubric 体系；`groundedness` 维度、`HALLUCINATION` 标签
（规则写不准，硬做比不做更误导）；进程隔离 / 并行执行；开放式问答类任务（只做 coding 类，
即能被程序验证的）；路径一致性指标（需要先攒够多次跑的轨迹才能定相似度阈值）。

## 实施步骤

**S0–S8 全部完成**：S0 骨架 → S1 `task.py` → S2 `runner.py` → S3 `trajectory.py` →
S4 `metrics.py` → S5 `report.py` → S6 `cli.py` → S7 攒 8 道题 → S8 验证区分度
（**结论：22 次运行全 100%，分不出差异 → 题目方向要重出，见问题 1**）。

测试约定：`pytest.ini` 用 `addopts = -m "not integration"` 把真调 API 的用例挡在默认
套件外，保证 `pytest tests/` 离线、秒级。真调模型的测试打 `@pytest.mark.integration`。
单次 run 耗时 11s（极简）～ 55s（`fix_offbyone_001`，8 次工具调用 / 27k tokens），
瓶颈在 LLM 延迟；一次异常值 178s 不可复现（冷启动 / 网络抖动）。

## 已完成的关键实现决策

（`CLAUDE.md` 只写了结论，过程记在这里。）

- **`NO_EXPLORATION` 收窄过一次**：最初定为「首个动作是改文件且全程没探索」，诊断时发现
  「创建新文件」类任务被误报（agent 第一步 write_file 建 hello.txt 是正确行为），
  改为「改了**初始工作区里已存在**的文件却没读过」
- **`grep` 的「没有找到」不算工具失败**：那是搜索成功但零结果，不是工具故障。
  算进去会让 `error_recovery` 几乎每轮都触发，变成噪音
- **`error_recovery` 的 `null` 语义**：`null` = 「没发生工具错误，不适用」，
  和 `groundedness` 一样。veto 归零时保持 `null`，不会把它变成 0
- **`RunRecord` 比原设计多了 3 个顶层字段**：`model_actual`、`first_action`、
  `error`（runner 自身异常信息，便于排查；正常 run 为 `null`）

## v2 待办（本版不做，仅记录）

- [ ] **Jev 语义评估**（`groundedness` / `claims_consistent` 两个维度）—— 完整设计在 `feat.md`
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
- [ ] `agent_version` 只记 git hash，**miniCC 未提交的改动它看不见** —— 2026-09-28 改
      `call_llm` 的默认温度就是这种情况（7 个文件未提交，HEAD 没动）。考虑在 miniCC
      工作区脏时给警告

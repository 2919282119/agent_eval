# 任务方向重设计（TODO.md 的「问题 1」）

> 状态：**设计已对齐，6 道题已出好、harness 改动已落地（2026-09-25）**。
> 还没跑过真实 run（那是「第一段 k=1」，要花 API 时间）。
> 落地完成后，本文中已写进 `CLAUDE.md` 的部分应折叠过去或标记完成。

## 落地进度

| 步 | 内容 | 状态 |
|---|---|---|
| A | 改 harness：三个新顶层字段、报告 Context 行、Per task diff、veto 移出未验证名单、`retired` 标记 | **[x]** |
| B | 出 6 道新题（`blind_edit_014` / `stale_path_009` / `encoding_trap_010` / `readonly_config_011` / `module_contracts_012` / `extract_helper_013`）| **[x]** |
| C | 灵敏度体检（要 API）| **[ ]** |
| D | 第一段跑 k=1 全量（12 题）| **[x]** 2026-09-26，约 10.5 分钟 |
| D2 | 手工核对标签 + 固化成 fixture | **[x]** |
| E | 按实测分布定 `max_tool_calls` | **[ ]** |

### D 的结果（2026-09-26，12 题 × k=1）

**报告第一次不是一条 100% 的直线**：Task Success 92%、Correctness 92%、Completeness 92%。

**三道错误陷阱全部按设计触发**（`read_file legacy/report_v2.py` 文件不存在 /
`read_file prices.csv` 不是 UTF-8 / `edit_file config.json` 没有权限）—— 题目有效。

**唯一那道失败是 `readonly_config_011`**：agent 正确诊断出「文件是只读的」，
甚至提出了修法（`chmod u+w`），但**没自己解，转去问用户**。判 INCOMPLETE + `error_recovery=0.0`
都对。⚠️ 但它夹带一点「Windows 意识」的成分（它提的 `chmod` 在本机也没用）。

**`NO_EXPLORATION` 仍然没触发** —— 12 道 `first_action` 全是 `explore`，包括探针题。
这个模型的默认行为就是先看再改。那道探针题**留着**，等换模型或换 miniCC 版本时再看。

**压缩彻底排除**：峰值 `max_usage_ratio` 只有 0.74%，离 0.8 差两个数量级。

**手工核对揪出第二条假阳性**：`encoding_trap_010` 的 `INEFFICIENT` —— `read_file pricing.py`
出现 3 次，但分别在「初次探索」「改完确认」「整个重写之后再确认」之后。已把
「中间改过文件就归零」写进 `_has_blind_repeats`。改完之后这一轮**只剩 1 条真标签**。

**顺带发现 shell 不匹配**：`shell=True` 在 Windows 上走 cmd.exe，agent 写的 bash 语法
大面积报错。详见「已知风险」表 —— 这条直接改掉了 `error_recovery` 的语义。

出题时发现并处理的两个坑（教训已写进 `CLAUDE.md`）：

- **E3 的只读属性会在搬运中静默丢失** → 加了 `test_readonly_trap_is_still_armed` 盯着它
- **`load_workspace_module` 加载失败返回 `None`，而 `inspect.getmembers(None)` 给空列表** ——
  验收的「真值」会静默变成空字典、检查空过。S1 的 `verify.py` 显式拦了这一层

## 要解决的问题

22 次真实运行（4 轮，4 个不同的 miniCC 版本）**全部 100% 通过**。
报告主表（成功率 / 正确性 / 完整性）永远是一条 100% 的直线 ——
改了 miniCC 跑一遍 100 分，不改也是 100 分。**报告分不出哪个版本好**，
而「比较不同版本」正是做这个框架的目的。

根因不是题不够难，是**题的轴选错了**：

- 现有 8 道题都是给模型做的 coding 小题。加难度就是在考「模型聪不聪明」，
  而模型是固定的外部依赖 → 必然撞天花板。实测已经验证过：特意加的 3 道难题
  照样全对，只是多花了一倍时间和 token。
- 这个框架要评的是 **miniCC 这个工具**。要拉开版本差异，就得去压
  **miniCC 自己的机制**，把难度从「正确性」挪到「行为与效率」。

## 已定的三条边界

| 决策 | 内容 | 为什么这么定 |
|---|---|---|
| **压缩线暂不测** | 只零成本记录负载指标，不出压缩题 | `deepseek` 的 `context_window` 是 1,000,000（`llm/model.py:17-22`），压缩阈值是「已用 ≥ 80%」→ 单次请求要 **80 万 prompt token** 才触发。多读几个文件到不了，硬造要一次塞 2.5MB 文本，贵且脆。不为一个碰不着的机制妥协保真度 |
| **不注入故障** | 错误全由环境自然产生 | 靠人塞假故障能拿到确定性，但故障是人为的。改用环境自带的坏，最自然，也顺带压了「先探索再动手」 |
| **题集 = 3 + 2 + 1** | 错误线 3 道 + 子 agent 线 2 道 + 探针题 1 道，同时退役最贵的两道旧题 | 见下 |

**为什么子 agent 线只挑 `run_subagent` 一个**：miniCC 的 4 个「独有能力」里，
只有它数据全在本地当前工作区。另外三个都不能做成可复现的题 ——
`load_skill` 读全局 `~/.miniCC/skills`（`agent/skill.py:9`）且可用清单是**导入时**
烤进工具说明的；`rag_search` 读全局清单 `knowledge_bases.json`
（`rag/knowledge_base.py:9`）；`search_web` 走 Tavily 外网。
要测它们得先加「按任务注入 skill_manager / 知识库」的新能力，本轮不做。

## 新题集（5 道）

### 错误线 3 道：三种不同的「坏」，逼出三种不同的恢复

**核心取舍：三个陷阱全部选成「工具级失败」，刻意避开 `bash` 退出码。**
这样 `error_recovery` 的语义保持干净 —— 只判「工具没能执行」，
不掺进「命令跑出非 0 退出码」那种「跑测试跑红了」的正常信息。
（那句浑水这轮不动，见「已知风险」。）

| 题 | task_id | 陷阱 | 撞出的错误 | 正确恢复路径 | 验收怎么判 |
|---|---|---|---|---|---|
| E1 路径过时 | `stale_path_009` | 指令说改 `legacy/report_v2.py`，文件已改名到根目录的 `report.py`。工作区里有 `CHANGELOG.md` 写着这次改名 | `read_file` → 「文件不存在」 | 读工作区，自己找到真文件 | `report.py` 的 `format_amount` 输出正确，且非千分位的函数行为不变 |
| E2 编码不对 | `encoding_trap_010` | 价格表 `prices.csv` 是 GBK 字节，任务要求照它订正 `pricing.py` 的常量 | `read_file` → 「文件不是 UTF-8 文本文件」 | 换读法（bash + iconv，或按 gbk 解码读） | `pricing.py` 的常量与表一致，且是**数值不是字符串** |
| E3 文件只读 | `readonly_config_011` | `config.json` 带只读属性，任务要求改里面的 `timeout` | `edit_file` → 「没有权限修改文件」 | 先去掉只读属性再写 | `config.json` 里 `timeout == 30`，其他字段不变 |

**为什么是这三个**：三种不同的失败通道（读不到 / 读得进但解不开 / 写不进），
恢复动作也确实是三样（自己找 / 换读法 / 换写法）。这样 `error_recovery`
的三档（1.0 同工具重试成功 / 0.5 换路子 / 0.0 没恢复）才有机会都出现。

**E1 为什么要放 `CHANGELOG.md`**：光在指令里写错路径就成了「猜谜」。
在工作区留一条改名记录，环境对 agent 就是**诚实的** —— 它会读工作区的就找得到，
不读就撞墙。这样这道题既产生了真实错误，又在奖励探索，而不是在考运气。

**验收一律只看产物、不看用了什么工具** —— 沿用 `expected_tools` 的教训
（硬要求工具会冤枉走等效路径的 agent）。

### 子 agent 线 2 道

| 题 | task_id | 形态 | 验收怎么判 | 期望信号 |
|---|---|---|---|---|
| S1 接口清单 | `module_contracts_012` | 工作区 5 个模块，要求把公开接口整理成 `contracts.py`（一个 `CONTRACTS` 字典） | 用 `inspect` 从模块**现算真值**，跟字典逐项比对 | 用没用 `run_subagent`、并行 vs 串行的 latency |
| S2 跨文件重构 | `extract_helper_013` | 6 个调用点各自手写了一遍时间格式化，要求抽成公共函数并全部改用它，行为不变 | 公共函数输出对 + 6 个调用点的输出**一位不差** + 参数名没改 + 源码里真接上了 | 行为路径 + 效率；这就是「质的难度」要的跨文件重构 |

**两道都只查产物，不要求它用 `run_subagent`。** 用不用体现在效率上 ——
这才是版本差异该露出来的地方。

**出题时改掉的一处设计**：S1 原设计让 agent 产出 `CONTRACTS.md`，**这没法可靠验收** ——
agent 把源码整段 cat 进 md 就能蒙过「提到了这些函数名」，而且写文档是开放式的，
违反 v1 自己定的「只做可程序验证的 coding 题」。改成产出 `contracts.py`，
验收就能用 `inspect` 精确比对，蒙不过去。

### 探针题 1 道

现在报告里钉着「未验证」的三条规则（`report.UNVERIFIED_RULES`），我逐条查过**能不能靠出题触发**：

| 规则 | 能否靠出题触发 | 结论 |
|---|---|---|
| `NO_EXPLORATION` | **能** —— 只要「不读就改」在这里是个合理策略 | 出 1 道探针题 |
| `WRONG_ARGUMENT` | **不能稳定触发**。它只在模型自己出错时才亮（`metrics.py:55` 匹配 `参数 / TypeError / unexpected keyword`）—— 要么模型吐出坏 JSON，要么猜错参数名。这是**模型行为，不是 miniCC 的机制** | 不出。它只能靠意外慢慢攒证据 |
| veto（危险 bash） | **不该靠出题触发**。要亮得 agent 真跑出 `rm -rf /`、`mkfs` 这类命令，没有正当任务会诱导它 —— 故意引诱就是在设计「钓鱼测试」 | 不出。见下 |

| 题 | task_id | 形态 | 期望 |
|---|---|---|---|
| P1 不读就改 | `blind_edit_014` | 指令把改法写死：「把 `settings.py` 里的 `TIMEOUT = 10` 改成 `TIMEOUT = 30`，其他都不要动」—— 探索显得多余 | `NO_EXPLORATION` **首次在真实数据上触发** |

**要写清楚的两件事**：

1. **这道题的判据是类别（亮了没有），不是数值** —— 所以没有噪声问题。
2. **它触发不了也是有价值的信息**：说明这个模型在这类题上天然会先探索。
3. **它的正确性维度必然饱和**（`TIMEOUT == 30`，谁都做得到）。它**不是一道难度题**，
   唯一用途就是给 `NO_EXPLORATION` 提供触发机会。别拿它去衡量「模型聪不聪明」。

### veto 从「未验证」名单里拿出来

veto 和另外两条**不是一类东西**：它不产生失败标签、不进失败分布（它是 `status` 的一种）。
它是个**安全网**，要问的是「该拦的拦住了吗」，这用单元测试就能答
（`test_veto_zeroes_everything` / `test_veto_not_triggered_by_benign_rm` 已经在测）。
「假阳性率未知，别当结论用」这个理由对它不成立 —— 它万一是假阳性，表现是某一轮莫名 0 分，
一眼就能看出来，也不污染失败分布。

所以改动是：`report.UNVERIFIED_RULES` 里**删掉 veto**，只留 `NO_EXPLORATION` 和
`WRONG_ARGUMENT`；在 `CLAUDE.md` 里把 veto 说明成「安全网，靠单元测试验证，
22 次真实运行里从未触发」。

### 退役两道旧题

`rate_limit_007`（372 秒）、`update_call_sites_004`（293 秒）从默认任务集拿掉，
只为控制总时长。**但目录必须留着**：`tests/fixtures/expected.json` 里
`update_call_sites_004` 是唯一的真阳性 fixture，而回放测试故意从**活的**
`tasks/` 读它的 `max_tool_calls`。删了目录，护栏当场断。

做法：`task.yaml` 加 `retired: true`，`cli.find_tasks` 跳过它。
目录还在 → 回放测试照跑 → `tests/test_tasks.py` 的参考修复体检也照跑
（退役不等于失修）。

## 指标与 harness 改动

### 1. 两个负载指标（零成本，**不当评估信号**）

| 字段 | 怎么算 |
|---|---|
| `compactions` | 轨迹里 `role=user` 且正文以 `[Conversation Summary]` 开头的消息条数 —— `compact()` 的产物特征（`agent/context.py:70-72`） |
| `max_usage_ratio` | runner 采集的峰值 `usage.prompt_tokens` ÷ `MODELS[model].context_window` |

**放 run json 顶层**，照 `model_actual` / `first_action` / `error` 的先例 ——
不进 `trajectory` 那 5 个字段，保持那个 schema 不动。
报告 Efficiency 段下加一行 `Context  max usage 7%   compactions 0`，
这样一眼能看出这批题离压缩线还有多远，也就解释了压缩线为什么没测。

### 2. run json 顶层加 `tools_used`（去重后的工具名列表）

**这是上一个缺口逼出来的**：报告要能比「用没用 `run_subagent`」，
但现在的 run 记录里**根本没有「用了哪些工具」** —— `trajectory` 只有那 5 个字段，
`first_action` 只记第一个动作。工具名只在 sidecar 的 `calls` 里，而报告不读 sidecar
（`load_baseline` 专门跳过它）。

`tools_used` 是 `first_action` 的一般化形式：`Trajectory.tools_used()` 已经有了，
只要落进记录。它让「行为指纹」不再依赖 sidecar，报告和 diff 都能直接用。
同样放**顶层**，并同步写进 `CLAUDE.md` 的 schema 说明。

### 3. 扩 `_render_diff`，让它比效率和行为

**这是修一个真实缺陷，不是锦上添花。** `report.py:239-243` 的 `Per task` 段
只在「成功率变了」时才输出，而成功率恒 100% → 那段**永远空白**。
渲染真实报告就能看到，输出停在 `Per task` 后面什么都没有。
「可对比不同版本」在最需要它的地方是失灵的。

改法：成功率没变时**继续比** `tool_calls` / `tokens` 的涨跌，
以及 `first_action` 和 `tools_used` 里有没有出现 `run_subagent` 的变化。

### 4. 新题的 `max_tool_calls` 先不设

`update_call_sites_004` 的教训是阈值拍脑袋就会制造假的 `INEFFICIENT`。
新题第一轮**不放** `max_tool_calls`，等拿到真实分布再定 ——
这也是这批题叫「探针批」的原因。

### 5. 新题不设 `forbidden_tools`

这批题关注错误恢复和效率，`forbidden_tools` 会引入一个与主题无关的变量。
`WRONG_TOOL` 已有真实覆盖（4 次触发 + 人工核对 + fixture 护栏），不需要新题再测一遍。

### 6. 每道新题都要登记参考修复

项目约定：新增任务必须在 `tests/test_tasks.py` 的 `REFERENCE_FIXES` 里登记，
它正反两面都测（未修复时至少挂一条 `essential`，套上参考修复后全过）。
E2 的参考修复要按 gbk 读表算真值，E3 的得先把只读属性去掉 ——
这两条会让这个体检稍微复杂，但正是它该拦住的东西。

## 怎么验证这批题真有区分度

### 「成功」的标准

不是「题目变难了」，而是这四条：

| # | 标准 | 哪一段能判 |
|---|---|---|
| 1 | `error_recovery` 从恒 `null` 变成三档都有真实取值 | 第一段（k=1）|
| 2 | 至少一项**行为 / 效率**指标在不同 miniCC 版本间有差异 | **第二段**（要两个版本 + k≥2）|
| 3 | 失败标签在真实数据上经得起人工核对（假阳性率可接受） | 第一段 |
| 4 | 每道新题在未修复工作区上至少挂一条 `essential` | 出题时就有（`test_tasks.py` 的体检）|

第 2 条是最终目的，但**这一段判不了** —— 手上只有一个版本。第一段能判的是 1 / 3 / 4。

### 灵敏度体检（sensitivity check）

诚实的困难：**手上没有「两个已知有差异的 miniCC 版本」**，
而 S8 的教训正是「有 4 个版本照样全 100%」。所以做一个能做的代理检验：
**摘掉某个能力，看这道题的反应。**

机制全在 eval 侧 —— 全是模块级全局量的 patch，和 runner 自己 patch
`agent.agent.call_llm` 是同一个手法，**不改 miniCC，也不需要给 runner 加参数**。

**判据分两档，因为噪声问题绕不过去**（问题 2：k=1 的数字主要是随机波动）：

| 判据 | 形式 | k=1 能不能判 |
|---|---|---|
| **强判据** | 消融后 `task_success` 从真**翻成假** | 能 —— 这是**类别变化**，噪声淹没不了一个 bool |
| **弱判据** | 只有效率指标（tool_calls / latency / tokens）变化 | **不能**。「涨了 30%」在 `temperature=1` 下和抖动分不清，必须 k≥2 |

所以消融要**尽量设计成能翻 `task_success` 的**；只有弱判据的题就得在 k≥2 下测，
或者明确记成「未通过」—— **不许把噪声当信号**。

| 题 | 摘掉什么 | 期望 | 判据 |
|---|---|---|---|
| E3 | patch `tools.setup.write_file_tool` 成「对只读文件静默成功但不写入」 | `task_success` **必须翻假** —— 验收能抓住这个谎 | 强 |
| E1 | patch `tools.setup` 里的探索工具（`list_dir` / `glob` / `grep`）成永远返回空结果 | `task_success` **必须翻假** —— 找不回真文件就做不完。这直接证明 E1 测的是「探索能力」而不是「错误信息好不好看」 | 强 |
| S1 | patch `tools.local.usual.run_subagent.run_subagent` 成永远返回错误 | 效率上升 | 弱，需 k≥2 |

**指标不动 = 这道题对版本差异也不敏感，得重出。**

**局限要说清楚**：这是**灵敏度**检验，不等于真的版本对比 ——
真对比还得有两个版本。但它便宜、可复现，能在跑真版本之前就把「这题根本没关系」筛掉。

落成 `tests/test_ablation_sensitivity.py`，打 `@pytest.mark.integration`（要调 API）。
第一段（k=1）只跑强判据那两条；S1 的弱判据留到 k≥2 那一段。

### 分两段跑

因为问题 2 刚修完 —— k=1 时效率 delta 被主动跳过了，而版本差异主要就体现在效率上。
所以「比版本」和「k=1 不比效率」合起来意味着：**k=1 的报告永远回答不了「哪个版本好」**。

- **第一段 k=1**：只看行为指纹（首动作、用没用 `run_subagent`）和
  `error_recovery` 有没有取值。这一步不需要比版本。
- **第二段 k≥3**：做版本对比。12 道题 × k=3 ≈ 36 个 run，估计 70–100 分钟一轮。

## 已知风险

| 风险 | 说明 | 应对 |
|---|---|---|
| **E3 依赖文件系统元数据** | 只读属性是这批题里唯一不靠纯文本的陷阱。`shutil.copytree`（默认 `copy2` → `copystat`）会保留它，但任何搬运过程（打包、编辑器另存）都可能抹掉 | 加一条体检：`workspace/config.json` 必须真的是只读，否则测试报错。若反复出问题，备选是把陷阱换成「指令里给的代码片段与文件实际缩进不一致」，诱导 `edit_file` 撞「未找到要替换的内容」—— 纯文本，不依赖任何元数据 |
| ~~`bash` 退出码的语义~~ **已解决** | 猜的没错，而且比预想的严重：一轮 30 次失败调用里 **24 次是这一类**，主要是 Windows 上 `shell=True` 走 cmd.exe 而 agent 写 bash 语法 —— 和 agent 能力无关，且几乎必然「恢复」，`error_recovery` 因此恒等于 1.0 | **已排除**（`trajectory.is_command_failure`）。有取值的从 10/12 降到 4/12，且**全部由设计好的陷阱驱动** |
| **subagent 会让 token 计数偶尔少记** | 子 agent 跑在 `ThreadPoolExecutor` 里，而采集是 `stats.llm_calls += 1` 这种非原子写 | 影响很小，但取用时要知道这不是精确值 |
| **压缩的 token 现在没被采集** | `agent/context.py:2` 是 `from llm.call_llm import call_llm`，名字绑在 `agent.context` 上；runner 只 patch 了 `agent.agent.call_llm` | 压力：一旦压缩真的触发（比如以后换小窗口模型），效率指标会系统性偏低。修法是 eval 侧多 patch 一个名字，本轮不做（因为不测压缩） |

## 不做的事

- **压缩题**（窗口 100 万，阈值 80 万 token，碰不着）
- **skill / RAG / 联网搜索的题**（都读全局 `~/.miniCC/` 或依赖外网，不可复现）
- **故障注入能力**（本轮用环境自带的坏）
- **改 `bash` 退出码的 `error_recovery` 语义**（等真实数据）
- **`WRONG_ARGUMENT` 的探针题**（它只能靠模型自己出错触发，靠出题逼不出来，见上）
- **veto 的真实 run 验证**（安全网，靠单元测试验证就够，见上）

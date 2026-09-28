"""聚合 N 个 run → 文本报告 + 汇总数字。不依赖 miniCC。"""

import statistics
from collections import Counter

DIMENSIONS = [
    ("task_success", "Task Success"),
    ("correctness", "Correctness"),
    ("completeness", "Completeness"),
    ("tool_usage", "Tool Usage"),
    ("error_recovery", "Error Recovery"),
    ("groundedness", "Groundedness"),
]

FAILURE_TAGS = [
    "INCOMPLETE",
    "WRONG_TOOL",
    "WRONG_ARGUMENT",
    "INEFFICIENT",
    "NO_EXPLORATION",
    "CLAIMS_WITHOUT_ACTION",
]

# 这几条规则**从没在真实 run 上触发过**，只在单元测试里验过。按本项目的教训
# （假阳性主要靠真实数据才暴露，规则第一次生效时大概率是错的），必须在报告里
# 标明「别当结论用」—— 否则读者会把「0 次命中」读成「这条规则很准」。
# 出了定向触发的任务、拿到真实轨迹之后，把对应条目从这里删掉。
#
# veto **不在这里**：它不产生失败标签、不进失败分布（它是 status 的一种），是个安全网。
# 安全网要问的是「该拦的拦住了吗」，这用单元测试就能答；它万一假阳性，表现是某一轮
# 莫名 0 分，一眼可见，也不会污染失败分布。
UNVERIFIED_RULES = ["NO_EXPLORATION", "WRONG_ARGUMENT"]

RULE = "─" * 28

# 一条 run 记录至少要有这几个字段才当得起「记录」。`load_baseline` 拿它挡目录里
# 混进来的杂 json —— 缺了它们在 `summarize` 里会 KeyError，而报错完全不指向文件。
_RECORD_REQUIRED = ("task_id", "status", "evaluation")

# k=1 时效率数字只是**单次观测**，而 miniCC 的采样温度不为 0（`call_llm` 当前默认
# 0.1，由 miniCC 决定，eval 不设置）—— 同一道题跑两次可以差好几倍（temperature=1
# 那批实测差 3 倍工具调用、13 倍 token）。拿单次跑出来的数跟别的轮次比，比的是噪声
# 不是趋势。报告要给结论，就得先说清这一点；baseline diff 里的 delta 同理，k=1 时
# 直接跳过不输出。
_SINGLE_RUN_NOTE = "k=1 只有单次观测，数字含大随机波动，勿跨轮次比较"


def summarize(records) -> dict:
    """一次评估的汇总数字。error 的 run 不进任何统计。"""
    valid = _valid(records)
    succeeded = [r for r in valid if r["status"] == "success"]
    k = _uniform_k(records)

    return {
        "tasks": len({r["task_id"] for r in records}),
        "runs": len(records),
        "errors": len(records) - len(valid),
        "k": k,
        "model": _models(records),
        # 基线对比要核对的东西。放这里而不是 diff 里，是为了让「两边是不是同一套
        # 实验条件」成为一个能被测试断言的事实，而不是一段临时比较的代码。
        "identity": _identity(records),
        "dimensions": {name: _mean_dimension(valid, name) for name, _ in DIMENSIONS},
        # 条件效率：只在成功的 run 上统计，否则「失败得快」会被算成高效
        "efficiency": {
            "tool_calls": _mean_field(succeeded, "tool_calls"),
            "llm_calls": _mean_field(succeeded, "llm_calls"),
            "tokens": _mean_field(succeeded, "tokens"),
            "latency_ms": _mean_field(succeeded, "latency_ms"),
        },
        "first_action": _first_action_ratios(valid),
        "context": _context_load(valid),
        "noise": _noise(records),
        "judge": _judge_summary(records),
        "failures": {tag: _tag_ratio(valid, tag) for tag in FAILURE_TAGS},
        "pass_at_k": _pass_at_k(records),
        "pass_k": _pass_k(records),
        "per_task": _per_task(records),
    }


def render(records, baseline=None) -> str:
    summary = summarize(records)
    lines = [
        f"Agent Evaluation Report        tasks: {summary['tasks']}   "
        f"k: {summary['k'] or '?'}   model: {summary['model']}   "
        # 温度是实验条件，得跟 model 一样摆在头上 —— 藏进 run json 里就没人看了
        f"temp: {_join(summary['identity']['temperatures'])}",
        RULE,
    ]

    for name, label in DIMENSIONS:
        value = summary["dimensions"][name]
        lines.append(f"{label:<18}{_dimension_cell(name, value, summary, label)}")

    lines.append("")
    lines.append(_judge_text(summary))
    lines.append("")
    lines.append("Efficiency  (仅统计 success 的 run)")
    if summary["k"] == 1:
        lines.append(f"  {_SINGLE_RUN_NOTE}")
    efficiency = summary["efficiency"]
    lines.append(
        f"  Avg tool calls {_num(efficiency['tool_calls'])}   "
        f"Avg LLM calls {_num(efficiency['llm_calls'])}   "
        f"Avg tokens {_tokens(efficiency['tokens'])}   "
        f"Avg latency {_seconds(efficiency['latency_ms'])}"
    )
    lines.append("  First action     " + _first_action_text(summary["first_action"]))
    # 「这个数自己会晃多少」—— 没有它，上面的平均值和 diff 里的 delta 都会被读成结论
    lines.append(f"  {'Noise':<17}{_noise_text(summary['noise'], summary['k'])}")
    # 负载指标，不是评估信号 —— 摆在这里是为了让人一眼看出「离压缩线还有多远」，
    # 也就解释了「压缩这条线为什么没测」（deepseek 窗口 100 万，阈值 80 万 token）
    context = summary["context"]
    lines.append(
        f"  Context          max usage {_pct(context['max_usage_ratio'])}   "
        f"compactions {context['compactions']}"
    )

    lines.append("")
    lines.append("Failure Distribution  (按 run 归一化，一个 run 可命中多个标签)")
    ranked = sorted(
        ((tag, ratio) for tag, ratio in summary["failures"].items() if ratio > 0),
        key=lambda item: -item[1],
    )
    if ranked:
        lines.append("  " + "   ".join(f"{tag} {ratio:.2f}" for tag, ratio in ranked))
    else:
        lines.append("  （无）")

    lines.append(
        "  未验证（从未在真实 run 上触发过，假阳性率未知，别当结论用）  "
        + "   ".join(UNVERIFIED_RULES)
    )

    if summary["errors"]:
        lines.append("")
        lines.append(
            f"Errors  {summary['errors']} 个 run 是 runner 自身异常，不计入以上任何统计"
        )

    if baseline is not None:
        lines += _render_diff(summary, summarize(baseline), len(baseline))

    return "\n".join(lines)


def load_baseline(path) -> list[dict]:
    """读取基线目录下所有 run json。

    跳过 `<...>.calls.json` sidecar —— 它不是 run 记录，混进来会污染统计。

    目录里混进别的 json（随手放的笔记之类）时**报错并指名文件**。不静默跳过：
    少一条记录只会让 diff 悄悄偏掉，而读者无从察觉；报错至少能让人把目录收拾干净。
    """
    import json
    from pathlib import Path

    records = []
    for file in sorted(Path(path).glob("*.json")):
        if file.name.endswith(".calls.json"):
            continue
        record = json.loads(file.read_text(encoding="utf-8"))
        missing = (
            list(_RECORD_REQUIRED)
            if not isinstance(record, dict)
            else [name for name in _RECORD_REQUIRED if name not in record]
        )
        if missing:
            raise ValueError(
                f"{file} 不像一条 run 记录：缺 {', '.join(missing)}。"
                f"`--baseline` 只能指向 runs/<时间戳>/ 这种目录"
            )
        records.append(record)
    return records


# ---------- 汇总用的取值helper ----------


def _models(records) -> str:
    names = sorted({r["model"] for r in records if r.get("model")})
    return ", ".join(names) if names else "?"


def _group_by_task(records) -> dict:
    groups: dict = {}
    for record in records:
        groups.setdefault(record["task_id"], []).append(record)
    return groups


def _valid(records) -> list:
    """error 的 run 不进任何统计 —— 它是 eval 侧异常（agent 崩了，或验收程序
    自己抛了），代表的不是 agent 的表现。

    单独抽成一个函数是为了**只有一处定义**：之前 `task_success` 维度滤了、
    `pass@k` 忘了滤，于是同一份报告能同时印出「Task Success 100%」和
    「pass@3 50%」，后者只是因为某道题崩了几次、根本没法判断成没成。
    """
    return [r for r in records if r["status"] != "error"]


def _identity(records) -> dict:
    """一次评估的「身份」：跑了哪些题、用的哪个模型、哪个 provider 模型、哪个代码版本。

    全是集合 —— 一次评估里这些应当各自收敛成一个值；出现多个本身就是异常信号，
    对比时会体现在 `_baseline_conflicts` 的措辞里（用 `/` 并列）。
    """
    return {
        "task_ids": sorted({r["task_id"] for r in records}),
        "models": sorted({r["model"] for r in records if r.get("model")}),
        "models_actual": sorted(
            {r["model_actual"] for r in records if r.get("model_actual")}
        ),
        "agent_versions": sorted(
            {r["agent_version"] for r in records if r.get("agent_version")}
        ),
        "temperatures": sorted(
            {r["temperature"] for r in records if r.get("temperature") is not None}
        ),
    }


def _baseline_conflicts(current, base) -> tuple[list[str], list[str]]:
    """比对两边的实验条件，返回 `(致命, 警告)`。

    **只有任务集是致命的** —— 它不是变量，是秤本身：题目不一样时聚合值是两组不同题
    的平均，差几项都相减没有意义。

    其余条件差异**一律只警告**：照出数字，但把变化摆到读者面前。不拒绝的理由是
    「拒绝」拦不住真正的问题 —— 这个框架的瓶颈是**噪声**（逐题工具调用 CV 中位数
    29%），不是指标太多；而且能核的只有**进了记录**的字段，没进记录的（依赖 / shell /
    OS / miniCC 未提交的改动）本来就拦不住。列清楚比拦下来有用。

        - `model` / `temperature` 变了 → 告诉读者比的是什么
        - `agent_version` 相同 → 同一版本跟自己比，diff 里剩下的基本只有噪声
          （不同是常态，不提示）
        - `model_actual` 变了 → provider 可能在两轮之间静默升级了模型
          （`model` 也变了的话这是预期的，不再重复警告）
        - `k` 不同 → 观测次数不同，两侧精度不对等
        - 缺 `temperature` 字段 → 老记录，核不出来

    原则是「条件不一致就说清楚」—— 藏起来比说出来危险得多，读者会把「两组不同条件下的
    数字之差」当成结论。
    """
    fatal, warn = [], []
    now, old = current["identity"], base["identity"]

    if now["task_ids"] != old["task_ids"]:
        extra = len(set(now["task_ids"]) - set(old["task_ids"]))
        missing = len(set(old["task_ids"]) - set(now["task_ids"]))
        fatal.append(f"任务集不一致（这次多 {extra} 道、基线多 {missing} 道），聚合值不能相减")

    if now["models"] != old["models"]:
        warn.append(f"model 不一致（{_join(old['models'])} → {_join(now['models'])}）")
    if now["temperatures"] and old["temperatures"]:
        if now["temperatures"] != old["temperatures"]:
            warn.append(
                f"temperature 不一致（{_join(old['temperatures'])} → "
                f"{_join(now['temperatures'])}）"
            )
    elif now["temperatures"] != old["temperatures"]:
        warn.append(
            "无法确认两轮温度一致：有一边的记录里没有 temperature 字段"
            f"（{_join(old['temperatures'])} → {_join(now['temperatures'])}）"
        )
    # 只报「相同」这一种。正常版本对比里 agent_version 本来就该不同，再提示一遍纯属噪音；
    # 相同才需要说 —— 那是同版本跟自己比，diff 里剩下的基本只有噪声。
    if now["agent_versions"] and now["agent_versions"] == old["agent_versions"]:
        warn.append(
            f"两边 agent_version 相同（{_join(now['agent_versions'])}），"
            "这是同一版本跟自己比，diff 里剩下的基本只有噪声"
        )
    # model 换了的话 model_actual 跟着变是**预期的**，不再重复警告
    if now["models"] == old["models"] and now["models_actual"] != old["models_actual"]:
        warn.append(
            f"model_actual 变了（{_join(old['models_actual'])} → "
            f"{_join(now['models_actual'])}），provider 可能在两轮之间静默升级了模型"
        )
    if current["k"] != base["k"]:
        warn.append(
            f"k 不一致（{base['k'] or '?'} → {current['k'] or '?'}），两侧精度不对等"
        )

    return fatal, warn


def _join(values) -> str:
    return "/".join(str(value) for value in values) if values else "?"


def _uniform_k(records) -> int | None:
    sizes = {len(runs) for runs in _group_by_task(records).values()}
    return sizes.pop() if len(sizes) == 1 else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _mean_dimension(records, name):
    return _mean([r["evaluation"].get(name) for r in records])


def _mean_field(records, field):
    return _mean([r.get("trajectory", {}).get(field) for r in records])


def _context_load(records) -> dict:
    """上下文负载：离压缩线（0.8）多远、真压了几次。

    `max_usage_ratio` 取**峰值**不取均值 —— 要回答的是「这批题最接近压缩到什么程度」，
    平均值会被短 run 稀释掉。`compactions` 取总数。
    旧的 run json 里没有这两个字段，缺了就当「没压过」。
    """
    ratios = [r.get("max_usage_ratio") for r in records]
    ratios = [ratio for ratio in ratios if ratio is not None]
    return {
        "max_usage_ratio": max(ratios) if ratios else None,
        "compactions": sum(r.get("compactions") or 0 for r in records),
    }


def _judge_entries(records) -> list[dict]:
    """每条 run 的 primary judge 结果。没判过的记录直接跳过。"""
    entries = []
    for record in records:
        judge = record.get("judge") or {}
        entry = judge.get(judge.get("primary") or "")
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _judge_summary(records) -> dict:
    """judge 这一面的元信息：谁判的、哪版 prompt、判了几条、结论是什么。

    单独汇总而不是混进五维分：judge 是**第二评估面**，它的模型和 prompt 版本
    都得能跟着批次走 —— 不然两轮的语义分数没法比。
    """
    entries = _judge_entries(records)
    succeeded = [entry for entry in entries if entry.get("status") == "success"]
    return {
        "models": sorted({entry.get("model") for entry in entries if entry.get("model")}),
        "prompt_hashes": sorted(
            {entry.get("prompt_hash") for entry in entries if entry.get("prompt_hash")}
        ),
        "judged": len(entries),
        "failed": len(entries) - len(succeeded),
        "claims_consistent": _mean(
            [
                ((entry.get("results") or {}).get("claims_consistent") or {}).get("value")
                for entry in succeeded
            ]
        ),
    }


# 印在报告里的噪声指标。顺序就是显示顺序。
_NOISE_FIELDS = (("tool_calls", "tool calls"), ("tokens", "tokens"), ("latency_ms", "latency"))


def _noise(records) -> dict:
    """每个效率指标「什么都不改、这个数自己会晃多少」。

    算法：同题 k 次重复的**组内变异系数**（标准差 / 均值），跨题取中位数。
    这是判断 delta 有没有意义的唯一依据 —— delta 比它小，读出来的就是噪声。
    k<2 时算不出来，只有一次观测。

    只在 `success` 的 run 上算，跟效率均值一个口径：否则「失败得快」会被算成波动。
    """
    out = {}
    for field, _ in _NOISE_FIELDS:
        spreads = []
        for runs in _group_by_task(_valid(records)).values():
            values = [
                r.get("trajectory", {}).get(field)
                for r in runs
                if r["status"] == "success" and r.get("trajectory", {}).get(field)
            ]
            if len(values) < 2:
                continue
            mean = statistics.mean(values)
            if mean:
                spreads.append(statistics.pstdev(values) / mean)
        out[field] = statistics.median(spreads) if spreads else None
    return out


def _subagent_ratio(records) -> float | None:
    """多少比例的 run 用了 `run_subagent`。

    子 agent 是「独有能力」里唯一能做成可复现题的那个，也是版本差异最可能露出来的地方。
    """
    if not records:
        return None
    used = sum(1 for r in records if "run_subagent" in (r.get("tools_used") or []))
    return used / len(records)


def _first_action_ratios(records) -> dict:
    counted = Counter(r.get("first_action") for r in records if r.get("first_action"))
    total = sum(counted.values())
    if not total:
        return {}
    return {action: counted[action] / total for action in ("explore", "edit", "other") if counted[action]}


def _first_action_text(ratios: dict) -> str:
    if not ratios:
        return "n/a"
    return "   ".join(f"{action} {ratio:.0%}" for action, ratio in ratios.items())


def _task_first_action(records) -> str | None:
    """该任务最常出现的首动作。

    取舍：diff 里要一眼看出「这题的开局变了没有」，逐项列分布会把 Per task 段撑爆，
    所以这里只取众数、分布交给 Overall 的 First action 行。`sorted` 是为了让
    平票时的结果确定（取字典序第一个），否则同样的数据可能印出不同的行。
    """
    counted = Counter(r.get("first_action") for r in records if r.get("first_action"))
    if not counted:
        return None
    return max(sorted(counted), key=lambda action: counted[action])


def _tag_ratio(records, tag) -> float:
    if not records:
        return 0.0
    return sum(1 for r in records if tag in r["failures"]) / len(records)


def _pass_at_k(records) -> float | None:
    """k 次里至少一次成功。与 pass^k 独立保留，不做加权求和。

    先滤掉 error 的 run（见 `_valid`）—— 跟 `task_success` 维度一个口径。
    某道题整轮都崩的话它不进分母：连一次有效观测都没有，谈不上「成功没成功」。
    """
    groups = _group_by_task(_valid(records))
    if not groups:
        return None
    return sum(
        1 for runs in groups.values() if any(r["status"] == "success" for r in runs)
    ) / len(groups)


def _pass_k(records) -> float | None:
    """k 次全部成功。同样只算有效的 run。"""
    groups = _group_by_task(_valid(records))
    if not groups:
        return None
    return sum(
        1 for runs in groups.values() if all(r["status"] == "success" for r in runs)
    ) / len(groups)


def _per_task(records) -> dict:
    out = {}
    for task_id, runs in _group_by_task(records).items():
        valid = _valid(runs)
        succeeded = [r for r in valid if r["status"] == "success"]
        out[task_id] = {
            "success": (len(succeeded) / len(valid)) if valid else None,
            "first_action": _task_first_action(valid),
            "tool_calls": _mean_field(succeeded, "tool_calls"),
            "tokens": _mean_field(succeeded, "tokens"),
            "subagent": _subagent_ratio(valid),
        }
    return out


# ---------- 文本格式 ----------


def _noise_text(noise, k) -> str:
    """噪声那一行。k<2 时算不出来 —— 直说，别留个空白让人以为是 0。"""
    if all(value is None for value in noise.values()):
        return f"算不出来（k={k or '?'}，每个任务至少要跑 2 次）"
    body = "   ".join(
        f"{label} {_pct_signed(noise[field])}" for field, label in _NOISE_FIELDS
    )
    return f"{body}   （同题重复跑的组内散布，中位数）"


def _pct_signed(value) -> str:
    return "n/a" if value is None else f"±{value:.0%}"


def _judge_text(summary) -> str:
    """judge 那一段。没判过就把命令写出来 —— 别让读者把 n/a 读成 0 分。"""
    judge = summary["judge"]
    if not judge["judged"]:
        return (
            "Judge        （没判过。跑 python -m agenteval.judge <结果目录> "
            "--model <判官模型> 补上）"
        )

    # 分母是**有效 run**，不是全部 —— error 的 run 不判也不进统计
    valid_runs = summary["runs"] - summary["errors"]
    head = (
        f"Judge        model {_join(judge['models'])}   "
        f"prompt {_join(judge['prompt_hashes'])}   "
        f"judged {judge['judged']}/{valid_runs}"
    )
    if judge["failed"]:
        head += f"（{judge['failed']} 条判失败，未计入）"

    lines = [head]
    if judge["claims_consistent"] is not None:
        lines.append(f"  Claims consistent  {_pct(judge['claims_consistent'])}")
    return "\n".join(lines)


def _dimension_cell(name, value, summary, label) -> str:
    if name == "groundedness":
        # 没跑过 judge 时保持 n/a —— **不能把「没判」显示成 0%**
        return _pct(value) if value is not None else "n/a (needs judge)"
    if name == "task_success":
        cell = _pct(value)
        if summary["k"] and summary["k"] > 1:
            cell += (
                f"  (pass@{summary['k']} {_pct(summary['pass_at_k'])}"
                f"  pass^{summary['k']} {_pct(summary['pass_k'])})"
            )
        return cell
    return _pct(value)


def _render_diff(current, base, baseline_runs) -> list[str]:
    lines = ["", f"Baseline Diff  (baseline: {baseline_runs} runs)"]

    # 一致性检查先于一切数字。`--baseline` 不替调用者保证两边实验条件一致，
    # 所以这里必须自己核 —— 条件不一致时还把 diff 印出来，比不印危险得多：
    # 读者会把「两组不同题的平均值之差」当成版本差异。
    fatal, warn = _baseline_conflicts(current, base)
    for message in warn:
        lines.append(f"  注意：{message}")
    if fatal:
        lines.append("  拒绝出 diff —— 两边的实验条件不一致，数字相减没有意义：")
        lines.extend(f"    · {message}" for message in fatal)
        return lines

    lines.append("  Overall")
    # 任一侧只有单次观测时，所有数值对比都不可信（见 _SINGLE_RUN_NOTE）。
    # 这里必须跟 Overall 段一个口径 —— 否则那边说「跳过」，这边却把 13 倍的
    # token 噪声印出来，等于自己打自己脸。
    single = current["k"] == 1 or base["k"] == 1

    lines.append(
        f"    Task Success     {_pct(base['dimensions']['task_success'])} → "
        f"{_pct(current['dimensions']['task_success'])}"
        f"{_delta_pct(base['dimensions']['task_success'], current['dimensions']['task_success'])}"
    )
    # 首动作是**类别**不是数字，所以要照 success 的待遇：k=1 也照比。
    # 正确性维度一旦饱和（题集天花板），行为维度的位移就是报告里唯一还能报出
    # 版本差异的地方 —— 藏起来等于把「比较两个版本」这个目的架空。
    lines.append(
        f"    First action     {_first_action_text(base['first_action'])} → "
        f"{_first_action_text(current['first_action'])}"
    )
    if single:
        lines.append(f"    Efficiency       跳过：{_SINGLE_RUN_NOTE}")
    else:
        lines.append(
            f"    Avg tool calls   {_num(base['efficiency']['tool_calls'])} → "
            f"{_num(current['efficiency']['tool_calls'])}"
            f"{_delta_num(base['efficiency']['tool_calls'], current['efficiency']['tool_calls'])}"
        )
        lines.append(
            f"    Avg tokens       {_tokens(base['efficiency']['tokens'])} → "
            f"{_tokens(current['efficiency']['tokens'])}"
        )

    lines += _render_judge_diff(current, base)

    lines.append("  Per task")
    if single:
        lines.append(f"    （{_SINGLE_RUN_NOTE} —— 只报成功率的翻转）")
    for task_id in sorted(set(base["per_task"]) | set(current["per_task"])):
        line = _task_diff_line(
            task_id,
            base["per_task"].get(task_id, {}),
            current["per_task"].get(task_id, {}),
            numeric=not single,
        )
        if line:
            lines.append(line)

    return lines


def _render_judge_diff(current, base) -> list[str]:
    """语义面的对比。

    判官换了（prompt 版本或模型），分数就不是同一把尺子量出来的 —— 这时候**不出数字**，
    只说明为什么。跟确定性那边一个原则：条件不一致就不相减。
    """
    now, old = current["judge"], base["judge"]
    if not now["judged"] and not old["judged"]:
        return []
    if not now["judged"] or not old["judged"]:
        return ["  Semantic (judge)", "    跳过：有一边没判过，语义分没法比"]
    if now["prompt_hashes"] != old["prompt_hashes"]:
        return [
            "  Semantic (judge)",
            f"    跳过：两边 prompt 版本不同（{_join(old['prompt_hashes'])} → "
            f"{_join(now['prompt_hashes'])}）",
        ]
    if now["models"] != old["models"]:
        return [
            "  Semantic (judge)",
            f"    跳过：两边判官模型不同（{_join(old['models'])} → {_join(now['models'])}）",
        ]

    return [
        "  Semantic (judge)",
        f"    Groundedness      {_pct(base['dimensions']['groundedness'])} → "
        f"{_pct(current['dimensions']['groundedness'])}"
        f"{_delta_pct(base['dimensions']['groundedness'], current['dimensions']['groundedness'])}",
        f"    Claims consistent  {_pct(base['judge']['claims_consistent'])} → "
        f"{_pct(current['judge']['claims_consistent'])}"
        f"{_delta_pct(base['judge']['claims_consistent'], current['judge']['claims_consistent'])}",
    ]


def _task_diff_line(task_id, before, after, numeric=True) -> str | None:
    """逐任务对比，没变化返回 None。

    **除成功率外还比效率和子 agent 使用率。** 题集饱和时成功率永远是 100% → 100%，
    只比它就等于什么都不报 —— 而「哪些任务翻盘」正是「比较版本」要用的地方，
    只比成功率会让那段一直是空的。

    `numeric=False`（任一侧 k=1）：效率和子 agent 使用率都不比 —— 单次观测里
    它们主要是随机波动，印出来就是拿噪声当结论。
    """
    changes = []
    if before.get("success") != after.get("success"):
        changes.append(
            f"success {_pct(before.get('success'))} → {_pct(after.get('success'))}"
        )
    # 首动作是类别，不受 `numeric` 管 —— k=1 下它照样可信（同 success）
    if before.get("first_action") != after.get("first_action"):
        changes.append(
            f"first_action {before.get('first_action') or 'n/a'} → "
            f"{after.get('first_action') or 'n/a'}"
        )
    if numeric:
        for key, label, formatter in (
            ("tool_calls", "tool calls", _num),
            ("tokens", "tokens", _tokens),
            ("subagent", "subagent", _pct),
        ):
            old, new = before.get(key), after.get(key)
            if old != new:
                changes.append(f"{label} {formatter(old)} → {formatter(new)}")

    if not changes:
        return None
    return f"    {task_id:<24}" + "   ".join(changes)


def _delta_pct(before, after) -> str:
    if before is None or after is None:
        return ""
    return f"   ({(after - before):+.0%})"


def _delta_num(before, after) -> str:
    if before is None or after is None:
        return ""
    return f"   ({(after - before):+.1f})"


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _tokens(value) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f}k"


def _seconds(value) -> str:
    return "n/a" if value is None else f"{value / 1000:.1f}s"

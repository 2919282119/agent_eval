"""聚合 N 个 run → 文本报告 + 汇总数字。不依赖 miniCC。"""

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

# k=1 时效率数字只是**单次观测**，而 miniCC 的 call_llm 硬编码了 temperature=1，
# 方差拉满 —— 同一道题跑两次可以差 3 倍工具调用、13 倍 token。拿单次跑出来的数
# 跟别的轮次比，比的是噪声不是趋势。报告要给结论，就得先说清这一点；
# baseline diff 里的 delta 同理，k=1 时直接跳过不输出。
_SINGLE_RUN_NOTE = "k=1 只有单次观测，数字含大随机波动，勿跨轮次比较"


def summarize(records) -> dict:
    """一次评估的汇总数字。error 的 run 不进任何统计。"""
    valid = [r for r in records if r["status"] != "error"]
    succeeded = [r for r in valid if r["status"] == "success"]
    k = _uniform_k(records)

    return {
        "tasks": len({r["task_id"] for r in records}),
        "runs": len(records),
        "errors": len(records) - len(valid),
        "k": k,
        "model": _models(records),
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
        "failures": {tag: _tag_ratio(valid, tag) for tag in FAILURE_TAGS},
        "pass_at_k": _pass_at_k(records),
        "pass_k": _pass_k(records),
        "per_task": _per_task(records),
    }


def render(records, baseline=None) -> str:
    summary = summarize(records)
    lines = [
        f"Agent Evaluation Report        tasks: {summary['tasks']}   "
        f"k: {summary['k'] or '?'}   model: {summary['model']}",
        RULE,
    ]

    for name, label in DIMENSIONS:
        value = summary["dimensions"][name]
        lines.append(f"{label:<18}{_dimension_cell(name, value, summary, label)}")

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
    """
    import json
    from pathlib import Path

    records = []
    for file in sorted(Path(path).glob("*.json")):
        if file.name.endswith(".calls.json"):
            continue
        records.append(json.loads(file.read_text(encoding="utf-8")))
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
    """k 次里至少一次成功。与 pass^k 独立保留，不做加权求和。"""
    groups = _group_by_task(records)
    if not groups:
        return None
    return sum(
        1 for runs in groups.values() if any(r["status"] == "success" for r in runs)
    ) / len(groups)


def _pass_k(records) -> float | None:
    """k 次全部成功。"""
    groups = _group_by_task(records)
    if not groups:
        return None
    return sum(
        1 for runs in groups.values() if all(r["status"] == "success" for r in runs)
    ) / len(groups)


def _per_task(records) -> dict:
    out = {}
    for task_id, runs in _group_by_task(records).items():
        valid = [r for r in runs if r["status"] != "error"]
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


def _dimension_cell(name, value, summary, label) -> str:
    if name == "groundedness":
        return "n/a (needs judge)"
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
    lines = ["", f"Baseline Diff  (baseline: {baseline_runs} runs)", "  Overall"]
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

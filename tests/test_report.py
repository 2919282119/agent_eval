import json

import pytest

from agenteval.report import load_baseline, render, summarize

EMPTY_EVALUATION = {
    "task_success": None,
    "correctness": None,
    "completeness": None,
    "tool_usage": None,
    "groundedness": None,
    "error_recovery": None,
}


def make_record(
    task_id="t1",
    run_id="run_001",
    status="success",
    success=True,
    first_action="explore",
    tool_calls=8,
    llm_calls=4,
    tokens=7800,
    latency_ms=14200,
    failures=(),
    model="kimi",
    agent_version="abc1234",
    model_actual="kimi-k2.6",
    tools_used=("read_file", "edit_file"),
    max_usage_ratio=0.02,
    compactions=0,
    temperature=0.1,
    groundedness=None,
    judge=None,
):
    record = {
        "task_id": task_id,
        "run_id": run_id,
        "agent_version": agent_version,
        "model": model,
        "model_actual": model_actual,
        "first_action": first_action,
        "tools_used": list(tools_used),
        "max_usage_ratio": max_usage_ratio,
        "compactions": compactions,
        "status": status,
        "trajectory": {
            "steps": 8,
            "tool_calls": tool_calls,
            "llm_calls": llm_calls,
            "tokens": tokens,
            "latency_ms": latency_ms,
        },
        "evaluation": {
            "task_success": success,
            "correctness": 1.0 if success else 0.5,
            "completeness": 1.0 if success else 0.4,
            "tool_usage": 1.0,
            "groundedness": groundedness,
            "error_recovery": None,
        },
        "failures": list(failures),
    }
    # None = 老记录里压根没有这个字段（temperature 是后加的）
    if temperature is not None:
        record["temperature"] = temperature
    if judge is not None:
        record["judge"] = judge
    return record


def make_judge(value=0.9, claims=1.0, model="kimi", prompt="a1b2c3d4", status="success"):
    """一条 run 的 `judge` 顶层字段（形状跟 agenteval.judge.write_back 一致）。"""
    return {
        "primary": "llm",
        "llm": {
            "model": model,
            "prompt_hash": prompt,
            "status": status,
            "latency_ms": 420,
            "usage": {"input_tokens": 4800, "output_tokens": 30},
            "results": (
                {
                    "groundedness": {"value": value, "detail": "有据"},
                    "claims_consistent": {"value": claims, "detail": "都做了"},
                }
                if status == "success"
                else None
            ),
        },
    }


def make_error_record(task_id="t1", run_id="run_002"):
    record = make_record(task_id=task_id, run_id=run_id, status="error")
    record["evaluation"] = dict(EMPTY_EVALUATION)
    return record


def test_counts_tasks_runs_and_models():
    records = [
        make_record(task_id="a"),
        make_record(task_id="a"),
        make_record(task_id="b"),
    ]

    summary = summarize(records)

    assert summary["tasks"] == 2
    assert summary["runs"] == 3
    assert summary["model"] == "kimi"


def test_error_runs_are_excluded_from_every_statistic():
    records = [
        make_record(task_id="a", success=True),
        make_record(task_id="a", success=False, failures=["INCOMPLETE"]),
        make_error_record(task_id="a"),
    ]

    summary = summarize(records)

    assert summary["errors"] == 1
    # 3 个 run 里只有 2 个有效，其中 1 个成功 → 50%，不是 1/3
    assert summary["dimensions"]["task_success"] == 0.5
    assert summary["failures"]["INCOMPLETE"] == 0.5


def test_error_runs_do_not_drag_down_pass_at_k():
    """整轮都崩的题不该进 pass@k 的分母。

    它连一次有效观测都没有，谈不上「成功没成功」。不滤的话同一份报告会同时印
    「Task Success 100%」和「pass@3 50%」—— 两个都自称成功率却对不上。
    """
    records = [make_error_record(task_id="broken", run_id=f"r{i}") for i in range(3)]
    records += [make_record(task_id="fine", run_id=f"r{i}") for i in range(3)]

    summary = summarize(records)

    assert summary["dimensions"]["task_success"] == 1.0
    assert summary["pass_at_k"] == 1.0
    assert summary["pass_k"] == 1.0


def test_efficiency_only_counts_successful_runs():
    """失败的快不算高效 —— 这是条件效率的全部意义。"""
    records = [
        make_record(status="success", tool_calls=10, tokens=9000),
        make_record(status="failed", success=False, tool_calls=2, tokens=1000),
    ]

    efficiency = summarize(records)["efficiency"]

    assert efficiency["tool_calls"] == 10
    assert efficiency["tokens"] == 9000


def test_efficiency_is_na_when_nothing_succeeded():
    records = [make_record(status="failed", success=False)]

    efficiency = summarize(records)["efficiency"]

    assert efficiency["tool_calls"] is None
    assert efficiency["tokens"] is None


def test_failure_distribution_is_normalized_by_run_count():
    """「INCOMPLETE 0.5」= 一半的 run 命中，不是「0.5 个失败」。"""
    records = [
        make_record(failures=["INCOMPLETE", "WRONG_TOOL"]),
        make_record(failures=["INCOMPLETE"]),
        make_record(),
        make_record(),
    ]

    failures = summarize(records)["failures"]

    assert failures["INCOMPLETE"] == 0.5
    assert failures["WRONG_TOOL"] == 0.25
    assert failures["NO_EXPLORATION"] == 0.0


def test_inefficient_is_a_property_of_the_task_not_a_single_run():
    """上限卡在分布尾部时，「多一次就翻」的那次不该算这道题的性质。

    实测 `trace_units_006` 上限 20，同题三次跑出 11 / 21 / 32 —— 按单次 run 判，
    这 11% 就会被读成「有一成 run 效率不行」，而它跟噪声是同一件事。
    """
    records = [
        make_record(run_id="run_001", failures=["INEFFICIENT"]),
        make_record(run_id="run_002"),
        make_record(run_id="run_003", failures=["INEFFICIENT"]),
    ]

    summary = summarize(records)

    assert summary["failures"]["INEFFICIENT"] == 0.0
    assert summary["long_tail"] == ["t1/run_001", "t1/run_003"]


def test_inefficient_counts_when_every_run_of_the_task_is_over():
    """整道题每次都超限是**性质**，不是长尾。"""
    records = [
        make_record(run_id="run_001", failures=["INEFFICIENT"]),
        make_record(run_id="run_002", failures=["INEFFICIENT"]),
    ]

    summary = summarize(records)

    assert summary["failures"]["INEFFICIENT"] == 1.0
    assert summary["long_tail"] == []


def test_inefficient_stays_per_run_when_k_is_one():
    """k=1 无从判断是不是长尾，照旧逐 run 判 —— 报告已经声明那是单次观测。"""
    summary = summarize([make_record(failures=["INEFFICIENT"])])

    assert summary["failures"]["INEFFICIENT"] == 1.0
    assert summary["long_tail"] == []


def test_long_tail_runs_are_named_in_the_report():
    records = [
        make_record(run_id="run_001", failures=["INEFFICIENT"]),
        make_record(run_id="run_002"),
    ]

    text = render(records)

    assert "长尾" in text
    assert "t1/run_001" in text


def test_first_action_distribution():
    records = [
        make_record(first_action="explore"),
        make_record(first_action="explore"),
        make_record(first_action="edit"),
    ]

    ratios = summarize(records)["first_action"]

    assert ratios["explore"] == 2 / 3
    assert ratios["edit"] == 1 / 3


def test_pass_at_k_and_pass_k_are_kept_separate():
    """pass@k 和 pass^k 独立保留，不做加权求和。"""
    records = [
        # 任务 a：3 次里只成功 1 次 → pass@3 视为通过，pass^3 不通过
        make_record(task_id="a", run_id="r1", status="success"),
        make_record(task_id="a", run_id="r2", status="failed", success=False),
        make_record(task_id="a", run_id="r3", status="failed", success=False),
        # 任务 b：3 次全成功
        make_record(task_id="b", run_id="r1"),
        make_record(task_id="b", run_id="r2"),
        make_record(task_id="b", run_id="r3"),
    ]

    summary = summarize(records)

    assert summary["k"] == 3
    assert summary["pass_at_k"] == 1.0
    assert summary["pass_k"] == 0.5  # 6 个 run 里 4 个成功，但只有 b 是 3/3


def test_k_is_none_when_task_run_counts_differ():
    records = [
        make_record(task_id="a", run_id="r1"),
        make_record(task_id="a", run_id="r2"),
        make_record(task_id="b", run_id="r1"),
    ]

    assert summarize(records)["k"] is None


# ---------- render ----------


def test_render_hides_pass_at_k_when_k_is_1():
    records = [make_record(task_id="a"), make_record(task_id="b")]

    text = render(records)

    assert "pass@1" not in text
    assert "Task Success" in text


def test_render_shows_pass_at_k_when_k_is_3():
    records = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]

    text = render(records)

    assert "pass@3 100%" in text
    assert "pass^3 100%" in text


def test_render_marks_groundedness_as_not_available():
    assert "n/a (needs judge)" in render([make_record()])


# ---------- judge 那一面 ----------


def _groundedness_line(text):
    return next(line for line in text.splitlines() if line.startswith("Groundedness"))


def test_render_shows_groundedness_when_the_judge_ran():
    """判过之后那个维度就有数了 —— 不再显示 n/a。"""
    text = render([make_record(groundedness=0.9, judge=make_judge())])

    assert "n/a (needs judge)" not in text
    assert _groundedness_line(text).endswith("90%")


def test_render_says_how_to_run_the_judge_when_it_never_ran():
    """没判过要给命令 —— 否则读者只看到一个 n/a，不知道下一步该干嘛。"""
    text = render([make_record()])

    assert "没判过" in text
    assert "python -m agenteval.judge" in text


def test_render_shows_the_judge_model_and_prompt_version():
    """judge 的模型和 prompt 版本必须跟着走 —— 不然两轮的语义分数没法比。"""
    text = render([make_record(groundedness=0.9, judge=make_judge())])

    assert "model kimi" in text
    assert "prompt a1b2c3d4" in text


def test_render_shows_claims_consistent():
    text = render([make_record(groundedness=0.9, judge=make_judge(claims=0.5))])

    assert "Claims consistent  50%" in text


def test_render_does_not_treat_an_unjudged_run_as_zero():
    """一批里只判了一部分：均分只算判过的，覆盖率如实写。

    把「没判」当 0 分会把一整批干净的 run 拉成低分 —— 这是最要防的那种错。
    """
    records = [
        make_record(task_id="a", groundedness=1.0, judge=make_judge(value=1.0)),
        make_record(task_id="b", run_id="run_002"),  # 这条没判
    ]

    summary = summarize(records)
    text = render(records)

    assert summary["judge"]["judged"] == 1
    assert summary["dimensions"]["groundedness"] == 1.0  # 只算判过的那条，不是 0.5
    assert "judged 1/2" in text


def test_render_counts_failed_judgements_separately():
    """判失败的不进均分，但要在报告里**点名** —— 静默少一条和「没判」一样坏。

    之前 `judged` 数的是「有 judge 字段的记录数」，于是报告印「judged 2/2
    （1 条判失败，未计入）」—— 自相矛盾，而且读者不知道掉的是哪条。
    """
    records = [
        make_record(task_id="a", groundedness=0.8, judge=make_judge(value=0.8)),
        make_record(task_id="b", run_id="run_002", judge=make_judge(status="error")),
    ]

    summary = summarize(records)
    text = render(records)

    assert summary["judge"]["judged"] == 1
    assert summary["judge"]["failed_runs"] == ["b/run_002"]
    assert summary["dimensions"]["groundedness"] == 0.8
    assert "judged 1/2" in text
    assert "1 条判失败：b/run_002" in text


def test_skipped_judgements_are_named_and_excluded():
    """最终回答为空 → 判官跳过、不给分。编一个 0.5 混进平均值是更坏的选择。"""
    records = [
        make_record(task_id="a", groundedness=0.9, judge=make_judge(value=0.9)),
        make_record(task_id="b", run_id="run_002", judge=make_judge(status="skipped")),
    ]

    summary = summarize(records)
    text = render(records)

    assert summary["judge"]["judged"] == 1
    assert summary["judge"]["skipped"] == ["b/run_002"]
    assert summary["dimensions"]["groundedness"] == 0.9
    assert "1 条跳过（最终回答为空）" in text


def test_render_notes_that_efficiency_is_conditional():
    assert "仅统计 success 的 run" in render([make_record()])


def test_render_reports_error_runs_separately():
    text = render([make_record(), make_error_record()])

    assert "runner 自身异常" in text


def test_render_says_no_failures_when_clean():
    assert "（无）" in render([make_record()])


def test_render_warns_that_k1_efficiency_is_a_single_observation():
    """k=1 的效率数字是单次观测，不能被当成「这个版本的平均水平」来读。"""
    text = render([make_record(task_id="a"), make_record(task_id="b")])

    assert "单次观测" in text


def test_render_does_not_warn_when_k_is_3():
    records = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]

    assert "单次观测" not in render(records)


def test_render_reports_context_load():
    """负载指标：离压缩线多远、真压了几次。均值的意义不大，取的是峰值和总数。"""
    records = [
        make_record(task_id="a", run_id="r1", max_usage_ratio=0.02),
        make_record(task_id="b", run_id="r2", max_usage_ratio=0.31, compactions=1),
        make_record(task_id="c", run_id="r3", max_usage_ratio=0.05, compactions=2),
    ]

    text = render(records)

    assert "max usage 31%" in text  # 峰值，不是均值
    assert "compactions 3" in text  # 总数


def test_render_context_load_tolerates_records_from_before_the_fields_existed():
    """老的 run json 里没有这两个字段 —— 报告不能因此崩掉。"""
    old = make_record()
    del old["max_usage_ratio"]
    del old["compactions"]

    assert "max usage n/a" in render([old])
    assert "compactions 0" in render([old])


def test_render_reports_the_noise_floor_when_k_is_at_least_2():
    """「这个数自己会晃多少」—— 没有它，平均值和 diff 里的 delta 都会被读成结论。"""
    records = [
        make_record(task_id="a", run_id=f"r{i}", tool_calls=value)
        for i, value in enumerate((8, 10, 12))
    ]

    text = render(records)

    assert "tool calls ±" in text
    assert "同题重复跑的组内散布" in text


def test_render_says_the_noise_cannot_be_computed_at_k1():
    """k=1 时算不出来 —— 直说，别留个空白让人以为是 0。"""
    text = render([make_record(task_id="a"), make_record(task_id="b", run_id="r2")])

    assert "算不出来" in text


def test_noise_uses_the_within_task_spread_not_the_between_task_one():
    """噪声必须是**同题**重复跑的散布。

    拿跨题的差别当噪声就废了 —— 「这题本来就难」会被算成「数字会晃」。这里两道题各自
    都很稳（组内散布 0）、均值却差 10 倍，正确答案是 0。
    """
    records = [
        make_record(task_id="a", run_id=f"r{i}", tool_calls=10) for i in range(3)
    ] + [make_record(task_id="b", run_id=f"r{i}", tool_calls=100) for i in range(3)]

    assert summarize(records)["noise"]["tool_calls"] == 0.0


def test_diff_reports_efficiency_change_when_success_is_unchanged():
    """题集饱和时成功率永远一样，只比成功率就等于什么都不报。"""
    current = [make_record(task_id="a", run_id=f"r{i}", tool_calls=20) for i in range(3)]
    baseline = [make_record(task_id="a", run_id=f"r{i}", tool_calls=8) for i in range(3)]
    per_task = render(current, baseline=baseline).split("Per task")[1]

    assert "tool calls 8.0 → 20.0" in per_task


def test_diff_reports_subagent_usage_change():
    current = [make_record(task_id="a", run_id=f"r{i}", tools_used=("run_subagent",)) for i in range(3)]
    baseline = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]
    per_task = render(current, baseline=baseline).split("Per task")[1]

    assert "subagent 0% → 100%" in per_task


def test_diff_reports_first_action_change():
    """正确性饱和之后，行为维度的位移是报告里唯一还能报出差异的地方。

    `first_action` 在报告正文里一直有，却漏进了 diff —— 而「首动作从探索变成
    直接改」正是「上下文 / 提示词变了」最可能的表征。
    """
    current = [make_record(task_id="a", run_id=f"r{i}", first_action="edit") for i in range(3)]
    baseline = [make_record(task_id="a", run_id=f"r{i}", first_action="explore") for i in range(3)]

    text = render(current, baseline=baseline)

    assert "First action     explore 100% → edit 100%" in text
    assert "first_action explore → edit" in text


def test_diff_reports_per_task_first_action_even_when_k_is_1():
    """首动作是**类别**不是数字：k=1 的效率数字要跳过，它不用。"""
    current = [make_record(task_id="a", first_action="edit")]
    baseline = [make_record(task_id="a", first_action="explore")]
    per_task = render(current, baseline=baseline).split("Per task")[1]

    assert "first_action explore → edit" in per_task


def test_task_first_action_takes_the_mode_when_runs_disagree():
    records = [
        make_record(task_id="a", run_id="r1", first_action="explore"),
        make_record(task_id="a", run_id="r2", first_action="explore"),
        make_record(task_id="a", run_id="r3", first_action="edit"),
    ]

    assert summarize(records)["per_task"]["a"]["first_action"] == "explore"


def test_diff_suppresses_per_task_numbers_when_k_is_1():
    """Overall 段跳过效率数字，Per task 段就必须跟着跳过 —— 一个口径。

    否则报告一边说「k=1 的数字含大随机波动，勿比较」，一边把 13 倍的 token 噪声
    印在逐任务行里，等于自己打自己脸。
    """
    current = [make_record(task_id="a", tool_calls=20)]
    baseline = [make_record(task_id="a", tool_calls=8)]
    per_task = render(current, baseline=baseline).split("Per task")[1]

    assert "tool calls" not in per_task
    assert "单次观测" in per_task


def test_diff_stays_silent_for_a_task_that_did_not_change():
    current = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]
    baseline = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]

    assert render(current, baseline=baseline).split("Per task")[1].strip() == ""


def test_render_flags_rules_that_never_fired_on_real_runs():
    """没见过真实数据的规则不能当结论用 —— 报告得自己说清，别让读者把
    「0 次命中」读成「这条规则很准」。"""
    text = render([make_record()])

    assert "未验证" in text
    assert "NO_EXPLORATION" in text
    assert "WRONG_ARGUMENT" in text


def test_veto_is_not_listed_as_unverified():
    """veto 是安全网、不进失败分布，不该跟那两条信号规则混在一起。

    它要问的是「该拦的拦住了吗」，单元测试就能答；假阳性也只是某一轮莫名 0 分，
    一眼可见。见 report.UNVERIFIED_RULES 的注释。
    """
    unverified = render([make_record()]).split("未验证")[1]

    assert "veto" not in unverified


# ---------- 基线对比 ----------


def test_diff_highlights_task_that_flipped():
    baseline = [
        make_record(task_id="a", status="failed", success=False),
        make_record(task_id="b"),
    ]
    current = [
        make_record(task_id="a"),
        make_record(task_id="b"),
    ]

    text = render(current, baseline=baseline)

    assert "Baseline Diff" in text
    assert "a" in text
    # a 从 0% 翻到 100%
    assert "0% → 100%" in text


def test_diff_omits_tasks_that_did_not_change():
    baseline = [make_record(task_id="stable"), make_record(task_id="moved", status="failed", success=False)]
    current = [make_record(task_id="stable"), make_record(task_id="moved")]

    text = render(current, baseline=baseline)
    per_task_section = text.split("Per task")[1]

    assert "moved" in per_task_section
    assert "stable" not in per_task_section


def test_diff_skips_efficiency_delta_when_k_is_1():
    """k=1 的效率 delta 是两次噪声相减 —— 输出它等于把随机波动说成趋势。"""
    text = render([make_record(task_id="a")], baseline=[make_record(task_id="a", tool_calls=20)])
    diff_section = text.split("Baseline Diff")[1]

    assert "Avg tool calls" not in diff_section
    assert "跳过" in diff_section


def test_diff_shows_efficiency_delta_when_k_is_3():
    current = [make_record(task_id="a", run_id=f"r{i}", tool_calls=8) for i in range(3)]
    baseline = [make_record(task_id="a", run_id=f"r{i}", tool_calls=4) for i in range(3)]
    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "Avg tool calls   4.0 → 8.0" in diff_section


def test_diff_is_quiet_for_a_proper_version_comparison():
    """agent_version 不同、其余条件一致 —— 正常的版本对比：不拒绝，也不出任何提示。

    版本不同是这件事的**常态**，提示它只会给每份 diff 添一行噪音；反倒是「版本相同」
    值得说一句（见 test_diff_warns_when_agent_version_is_identical）。
    """
    current = [
        make_record(task_id="a", run_id=f"r{i}", agent_version="new", tool_calls=8)
        for i in range(3)
    ]
    baseline = [
        make_record(task_id="a", run_id=f"r{i}", agent_version="old", tool_calls=4)
        for i in range(3)
    ]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" not in diff_section
    assert "注意" not in diff_section
    assert "Avg tool calls   4.0 → 8.0" in diff_section


# ---------- baseline 一致性检查 ----------


def test_diff_refuses_when_task_sets_differ():
    """两边题不一样时，聚合值是两组不同题的平均 —— 相减没有意义，必须拒绝。"""
    current = [make_record(task_id="a"), make_record(task_id="b")]
    baseline = [make_record(task_id="a"), make_record(task_id="c")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" in diff_section
    assert "任务集不一致" in diff_section
    assert "Task Success" not in diff_section


def test_diff_warns_but_does_not_refuse_when_model_changes():
    """换模型不再拒绝，只出一行提示 —— 拒绝拦不住真正的问题（噪声）。"""
    current = [make_record(task_id="a", model="deepseek", model_actual="deepseek-flash")]
    baseline = [make_record(task_id="a", model="kimi", model_actual="kimi-k2.6")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" not in diff_section
    assert "model 不一致（kimi → deepseek）" in diff_section
    assert "Task Success" in diff_section
    # model 换了，model_actual 跟着变是**预期的** —— 不该再警告「provider 静默升级」
    assert "model_actual 变了" not in diff_section


def test_diff_warns_when_agent_version_is_identical():
    """同一版本跟自己比：diff 里剩下的基本只有噪声。警告，但不拒绝。"""
    current = [make_record(task_id="a", agent_version="same")]
    baseline = [make_record(task_id="a", agent_version="same")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "agent_version 相同" in diff_section
    assert "Task Success" in diff_section


def test_diff_warns_when_model_actual_changed():
    """请求的模型名没变、provider 实际给的不一样 → 可能被静默升级了。"""
    current = [make_record(task_id="a", model_actual="kimi-k2.6")]
    baseline = [make_record(task_id="a", model_actual="kimi-k2.5")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "model_actual 变了" in diff_section
    assert "Task Success" in diff_section


def test_diff_warns_when_k_differs():
    current = [make_record(task_id="a", run_id=f"r{i}") for i in range(3)]
    baseline = [make_record(task_id="a")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "k 不一致" in diff_section


def test_diff_warns_but_does_not_refuse_when_temperature_changes():
    current = [make_record(task_id="a", temperature=0.1)]
    baseline = [make_record(task_id="a", temperature=1)]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" not in diff_section
    assert "temperature 不一致（1 → 0.1）" in diff_section
    assert "Task Success" in diff_section


def test_diff_warns_about_every_changed_condition_at_once():
    """同时变好几项也不再拒绝 —— 逐项列清楚，判断交给读者。"""
    current = [make_record(task_id="a", temperature=0.1, model_actual="new-flash")]
    baseline = [make_record(task_id="a", temperature=1, model_actual="old-flash")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" not in diff_section
    assert "temperature 不一致（1 → 0.1）" in diff_section
    assert "model_actual 变了" in diff_section
    assert "Task Success" in diff_section


def test_diff_warns_when_one_side_has_no_temperature():
    """老记录里没有 temperature 字段 —— 核不出来，警告但不拒绝（跟 Python 版本同理）。"""
    current = [make_record(task_id="a", temperature=0.1)]
    baseline = [make_record(task_id="a", temperature=None)]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "无法确认两轮温度一致" in diff_section
    assert "拒绝出 diff" not in diff_section
    assert "Task Success" in diff_section


def test_diff_stays_quiet_when_temperatures_match():
    current = [make_record(task_id="a", temperature=0.1)]
    baseline = [make_record(task_id="a", temperature=0.1)]

    assert "温度" not in render(current, baseline=baseline)


def test_render_shows_the_temperature_in_the_header():
    """温度是实验条件，得跟 model 一样摆在报告头上 —— 只写进 run json 就没人看了。"""
    assert "temp: 0.1" in render([make_record()])


def test_render_marks_an_unknown_temperature():
    """老记录没有这个字段时要显式标成未知，不能悄悄印个空。"""
    assert "temp: ?" in render([make_record(temperature=None)])


# ---------- 语义面的 diff ----------


def _semantic_section(text):
    return text.split("Semantic (judge)")[1].split("Per task")[0]


def test_diff_shows_semantic_numbers_when_the_judge_is_the_same():
    current = [
        make_record(task_id="a", run_id=f"r{i}", groundedness=0.9,
                    judge=make_judge(value=0.9, claims=0.8))
        for i in range(3)
    ]
    baseline = [
        make_record(task_id="a", run_id=f"r{i}", groundedness=0.5,
                    judge=make_judge(value=0.5, claims=0.4))
        for i in range(3)
    ]

    section = _semantic_section(render(current, baseline=baseline))

    assert "50% → 90%" in section
    assert "40% → 80%" in section


def test_diff_refuses_semantic_numbers_when_the_prompt_changed():
    """prompt 版本不同 = 问的不是同一个问题 —— 拿两把尺子的读数相减没有意义。"""
    current = [make_record(task_id="a", groundedness=0.9, judge=make_judge(prompt="newhash1"))]
    baseline = [make_record(task_id="a", groundedness=0.5, judge=make_judge(prompt="oldhash1"))]

    section = _semantic_section(render(current, baseline=baseline))

    assert "跳过" in section
    assert "oldhash1 → newhash1" in section
    assert "→ 90%" not in section


def test_diff_refuses_semantic_numbers_when_the_judge_model_changed():
    """换判官模型跟换 prompt 一样 —— 也是换了一把尺子，不出数字。"""
    current = [make_record(task_id="a", groundedness=0.9, judge=make_judge(model="kimi"))]
    baseline = [make_record(task_id="a", groundedness=0.5, judge=make_judge(model="deepseek"))]

    section = _semantic_section(render(current, baseline=baseline))

    assert "跳过" in section
    assert "判官模型不同" in section


def test_diff_skips_semantics_when_only_one_side_was_judged():
    current = [make_record(task_id="a", groundedness=0.9, judge=make_judge())]
    baseline = [make_record(task_id="a")]

    section = _semantic_section(render(current, baseline=baseline))

    assert "有一边没判过" in section


def test_diff_has_no_semantic_block_when_nobody_was_judged():
    current = [make_record(task_id="a")]
    baseline = [make_record(task_id="a")]

    assert "Semantic" not in render(current, baseline=baseline)


def test_load_baseline_reads_json_files(tmp_path):
    (tmp_path / "a_run_001.json").write_text(
        json.dumps(make_record(task_id="a")), encoding="utf-8"
    )
    (tmp_path / "b_run_001.json").write_text(
        json.dumps(make_record(task_id="b")), encoding="utf-8"
    )

    records = load_baseline(tmp_path)

    assert sorted(r["task_id"] for r in records) == ["a", "b"]


def test_load_baseline_skips_calls_sidecar(tmp_path):
    """`.calls.json` 是工具调用明细，不是 run 记录，混进来会污染统计。"""
    (tmp_path / "a_run_001.json").write_text(
        json.dumps(make_record(task_id="a")), encoding="utf-8"
    )
    (tmp_path / "a_run_001.calls.json").write_text(
        json.dumps([{"name": "read_file", "arguments": {}, "ok": True, "error": None}]),
        encoding="utf-8",
    )

    records = load_baseline(tmp_path)

    assert len(records) == 1
    assert records[0]["task_id"] == "a"


def test_load_baseline_rejects_a_json_that_is_not_a_run_record(tmp_path):
    """目录里混进别的 json 要**报错并指名文件**。

    静默跳过只会让 diff 悄悄偏掉，而读者无从察觉；报错至少能让人把目录收拾干净。
    修改前这里是 `KeyError: 'status'`，完全不指向是哪个文件。
    """
    (tmp_path / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_baseline(tmp_path)

    assert "notes.json" in str(excinfo.value)


# ---------- 数据本身的坑：没跑完的 run、被环境破坏的工作区 ----------


def test_truncated_runs_are_named_in_the_report():
    """撞上 miniCC 循环上限的 run 原本跟正常跑完的**长得一模一样**。

    验收看的是产物，产物对了就还是 success —— 于是「agent 没来得及收尾」这件事
    在报告里完全消失。实测 12 题 × k=3 里 4 条，而且它们的最终回答是空的、
    还被 judge 编了个 0.5 塞进两个维度。
    """
    records = [make_record(task_id="a"), make_record(task_id="b")]
    records[0]["truncated"] = True

    summary = summarize(records)
    text = render(records)

    assert summary["truncated"] == ["a/run_001"]
    assert "Truncated" in text
    assert "a/run_001" in text
    assert "循环上限" in text


def test_nothing_is_said_when_no_run_was_truncated():
    text = render([make_record()])

    assert "Truncated" not in text


def test_environment_block_names_the_run_whose_workspace_broke():
    """工作区被破坏过 → 这条 run 的失败标签可能来自环境，必须在报告里说清。

    实测 `top_words_005/run_003`：`rm … ; cat wordcount.py` 在 cmd.exe 下被拼成
    一条 rm 命令，把工作区的 `wordcount.py` 删了，agent 只能用 write_file 重写 →
    触发 WRONG_TOOL。标签本身没说错，但拿它当「miniCC 行为变差」就是错的。
    """
    records = [
        make_record(task_id="ok"),
        make_record(task_id="bad", run_id="run_002"),
    ]
    records[1]["missing_initial_files"] = ["wordcount.py"]
    records[1]["posix_separator_calls"] = 10

    summary = summarize(records)
    text = render(records)

    assert summary["environment"]["broken"] == {"bad/run_002": ["wordcount.py"]}
    assert "Environment" in text
    assert "bad/run_002" in text and "wordcount.py" in text
    # `;` 是**底数**不是标志 —— 36 个真实 run 里 27 个都含它，所以要印成 aggregate
    assert "1/2" in text


def test_nothing_is_said_when_no_workspace_broke():
    assert "Environment" not in render([make_record()])


def test_dimension_denominator_is_printed_when_not_every_run_counts():
    """分母不是 run 总数的维度要印 n。

    不印的话「Error Recovery 83%」会被读成「83% 的 run 恢复得不错」—— 实际上它
    只在**出过工具错误**的 run 上有值（实测那轮是 12/36）。分母变了读者看不出来，
    两个版本也就没法区分「恢复变差」和「错误变多」。
    """
    records = [make_record(run_id=f"run_{i:03d}") for i in range(1, 4)]
    records[0]["evaluation"]["error_recovery"] = 1.0
    records[1]["evaluation"]["error_recovery"] = 0.0

    summary = summarize(records)
    text = render(records)

    assert summary["dimension_counts"]["error_recovery"] == 2
    assert summary["valid_runs"] == 3
    assert "(n=2/3)" in text
    # 每条都有值的维度不加这个尾巴 —— 印满就是噪音
    assert "(n=3/3)" not in text


def test_denominator_is_not_printed_when_every_run_counts():
    records = [make_record(run_id=f"run_{i:03d}") for i in range(1, 4)]

    assert "(n=" not in render(records)


def test_zero_signal_lines_say_so():
    """整批没触发的线要说「没信号」，不能印成「0」让人当成结论。

    子 agent（36 个 run 里 0 次）和压缩（compactions 0）都是这种情况 ——
    「Subagent 0/36」和「compactions 0」看着像测出来的结果，其实是空转。
    """
    text = render([make_record()])

    assert "0/1 用了 run_subagent" in text
    assert "一次没用，这条线没信号" in text
    assert "没触发过压缩，这条线没信号" in text


def test_subagent_line_drops_the_caveat_once_it_is_used():
    text = render([make_record(tools_used=("read_file", "run_subagent"))])

    assert "1/1 用了 run_subagent" in text
    assert "一次没用" not in text

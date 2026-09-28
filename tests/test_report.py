import json

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
):
    return {
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
            "groundedness": None,
            "error_recovery": None,
        },
        "failures": list(failures),
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
    """「INEFFICIENT 0.5」= 一半的 run 命中，不是「0.5 个失败」。"""
    records = [
        make_record(failures=["INEFFICIENT", "INCOMPLETE"]),
        make_record(failures=["INEFFICIENT"]),
        make_record(),
        make_record(),
    ]

    failures = summarize(records)["failures"]

    assert failures["INEFFICIENT"] == 0.5
    assert failures["INCOMPLETE"] == 0.25
    assert failures["WRONG_TOOL"] == 0.0


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


def test_diff_stays_quiet_for_a_proper_version_comparison():
    """agent_version 不同、其余条件一致 —— 这才是一次正常的版本对比。"""
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


def test_diff_refuses_when_models_differ():
    """模型不同时比的是模型，不是 miniCC 版本。"""
    current = [make_record(task_id="a", model="deepseek")]
    baseline = [make_record(task_id="a", model="kimi")]

    diff_section = render(current, baseline=baseline).split("Baseline Diff")[1]

    assert "拒绝出 diff" in diff_section
    assert "模型不一致" in diff_section


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

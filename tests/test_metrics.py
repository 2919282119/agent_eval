import json
from pathlib import Path

import pytest

from agenteval.metrics import blank_evaluation, evaluate
from agenteval.task import Check, Task
from agenteval.trajectory import LlmStats, build

NO_RESULT = object()

# 任务开始时工作区里就有的文件
INITIAL_FILES = {"a.py", "b.py"}


def score(traj, checks, task=None, initial_files=INITIAL_FILES):
    """薄包装：绝大多数用例共用同一份初始文件清单。"""
    return evaluate(traj, checks, task or make_task(), initial_files)


def traj_from(spec, answer=None):
    """spec: [(tool_name, arguments_dict, result)]

    result 传 NO_RESULT 表示这次调用没有对应的工具结果（轨迹中断）。
    """
    messages = [{"role": "user", "content": "任务"}]
    for index, (name, arguments, result) in enumerate(spec):
        call_id = f"c{index}"
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        if result is not NO_RESULT:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    if answer is not None:
        messages.append({"role": "assistant", "content": answer, "tool_calls": None})
    return build(messages, LlmStats())


def make_task(expected=None, forbidden=None, max_calls=None):
    return Task(
        task_id="t1",
        instruction="修一下",
        root=Path("."),
        expected_tools=expected or [],
        forbidden_tools=forbidden or [],
        max_tool_calls=max_calls,
    )


def essential(passed=True):
    return Check("关键项", passed, weight="essential")


def important(passed=True):
    return Check("重要项", passed, weight="important")


def minor(passed=True):
    return Check("次要项", passed, weight="minor")


OK_READ = ("read_file", {"path": "a.py"}, "内容")
OK_EDIT = ("edit_file", {"path": "a.py"}, {"returncode": 0, "output": "ok"})


# ---------- 五维分 ----------


def test_task_success_requires_all_essential_passing():
    traj = traj_from([OK_READ, OK_EDIT])

    assert score(traj, [essential(True), essential(False)]).evaluation[
        "task_success"
    ] is False
    assert score(traj, [essential(True), essential(True)]).evaluation[
        "task_success"
    ] is True


def test_task_success_is_false_when_the_task_has_no_essential_check():
    """没有关键项的题**永远不算成功** —— fail-closed，不是漏写。

    没有关键项就没法判定「做完了」，报成功等于放水。题目写漏 essential 属于出题错误，
    由 `tests/test_tasks.py` 的参考修复体检拦住（要求未修复时至少挂一条 essential）。
    """
    result = score(traj_from([OK_READ, OK_EDIT]), [important(True), minor(True)])

    assert result.evaluation["task_success"] is False


def test_correctness_covers_important_too():
    """只算 essential 的话 correctness 会恒等于 task_success，这个用例就是防它退化。"""
    result = score(traj_from([OK_READ]), [essential(True), important(False)])

    assert result.evaluation["task_success"] is True  # essential 全过
    assert result.evaluation["correctness"] == 0.5  # 但重要项挂了
    assert "INCOMPLETE" in result.failures


def test_completeness_is_weighted_by_3_2_1():
    checks = [essential(False), important(True), minor(True)]

    # 通过的是 2 + 1 = 3，总分 3 + 2 + 1 = 6
    assert score(traj_from([OK_READ]), checks).evaluation["completeness"] == 0.5


def test_groundedness_is_null_in_v1():
    assert score(traj_from([OK_READ]), [essential(True)]).evaluation["groundedness"] is None


def test_tool_usage_penalizes_overwriting_an_existing_file():
    """write_file 去覆盖**初始工作区里已有**的文件 —— 这才是真违规。"""
    traj = traj_from([("write_file", {"path": "a.py"}, {"returncode": 0}), OK_EDIT])

    result = score(traj, [essential(True)], make_task(forbidden=["write_file"]))

    assert result.evaluation["tool_usage"] == 0.5
    assert "WRONG_TOOL" in result.failures


def test_tool_usage_ignores_scratch_files():
    """实测踩过：agent 用 write_file 建一次性验证脚本来自查，不该判违规。

    而且「主动写脚本验证」正是 CLAIMS_WITHOUT_ACTION 奖励的行为，
    两条规则不能互相打架。
    """
    traj = traj_from(
        [
            ("write_file", {"path": "_check.py"}, {"returncode": 0}),
            ("bash", {"command": "python _check.py"}, {"returncode": 0}),
        ]
    )

    result = score(traj, [essential(True)], make_task(forbidden=["write_file"]))

    assert result.evaluation["tool_usage"] == 1.0
    assert "WRONG_TOOL" not in result.failures


def test_tool_usage_ignores_expected_tools():
    """expected_tools 只记录不扣分：用等效工具达成目标不该被冤枉。"""
    traj = traj_from([("grep", {"pattern": "x"}, {"returncode": 0})])

    result = score(traj, [essential(True)], make_task(expected=["read_file"]))

    assert result.evaluation["tool_usage"] == 1.0
    assert "WRONG_TOOL" not in result.failures


def test_tool_usage_is_clipped_to_zero():
    """两个不同工具各自违规 → 1.0 - 0.5×2，裁到 0 而不是负数。"""
    traj = traj_from(
        [
            ("write_file", {"path": "a.py"}, {}),
            ("bash", {"command": "x"}, {"returncode": 0}),
        ]
    )

    result = score(
        traj,
        [essential(True)],
        make_task(forbidden=["write_file", "bash"]),
    )

    assert result.evaluation["tool_usage"] == 0.0


# ---------- error_recovery 三档 ----------


def test_error_recovery_is_null_without_errors():
    assert score(traj_from([OK_READ, OK_EDIT]), [essential(True)]).evaluation[
        "error_recovery"
    ] is None


def test_error_recovery_true_recovery():
    """同一个工具重试成功。"""
    traj = traj_from(
        [
            ("read_file", {"path": "a.py"}, {"error": "文件不存在"}),
            ("read_file", {"path": "a.py"}, "内容"),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 1.0


def test_error_recovery_detour():
    """换了别的工具绕过去。"""
    traj = traj_from(
        [
            ("read_file", {"path": "a.py"}, {"error": "文件不存在"}),
            ("bash", {"command": "ls"}, {"returncode": 0, "output": "a.py"}),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 0.5


def test_error_recovery_gives_up():
    """失败后直接收尾，没有任何成功的后续动作。"""
    traj = traj_from(
        [("read_file", {"path": "a.py"}, {"error": "文件不存在"})],
        answer="文件读不了，我先看看别的",
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 0.0


def test_error_recovery_zero_when_nothing_after_the_failure_works():
    """三档里的最低档：出错之后又试了几次，全都没成。

    跟「出错后直接放弃」（后面一次调用都没有）是两条不同的路径，之前只测了后者。
    """
    traj = traj_from(
        [
            ("read_file", {"path": "a.py"}, {"error": "文件不存在"}),
            ("grep", {"pattern": "x"}, {"error": "命令执行异常"}),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 0.0


def test_error_recovery_ignores_bash_exit_code():
    """bash 的非 0 退出码**不算工具故障**。

    实测（2026-09-26 那轮）：Windows 上 `shell=True` 走 cmd.exe，而 agent 写的是
    bash 语法 —— 12 个 run 共 30 次失败调用，24 次是这一类，和 agent 能力无关，
    而且几乎必然「恢复」（换个写法重试就行）。算进来这个维度就恒等于 1.0。
    """
    traj = traj_from([("bash", {"command": "pwd; ls"}, {"returncode": 1})])

    assert score(traj, [essential(True)]).evaluation["error_recovery"] is None


def test_error_recovery_still_counts_real_tool_failures():
    """真·工具故障（读不到 / 写不进 / 参数错）照旧要算。"""
    traj = traj_from(
        [
            ("read_file", {"path": "a.py"}, {"error": "没有权限读取文件: a.py"}),
            ("read_file", {"path": "a.py"}, "内容"),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 1.0


def test_error_recovery_only_judges_tool_level_failures():
    """两类混在一起时按工具级那次判，且它之后的命令失败救不了它。"""
    traj = traj_from(
        [
            ("bash", {"command": "pwd"}, {"returncode": 1}),
            ("write_file", {"path": "a.py"}, {"error": "没有权限写入文件: a.py"}),
            ("bash", {"command": "echo hi"}, {"returncode": 0}),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 0.5


def test_error_recovery_sees_string_form_tool_failure():
    """read_file 失败返回的是一句中文，不是 error dict，同样要能识别。"""
    traj = traj_from(
        [
            ("read_file", {"path": "nope.py"}, "文件不存在: nope.py"),
            ("read_file", {"path": "nope.py"}, "内容"),
        ]
    )

    assert score(traj, [essential(True)]).evaluation["error_recovery"] == 1.0


# ---------- 失败标签 ----------


def test_wrong_argument_from_json_parse_failure():
    traj = traj_from([("bash", {}, {"error": "工具参数 JSON 解析失败: Expecting value"})])

    assert "WRONG_ARGUMENT" in score(traj, [essential(True)]).failures


def test_wrong_argument_not_triggered_by_plain_failure():
    traj = traj_from([("bash", {"command": "pytest"}, {"error": "工具执行失败: boom"})])

    assert "WRONG_ARGUMENT" not in score(traj, [essential(True)]).failures


def test_inefficient_by_max_tool_calls():
    traj = traj_from([OK_READ, OK_EDIT, OK_READ])

    assert "INEFFICIENT" in score(traj, [essential(True)], make_task(max_calls=2)).failures


def test_inefficient_by_repeated_identical_call():
    repeat = ("read_file", {"path": "a.py"}, "内容")

    assert "INEFFICIENT" not in score(traj_from([repeat, repeat]), [essential(True)]).failures
    assert "INEFFICIENT" in score(
        traj_from([repeat, repeat, repeat]), [essential(True)]
    ).failures


def test_inefficient_ignores_reads_separated_by_a_change():
    """`读 → 改 → 读（确认）` 是正当工作流，不是盲目重复。

    实测踩过：encoding_trap_010 那轮 `read_file pricing.py` 出现 3 次，分别在
    「初次探索」「改完确认」「整个重写之后再确认」之后 —— 判 INEFFICIENT 是冤枉的。
    """
    traj = traj_from(
        [
            OK_READ,
            ("edit_file", {"path": "a.py"}, {"returncode": 0}),
            OK_READ,
            ("edit_file", {"path": "a.py"}, {"returncode": 0}),
            OK_READ,
        ]
    )

    assert "INEFFICIENT" not in score(traj, [essential(True)]).failures


def test_inefficient_counts_repeats_among_read_only_calls():
    """只读类工具之间重复也算 —— 中间没人动过文件，读到的就是同一份内容。"""
    other = ("read_file", {"path": "b.py"}, "内容")
    traj = traj_from([OK_READ, other, OK_READ, other, OK_READ])

    assert "INEFFICIENT" in score(traj, [essential(True)]).failures


def test_inefficient_ignores_reads_interleaved_with_a_bash_rewrite():
    """实测踩过的误报：agent 用 bash 重写同一个文件之后再读。

    `encoding_trap_010` 那轮三次 `read_file _dump.txt` 中间夹着两次
    `python -c "open('_dump.txt','w')..."` —— 它读的是三个**不同版本**的文件。
    只认 `edit_file` / `write_file` 会把这种判成盲目重复。
    """
    rewrite = (
        "bash",
        {"command": "python -c \"open('a.py','w').write('new')\""},
        {"returncode": 0},
    )
    traj = traj_from([OK_READ, rewrite, OK_READ, rewrite, OK_READ])

    assert "INEFFICIENT" not in score(traj, [essential(True)]).failures


def test_no_exploration_when_editing_existing_file_blindly():
    traj = traj_from([OK_EDIT], answer="改好了")

    assert "NO_EXPLORATION" in score(traj, [essential(True)]).failures


def test_no_exploration_not_triggered_when_creating_new_file():
    """「创建一个新文件」本来就不需要先探索仓库，不能算没检查就动手。"""
    traj = traj_from(
        [("write_file", {"path": "hello.txt"}, "文件写入成功: hello.txt")],
        answer="建好了",
    )

    assert "NO_EXPLORATION" not in score(traj, [essential(True)]).failures


def test_no_exploration_not_triggered_when_exploring_first():
    assert "NO_EXPLORATION" not in score(
        traj_from([OK_READ, OK_EDIT]), [essential(True)]
    ).failures


def test_no_exploration_not_triggered_by_bash_exploration():
    """全程只用 bash 探索和改文件：没碰过 edit_file/write_file，不该报。"""
    traj = traj_from(
        [
            ("bash", {"command": "ls -la"}, {"returncode": 0, "output": "a.py"}),
            ("bash", {"command": "sed -i s/a/b/ a.py"}, {"returncode": 0}),
        ]
    )

    assert "NO_EXPLORATION" not in score(traj, [essential(True)]).failures


def test_no_exploration_accepts_absolute_paths():
    traj = traj_from(
        [("edit_file", {"path": r"D:\work\ws\a.py", "old_text": "x", "new_text": "y"}, {})]
    )

    assert "NO_EXPLORATION" in score(traj, [essential(True)]).failures


def test_claims_without_action_when_tests_never_ran():
    traj = traj_from([OK_READ, OK_EDIT], answer="已修复 average()，测试全部通过。")

    assert "CLAIMS_WITHOUT_ACTION" in score(traj, [essential(True)]).failures


def test_claims_without_action_ok_when_tests_actually_ran():
    traj = traj_from(
        [OK_READ, OK_EDIT, ("bash", {"command": "python -m pytest -q"}, {"returncode": 0})],
        answer="已修复，测试全部通过。",
    )

    assert "CLAIMS_WITHOUT_ACTION" not in score(traj, [essential(True)]).failures


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -m pytest tests/",
        "python test_calc_stats.py",
        "python3 tests/test_all.py",
        'python3 -c "assert f(1) == 2; print(\'OK\')"',
        "python -c \"from calc import average; print(average([]))\"",
        "npm test",
        "go test ./...",
    ],
)
def test_claims_without_action_recognizes_code_execution(command):
    """实测踩过两次：先不认 `python test_x.py`，后不认 `python3 -c "assert ..."`。

    口径已退到「只要跑过代码就不判」—— 想精确判断「这次算不算测试」判不准。
    """
    traj = traj_from(
        [("bash", {"command": command}, {"returncode": 0})],
        answer="修好了，测试全部通过。",
    )

    assert "CLAIMS_WITHOUT_ACTION" not in score(traj, [essential(True)]).failures


def test_claims_without_action_fires_when_nothing_was_executed():
    """这条规则现在只在「一行代码都没跑过」时触发。"""
    traj = traj_from(
        [
            ("read_file", {"path": "a.py"}, "内容"),
            ("edit_file", {"path": "a.py"}, {"returncode": 0}),
            ("bash", {"command": "ls"}, {"returncode": 0}),
        ],
        answer="修好了，测试全部通过。",
    )

    assert "CLAIMS_WITHOUT_ACTION" in score(traj, [essential(True)]).failures


def test_claims_without_action_ignores_negated_claims():
    traj = traj_from([OK_READ], answer="测试没有通过，我还需要再看看。")

    assert "CLAIMS_WITHOUT_ACTION" not in score(traj, [essential(True)]).failures


# ---------- veto ----------


def test_veto_zeroes_everything():
    traj = traj_from([("bash", {"command": "rm -rf /"}, {"returncode": 0})])

    result = score(traj, [essential(True), important(True)])

    assert result.vetoed is True
    assert result.evaluation["task_success"] is False
    assert result.evaluation["correctness"] == 0
    assert result.evaluation["completeness"] == 0
    assert result.evaluation["tool_usage"] == 0
    # error_recovery 本来是 None（没发生工具错误，不适用），归零不会把它变成 0
    assert result.evaluation["error_recovery"] is None


def test_veto_not_triggered_by_benign_rm():
    traj = traj_from([("bash", {"command": "rm -rf ./build"}, {"returncode": 0})])

    assert score(traj, [essential(True)]).vetoed is False


def test_blank_evaluation_is_all_null():
    assert set(blank_evaluation().values()) == {None}

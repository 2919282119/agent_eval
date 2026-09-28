import json

import pytest

from agenteval.trajectory import LlmStats, build


def assistant(*calls, content=None):
    """calls: (call_id, name, arguments_json)"""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": n, "arguments": a}}
            for cid, n, a in calls
        ],
    }


def tool(call_id, payload):
    return {"role": "tool", "tool_call_id": call_id, "content": payload}


def answer(content):
    return {"role": "assistant", "content": content, "tool_calls": None}


def user(content="修一下"):
    return {"role": "user", "content": content}


def test_summary_matches_design_example():
    """设计文档里的例子：3 条 assistant + 5 条 tool 结果 → steps 8 / tool_calls 5 / llm_calls 3。

    （轨迹在工具轮结束，所以没有最终回答消息。）
    """
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}'),
                  ("c2", "grep", '{"pattern": "average"}')),
        tool("c1", json.dumps("a.py 的内容")),
        tool("c2", json.dumps({"returncode": 0, "output": "a.py:12"})),
        assistant(("c3", "edit_file", '{"path": "a.py"}')),
        tool("c3", json.dumps({"returncode": 0, "output": "ok"})),
        assistant(("c4", "bash", '{"command": "pytest"}'),
                  ("c5", "read_file", '{"path": "a.py"}')),
        tool("c4", json.dumps({"returncode": 0, "output": "2 passed"})),
        tool("c5", json.dumps("改完的内容")),
    ]
    stats = LlmStats(llm_calls=3, tokens=6200, latency_ms=12400, model_actual="kimi-k2.6")

    traj = build(messages, stats)

    assert traj.summary() == {
        "steps": 8,
        "tool_calls": 5,
        "llm_calls": 3,
        "tokens": 6200,
        "latency_ms": 12400,
    }


def test_steps_counts_the_final_answer_too():
    """真实轨迹结尾总有一条不带工具调用的 assistant 回答，它也要计入 steps。"""
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", json.dumps("内容")),
        answer("看完了"),
    ]

    traj = build(messages, LlmStats(llm_calls=2))

    assert traj.steps == 3  # 2 条 assistant + 1 条 tool


def test_extracts_names_arguments_and_order():
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py", "limit": 10}')),
        tool("c1", json.dumps("内容")),
    ]

    traj = build(messages, LlmStats())

    assert traj.tools_used() == ["read_file"]
    assert traj.calls[0].arguments == {"path": "a.py", "limit": 10}
    assert traj.calls[0].ok is True


def test_explicit_error_key_marks_failure():
    messages = [
        assistant(("c1", "read_file", '{"path": "missing.py"}')),
        tool("c1", json.dumps({"error": "文件不存在: missing.py"})),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is False
    assert traj.calls[0].error == "文件不存在: missing.py"


def test_bash_nonzero_returncode_marks_failure():
    messages = [
        assistant(("c1", "bash", '{"command": "pytest"}')),
        tool("c1", json.dumps({"returncode": 1, "output": "1 failed"})),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is False
    assert traj.calls[0].error == "退出码 1"


def test_string_result_is_success():
    """read_file 成功时 miniCC 会把字符串 json.dumps 一遍，不能误判为错误。"""
    messages = [
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", json.dumps("def average(xs): ...")),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is True
    assert traj.calls[0].error is None


@pytest.mark.parametrize(
    "message",
    [
        "文件不存在: a.py",
        "目录不存在: src",
        "路径不存在: src",
        "没有权限读取文件: a.py",
        "没有权限写入文件: a.py",
        "没有权限修改文件: a.py",
        "没有权限访问目录: src",
        "文件不是 UTF-8 文本文件: a.py",
        "未找到要替换的内容: def average",
        "读取文件失败: boom",
        "写入文件失败: boom",
        "修改文件失败: boom",
        "列出目录失败: boom",
        "搜索失败: boom",
        "加载 Skill 失败: boom",
    ],
)
def test_string_form_tool_failure_is_detected(message):
    """builtin 工具失败时只返回一句中文，没有结构化错误通道，必须靠前缀识别。"""
    messages = [
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", json.dumps(message)),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is False
    assert traj.calls[0].error == message


def test_grep_no_match_is_not_an_error():
    """grep「没有找到」是搜索成功但零结果，不是工具故障。"""
    messages = [
        assistant(("c1", "grep", '{"pattern": "不存在的东西"}')),
        tool("c1", json.dumps("没有找到: 不存在的东西")),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is True


def test_call_without_result_marks_failure():
    """agent 崩了，最后那次工具调用没有结果，不能算成功。"""
    messages = [assistant(("c1", "bash", '{"command": "sleep 999"}'))]

    traj = build(messages, LlmStats())

    assert traj.calls[0].ok is False
    assert "中断" in traj.calls[0].error


def test_unparseable_arguments_are_preserved():
    messages = [
        assistant(("c1", "bash", "{不是合法 json")),
        tool("c1", json.dumps({"error": "工具参数 JSON 解析失败"})),
    ]

    traj = build(messages, LlmStats())

    assert traj.calls[0].arguments == {"raw": "{不是合法 json"}


def test_first_action_classification():
    cases = [
        ("read_file", "explore"),
        ("list_dir", "explore"),
        ("glob", "explore"),
        ("grep", "explore"),
        ("write_file", "edit"),
        ("edit_file", "edit"),
        ("bash", "other"),
    ]
    for tool_name, expected in cases:
        messages = [
            assistant(("c1", tool_name, "{}")),
            tool("c1", json.dumps({"returncode": 0})),
        ]
        assert build(messages, LlmStats()).first_action == expected, tool_name


def test_first_action_is_none_without_tool_calls():
    traj = build([user(), answer("不用工具，直接答")], LlmStats())

    assert traj.first_action is None
    assert traj.calls == []
    assert traj.steps == 1


def test_final_answer_is_the_last_assistant_text():
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}'), content="先看看"),
        tool("c1", json.dumps("内容")),
        answer("改好了，测试全部通过。"),
    ]

    assert build(messages, LlmStats()).final_answer == "改好了，测试全部通过。"


def test_final_answer_is_empty_without_text():
    """agent 崩了、或最后一条 assistant 消息只有工具调用 —— 不该炸，给空串。"""
    messages = [user(), assistant(("c1", "bash", '{"command": "sleep 999"}'))]

    assert build(messages, LlmStats()).final_answer == ""


def test_non_json_tool_result_is_treated_as_success():
    """**没有护栏的假设**：miniCC 先把工具结果 `json.dumps` 再塞进 tool 消息
    （`agent/agent.py:104,159`），所以那句中文报错是**带引号的 JSON 字符串**，
    `_result_error` 才认得出。

    要是它哪天改成直接塞裸字符串，`json.loads` 抛异常 → 这里 `return None` →
    **失败被当成成功**，而且是静默的。钉住当前行为，改的时候至少有人看得见。
    """
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", "文件不存在: a.py"),  # 裸字符串，不是 json.dumps 过的
    ]

    call = build(messages, LlmStats()).calls[0]

    assert call.ok is True
    assert call.error is None


def test_final_answer_flattens_multimodal_content():
    """`content` 是多模态块时要摊平成 str。

    不摊平的话 `metrics` 那边 `re.search` 会拿 list 去匹配、直接 TypeError，
    整次 run 白跑（而且会被兜底吞成一条 error，看不出真正原因）。
    """
    messages = [
        user(),
        assistant(content=[{"type": "text", "text": "测试全部通过"}]),
    ]

    assert build(messages, LlmStats()).final_answer == "测试全部通过"


def test_final_answer_ignores_content_it_cannot_read():
    """认不出的形状给空串，绝不返回非 str —— 类型契约比内容完整更优先。"""
    messages = [user(), assistant(content=42)]

    assert build(messages, LlmStats()).final_answer == ""


# ---------- 工具结果正文（给 judge 用的）----------


def test_call_carries_the_tool_result_text():
    """结果正文要进 sidecar —— judge 判 groundedness（回答有没有依据）全靠它。"""
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", json.dumps("def average(xs): ...")),
    ]

    assert build(messages, LlmStats()).calls[0].result == "def average(xs): ..."


def test_tool_result_is_truncated():
    """长结果要截断（`trajectory._RESULT_LIMIT` = 500）。

    不截的话 sidecar 会被撑爆 —— 一次 read_file 就是几万字符。
    """
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "a.py"}')),
        tool("c1", json.dumps("x" * 5000)),
    ]

    assert len(build(messages, LlmStats()).calls[0].result) == 500


def test_dict_result_is_kept_as_readable_json():
    """bash 的结果是 dict（`returncode` + `output`），正文要读得出来。"""
    messages = [
        user(),
        assistant(("c1", "bash", '{"command": "pytest -q"}')),
        tool("c1", json.dumps({"returncode": 0, "output": "2 passed"})),
    ]

    assert "2 passed" in build(messages, LlmStats()).calls[0].result


def test_failed_call_still_carries_the_error_text():
    """失败也留正文 —— 错误消息本身就是「agent 当时看到了什么」的一部分。"""
    messages = [
        user(),
        assistant(("c1", "read_file", '{"path": "nope.py"}')),
        tool("c1", json.dumps("文件不存在: nope.py")),
    ]

    call = build(messages, LlmStats()).calls[0]

    assert call.ok is False
    assert call.result == "文件不存在: nope.py"


def test_interrupted_call_has_no_result_text():
    """轨迹中断的那次调用没有结果 —— 正文是空串，不是 None。"""
    messages = [assistant(("c1", "bash", '{"command": "sleep 999"}'))]

    assert build(messages, LlmStats()).calls[0].result == ""

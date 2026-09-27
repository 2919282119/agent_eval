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

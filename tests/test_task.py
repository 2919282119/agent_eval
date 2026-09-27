import pytest

from agenteval.task import Check, load_task, run_verifier, workspace_files

YAML_FULL = """\
task_id: fix_offbyone_001
category: correctness
instruction: |
  calc.py 的 average() 在空列表时崩溃。请修复。
expected_tools: [read_file, edit_file]
forbidden_tools: [write_file]
max_tool_calls: 12
"""

YAML_MINIMAL = """\
task_id: minimal_001
instruction: 随便做点什么
"""

YAML_RETIRED = """\
task_id: retired_001
instruction: 随便做点什么
retired: true
"""

VERIFY_MIXED = """\
from agenteval.task import Check

def check(workspace):
    return [
        Check("空列表返回 None", True, weight="essential"),
        Check("非空列表结果不变", False, weight="important"),
        Check("未引入副作用", True, weight="minor", detail="diff 为空"),
    ]
"""

VERIFY_NOT_A_LIST = """\
def check(workspace):
    return "我不是 list"
"""


def make_task_dir(tmp_path, yaml_src, verify_src):
    task_dir = tmp_path / "some_task"
    (task_dir / "workspace").mkdir(parents=True)
    (task_dir / "task.yaml").write_text(yaml_src, encoding="utf-8")
    (task_dir / "verify.py").write_text(verify_src, encoding="utf-8")
    return task_dir


def test_load_task_reads_all_fields(tmp_path):
    task_dir = make_task_dir(tmp_path, YAML_FULL, VERIFY_MIXED)

    task = load_task(task_dir)

    assert task.task_id == "fix_offbyone_001"
    assert task.category == "correctness"
    assert "average()" in task.instruction
    assert task.expected_tools == ["read_file", "edit_file"]
    assert task.forbidden_tools == ["write_file"]
    assert task.max_tool_calls == 12
    assert task.root == task_dir
    assert task.workspace == task_dir / "workspace"


def test_load_task_applies_defaults(tmp_path):
    task_dir = make_task_dir(tmp_path, YAML_MINIMAL, VERIFY_MIXED)

    task = load_task(task_dir)

    assert task.category == "uncategorized"
    assert task.expected_tools == []
    assert task.forbidden_tools == []
    assert task.max_tool_calls is None
    assert task.retired is False


def test_load_task_reads_retired_flag(tmp_path):
    task_dir = make_task_dir(tmp_path, YAML_RETIRED, VERIFY_MIXED)

    assert load_task(task_dir).retired is True


def test_check_rejects_unknown_weight():
    with pytest.raises(ValueError, match="非法的 weight"):
        Check("x", True, weight="critical")


def test_check_score_follows_weight():
    assert Check("x", True, weight="essential").score == 3
    assert Check("x", True, weight="important").score == 2
    assert Check("x", True, weight="minor").score == 1


def test_run_verifier_returns_checks(tmp_path):
    task = load_task(make_task_dir(tmp_path, YAML_FULL, VERIFY_MIXED))

    checks = run_verifier(task, task.workspace)

    assert [c.name for c in checks] == [
        "空列表返回 None",
        "非空列表结果不变",
        "未引入副作用",
    ]
    assert [c.passed for c in checks] == [True, False, True]
    assert [c.weight for c in checks] == ["essential", "important", "minor"]
    assert checks[2].detail == "diff 为空"


def test_run_verifier_receives_workspace_path(tmp_path):
    """verify.py 拿到的是复制后的临时工作区路径，不是任务模板目录。"""
    echo = (
        "from agenteval.task import Check\n"
        "def check(workspace):\n"
        "    return [Check(str(workspace), True)]\n"
    )
    task = load_task(make_task_dir(tmp_path, YAML_FULL, echo))
    scratch = tmp_path / "run_001"

    checks = run_verifier(task, scratch)

    assert checks[0].name == str(scratch)


def test_run_verifier_rejects_non_list(tmp_path):
    task = load_task(make_task_dir(tmp_path, YAML_FULL, VERIFY_NOT_A_LIST))

    with pytest.raises(TypeError, match="必须返回 list"):
        run_verifier(task, task.workspace)


def test_workspace_files_lists_relative_paths(tmp_path):
    task_dir = make_task_dir(tmp_path, YAML_FULL, VERIFY_MIXED)
    (task_dir / "workspace" / "pkg").mkdir()
    (task_dir / "workspace" / "pkg" / "deep.py").write_text("x = 1\n", encoding="utf-8")
    (task_dir / "workspace" / "top.py").write_text("y = 2\n", encoding="utf-8")

    task = load_task(task_dir)

    assert workspace_files(task) == {"pkg/deep.py", "top.py"}

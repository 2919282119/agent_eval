import agenteval.cli as cli
from agenteval.cli import find_tasks, main, write_report
from agenteval.report import load_baseline
from agenteval.runner import RunResult

# 一份形状合法的最小记录 —— 用假的 run_task 把 main 整条路跑通，不花 API
FAKE_RECORD = {
    "task_id": "alpha",
    "run_id": "run_001",
    "status": "success",
    "model": "deepseek",
    "failures": [],
    "first_action": "explore",
    "tools_used": ["read_file"],
    "max_usage_ratio": 0.01,
    "compactions": 0,
    "trajectory": {
        "steps": 2,
        "tool_calls": 1,
        "llm_calls": 1,
        "tokens": 100,
        "latency_ms": 10,
    },
    "evaluation": {
        "task_success": True,
        "correctness": 1.0,
        "completeness": 1.0,
        "tool_usage": 1.0,
        "groundedness": None,
        "error_recovery": None,
    },
}


def write_task(root, task_id, extra=""):
    task_dir = root / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        f"task_id: {task_id}\ninstruction: |\n  随便做点什么。\n{extra}",
        encoding="utf-8",
    )
    return task_dir


def test_find_tasks_loads_every_task_dir(tmp_path):
    write_task(tmp_path, "alpha")
    write_task(tmp_path, "beta")

    assert [task.task_id for task in find_tasks(tmp_path)] == ["alpha", "beta"]


def test_find_tasks_skips_retired(tmp_path):
    """退役的题不跑，但**目录必须留着** —— 真实轨迹回放测试（tests/fixtures）
    是从活的 tasks/<id>/ 读配置的，删目录护栏就断了。"""
    write_task(tmp_path, "alive")
    write_task(tmp_path, "gone", extra="retired: true\n")

    assert [task.task_id for task in find_tasks(tmp_path)] == ["alive"]
    assert (tmp_path / "gone" / "task.yaml").exists()


def test_main_saves_the_report_next_to_the_run_records(tmp_path, monkeypatch):
    """跑完 `main` 之后 run 目录里必须有 report.txt。

    只测 `write_report` 本身抓不到「main 忘了调它」—— 所以这里把 main 整条路
    走一遍（`run_task` 换成假的，不调 API）。
    """
    write_task(tmp_path / "taskroot", "alpha")
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: RunResult(FAKE_RECORD, [], "做完了")
    )

    assert main(["--tasks", str(tmp_path / "taskroot"), "--out", str(tmp_path / "out")]) == 0

    reports = list((tmp_path / "out").glob("*/report.txt"))
    assert len(reports) == 1
    assert reports[0].read_text(encoding="utf-8").startswith("Agent Evaluation Report")


def test_main_returns_1_when_there_are_no_tasks(tmp_path, capsys):
    """空任务集要报错退出，而不是安静地写一份「零个任务」的报告。"""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["--tasks", str(empty), "--out", str(tmp_path / "out")]) == 1
    assert "没找到任何任务" in capsys.readouterr().err


def test_write_report_saves_the_text(tmp_path):
    write_report(tmp_path, "Agent Evaluation Report\n───\n")

    assert (tmp_path / "report.txt").read_text(encoding="utf-8").startswith(
        "Agent Evaluation Report"
    )


def test_saved_report_is_not_mistaken_for_a_run_record(tmp_path):
    """`load_baseline` 只读 *.json —— 报告文件不能被当成 run 记录混进统计。"""
    (tmp_path / "t1_run_001.json").write_text(
        '{"task_id": "t1", "status": "success", "evaluation": {}}', encoding="utf-8"
    )
    write_report(tmp_path, "报告")

    assert [record["task_id"] for record in load_baseline(tmp_path)] == ["t1"]

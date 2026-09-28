import json
from pathlib import Path

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


def _write_fake_judgement(argv):
    """冒充 judge.main：直接在磁盘上给每条记录补上 judge 键。"""
    for path in Path(argv[0]).glob("*.json"):
        if path.name.endswith(".calls.json"):
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record["evaluation"]["groundedness"] = 0.8
        record["judge"] = {
            "primary": "llm",
            "llm": {
                "model": "kimi",
                "prompt_hash": "abc12345",
                "status": "success",
                "results": {
                    "groundedness": {"value": 0.8, "detail": ""},
                    "claims_consistent": {"value": 1.0, "detail": ""},
                },
            },
        }
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return 0


def test_main_judges_the_batch_when_asked(tmp_path, monkeypatch):
    """`--judge` 让一条命令顶原来两条。

    真正要验的是 **cli 在判完之后重读了记录** —— 不重读的话记录改了、报告还是空的，
    而报告是从内存里的 `records` 渲染的。
    """
    write_task(tmp_path / "taskroot", "alpha")
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: RunResult(FAKE_RECORD, [], "做完了")
    )
    seen = []
    monkeypatch.setattr(
        cli.judge, "main", lambda argv: (seen.append(argv), _write_fake_judgement(argv))[1]
    )

    out = tmp_path / "out"
    assert main(["--tasks", str(tmp_path / "taskroot"), "--out", str(out), "--judge", "kimi"]) == 0

    assert seen[0][1:] == ["--model", "kimi"]
    text = list(out.glob("*/report.txt"))[0].read_text(encoding="utf-8")
    assert "80%" in text and "model kimi" in text


def test_main_does_not_judge_by_default(tmp_path, monkeypatch):
    """不传 `--judge` 就一次都不判 —— 默认行为不许变。"""
    write_task(tmp_path / "taskroot", "alpha")
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: RunResult(FAKE_RECORD, [], "做完了")
    )
    called = []
    monkeypatch.setattr(cli.judge, "main", lambda argv: called.append(argv))

    assert main(["--tasks", str(tmp_path / "taskroot"), "--out", str(tmp_path / "out")]) == 0

    assert called == []
    assert "n/a (needs judge)" in list((tmp_path / "out").glob("*/report.txt"))[0].read_text(
        encoding="utf-8"
    )


def test_main_appends_the_analysis_when_asked(tmp_path, monkeypatch):
    """一段 LLM 解读追加在报告末尾，而且它拿到的是**渲染好的报告文本**。"""
    write_task(tmp_path / "taskroot", "alpha")
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: RunResult(FAKE_RECORD, [], "做完了")
    )
    seen = []
    monkeypatch.setattr(
        cli.analyze,
        "section",
        lambda report_text, model: (seen.append((report_text, model)), "=== AI 分析 ===\n看不出差别")[1],
    )

    out = tmp_path / "out"
    assert main(["--tasks", str(tmp_path / "taskroot"), "--out", str(out), "--analyze", "kimi"]) == 0

    text = list(out.glob("*/report.txt"))[0].read_text(encoding="utf-8")
    assert "AI 分析" in text
    assert seen[0][1] == "kimi"
    assert "Agent Evaluation Report" in seen[0][0], "解读要读到渲染好的报告，不是裸记录"


def test_main_does_not_analyze_by_default(tmp_path, monkeypatch):
    """不传 `--analyze` 就一次都不解读 —— 默认行为不许变。"""
    write_task(tmp_path / "taskroot", "alpha")
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: RunResult(FAKE_RECORD, [], "做完了")
    )
    called = []
    monkeypatch.setattr(cli.analyze, "section", lambda *a: called.append(a))

    assert main(["--tasks", str(tmp_path / "taskroot"), "--out", str(tmp_path / "out")]) == 0

    assert called == []
    assert "AI 分析" not in list((tmp_path / "out").glob("*/report.txt"))[0].read_text(
        encoding="utf-8"
    )


def test_main_returns_1_when_there_are_no_tasks(tmp_path, capsys):
    """空任务集要报错退出，而不是安静地写一份「零个任务」的报告。"""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["--tasks", str(empty), "--out", str(tmp_path / "out")]) == 1
    assert "没找到任何任务" in capsys.readouterr().err


def test_main_rejects_a_broken_baseline_before_running_anything(tmp_path, monkeypatch, capsys):
    """基线目录指错要在**开跑之前**报错。

    实测踩过：`load_baseline` 原先在 `main` 的 `finally` 里调 —— 目录指错时异常从
    finally 抛出，**连本轮的 report.txt 一起带走**（run json 都写好了，报告没了）。
    那个 finally 存在的理由就是「一定要留一份」，它自己再炸掉就白设了。
    """
    write_task(tmp_path / "taskroot", "alpha")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")

    ran = []
    monkeypatch.setattr(
        cli, "run_task", lambda task, run_idx, model: ran.append(task.task_id)
    )

    code = main(
        [
            "--tasks", str(tmp_path / "taskroot"),
            "--out", str(tmp_path / "out"),
            "--baseline", str(baseline),
        ]
    )

    assert code == 1
    assert ran == [], "基线没读成，一个 run 都不该跑"
    assert "notes.json" in capsys.readouterr().err


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

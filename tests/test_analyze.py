"""报告解读层的离线测试 —— 一次 API 都不调。"""

import json

import pytest

import agenteval.analyze as analyze_mod
from agenteval.analyze import main, prompt_hash, section

RECORD = {
    "task_id": "alpha",
    "run_id": "run_001",
    "instruction": "修一下 calc.py",
    "status": "success",
    "failures": [],
    "temperature": 0.1,
    "model": "deepseek",
    "model_actual": "deepseek-flash",
    "agent_version": "a10dd34",
    "first_action": "explore",
    "tools_used": ["read_file"],
    "max_usage_ratio": 0.01,
    "compactions": 0,
    "trajectory": {"steps": 2, "tool_calls": 8, "llm_calls": 4, "tokens": 7800, "latency_ms": 100},
    "evaluation": {
        "task_success": True, "correctness": 1.0, "completeness": 1.0,
        "tool_usage": 1.0, "groundedness": None, "error_recovery": None,
    },
}


class _Message:
    def __init__(self, content):
        self.content = content


class FakeResponse:
    def __init__(self, content):
        self.choices = [type("Choice", (), {"message": _Message(content)})()]


def fake_ask(text):
    return lambda report_text, model: FakeResponse(text)


# ---------- prompt 里的护栏 ----------


def test_system_prompt_holds_the_guardrails():
    """这几条是这一层存在的理由 —— 少任何一条，解读就会把噪声说成趋势。

    报告里最显眼的数字（工具调用 / token / 延迟）全是噪声量级的，一个自由的 LLM
    几乎必然写出「B 略快」。所以在 prompt 层面把规矩钉死，别指望模型自觉。
    """
    system = analyze_mod._SYSTEM

    assert "Noise" in system  # 指给模型看噪声那一行
    assert "看不出差别" in system  # delta 小于噪声必须这么说
    assert "没有数据" in system  # 没判过 ≠ 0 分
    assert "类别" in system  # 只对类别信号下强结论
    assert "不支持" in system  # 还要说清数据不支持什么


def test_prompt_hash_is_stable():
    assert prompt_hash() == prompt_hash()


# ---------- 调模型 ----------


def test_analyze_returns_the_model_text():
    assert analyze_mod.analyze("报告正文", "kimi", ask=fake_ask(" 这是分析。 ")) == "这是分析。"


def test_analyze_swallows_a_failing_call():
    """报告已经写好了，解读拿不到不该连累它。"""
    def boom(report_text, model):
        raise TimeoutError("超时")

    text = analyze_mod.analyze("报告正文", "kimi", ask=boom)

    assert "分析失败" in text
    assert "TimeoutError" in text


def test_analyze_handles_an_empty_reply():
    assert "没返回内容" in analyze_mod.analyze("报告", "kimi", ask=fake_ask("   "))


# ---------- 追加的那一段 ----------


def test_section_labels_itself_as_generated():
    """读者必须一眼看出哪些字是数据、哪些是解读。"""
    text = section("报告正文", "kimi", ask=fake_ask("分析"))

    assert "AI 分析" in text
    assert "kimi" in text
    assert prompt_hash() in text
    assert "不是数据本身" in text
    assert text.rstrip().endswith("分析")


# ---------- 入口 ----------


def _write_runs(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "alpha_run_001.json").write_text(
        json.dumps(RECORD, ensure_ascii=False), encoding="utf-8"
    )


def test_main_prints_the_section(tmp_path, monkeypatch, capsys):
    _write_runs(tmp_path)
    monkeypatch.setattr(analyze_mod, "_ask", fake_ask("这批数据看不出差别。"))

    assert main([str(tmp_path), "--model", "kimi"]) == 0

    out = capsys.readouterr().out
    assert "AI 分析" in out
    assert "看不出差别" in out


def test_main_errors_on_an_empty_directory(tmp_path, capsys):
    assert main([str(tmp_path), "--model", "kimi"]) == 1
    assert "没有 run 记录" in capsys.readouterr().err


def test_main_rejects_a_broken_baseline(tmp_path, capsys):
    """基线读不了就要退出 —— 别在「看不到对比」的前提下假装什么都看到了。"""
    _write_runs(tmp_path / "run")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")

    code = main([str(tmp_path / "run"), "--model", "kimi", "--baseline", str(baseline)])

    assert code == 1
    assert "notes.json" in capsys.readouterr().err


def test_main_refuses_to_pick_a_model(tmp_path):
    """解读用哪个模型也得显式给 —— 免得默认填空。"""
    with pytest.raises(SystemExit):
        main([str(tmp_path)])

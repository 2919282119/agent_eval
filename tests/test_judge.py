"""judge 这一步的离线测试 —— 一次 API 都不调。

判官模型那一层（`judge._ask`）是可替换的：所有用例都注入假的 `ask`，验的是
「state 组装对不对、回答解析严不严、失败隔不隔离、写回会不会污染确定性记录」。
"""

import json

import pytest

import agenteval.judge as judge
from agenteval.judge import (
    build_state,
    judge_one,
    main,
    parse_reply,
    prompt_hash,
    run_jsons,
    write_back,
)


class FakeUsage:
    prompt_tokens = 1234
    completion_tokens = 56


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = FakeUsage()


def fake_ask(content):
    return lambda state, model: FakeResponse(content)

GOOD_REPLY = json.dumps(
    {
        "groundedness": {"value": 0.9, "detail": "回答里的改动都能在结果里找到"},
        "claims_consistent": {"value": 1.0, "detail": "声称的动作都在日志里"},
    },
    ensure_ascii=False,
)

RECORD = {
    "task_id": "alpha",
    "run_id": "run_001",
    "instruction": "修一下 calc.py 的空列表崩溃",
    "status": "success",
    "failures": [],
    "evaluation": {
        "task_success": True,
        "correctness": 1.0,
        "completeness": 1.0,
        "tool_usage": 1.0,
        "groundedness": None,
        "error_recovery": None,
    },
}

SIDECAR = {
    "final_answer": "已修复，测试全部通过。",
    "calls": [
        {"name": "read_file", "arguments": {"path": "calc.py"}, "ok": True, "error": None,
         "result": "def average(xs): ..."},
        {"name": "edit_file", "arguments": {"path": "calc.py"}, "ok": True, "error": None,
         "result": "修改文件成功: calc.py"},
    ],
}


# ---------- state 组装 ----------


def test_build_state_keeps_only_what_the_judge_needs():
    """只送四样东西。完整 `state.messages` 又长又杂（系统提示、每次读文件的全文），
    既超上下文又降低判断质量。"""
    state = build_state(RECORD, SIDECAR)

    assert state["task"] == "修一下 calc.py 的空列表崩溃"
    assert state["deterministic_result"] == {"task_success": True, "failures": []}
    assert state["final_answer"] == "已修复，测试全部通过。"
    assert [call["name"] for call in state["tool_calls"]] == ["read_file", "edit_file"]


def test_build_state_drops_arguments_and_keeps_result():
    """`arguments` 不送 —— `write_file` 的 arguments 里是整个文件内容，会把 state 撑爆。
    要认「改了哪个文件」，看工具结果就够（miniCC 会回「修改文件成功: calc.py」）。"""
    state = build_state(RECORD, SIDECAR)

    assert "arguments" not in state["tool_calls"][0]
    assert state["tool_calls"][0]["result"] == "def average(xs): ..."


def test_build_state_tolerates_a_run_without_a_sidecar():
    """sidecar 丢了也要能组出 state（只是内容少），不能崩。"""
    state = build_state({"task_id": "a", "evaluation": None}, {})

    assert state["tool_calls"] == []
    assert state["final_answer"] == ""
    assert state["task"] == ""


# ---------- 回答解析 ----------


def test_parse_reply_reads_a_clean_json():
    results = parse_reply(GOOD_REPLY)

    assert results["groundedness"]["value"] == 0.9
    assert "结果里找到" in results["groundedness"]["detail"]


def test_parse_reply_survives_markdown_fences():
    """模型经常把 JSON 包在 ``` 里，或者在前后加一句话。"""
    assert parse_reply(f"```json\n{GOOD_REPLY}\n```")["groundedness"]["value"] == 0.9
    assert parse_reply(f"好的，这是我的判断：\n{GOOD_REPLY}")["claims_consistent"]["value"] == 1.0


@pytest.mark.parametrize(
    "reply",
    [
        "完全不是 JSON",
        json.dumps({"groundedness": {"value": 0.9}}),  # 缺 claims_consistent
        json.dumps({"groundedness": {"value": "高"}, "claims_consistent": {"value": 1}}),
        json.dumps({"groundedness": {"value": 1.5}, "claims_consistent": {"value": 1}}),
        json.dumps({"groundedness": {"value": True}, "claims_consistent": {"value": 1}}),
        json.dumps({"groundedness": 0.9, "claims_consistent": {"value": 1}}),
    ],
)
def test_parse_reply_refuses_to_invent_a_score(reply):
    """**允许它没判出来，不允许它编一个分数。** 缺键、值不是数字、超出 [0,1] 全算失败。"""
    with pytest.raises(ValueError):
        parse_reply(reply)


# ---------- 失败隔离 ----------


def test_judge_one_fills_results_on_success():
    verdict = judge_one(RECORD, SIDECAR, "kimi", ask=fake_ask(GOOD_REPLY))

    assert verdict["status"] == "success"
    assert verdict["model"] == "kimi"
    assert verdict["prompt_hash"] == prompt_hash()
    assert verdict["usage"] == {"input_tokens": 1234, "output_tokens": 56}
    assert verdict["results"]["claims_consistent"]["value"] == 1.0


def test_judge_one_swallows_a_failing_model_call():
    """判官挂了（超时、key 不对、模型名写错）只记 `status=error`，不往外抛。"""
    def boom(state, model):
        raise TimeoutError("judge 超时")

    verdict = judge_one(RECORD, SIDECAR, "kimi", ask=boom)

    assert verdict["status"] == "error"
    assert "TimeoutError" in verdict["error"]
    assert verdict["results"] is None


def test_judge_one_swallows_a_garbage_reply():
    verdict = judge_one(RECORD, SIDECAR, "kimi", ask=fake_ask("我不知道"))

    assert verdict["status"] == "error"
    assert "判官没返回 JSON 对象" in verdict["error"]


# ---------- 写回 ----------


def test_write_back_fills_groundedness_and_keeps_everything_else(tmp_path):
    path = tmp_path / "alpha_run_001.json"
    write_back(
        path,
        json.loads(json.dumps(RECORD)),
        judge_one(RECORD, SIDECAR, "kimi", ask=fake_ask(GOOD_REPLY)),
    )
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["judge"]["primary"] == "llm"
    assert saved["judge"]["llm"]["status"] == "success"
    # 报告读的还是 evaluation.groundedness —— 填上它，报告的读取路径不用改
    assert saved["evaluation"]["groundedness"] == 0.9

    # 除了 `judge` 和它填的那一项，其余字段一字未动
    expected = json.loads(json.dumps(RECORD))
    expected["evaluation"]["groundedness"] = 0.9
    assert saved == {**expected, "judge": saved["judge"]}


def test_write_back_leaves_groundedness_null_when_the_judge_failed(tmp_path):
    """判失败时 `groundedness` 必须保持 `null` —— 不能让「没判」变成「零分」。"""
    def boom(state, model):
        raise RuntimeError("挂了")

    path = tmp_path / "alpha_run_001.json"
    record = json.loads(json.dumps(RECORD))
    write_back(path, record, judge_one(RECORD, SIDECAR, "kimi", ask=boom))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["judge"]["llm"]["status"] == "error"
    assert saved["evaluation"]["groundedness"] is None


def test_write_back_keeps_other_providers(tmp_path):
    """`judge` 按 provider 分键 —— 再判一次不能把上一次的结果顶掉。"""
    path = tmp_path / "r.json"
    record = {"evaluation": {"groundedness": None}, "judge": {"jev": {"status": "success"}}}

    write_back(path, record, judge_one({"evaluation": {}}, {}, "kimi", ask=fake_ask(GOOD_REPLY)))

    assert set(record["judge"]) == {"primary", "jev", "llm"}


# ---------- prompt 版本 ----------


def test_prompt_hash_is_stable_and_changes_with_the_questions():
    """问题措辞是评估逻辑的一部分：改了它，两批判出来的分数就不可比。"""
    before = prompt_hash()
    assert prompt_hash() == before

    original = judge._QUESTIONS["groundedness"]
    judge._QUESTIONS["groundedness"] = original + "（改一下）"
    try:
        assert prompt_hash() != before
    finally:
        judge._QUESTIONS["groundedness"] = original


# ---------- 入口 ----------


def test_run_jsons_skips_sidecars(tmp_path):
    (tmp_path / "a_run_001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a_run_001.calls.json").write_text("{}", encoding="utf-8")
    (tmp_path / "report.txt").write_text("报告", encoding="utf-8")

    assert [p.name for p in run_jsons(tmp_path)] == ["a_run_001.json"]


def test_main_judges_every_run_and_reports_the_tally(tmp_path, monkeypatch, capsys):
    for name in ("alpha_run_001", "beta_run_001"):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({**RECORD, "task_id": name.split("_")[0]}), encoding="utf-8"
        )
        (tmp_path / f"{name}.calls.json").write_text(json.dumps(SIDECAR), encoding="utf-8")
    monkeypatch.setattr(judge, "_ask", fake_ask(GOOD_REPLY))
    monkeypatch.setattr(judge, "load_mini_cc_env", lambda: None)

    assert main([str(tmp_path), "--model", "kimi"]) == 0

    out = capsys.readouterr().out
    assert "判了 2 条，失败 0 条" in out
    assert json.loads((tmp_path / "alpha_run_001.json").read_text(encoding="utf-8"))["judge"]


def test_main_returns_1_when_every_judgement_failed(tmp_path, monkeypatch, capsys):
    """全失败多半是 key / 模型名 / 网络的问题，退出码要能反映 —— 脚本里看得见。"""
    (tmp_path / "alpha_run_001.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (tmp_path / "alpha_run_001.calls.json").write_text(
        json.dumps(SIDECAR, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(judge, "load_mini_cc_env", lambda: None)

    def boom(state, model):
        raise RuntimeError("没有 key")

    monkeypatch.setattr(judge, "_ask", boom)

    assert main([str(tmp_path), "--model", "kimi"]) == 1
    assert "判了 0 条，失败 1 条" in capsys.readouterr().out


def test_main_refuses_to_pick_a_judge_model(tmp_path, capsys):
    """`--model` 必填：拿被测模型判自己是有偏的，不该默默给个默认值。"""
    with pytest.raises(SystemExit):
        main([str(tmp_path)])


def test_main_errors_on_an_empty_directory(tmp_path, capsys):
    assert main([str(tmp_path), "--model", "kimi"]) == 1
    assert "没有 run 记录" in capsys.readouterr().err


# ---------- 空回答：跳过，不给分 ----------


def test_a_run_with_no_final_answer_is_skipped_without_calling_the_model():
    """空回答没有可判的东西 —— 那时候**不能给分**，也不该白花一次 API 钱。

    之前 prompt 里那句「拿不准就给中间值」让模型给空回答打了 0.5，一个凭空编的
    数字混进了两个维度的平均（实测 12 题 × k=3 里 4 条，把 Groundedness 从 92%
    拖到 88%）。空回答多半是 agent 撞了 miniCC 的循环上限，压根不是「没答好」。
    """
    calls = []

    def should_not_be_called(state, model):
        calls.append(state)
        return FakeResponse(GOOD_REPLY)

    verdict = judge_one(
        RECORD, {"final_answer": "   \n\n", "calls": []}, "kimi", ask=should_not_be_called
    )

    assert verdict["status"] == "skipped"
    assert verdict["results"] is None
    assert "最终回答为空" in verdict["reason"]
    assert calls == []


def test_a_missing_sidecar_is_also_skipped():
    """sidecar 丢了 → 既没有回答也没有调用日志，同样没有可判的。"""
    verdict = judge_one(RECORD, {}, "kimi", ask=fake_ask(GOOD_REPLY))

    assert verdict["status"] == "skipped"


def test_write_back_leaves_groundedness_null_when_skipped(tmp_path):
    """跳过跟失败一样：`groundedness` 保持 `null`，不能变成 0 分。"""
    path = tmp_path / "alpha_run_001.json"
    record = json.loads(json.dumps(RECORD))
    write_back(path, record, judge_one(RECORD, {}, "kimi", ask=fake_ask(GOOD_REPLY)))

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["judge"]["llm"]["status"] == "skipped"
    assert saved["evaluation"]["groundedness"] is None


def test_main_tallies_skipped_runs_separately(tmp_path, monkeypatch, capsys):
    (tmp_path / "alpha_run_001.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (tmp_path / "alpha_run_001.calls.json").write_text(
        json.dumps({**SIDECAR, "final_answer": ""}), encoding="utf-8"
    )
    monkeypatch.setattr(judge, "_ask", fake_ask(GOOD_REPLY))
    monkeypatch.setattr(judge, "load_mini_cc_env", lambda: None)

    # 一条都没判成 → 退出码要能反映（多半是 key / 模型名的问题）
    assert main([str(tmp_path), "--model", "kimi"]) == 1
    assert "跳过 1 条（最终回答为空" in capsys.readouterr().out


# ---------- 解析失败：换句话重试 ----------


def test_judge_retries_once_with_a_reworded_prompt():
    """一个字符的笔误不该让整条记录掉出统计。

    实测有过模型把键名打成 `groundroundedness`。重试必须**换个说法** ——
    judge 的 temperature 是 0，原样再问一遍拿回的多半是同一个坏回答。
    """
    seen = []
    replies = iter(["这不是 JSON", GOOD_REPLY])

    def flaky(state, model):
        seen.append(state)
        return FakeResponse(next(replies))

    verdict = judge_one(RECORD, SIDECAR, "kimi", ask=flaky)

    assert verdict["status"] == "success"
    assert verdict["results"]["groundedness"]["value"] == 0.9
    assert len(seen) == 2
    assert "repair" not in seen[0]
    assert "repair" in seen[1]


def test_judge_gives_up_after_attempts():
    """重试也有上限 —— 系统性地答不对就别再烧钱了。"""
    calls = []

    def always_bad(state, model):
        calls.append(state)
        return FakeResponse("不是 JSON")

    verdict = judge_one(RECORD, SIDECAR, "kimi", ask=always_bad)

    assert verdict["status"] == "error"
    assert verdict["results"] is None
    assert len(calls) == judge.ATTEMPTS
    assert f"问了 {judge.ATTEMPTS} 次" in verdict["error"]

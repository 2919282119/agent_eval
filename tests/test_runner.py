import inspect
import os
import shutil
from pathlib import Path

import agent.agent as A
import pytest

from agenteval.metrics import blank_evaluation
from agenteval.runner import AgentOutcome, drive_agent, run_task
from agenteval.task import load_task
from agenteval.trajectory import LlmStats

TASK_YAML = """\
task_id: hello_001
category: smoke
instruction: |
  在当前目录下创建 hello.txt，内容为一行 hi。做完告诉我。
"""

VERIFY = """\
from pathlib import Path
from agenteval.task import Check

def check(workspace):
    target = Path(workspace) / "hello.txt"
    exists = target.exists()
    has_hi = exists and "hi" in target.read_text(encoding="utf-8")
    return [
        Check("hello.txt 已创建", exists, weight="essential"),
        Check("内容包含 hi", has_hi, weight="important"),
        Check("没有多余文件", not (Path(workspace) / "junk.txt").exists(), weight="minor"),
    ]
"""


def make_task(tmp_path):
    task_dir = tmp_path / "hello_task"
    (task_dir / "workspace").mkdir(parents=True)
    (task_dir / "task.yaml").write_text(TASK_YAML, encoding="utf-8")
    (task_dir / "verify.py").write_text(VERIFY, encoding="utf-8")
    return load_task(task_dir)


@pytest.mark.integration
def test_drive_agent_runs_in_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "seed.py").write_text("x = 1\n", encoding="utf-8")

    cwd_before = os.getcwd()
    call_llm_before = A.call_llm
    load_cc_md_before = A.load_cc_md

    outcome = drive_agent(
        "在当前目录下创建 hello.txt，内容为一行 hi。做完告诉我。",
        workspace,
    )

    assert outcome.error is None

    # token / latency 采集（miniCC 自己完全不采集这些）
    assert outcome.stats.llm_calls >= 1
    assert outcome.stats.tokens > 0
    assert outcome.stats.latency_ms > 0
    assert outcome.stats.model_actual
    # 峰值 prompt_tokens —— max_usage_ratio 就是拿它算的
    assert outcome.stats.max_prompt_tokens > 0
    # 挂在 ContextManager 实例上的压缩计数 wrapper 跑通了（100 万窗口的题压不到 0.8）
    assert outcome.compactions == 0

    # messages 就是完整 trajectory：user 开头，后面是 assistant / tool 交替
    assert outcome.messages[0]["role"] == "user"
    assert any(m.get("role") == "assistant" for m in outcome.messages)

    # 真的在 workspace 里动的手，没有跑到别处去
    assert (workspace / "hello.txt").exists()

    # chdir 与两个 patch 都已还原
    assert os.getcwd() == cwd_before
    assert A.call_llm is call_llm_before
    assert A.load_cc_md is load_cc_md_before


@pytest.mark.integration
def test_run_task_end_to_end(tmp_path):
    """真 agent + 真 verifier + 真算分，走完整条链。"""
    task = make_task(tmp_path)

    result = run_task(task, run_idx=1)
    record = result.record

    assert record["task_id"] == "hello_001"
    assert record["run_id"] == "run_001"
    assert record["agent_version"]
    assert record["model"]
    assert record["model_actual"]
    assert record["status"] in {"success", "failed", "vetoed"}
    assert record["error"] is None

    assert record["trajectory"]["tokens"] > 0
    assert record["trajectory"]["llm_calls"] >= 1
    assert record["trajectory"]["steps"] >= 2

    # 行为指纹与负载指标（顶层，不进 trajectory 的 5 字段 schema）
    assert record["tools_used"] == sorted(set(record["tools_used"]))
    assert record["tools_used"], "至少跑过一个工具"
    assert record["max_usage_ratio"] > 0
    assert record["compactions"] == 0

    evaluation = record["evaluation"]
    assert evaluation["task_success"] is True
    assert evaluation["groundedness"] is None  # v1 不产出
    assert record["failures"] == []

    # sidecar 里有逐次工具调用明细 + 最终回答，能用来事后核对失败标签
    assert result.calls
    assert {"name", "arguments", "ok", "error", "result"} == set(result.calls[0])
    assert result.final_answer


def test_crashing_verifier_becomes_an_error_record(tmp_path, monkeypatch):
    """验收程序抛异常必须变成 status="error" 的记录，不能冲出去。

    冲出去的后果不是丢一次 run —— 是**整批 run 全没**：异常一路穿过
    `run_task` / `cli.main`，没有一层接得住。而验收读的是 agent 改过的产物，
    抛异常随时可能发生（实测：删掉一个调用点文件就让 `extract_helper_013`
    的验收抛 FileNotFoundError）。

    `drive_agent` 早就防了这一层（agent 崩了要记成 error），验收这层原先没有。

    离线用例：把真 agent 和真验收都换掉，不花 API。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1)
        ),
    )

    def boom(task, workspace):
        raise FileNotFoundError("billing.py")

    monkeypatch.setattr(runner, "run_verifier", boom)

    record = run_task(task, run_idx=1).record

    assert record["status"] == "error"
    assert "验收程序异常" in record["error"]
    assert "FileNotFoundError" in record["error"]
    # 跟 agent 崩溃一个待遇：所有维度为 null，不进任何统计
    assert record["evaluation"] == blank_evaluation()
    assert record["failures"] == []


def test_run_task_never_lets_an_exception_escape(tmp_path, monkeypatch):
    """**任何**漏网的异常都要降级成 error 记录。

    冲出 `cli.main` 的代价不是丢一次 run，是整批 run 全没 —— 而 `run_task` 里
    除了已经单独兜住的验收，还有 copytree、drive_agent、trajectory.build、
    写记录本身。这里拿最凶的一种试：崩的就是 `trajectory.build`。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1)
        ),
    )

    def exploding_build(messages, stats):
        raise RuntimeError("轨迹解析炸了")

    monkeypatch.setattr(runner.trajectory, "build", exploding_build)

    record = run_task(task, run_idx=1).record

    assert record["status"] == "error"
    assert "runner 异常" in record["error"]
    assert "轨迹解析炸了" in record["error"]
    assert record["evaluation"] == blank_evaluation()


def test_error_run_keeps_its_workspace_for_postmortem(tmp_path, monkeypatch):
    """崩掉的 run 要留住现场，事后才查得出它崩前动过什么。

    代价是每崩一次多留一个临时目录 —— 所以路径写进记录的 `error` 字段，
    不让它变成「悄悄漏在 %TEMP% 里的目录」。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1), error="agent 崩了"
        ),
    )

    record = run_task(task, run_idx=1).record

    assert record["status"] == "error"
    saved = Path(record["error"].split("现场保留在 ")[1].rstrip("）"))
    try:
        assert saved.is_dir(), "error run 的工作区被删了，现场就没了"
    finally:
        shutil.rmtree(saved, ignore_errors=True)


def test_record_carries_the_temperature_minicc_actually_uses(tmp_path, monkeypatch):
    """温度是实验条件，必须进记录 —— 而且不能硬编码。

    eval **不设置**温度（`counting_call_llm` 原样转发），所以有效值就是 miniCC 的
    默认值。这里跟 miniCC 的签名直接对，而不是跟 `temperature_default()` 对 ——
    后者是自我印证，签名才是事实来源。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1)
        ),
    )

    expected = inspect.signature(A.call_llm).parameters["temperature"].default
    record = run_task(task, run_idx=1).record

    assert record["temperature"] == expected


def test_temperature_is_null_when_minicc_does_not_expose_it(monkeypatch):
    """`call_llm` 又把温度写死时记 null —— 「无从得知」，不能编一个数字顶上。"""
    import agenteval.runner as runner

    monkeypatch.setattr(
        A, "call_llm", lambda config, messages, tools=None: None
    )

    assert runner.temperature_default() is None

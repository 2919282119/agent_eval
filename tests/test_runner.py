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

    # 数据质量字段：没被切断、工作区没被破坏
    assert record["truncated"] is False
    assert record["missing_initial_files"] == []

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


# ---------- 没跑完的 run ----------


def test_hit_loop_cap_needs_both_the_cap_and_an_unfinished_last_turn():
    """判断被切断的两个条件，缺一不可。

    只看「调用次数到顶」会误标恰好用满上限、但正常收尾的 run；两个一起看才准 ——
    `agent_loop` 正常收尾是「这次回答没有工具调用」→ `return`，被切断是 `break`，
    最后一轮的工具结果 agent 根本没机会看到。
    """
    from agenteval.runner import _hit_loop_cap

    limit = A.MAX_LOOP_CNT
    unfinished = [{"role": "user"}, {"role": "assistant", "tool_calls": [{"id": "x"}]}]
    finished = [{"role": "user"}, {"role": "assistant", "tool_calls": []}]

    assert _hit_loop_cap(unfinished, limit) is True
    assert _hit_loop_cap(unfinished, limit - 1) is False
    assert _hit_loop_cap(finished, limit) is False
    assert _hit_loop_cap([], limit) is False


def test_hit_loop_cap_is_false_when_minicc_does_not_expose_the_limit(monkeypatch):
    """上限读不到就当没被切断 —— 猜一个数字顶上会误标。"""
    from agenteval.runner import _hit_loop_cap

    monkeypatch.setattr(A, "MAX_LOOP_CNT", None)

    assert _hit_loop_cap([{"role": "assistant", "tool_calls": [{"id": "x"}]}], 999) is False


def test_record_flags_a_truncated_run(tmp_path, monkeypatch):
    """被切断的 run 产物可能照样过验收 —— 但「没来得及收尾」必须留在记录里。"""
    import agenteval.runner as runner

    task = make_task(tmp_path)
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1), truncated=True
        ),
    )

    assert run_task(task, run_idx=1).record["truncated"] is True


# ---------- 工作区被破坏 ----------


def test_workspace_damage_is_recorded(tmp_path, monkeypatch):
    """初始工作区里有、跑完不见了的文件 —— 硬事实，报告据此给标签打「仅供参考」。

    实测 `top_words_005/run_003`：`rm … ; cat wordcount.py` 在 cmd.exe 下被拼成一条
    rm 命令，把工作区里的 `wordcount.py` 删了；agent 只能用 write_file 重写 →
    触发 WRONG_TOOL。标签没说错，但成因是环境。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    (task.workspace / "seed.py").write_text("x = 1\n", encoding="utf-8")

    def damage(instruction, workspace, model=None):
        (Path(workspace) / "seed.py").unlink()
        return AgentOutcome([], LlmStats(latency_ms=1))

    monkeypatch.setattr(runner, "drive_agent", damage)

    assert run_task(task, run_idx=1).record["missing_initial_files"] == ["seed.py"]


def test_pycache_is_not_workspace_damage(tmp_path, monkeypatch):
    """agent 清 `__pycache__` 是常见的收尾动作，不是「工作区被破坏」。

    不排除它的话，`module_contracts_012` 的初始工作区里就带着 4 个 .pyc ——
    任何一次正常清理都会被误报成环境事故。
    """
    import agenteval.runner as runner

    task = make_task(tmp_path)
    cache = task.workspace / "__pycache__"
    cache.mkdir()
    (cache / "seed.cpython-311.pyc").write_text("x", encoding="utf-8")

    def clean(instruction, workspace, model=None):
        shutil.rmtree(Path(workspace) / "__pycache__")
        return AgentOutcome([], LlmStats(latency_ms=1))

    monkeypatch.setattr(runner, "drive_agent", clean)

    assert run_task(task, run_idx=1).record["missing_initial_files"] == []


def test_error_runs_do_not_report_workspace_damage(tmp_path, monkeypatch):
    """error 的 run 工作区可能是半成品，报「文件全丢了」是误导。"""
    import agenteval.runner as runner

    task = make_task(tmp_path)
    (task.workspace / "seed.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "drive_agent",
        lambda instruction, workspace, model=None: AgentOutcome(
            [], LlmStats(latency_ms=1), error="agent 崩了"
        ),
    )

    record = run_task(task, run_idx=1).record

    assert record["status"] == "error"
    assert record["missing_initial_files"] == []
    shutil.rmtree(Path(record["error"].split("现场保留在 ")[1].rstrip("）")), ignore_errors=True)


def test_posix_separator_calls_counts_bash_commands_with_a_semicolon():
    """`;` 是**底数**不是标志 —— 36 个真实 run 里 27 个都含它。

    记它只是为了给「工作区被误删」一个解释：Windows 上 cmd.exe 不把 `;` 当分隔符，
    每次含 `;` 的调用都是一次误伤的机会。
    """
    from agenteval.runner import _posix_separator_calls
    from agenteval.trajectory import ToolCall

    traj = type(
        "T",
        (),
        {
            "calls": [
                ToolCall("bash", {"command": "pwd; ls"}, True),
                ToolCall("bash", {"command": "ls"}, True),
                ToolCall("read_file", {"path": "a;b.py"}, True),  # 别的工具不算
            ]
        },
    )()

    assert _posix_separator_calls(traj) == 1
    assert _posix_separator_calls(type("T", (), {"calls": []})()) == 0

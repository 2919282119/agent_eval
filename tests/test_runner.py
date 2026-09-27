import os

import agent.agent as A
import pytest

from agenteval.runner import drive_agent, run_task
from agenteval.task import load_task

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
    assert {"name", "arguments", "ok", "error"} == set(result.calls[0])
    assert result.final_answer

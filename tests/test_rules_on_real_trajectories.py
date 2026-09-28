"""拿真实 run 的轨迹回放失败标签 —— 规则的回归护栏。

为什么必须有这个：失败标签的准确性**只能拿真实数据验**。曾经 8 个真实 run 报出 7 条
标签，人工逐条核对 sidecar 后发现 **6 条是假阳性**（agent 明明真验证过、真做对了，
却被判违规）。单元测试拦不住这类错 —— 写用例时脑子里想的场景，和 agent 实际会做的
事不一样。所以把真实轨迹冻成 fixture，以后一改规则就在这里立刻见分晓。

回放**不需要重跑 API**：sidecar 里的 `calls`（含 ok/error）+ `final_answer` 就是
`metrics.evaluate` 判定失败标签所需的全部输入。这里直接从 sidecar 重建 Trajectory。

任务配置（forbidden_tools / max_tool_calls / initial_files）从**活的** `tasks/<id>/`
读，而不是快照进 fixture —— 规则一改、或题目配置一改，测试立刻报错，正是要的信号。
"""

import json
from pathlib import Path

import pytest

from agenteval.metrics import evaluate
from agenteval.task import Check, load_task, workspace_files
from agenteval.trajectory import LlmStats, ToolCall, Trajectory

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RUNS_DIR = FIXTURES / "runs"
TASKS_DIR = ROOT / "tasks"

EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["runs"]


def sidecar_paths():
    return sorted(RUNS_DIR.glob("*/*.calls.json"))


def fixture_key(path: Path) -> str:
    return f"{path.parent.name}/{path.name[: -len('.calls.json')]}"


def replay(path: Path, expected: dict):
    task = load_task(TASKS_DIR / expected["task_id"])
    sidecar = json.loads(path.read_text(encoding="utf-8"))

    traj = Trajectory(
        messages=[],
        stats=LlmStats(),
        calls=[
            ToolCall(c["name"], c["arguments"], c["ok"], c["error"], c.get("result") or "")
            for c in sidecar["calls"]
        ],
        steps=0,
        first_action=None,
        final_answer=sidecar["final_answer"],
    )
    # INCOMPLETE 依赖验收结论，而 sidecar 里没有它 —— 补上该 run 当时的验收结果。
    # 其余五个标签全都只看 calls / final_answer，是真正的回放。
    checks = [Check("（回放）该 run 的验收结论", expected["checks_passed"], weight="essential")]

    return evaluate(traj, checks, task, workspace_files(task))


@pytest.mark.parametrize("path", sidecar_paths(), ids=fixture_key)
def test_every_fixture_has_a_hand_checked_expectation(path):
    """新加的 sidecar 必须在 expected.json 里登记 —— 否则它只是被静默跳过。"""
    assert fixture_key(path) in EXPECTED, f"{fixture_key(path)} 没有核对过的期望值"


@pytest.mark.parametrize("path", sidecar_paths(), ids=fixture_key)
def test_failure_tags_match_hand_checked_expectation(path):
    expected = EXPECTED[fixture_key(path)]

    result = replay(path, expected)

    assert sorted(result.failures) == sorted(expected["expected_failures"])
    # 维度分也要钉住：`error_recovery` 的语义改过（bash 退出码不算工具故障），
    # 而它不出现在失败标签里 —— 只断言标签会漏掉这一整块。
    assert result.evaluation["error_recovery"] == expected["expected_error_recovery"]


def test_fixtures_still_pin_down_known_false_positives():
    """这组 fixture 存在的理由就是那批假阳性。

    如果有一天它们只剩「本来就干净」的轨迹，回放就退化成空跑 —— 这个断言防的就是那个。
    """
    superseded = [
        key
        for key, entry in EXPECTED.items()
        if entry["recorded_failures"] and not entry["expected_failures"]
    ]

    assert superseded, "fixture 里必须保留当时被误报的轨迹，否则护栏失效"

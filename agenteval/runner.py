"""唯一与 miniCC 耦合的模块。

进程内驱动：os.chdir(workspace) + patch agent.agent 的两个模块级全局量。
**串行跑安全，并行跑不安全** —— patch 是模块级全局状态，见 CLAUDE.md「架构」。
"""

import functools
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import agent.agent as A
from agent.context import ContextManager
from agent.session import AgentState
from dotenv import load_dotenv
from llm.model import DEFAULT_MODEL, MODELS
from tools.setup import tools_setup

from agenteval import trajectory
from agenteval.metrics import blank_evaluation, evaluate
from agenteval.task import run_verifier, workspace_files
from agenteval.trajectory import LlmStats

MINI_CC_ROOT = Path(A.__file__).resolve().parents[1]


def _remove_even_if_readonly(func, path, _exc_info):
    """Windows 上只读文件会让 rmtree 半途而废（PermissionError WinError 5）。

    `readonly_config_011` 的陷阱正是只读文件：agent 没解开属性的话工作区就删不掉，
    而 `ignore_errors=True` 会让这件事**静默**发生 —— 每跑一次漏一个临时目录。
    注意 `ignore_errors=True` 会覆盖 `onerror`，所以只能二选一。
    """
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def load_mini_cc_env() -> None:
    """显式加载 miniCC 的 .env。

    miniCC 里的 load_dotenv() 是 cwd 敏感的：从本仓库目录 import 时它找不到自己的
    .env，四个 API key 全部缺失。不改 miniCC 的代码，只能在这边补上。
    load_dotenv 默认不覆盖已存在的环境变量。
    """
    load_dotenv(MINI_CC_ROOT / ".env")


@functools.cache
def agent_version() -> str:
    """miniCC 的代码版本，用于「对比不同版本」。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(MINI_CC_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


@dataclass
class AgentOutcome:
    messages: list
    stats: LlmStats
    error: str | None = None
    # 真正压缩过几次（compact 返回 True 才算 —— auto_compact 会被调用但可能
    # 因为历史不足 10 条而没压成）
    compactions: int = 0


@dataclass
class RunResult:
    """一次 run 的产出。

    `record` 落进 <task_id>_<run_id>.json；
    `calls` 与 `final_answer` 落进 sidecar —— 失败标签判得对不对全靠它们事后核对
    （`CLAIMS_WITHOUT_ACTION` 就是按回答措辞判的），放进 record 会破坏
    trajectory 那 5 个字段的 schema。
    """

    record: dict
    calls: list[dict]
    final_answer: str


def drive_agent(instruction, workspace, model: str = DEFAULT_MODEL) -> AgentOutcome:
    """在 workspace 里跑一次 agent_loop。

    异常不往外抛：agent 崩溃要变成 `status="error"` 的记录，而不是让整轮评估挂掉。
    """
    load_mini_cc_env()

    stats = LlmStats()
    compactions = 0
    original_call_llm = A.call_llm
    original_load_cc_md = A.load_cc_md

    def counting_call_llm(config, messages, tools=None):
        response = original_call_llm(config, messages, tools=tools)
        stats.llm_calls += 1
        if response.usage is not None:
            stats.tokens += response.usage.total_tokens
            stats.max_prompt_tokens = max(
                stats.max_prompt_tokens, response.usage.prompt_tokens
            )
        stats.model_actual = response.model
        return response

    state = AgentState(
        session_id=str(uuid.uuid4()),
        messages=[{"role": "user", "content": instruction}],
        cwd=str(workspace),
        model=model,
    )

    cwd_before = os.getcwd()
    started = time.perf_counter()
    error = None

    A.call_llm = counting_call_llm
    A.load_cc_md = lambda: ""

    try:
        os.chdir(workspace)
        registry = tools_setup(model, str(workspace))
        context_manager = ContextManager(MODELS[model].context_window)
        # 在实例上挂计数 wrapper：auto_compact 走的是 self.compact（context.py:81），
        # 所以实例属性就能截住，不用改 miniCC 源码。不去扫 messages 里有多少条
        # `[Conversation Summary]` —— 重复压缩会把旧摘要再摘一次，那样会少数。
        original_compact = context_manager.compact

        def counting_compact(agent_state):
            nonlocal compactions
            compacted = original_compact(agent_state)
            if compacted:
                compactions += 1
            return compacted

        context_manager.compact = counting_compact
        A.agent_loop(
            state,
            registry,
            context_manager,
            memory_manager=None,
            verbose=False,
            permission_mode="auto",
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        stats.latency_ms = int((time.perf_counter() - started) * 1000)
        os.chdir(cwd_before)
        A.call_llm = original_call_llm
        A.load_cc_md = original_load_cc_md

    return AgentOutcome(
        messages=state.messages, stats=stats, error=error, compactions=compactions
    )


def run_task(task, run_idx, model: str = DEFAULT_MODEL) -> RunResult:
    """跑一次任务，返回 RunResult（落盘记录 + 工具调用明细）。

    `status` 的四种取值：
      success / failed —— agent 跑完了，由验收结果决定
      vetoed           —— bash 命令命中危险规则，总分归零
      error            —— runner 自身异常（agent 崩溃 / 超时），不进任何统计
    """
    workspace = Path(tempfile.mkdtemp(prefix=f"agenteval_{task.task_id}_"))
    try:
        shutil.copytree(task.workspace, workspace, dirs_exist_ok=True)
        outcome = drive_agent(task.instruction, workspace, model)
        traj = trajectory.build(outcome.messages, outcome.stats)

        if outcome.error is not None:
            evaluation = blank_evaluation()
            failures = []
            status = "error"
        else:
            checks = run_verifier(task, workspace)
            result = evaluate(traj, checks, task, workspace_files(task))
            evaluation = result.evaluation
            failures = result.failures
            if result.vetoed:
                status = "vetoed"
            elif evaluation["task_success"]:
                status = "success"
            else:
                status = "failed"

        return RunResult(
            record={
                "task_id": task.task_id,
                "run_id": f"run_{run_idx:03d}",
                "agent_version": agent_version(),
                "model": model,
                "model_actual": outcome.stats.model_actual,
                "first_action": traj.first_action,
                # 行为指纹：first_action 的「一般化形式」，让报告不必读 sidecar
                # 就知道它用没用 run_subagent
                "tools_used": sorted(set(traj.tools_used())),
                # 负载指标，不是评估信号：离压缩线（0.8）还有多远、真压了几次。
                # 用配置里的 context_window 归一化 —— 比裸 prompt_tokens 更能跨模型比。
                "max_usage_ratio": round(
                    outcome.stats.max_prompt_tokens / MODELS[model].context_window, 4
                ),
                "compactions": outcome.compactions,
                "status": status,
                "error": outcome.error,
                "trajectory": traj.summary(),
                "evaluation": evaluation,
                "failures": failures,
            },
            calls=[asdict(call) for call in traj.calls],
            final_answer=traj.final_answer,
        )
    finally:
        shutil.rmtree(workspace, onerror=_remove_even_if_readonly)

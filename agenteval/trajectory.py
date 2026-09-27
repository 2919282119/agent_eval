"""state.messages → 轨迹结构与效率指标。不依赖 miniCC。"""

import json
from dataclasses import dataclass

EXPLORE_TOOLS = {"read_file", "list_dir", "glob", "grep"}
EDIT_TOOLS = {"write_file", "edit_file"}

# tool_call_id 有调用但没有对应结果 —— 轨迹被中断（run 崩了）
_NO_RESULT = "无返回结果（轨迹中断）"

# bash 返回码非 0 时给这个前缀的描述（见 _result_error）。
_COMMAND_FAILURE_PREFIX = "退出码 "

# miniCC 的 builtin 工具失败时**返回纯字符串**而不是 error dict（只有 bash 用
# returncode，未知工具 / 权限拒绝 / 执行异常用 error 字段）。所以这里必须靠前缀
# 把人话认成失败 —— 是启发式规则，不是精确判定。
# 刻意不含 grep 的「没有找到」：那是搜索成功但零结果，不是工具故障。
_TOOL_FAILURE_PREFIXES = (
    "文件不存在",
    "目录不存在",
    "路径不存在",
    "没有权限",
    "文件不是 UTF-8",
    "未找到要替换的内容",
    "读取文件失败",
    "写入文件失败",
    "修改文件失败",
    "列出目录失败",
    "搜索失败",
    "加载 Skill 失败",
)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    ok: bool
    error: str | None = None


@dataclass
class LlmStats:
    """runner 包装 call_llm 时采集的原始计数。"""

    llm_calls: int = 0
    tokens: int = 0
    latency_ms: int = 0
    model_actual: str | None = None
    # 峰值 prompt_tokens。miniCC 的 ContextManager 就是拿这个算 usage_ratio 决定要不要
    # 压缩的（agent/context.py:12-16, 78-81），所以它换算出的比例就是「离压缩线多远」。
    max_prompt_tokens: int = 0


@dataclass
class Trajectory:
    messages: list
    stats: LlmStats
    calls: list[ToolCall]
    steps: int
    first_action: str | None  # explore | edit | other | None（没有工具调用）
    final_answer: str  # agent 最后一条回答的正文；没有则空串

    def tools_used(self) -> list[str]:
        return [c.name for c in self.calls]

    def summary(self) -> dict:
        """落进 RunRecord 的 trajectory 字段。"""
        return {
            "steps": self.steps,
            "tool_calls": len(self.calls),
            "llm_calls": self.stats.llm_calls,
            "tokens": self.stats.tokens,
            "latency_ms": self.stats.latency_ms,
        }


def build(messages, stats: LlmStats) -> Trajectory:
    calls = _extract_calls(messages)
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    tool_count = sum(1 for m in messages if m.get("role") == "tool")

    return Trajectory(
        messages=messages,
        stats=stats,
        calls=calls,
        steps=assistant_count + tool_count,
        first_action=_first_action(calls),
        final_answer=final_answer(messages),
    )


def final_answer(messages) -> str:
    """agent 最后一条带正文的 assistant 消息。

    CLAIMS_WITHOUT_ACTION 这类标签是按回答的措辞判的，所以回答本身必须能拿到 ——
    它要落进 sidecar，否则失败标签事后没法核对。
    """
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return message["content"]
    return ""


def _extract_calls(messages) -> list[ToolCall]:
    results = {
        m.get("tool_call_id"): _result_error(m.get("content"))
        for m in messages
        if m.get("role") == "tool"
    }

    calls = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            call_id = raw_call.get("id")

            if call_id not in results:
                ok, error = False, _NO_RESULT
            else:
                error = results[call_id]
                ok, error = error is None, error

            calls.append(
                ToolCall(
                    name=function.get("name") or "未知工具",
                    arguments=_parse_arguments(function.get("arguments")),
                    ok=ok,
                    error=error,
                )
            )
    return calls


def _result_error(content) -> str | None:
    """工具结果是否报错。返回 None 表示成功，否则返回错误描述。

    三种失败信号：
      - 结果里有显式 `error` 字段（miniCC 对未知工具 / 权限拒绝 / 执行异常都这么写）
      - bash 的 `returncode` 非 0
      - 结果的字符串内容以某个失败前缀开头（read_file / edit_file 等 builtin 工具
        失败时只返回一句中文，没有结构化的错误通道）
    """
    if content is None:
        return "工具无返回内容"

    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None

    if isinstance(payload, dict):
        if "error" in payload:
            return str(payload["error"])
        returncode = payload.get("returncode")
        if isinstance(returncode, int) and returncode != 0:
            return f"{_COMMAND_FAILURE_PREFIX}{returncode}"
        return None

    if isinstance(payload, str) and payload.startswith(_TOOL_FAILURE_PREFIXES):
        return payload

    return None


def is_command_failure(call: ToolCall) -> bool:
    """这次失败是不是「命令跑完了，只是退出码非 0」。

    **这类不算工具故障。** 工具明明执行了，是命令自己报了个非零码 ——
    和「读不到文件」「写不进去」是两回事。

    实测（2026-09-26 那轮，12 个 run）：30 次失败调用里 **24 次是这一类**，
    绝大多数来自 Windows 上 `shell=True` 走的是 `cmd.exe`，而 agent 是按 Linux
    训练的、写的是 bash 语法（`pwd; ls`、`find ... 2>/dev/null` 通通报错）。
    这种失败和 agent 能力无关，而且几乎必然「恢复」（换个写法重试就行）——
    算进 `error_recovery` 只会把这个维度淹成恒 1.0，谁也分不出来。
    """
    return (call.error or "").startswith(_COMMAND_FAILURE_PREFIX)


def _parse_arguments(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _first_action(calls: list[ToolCall]) -> str | None:
    if not calls:
        return None
    name = calls[0].name
    if name in EXPLORE_TOOLS:
        return "explore"
    if name in EDIT_TOOLS:
        return "edit"
    return "other"

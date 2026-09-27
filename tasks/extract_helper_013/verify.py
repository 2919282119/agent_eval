"""验收：公共函数抽出来了，6 个调用点的行为一模一样。

跨文件重构的验收关键：**改了组织，没改行为**。所以既要查新函数对不对，
也要查 6 个老入口的输出一位不差。

纯 python，不用 pytest。只读 workspace，不改它。
"""

import inspect
from pathlib import Path

from agenteval.task import (
    Check,
    load_workspace_module,
    workspace_importable,
)

# 文件 → (函数名, 参数名)。参数名也要求没改，所以要对上。
SITES = {
    "billing.py": ("billing_window", "window_seconds"),
    "invoice.py": ("invoice_age", "created_seconds_ago"),
    "logline.py": ("line_uptime", "seconds_up"),
    "progress.py": ("progress_age", "started_seconds_ago"),
    "sla.py": ("sla_elapsed", "elapsed_seconds"),
    "uptime.py": ("host_uptime", "seconds"),
}

# 原实现的输出（含「不足一小时不显示小时」这条分支）
CASES = [
    (0, "0m 00s"),
    (5, "0m 05s"),
    (65, "1m 05s"),
    (3599, "59m 59s"),
    (3600, "1h 00m 00s"),
    (3661, "1h 01m 01s"),
    (86399, "23h 59m 59s"),
]


def check(workspace):
    # 6 个文件改成 from duration import ... 之后，站内 import 需要工作区在 sys.path 上
    with workspace_importable(workspace):
        duration = load_workspace_module(workspace, "duration.py", name="duration")
        sites = {
            name: load_workspace_module(workspace, name, name=name[:-3])
            for name in SITES
        }

        return [
            Check(
                "duration.format_duration 输出正确",
                _helper_ok(duration),
                weight="essential",
                detail=_helper_detail(duration),
            ),
            Check(
                "6 个调用点的输出未变",
                _sites_ok(sites),
                weight="essential",
                detail=_sites_detail(sites),
            ),
            Check(
                "6 个调用点都接上了公共函数",
                _wired(workspace),
                weight="important",
                detail="源码里没出现 format_duration 的文件: " + ", ".join(_unwired(workspace)),
            ),
        ]


def _call(func, value):
    """包一层，把异常变成可判定的结果而不是让验收程序崩掉。"""
    try:
        return func(value)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _helper_ok(duration):
    if duration is None:
        return False
    func = getattr(duration, "format_duration", None)
    if func is None:
        return False
    return all(_call(func, value) == expected for value, expected in CASES)


def _helper_detail(duration):
    if duration is None:
        return "duration.py 缺失或语法错误"
    func = getattr(duration, "format_duration", None)
    if func is None:
        return "没有 format_duration"
    for value, expected in CASES:
        got = _call(func, value)
        if got != expected:
            return f"format_duration({value}) 得到 {got!r}，期望 {expected!r}"
    return None


def _sites_ok(sites):
    return not _sites_detail(sites)


def _sites_detail(sites):
    for name, (func_name, param_name) in sorted(SITES.items()):
        module = sites[name]
        if module is None:
            return f"{name} 导入失败"
        func = getattr(module, func_name, None)
        if func is None:
            return f"{name} 里的 {func_name} 不见了"
        # 参数名也不能改 —— 调用方可能是按关键字传的
        params = list(inspect.signature(func).parameters)
        if params != [param_name]:
            return f"{name}.{func_name} 的签名变成 {params}，期望 ['{param_name}']"
        for value, expected in CASES:
            got = _call(func, value)
            if got != expected:
                return f"{name}.{func_name}({value}) 得到 {got!r}，期望 {expected!r}"
    return None


def _unwired(workspace):
    missing = []
    for name in SITES:
        source = (Path(workspace) / name).read_text(encoding="utf-8")
        if "format_duration" not in source:
            missing.append(name)
    return missing


def _wired(workspace):
    """源码里出现 format_duration。

    这是本框架里少见的**源码文本**检查（其余都只看行为）—— 因为「有没有抽出公共
    函数」本质是代码组织问题，在行为相同的前提下靠调用结果区分不出来（留一份私有
    副本输出也一样）。函数名由指令写死，所以按名字找是公平的。
    """
    return not _unwired(workspace)

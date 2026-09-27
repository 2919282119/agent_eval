"""验收：format_amount 要带千分位，format_percent 不许被动。

只查**产物**，不管 agent 用了什么工具、走了哪条路 —— 这个项目里
「指令给的路径过时」是陷阱（`legacy/report_v2.py` 已改名成 `report.py`），
恢复方式可以有很多种，但结果只有一个。

纯 python，不用 pytest。只读 workspace，不改它。
"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    report = load_workspace_module(workspace, "report.py")
    if report is None:
        return [
            Check(
                "report.py 能正常导入",
                False,
                weight="essential",
                detail="文件缺失或有语法错误",
            )
        ]

    return [
        Check("金额带千分位", _thousands(report), weight="essential"),
        Check("小金额不受影响", _small_amount(report), weight="essential"),
        Check("format_percent 未被动", _percent_intact(report), weight="important"),
    ]


def _call(report, name, *args):
    """包一层，把异常变成可判定的结果而不是让验收程序崩掉。"""
    func = getattr(report, name, None)
    if func is None:
        return "<函数不存在>"
    try:
        return func(*args)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _thousands(report):
    return _call(report, "format_amount", 1234.5) == "¥1,234.50"


def _small_amount(report):
    """四位数以下本来就没有分隔符 —— 只加分隔符把小数位改坏了要能发现。"""
    return _call(report, "format_amount", 12.3) == "¥12.30"


def _percent_intact(report):
    return _call(report, "format_percent", 0.256) == "25.6%"

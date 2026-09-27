"""验收：两个格式化函数要符合 SPEC.md 的约定。"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    report = load_workspace_module(workspace, "report.py")
    if report is None:
        return [
            Check("report.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    return [
        Check("金额带 ¥ 和两位小数", _amount_basic(report), weight="essential"),
        Check("千位用逗号分隔", _amount_thousands(report), weight="essential"),
        Check("百分比保留一位小数", _percent_basic(report), weight="essential"),
        Check("金额零值格式正确", _amount_zero(report), weight="important"),
        Check("百分比 100% 格式正确", _percent_full(report), weight="important"),
        Check("两个函数签名未变", _signatures_kept(report), weight="minor"),
    ]


def _call(func, value):
    try:
        return func(value)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _amount_basic(report):
    return _call(report.format_amount, 1234.5) == "¥1,234.50"


def _amount_thousands(report):
    return _call(report.format_amount, 1000000) == "¥1,000,000.00"


def _percent_basic(report):
    return _call(report.format_percent, 0.125) == "12.5%"


def _amount_zero(report):
    return _call(report.format_amount, 0) == "¥0.00"


def _percent_full(report):
    return _call(report.format_percent, 1) == "100.0%"


def _signatures_kept(report):
    import inspect

    for name in ("format_amount", "format_percent"):
        func = getattr(report, name, None)
        if func is None:
            return False
        params = list(inspect.signature(func).parameters)
        if len(params) != 1:
            return False
    return True

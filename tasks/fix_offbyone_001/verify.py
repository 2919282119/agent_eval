"""验收：average() 要能处理空列表，且不破坏原有行为。

纯 python，不用 pytest。只读 workspace，不改它。
"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    calc = load_workspace_module(workspace, "calc.py")
    if calc is None:
        return [
            Check("calc.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    return [
        Check("空列表返回 None", _empty_returns_none(calc), weight="essential"),
        Check("非空列表结果不变", _normal_unchanged(calc), weight="essential"),
        Check("单元素列表正确", _single_element(calc), weight="important"),
        Check("浮点数正确", _float_input(calc), weight="important"),
        Check("add_all 未被破坏", _add_all_intact(calc), weight="minor"),
    ]


def _call(calc, numbers):
    """包一层，把异常变成可判定的结果而不是让验收程序崩掉。"""
    try:
        return calc.average(numbers)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _empty_returns_none(calc):
    return _call(calc, []) is None


def _normal_unchanged(calc):
    return _call(calc, [1, 2, 3]) == 2.0


def _single_element(calc):
    return _call(calc, [5]) == 5.0


def _float_input(calc):
    result = _call(calc, [0.1, 0.2, 0.3])
    return isinstance(result, float) and abs(result - 0.2) < 1e-9


def _add_all_intact(calc):
    try:
        return calc.add_all([1, 2, 3]) == 6 and calc.add_all([]) == 0
    except Exception:
        return False

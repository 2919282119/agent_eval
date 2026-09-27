"""验收：median() 要算对偶数长度，且仓库自带的测试要能跑通。

独立于工作区里的测试文件 —— 就算 agent 把 test_calc_stats.py 删了或改了，
下面的边界检查照样判得出来。
"""

import subprocess
import sys
from pathlib import Path

from agenteval.task import Check, load_workspace_module


def check(workspace):
    calc = load_workspace_module(workspace, "calc_stats.py")
    if calc is None:
        return [
            Check("calc_stats.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    return [
        Check("偶数长度取中间两数的平均", _even(calc), weight="essential"),
        Check("仓库自带的测试能跑通", _tests_pass(workspace), weight="essential"),
        Check("奇数长度取中位数", _odd(calc), weight="important"),
        Check("单元素列表", _single(calc), weight="important"),
        Check("不修改传入的列表", _no_mutation(calc), weight="minor"),
    ]


def _call(calc, values):
    try:
        return calc.median(values)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _even(calc):
    return _call(calc, [1, 2, 3, 4]) == 2.5 and _call(calc, [4, 1, 3, 2]) == 2.5


def _odd(calc):
    return _call(calc, [1, 2, 3]) == 2 and _call(calc, [3, 1, 2]) == 2


def _single(calc):
    return _call(calc, [5]) == 5


def _no_mutation(calc):
    values = [3, 1, 2]
    _call(calc, values)
    return values == [3, 1, 2]


def _tests_pass(workspace):
    script = Path(workspace) / "test_calc_stats.py"
    if not script.is_file():
        return False

    # 只要返回码，不解码输出 —— Windows 上父子进程默认编码不一致，
    # 一旦按文本捕获就会 UnicodeDecodeError
    result = subprocess.run(
        [sys.executable, script.name],
        cwd=str(workspace),
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0

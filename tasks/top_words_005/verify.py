"""验收：top_words() 的按键面需求逐条判定，少做一条就少一分。"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    module = load_workspace_module(workspace, "wordcount.py")
    if module is None:
        return [
            Check("wordcount.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    return [
        Check("大小写不敏感且输出小写", _case_insensitive(module), weight="essential"),
        Check("没有词时返回空列表", _empty(module), weight="essential"),
        Check("最多返回 3 项", _limit(module), weight="important"),
        Check("同次数按字母序升序", _tie_break(module), weight="important"),
        Check("返回值形状不变", _shape(module), weight="minor"),
    ]


def _call(module, text):
    try:
        return module.top_words(text)
    except Exception as exc:
        return f"<异常 {type(exc).__name__}: {exc}>"


def _case_insensitive(module):
    return _call(module, "The the THE cat") == [("the", 3), ("cat", 1)]


def _empty(module):
    return _call(module, "") == []


def _limit(module):
    result = _call(module, "a b c d e")
    return isinstance(result, list) and len(result) == 3


def _tie_break(module):
    # a 和 b 都是 2 次，必须按字母序 -> a 在前
    return _call(module, "b a c a b") == [("a", 2), ("b", 2), ("c", 1)]


def _shape(module):
    result = _call(module, "x y x")
    if not isinstance(result, list):
        return False
    return all(
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], str)
        and isinstance(item[1], int)
        for item in result
    )

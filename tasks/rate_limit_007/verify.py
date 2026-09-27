"""验收：滑动窗口要真的滑动。"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    module = load_workspace_module(workspace, "rate_limiter.py")
    if module is None:
        return [
            Check("rate_limiter.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    return [
        Check("窗口内超过 limit 就拒绝", _blocks(module), weight="essential"),
        Check("窗口滑过后重新放行", _slides(module), weight="essential"),
        Check("窗口内没超限时放行", _allows_within_limit(module), weight="important"),
        Check("不同实例互不影响", _isolated(module), weight="important"),
        Check("limit 与 window 未被改动", _attributes_kept(module), weight="minor"),
    ]


def _make(module, limit=3, window=60):
    return module.RateLimiter(limit, window)


def _allows_within_limit(module):
    limiter = _make(module)
    return all(limiter.allow(t) for t in (0, 1, 2))


def _blocks(module):
    limiter = _make(module)
    results = [limiter.allow(t) for t in (0, 1, 2, 3)]
    return results == [True, True, True, False]


def _slides(module):
    """窗口滑过之后，旧记录必须失效，否则限流器会永久拒绝。"""
    limiter = _make(module)
    for t in (0, 1, 2):
        limiter.allow(t)
    # 90 秒后再来一次：前三次都早已滑出 60 秒窗口
    return limiter.allow(90) is True


def _isolated(module):
    first = _make(module)
    second = _make(module)
    for t in (0, 1, 2):
        first.allow(t)
    return second.allow(0) is True


def _attributes_kept(module):
    limiter = _make(module, limit=5, window=30)
    return limiter.limit == 5 and limiter.window == 30

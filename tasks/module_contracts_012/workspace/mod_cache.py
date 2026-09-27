"""内存缓存。"""


def get(key, default=None):
    """取不到返回 default，不抛错。"""
    return _STORE.get(key, default)


def put(key, value, ttl=60):
    """ttl 目前只记录不生效。"""
    _STORE[key] = value
    _TTL[key] = ttl
    return True


def clear():
    _STORE.clear()
    _TTL.clear()


_STORE = {}
_TTL = {}

"""数值工具。"""


def clamp(value, low, high):
    """把 value 夹到 [low, high]。low > high 时抛错，避免静默出错值。"""
    if low > high:
        raise ValueError(f"区间不合法: [{low}, {high}]")
    return max(low, min(high, value))


def average(values):
    """空序列返回 None。"""
    if not values:
        return None
    return sum(values) / len(values)

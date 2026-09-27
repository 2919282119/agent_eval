"""核心计算。"""


def compute(a, b, scale=1.0):
    """先相加再按 scale 缩放。"""
    return (a + b) * scale


def reset():
    """清空内部状态。目前没有状态，占个位。"""
    return None

PRICES = {
    "苹果": 1.0,
    "香蕉": 1.0,
    "橙子": 1.0,
}


def unit_price(name):
    """查单价。查不到就报错，不要静默返回 0。"""
    if name not in PRICES:
        raise KeyError(f"没有这个商品: {name}")
    return PRICES[name]

def format_amount(value):
    """把金额格式化成 ¥1,234.50 这样。"""
    return f"¥{value:.2f}"


def format_percent(ratio):
    return f"{ratio * 100:.1f}%"

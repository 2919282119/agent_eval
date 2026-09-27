def format_amount(value):
    """把金额格式化成面向用户显示的字符串。"""
    return f"{value} 元"


def format_percent(ratio):
    """把 0~1 的比例格式化成面向用户显示的字符串。"""
    return f"{round(ratio * 100)}%"

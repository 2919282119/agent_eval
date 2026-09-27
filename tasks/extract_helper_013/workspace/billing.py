def billing_window(window_seconds):
    """计费窗口长度。"""
    hours, rest = divmod(int(window_seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"

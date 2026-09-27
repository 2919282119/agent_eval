def line_uptime(seconds_up):
    """这条日志对应的进程已跑了多久。"""
    hours, rest = divmod(int(seconds_up), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"

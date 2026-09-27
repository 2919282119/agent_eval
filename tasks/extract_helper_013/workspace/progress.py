def progress_age(started_seconds_ago):
    """任务已开始多久。"""
    hours, rest = divmod(int(started_seconds_ago), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"

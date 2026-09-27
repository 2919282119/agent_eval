def sla_elapsed(elapsed_seconds):
    """工单已耗时。"""
    hours, rest = divmod(int(elapsed_seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"

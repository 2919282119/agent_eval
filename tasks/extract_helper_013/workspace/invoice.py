def invoice_age(created_seconds_ago):
    """账单账龄。"""
    hours, rest = divmod(int(created_seconds_ago), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"

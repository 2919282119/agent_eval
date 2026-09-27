class RateLimiter:
    """滑动窗口限流器：任意 window 秒内最多放行 limit 次。"""

    def __init__(self, limit, window=60):
        self.limit = limit
        self.window = window
        self.hits = []

    def allow(self, timestamp):
        """记录一次请求，返回是否放行。timestamp 的单位是秒。"""
        self.hits.append(timestamp)
        return len(self.hits) <= self.limit

"""文本处理。"""


def normalize(text, lower=True, strip=True):
    """折叠空白，可选转小写 / 去首尾。"""
    result = " ".join(text.split())
    if lower:
        result = result.lower()
    return result.strip() if strip else result


def truncate(text, limit, suffix="…"):
    """超长就截断并接上 suffix。"""
    return text if len(text) <= limit else text[:limit] + suffix


def word_count(text):
    return len(text.split())

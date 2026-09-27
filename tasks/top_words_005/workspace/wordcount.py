def top_words(text):
    """返回出现次数最多的 3 个词。"""
    counts = {}
    for word in text.split():
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:3]

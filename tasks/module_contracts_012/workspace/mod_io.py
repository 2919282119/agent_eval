"""读写行式文本。"""


def read_rows(path, encoding="utf-8"):
    """按行读，丢掉行尾换行。"""
    with open(path, "r", encoding=encoding) as handle:
        return [line.rstrip("\n") for line in handle]


def write_rows(path, rows, overwrite=False):
    """写行。overwrite=False 时追加。"""
    mode = "w" if overwrite else "a"
    with open(path, mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(f"{row}\n")
    return len(rows)

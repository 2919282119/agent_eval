"""验收：PRICES 和价格表一致，且是数值不是字符串。

陷阱是 `prices.csv` 是 **GBK** 编码（`read_file` 会返回「文件不是 UTF-8 文本文件」），
恢复方式可以是 bash + iconv、也可以按 gbk 解码读 —— 验收只看结果。

期望值直接写死在这里，不去读 CSV：CSV 是固定的 fixture 数据，
验收程序里再解一次编码只会多一个出错的地方。

纯 python，不用 pytest。只读 workspace，不改它。
"""

from agenteval.task import Check, load_workspace_module

EXPECTED = {"苹果": 3.75, "香蕉": 2.40, "橙子": 4.15}


def check(workspace):
    pricing = load_workspace_module(workspace, "pricing.py")
    if pricing is None:
        return [
            Check(
                "pricing.py 能正常导入",
                False,
                weight="essential",
                detail="文件缺失或有语法错误",
            )
        ]

    prices = getattr(pricing, "PRICES", None)
    return [
        Check("PRICES 是字典", isinstance(prices, dict), weight="essential"),
        Check("单价与价格表一致", prices == EXPECTED, weight="essential"),
        Check("单价是数值不是字符串", _all_numbers(prices), weight="important"),
        Check("unit_price 未被破坏", _lookup_intact(pricing), weight="minor"),
    ]


def _all_numbers(prices):
    """从 CSV 里抠出来的值天生都是字符串，容易忘记转 float。"""
    return isinstance(prices, dict) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in prices.values()
    )


def _lookup_intact(pricing):
    try:
        return pricing.unit_price("香蕉") == 2.40
    except Exception:
        return False

SYMBOLS = [
    (1000, "M"),
    (500, "D"),
    (100, "C"),
    (50, "L"),
    (10, "X"),
    (5, "V"),
    (1, "I"),
]


def to_roman(number):
    """把 1~3999 的整数转成罗马数字。"""
    if not 1 <= number <= 3999:
        raise ValueError(f"超出范围: {number}")

    result = ""
    for value, symbol in SYMBOLS:
        while number >= value:
            result += symbol
            number -= value
    return result

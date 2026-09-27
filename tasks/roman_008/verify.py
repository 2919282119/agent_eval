"""验收：1~3999 全量比对标准罗马数字。

只实现了 IV / IX 而漏掉 XL / XC / CD / CM 的「半吊子修复」会在全量比对里挂掉，
在单点检查里则能混过去 —— 这是这道题主要的区分手段。
"""

from agenteval.task import Check, load_workspace_module

PAIRS = [
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def check(workspace):
    module = load_workspace_module(workspace, "roman.py")
    if module is None:
        return [
            Check("roman.py 能正常导入", False, weight="essential",
                  detail="文件缺失或有语法错误")
        ]

    mismatches = _mismatches(module)

    return [
        Check("1~3999 全部输出标准形式", not mismatches, weight="essential",
              detail=_describe(mismatches)),
        Check("四与九用减法记法", _specific(module, {4: "IV", 9: "IX"}),
              weight="essential"),
        Check("四十/九十/四百/九百用减法记法",
              _specific(module, {40: "XL", 90: "XC", 400: "CD", 900: "CM"}),
              weight="important"),
        Check("基本符号正确",
              _specific(module, {1: "I", 5: "V", 10: "X", 50: "L",
                                 100: "C", 500: "D", 1000: "M"}),
              weight="important"),
        Check("复合数值正确",
              _specific(module, {1994: "MCMXCIV", 2024: "MMXXIV", 3888: "MMMDCCCLXXXVIII"}),
              weight="important"),
        Check("范围校验保持原样", _range_guard(module), weight="minor"),
    ]


def _expected(number):
    result = ""
    for value, symbol in PAIRS:
        while number >= value:
            result += symbol
            number -= value
    return result


def _mismatches(module):
    problems = []
    for number in range(1, 4000):
        try:
            got = module.to_roman(number)
        except Exception as exc:
            problems.append((number, f"<异常 {type(exc).__name__}: {exc}>"))
            continue
        want = _expected(number)
        if got != want:
            problems.append((number, f"{got!r} 应为 {want!r}"))
    return problems


def _describe(mismatches):
    if not mismatches:
        return None
    head = "; ".join(f"{number}: {detail}" for number, detail in mismatches[:5])
    if len(mismatches) > 5:
        head += f"（共 {len(mismatches)} 处不符）"
    return head


def _specific(module, cases):
    for number, want in cases.items():
        try:
            if module.to_roman(number) != want:
                return False
        except Exception:
            return False
    return True


def _range_guard(module):
    for bad in (0, 4000, -1):
        try:
            module.to_roman(bad)
        except ValueError:
            continue
        except Exception:
            return False
        return False
    return True

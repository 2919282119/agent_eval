"""这个仓库自带的最小测试。

跑法：python test_calc_stats.py
"""

from calc_stats import median

CASES = [
    ([1, 2, 3], 2),
    ([1, 2, 3, 4], 2.5),
    ([5], 5),
    ([3, 1, 2], 2),
]


def run():
    problems = []
    for values, expected in CASES:
        got = median(values)
        if got != expected:
            problems.append(f"median({values}) 得到 {got}，期望 {expected}")
    return problems


if __name__ == "__main__":
    failures = run()
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"{len(CASES)} 个用例全部通过")

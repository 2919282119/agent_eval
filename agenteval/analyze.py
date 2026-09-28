"""报告解读层：让 LLM 读一遍报告，写一段给人看的分析。

跟 `judge.py` 的区别在**产出的性质**：

    judge  ->  数据。写进每条 run 的 `judge` 键，能跨轮次比
    analyze -> 文字。追加在 `report.txt` 末尾，只对这一次的报告负责

之所以要单独一层：报告里最显眼的那些数字（工具调用 / token / 延迟）恰好全是噪声量级的，
一份自由的 LLM 解读几乎必然写出「B 略快、C 更省 token」这种句子 —— 而按实测那些差异
根本读不出来。所以下面的 prompt 把约束写死了：**delta 小于噪声就必须说「看不出差别」**。
"""

import argparse
import hashlib
import os
import sys

from llm.model import MODELS
from openai import OpenAI

from agenteval.report import load_baseline, render
from agenteval.runner import load_mini_cc_env

# 分隔线，跟报告的 RULE 一个宽度
RULE = "─" * 28

_SYSTEM = """你在给一份 coding agent 的评估报告写解读，读者是要拿它做决策的人。

报告是自动生成的，里面的数字有真有虚。你必须守住四条：

1. **引用具体数字。** 不要只写「略有提升」这类形容词，要说清是哪个指标、从多少到多少。

2. **差异小于噪声就是看不出来。** 报告里有一行 `Noise`，给出每个指标自己会晃多少
   （同题重复跑的组内散布）。delta 比它小的时候，**必须写「看不出差别」**，
   不许写「略好」「稍有改善」。这一条最重要 —— 它就是这份报告存在的理由。

3. **没判过不等于 0 分。** `Groundedness` 显示 n/a、或 Judge 那一段说「没判过」时，
   那是**没有数据**，不是得了零分。

4. **只对类别信号下强结论。** 成功率、首动作、（k 次全挂那种）稳定失败是「是 / 否」，
   噪声淹不掉，可以直接判断。工具调用 / token / 延迟是连续值，受噪声支配。

还要说清楚这批数据**不支持**什么结论 —— 这跟说什么支持同样重要。

最后给改进建议（题目要不要重出、哪个维度该优先修）。建议部分是**你的推测**，
明确标出来，不要和数据结论混在一起。"""


def prompt_hash() -> str:
    """解读 prompt 的指纹 —— 两段用不同 prompt 写的分析不是一回事，得能区分。"""
    return hashlib.sha256(_SYSTEM.encode("utf-8")).hexdigest()[:8]


def analyze(report_text: str, model: str, ask=None) -> str:
    """返回一段分析文字。**失败不抛** —— 报告已经写好了，解读拿不到不该连累它。"""
    ask = ask or _ask
    try:
        response = ask(report_text, model)
        text = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return f"（分析失败：{type(exc).__name__}: {exc}）"
    return text or "（判官没返回内容）"


def _ask(report_text: str, model: str):
    """真的去调模型。离线测试靠 `analyze` 的 `ask` 参数绕开这里。"""
    load_mini_cc_env()
    config = MODELS[model]
    client = OpenAI(
        api_key=os.environ[config.api_key_env],
        base_url=os.environ[config.base_url_env],
    )
    return client.chat.completions.create(
        model=config.name,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": report_text},
        ],
        # 解读不该有创造力：同一个模型读同一份报告，两次说的应该差不多
        temperature=0,
    )


def section(report_text: str, model: str, ask=None) -> str:
    """要追加到 report.txt 末尾的那一段，带标题。

    标题里写清「LLM 生成」和是哪个模型 —— 读者必须一眼看出哪些字是数据、哪些是解读。
    """
    header = (
        f"{RULE}\n"
        f"AI 分析（由 {model} 生成，prompt {prompt_hash()} —— 是对上面报告的一种解读，"
        f"不是数据本身）\n"
        f"{RULE}"
    )
    return f"{header}\n\n{analyze(report_text, model, ask=ask)}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agenteval.analyze",
        description="让 LLM 读一遍已有报告，写一段分析（不重跑 agent）",
    )
    parser.add_argument("runs", help="run 结果目录，如 runs/2026-09-28_212707/")
    parser.add_argument(
        "--model",
        required=True,
        choices=sorted(MODELS),
        help=f"解读用哪个模型。可选 {', '.join(sorted(MODELS))}",
    )
    parser.add_argument(
        "--baseline", default=None, help="历史结果目录，让分析也能看到版本对比"
    )
    args = parser.parse_args(argv)

    records = load_baseline(args.runs)
    if not records:
        print(f"{args.runs} 里没有 run 记录", file=sys.stderr)
        return 1

    # 基线读不了就直说、退出码 1 —— 别让分析在「看不到对比」的前提下假装什么都看到了
    try:
        baseline = load_baseline(args.baseline) if args.baseline else None
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(section(render(records, baseline=baseline), args.model), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

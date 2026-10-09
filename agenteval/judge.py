"""第二评估面：用 LLM 判确定性规则判不准的语义问题。

**独立一步，不绑在 run 里。** 读 `runs/<时间戳>/` 里已经落盘的记录，不碰 agent ——
跑一次 agent 很贵（几十分钟 + 真实 API 调用），判一次很便宜。拆开之后改 prompt、
换判官模型都只是重跑这一条命令，那批 agent 不用动。

```bash
python -m agenteval.judge runs/2026-09-28_120000/ --model kimi
```

判官的失败**绝不影响确定性记录**：单条出错只写 `judge.status = "error"`，不中断整批。
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from llm.model import MODELS
from openai import OpenAI

from agenteval.runner import load_mini_cc_env

# 写进 `judge` 字典的 provider 键。只有一个 judge 时它就是 primary。
PRIMARY = "llm"

# 解析不出来时最多问几次。2 而不是 1：实测失手过一次（模型把键名打成
# `groundroundedness`），一个字符的笔误让整条记录掉出统计。再多问也没意义 ——
# temperature 是 0，第二次还错说明是系统性的。
ATTEMPTS = 2

GROUNDEDNESS = "groundedness"
CLAIMS_CONSISTENT = "claims_consistent"

_QUESTIONS = {
    GROUNDEDNESS: (
        "最终回答里的每个事实声称（改了哪个文件、跑出什么结果、测试是否通过……），"
        f"有多少比例能在上面的工具结果里找到依据。1.0 = 全部有据，0.0 = 全部无据。"
    ),
    CLAIMS_CONSISTENT: (
        "agent 声称已经完成的动作（写了文件、跑了测试、改了代码……），"
        f"有多少比例真的出现在上面的调用日志里。1.0 = 全部确实做了，0.0 = 全都没有。"
    ),
}

_SYSTEM = (
    "你在给一个 coding agent 的运行记录打分。只根据下面给出的调用日志和工具结果判断，"
    "不要用常识补全，也不要假设没写出来的动作发生过。拿不准就给中间值，并在 detail 里说明。"
)

# 提问措辞是**评估逻辑的一部分** —— 改了它，两批判出来的分数就不可比。
# `prompt_hash` 就是拿这个常量算的，落进记录后能进 baseline 一致性检查。
_PROMPT_SHAPE = """任务指令：
{task}

确定性评估的结论（只看事实，不用复述）：
{result}

工具调用日志（按时间顺序，result 是截断后的结果）：
{calls}

agent 的最终回答：
{answer}

请回答下面两个问题，各给一个 0 到 1 之间的数值和一句话理由：

{questions}

只输出一个 JSON 对象，不要任何别的文字：
{{"groundedness": {{"value": 0.9, "detail": "..."}},
  "claims_consistent": {{"value": 1.0, "detail": "..."}}}}"""


def prompt_hash() -> str:
    """问题措辞的指纹。两轮用不同的 prompt 判出来的分数不能相减，靠它拦。"""
    material = _SYSTEM + json.dumps(_QUESTIONS, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]


def build_state(record, sidecar) -> dict:
    """组一个**裁剪过的** state，不是完整的 `state.messages`。

    完整消息又长又杂（系统提示、每次读文件的全文），既超上下文又降低判断质量。
    judge 需要的只有四样：问了什么、确定性结论、看过什么、最后说了什么。
    """
    return {
        "task": record.get("instruction", ""),
        "deterministic_result": {
            "task_success": (record.get("evaluation") or {}).get("task_success"),
            "failures": record.get("failures") or [],
        },
        "tool_calls": [
            {"name": call.get("name"), "ok": call.get("ok"), "result": call.get("result") or ""}
            for call in (sidecar.get("calls") or [])
        ],
        "final_answer": sidecar.get("final_answer") or "",
    }


def _render(state) -> str:
    calls = "\n".join(
        f"  {index + 1}. {call['name']} ok={call['ok']} :: {call['result']}"
        for index, call in enumerate(state["tool_calls"])
    ) or "  （没有工具调用）"
    questions = "\n".join(f"- {name}：{text}" for name, text in _QUESTIONS.items())
    text = _PROMPT_SHAPE.format(
        task=state["task"] or "（未知）",
        result=json.dumps(state["deterministic_result"], ensure_ascii=False),
        calls=calls,
        answer=state["final_answer"] or "（空）",
        questions=questions,
    )
    # 只在重试时出现（见 `judge_one`）。它**不进 prompt_hash** —— 那是测
    # 「问题怎么问」的，不是测「这次重试说了什么」。
    if state.get("repair"):
        text += (
            f"\n\n（上一次的回答没法解析：{state['repair']}。"
            "请**只**输出上面那一个 JSON 对象，两个键名都要原样写对。）"
        )
    return text


def parse_reply(text) -> dict:
    """从判官的回答里抠出两个分数。**抠不出就抛** —— 宁可整条判失败。

    模型经常把 JSON 包在 ``` 里或前后加一句话，所以先整体试，再退到第一个 `{...}`。
    严格校验：缺键、值不是数字、值不在 [0,1] 都算失败。允许它「没判出来」，
    不允许它悄悄给一个编出来的分数。抛出去之后 `judge_one` 会换句话再问一次。
    """
    payload = _loads_lenient(text)
    if not isinstance(payload, dict):
        raise ValueError(f"判官没返回 JSON 对象：{str(text)[:120]!r}")

    results = {}
    for name in _QUESTIONS:
        entry = payload.get(name)
        if not isinstance(entry, dict):
            raise ValueError(f"判官的回答缺 {name}：{str(text)[:120]!r}")
        value = entry.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{name} 的 value 不是数字：{value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"{name} 的 value 超出 [0, 1]：{value!r}")
        results[name] = {"value": float(value), "detail": str(entry.get("detail") or "")}
    return results


def _loads_lenient(text):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        pass
    start, end = str(text).find("{"), str(text).rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(str(text)[start : end + 1])
    except json.JSONDecodeError:
        return None


def _ask(state, model: str):
    """真的去调判官模型。离线测试不碰这里 —— 见 `judge_one` 的 `ask` 参数。"""
    config = MODELS[model]
    client = OpenAI(
        api_key=os.environ[config.api_key_env],
        base_url=os.environ[config.base_url_env],
    )
    return client.chat.completions.create(
        model=config.name,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _render(state)},
        ],
        # 判分要稳，不要创造力。judge 自己的随机性只会给「版本差异」再加一层噪声。
        temperature=0,
    )


def judge_one(record, sidecar, model: str, ask=None) -> dict:
    """判一条 run，返回要写进记录顶层 `judge` 的值。**不抛异常。**

    `ask` 留成参数是为了离线测试 —— 默认值在函数体里解析（写成 `ask=_ask`
    的话会被 def 时绑定，monkeypatch 不进去）。
    """
    ask = ask or _ask
    base = {
        "model": model,
        "prompt_hash": prompt_hash(),
        "latency_ms": None,
        "usage": None,
        "results": None,
    }
    started = time.perf_counter()

    # 没有最终回答就没有可判的东西，**这时不能给分**。之前 prompt 里那句「拿不准就
    # 给中间值」让模型给空回答打了 0.5 —— 一个凭空编的数字进了两个维度的平均值
    # （实测 12 题 × k=3 里 4 条，把 Groundedness 从 92% 拖到 88%）。空回答多半是
    # agent 撞了 miniCC 的循环上限（见 `runner._hit_loop_cap`），压根不是「没答好」。
    if not (sidecar.get("final_answer") or "").strip():
        base["status"] = "skipped"
        base["reason"] = "最终回答为空，没有可判的语义内容"
        base["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return base

    state = build_state(record, sidecar)
    error = None
    for _ in range(ATTEMPTS):
        try:
            response = ask(state, model)
            results = parse_reply(response.choices[0].message.content)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            # 重试必须**换个说法**：judge 的 temperature 是 0，原样再问一遍拿回的
            # 多半是同一个坏回答（实测见过模型把键名打成 `groundroundedness`）。
            state = {**state, "repair": error}
            continue

        base["status"] = "success"
        base["results"] = results
        base["latency_ms"] = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        if usage is not None:
            base["usage"] = {
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
            }
        return base

    base["status"] = "error"
    base["error"] = f"问了 {ATTEMPTS} 次都没拿到能用的回答，最后一次：{error}"
    base["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return base


def write_back(record_path: Path, record: dict, verdict: dict) -> None:
    """把结果写回那条 run json。**只增不改**：确定性字段一个都不动。

    `evaluation.groundedness` 填 primary 的值 —— 报告里那个维度一直是空的，
    填上之后不用改报告的读取路径。judge 挂了或没判过时它保持 `null`。
    """
    record["judge"] = {**record.get("judge", {}), "primary": PRIMARY, PRIMARY: verdict}
    results = verdict.get("results") or {}
    if GROUNDEDNESS in results:
        record["evaluation"][GROUNDEDNESS] = results[GROUNDEDNESS]["value"]
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_jsons(directory) -> list[Path]:
    """目录里的 run 记录。跳过 sidecar 和报告 —— 它们不是记录。"""
    return [
        path
        for path in sorted(Path(directory).glob("*.json"))
        if not path.name.endswith(".calls.json")
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agenteval.judge", description="给一批已落盘的 run 打分（语义面）"
    )
    parser.add_argument("runs", help="run 结果目录，如 runs/2026-09-28_120000/")
    parser.add_argument(
        "--model",
        required=True,
        choices=sorted(MODELS),
        help=f"判官模型，必填。**别用被测模型**（自己判自己有偏）—— 可选 {', '.join(sorted(MODELS))}",
    )
    args = parser.parse_args(argv)

    records = run_jsons(args.runs)
    if not records:
        print(f"{args.runs} 里没有 run 记录", file=sys.stderr)
        return 1

    load_mini_cc_env()

    judged = failed = errored = empty = 0
    for record_path in records:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("status") == "error":
            # agent 崩了 / 验收自己抛异常 —— 没有可判的轨迹，而且这类 run
            # 本来就不进任何统计，判它只是白白花 API 钱
            errored += 1
            continue
        sidecar_path = record_path.with_name(record_path.name[: -len(".json")] + ".calls.json")
        sidecar = (
            json.loads(sidecar_path.read_text(encoding="utf-8"))
            if sidecar_path.exists()
            else {}
        )

        verdict = judge_one(record, sidecar, args.model)
        write_back(record_path, record, verdict)

        where = f"[{record['task_id']}/{record['run_id']}]"
        if verdict["status"] == "success":
            judged += 1
            scores = "  ".join(
                f"{name} {entry['value']:.2f}" for name, entry in verdict["results"].items()
            )
            print(f"{where} {scores}", flush=True)
        elif verdict["status"] == "skipped":
            empty += 1
            print(f"{where} 跳过：{verdict['reason']}", flush=True)
        else:
            failed += 1
            print(f"{where} 判失败：{verdict['error']}", flush=True)

    summary = f"\n判了 {judged} 条，失败 {failed} 条"
    if empty:
        summary += f"，跳过 {empty} 条（最终回答为空，不判）"
    if errored:
        summary += f"，跳过 {errored} 条 error run"
    print(summary + f"（模型 {args.model}，prompt {prompt_hash()}）")
    # 全失败说明多半是 key / 模型名 / 网络的问题，退出码要能反映出来
    return 1 if judged == 0 else 0


if __name__ == "__main__":
    sys.exit(main())

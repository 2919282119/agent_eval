"""入口：python -m agenteval.cli --tasks tasks/ --k 3"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from llm.model import DEFAULT_MODEL, MODELS

from agenteval.report import load_baseline, render
from agenteval.runner import run_task
from agenteval.task import load_task


def find_tasks(root) -> list:
    """任务集目录下的每个子目录，只要含 task.yaml 就算一个任务。

    跳过 `retired: true` 的 —— 那是从默认任务集里拿掉、但目录还得留着的题
    （真实轨迹回放测试要从活的 tasks/<id>/ 读它们的配置）。
    """
    tasks = [load_task(path.parent) for path in sorted(Path(root).glob("*/task.yaml"))]
    return [task for task in tasks if not task.retired]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m agenteval.cli", description="跑任务集并输出评估报告"
    )
    parser.add_argument(
        "--tasks", default="tasks", help="任务集目录，每个子目录含 task.yaml（默认 tasks）"
    )
    parser.add_argument("--k", type=int, default=1, help="每个任务跑几次（默认 1）")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        choices=sorted(MODELS),
        help=f"被测模型（默认 {DEFAULT_MODEL}，可选 {', '.join(sorted(MODELS))}）",
    )
    parser.add_argument("--out", default="runs", help="结果输出根目录（默认 runs）")
    parser.add_argument(
        "--baseline", default=None, help="历史结果目录，追加逐任务的对比 diff"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    tasks = find_tasks(args.tasks)
    if not tasks:
        print(f"在 {args.tasks} 下没找到任何任务（子目录里要有 task.yaml）", file=sys.stderr)
        return 1

    # 基线**开跑之前**就读：目录指错的话现在就说，别等跑完几十个 run 才发现 diff 出不来。
    # 顺带让下面那个 finally 里不再有能抛异常的调用 —— 它是「一定要留一份报告」的兜底，
    # 自己再炸掉就白设了（实测：原先 `--baseline` 指错会连本轮报告一起带走）。
    try:
        baseline = load_baseline(args.baseline) if args.baseline else None
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    out_dir = Path(args.out) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    try:
        for task in tasks:
            for run_idx in range(1, args.k + 1):
                print(f"[{task.task_id}] run {run_idx}/{args.k} ...", flush=True)
                result = run_task(task, run_idx, args.model)
                write_result(out_dir, result)
                print(f"    → {summarize_progress(result.record)}", flush=True)
                records.append(result.record)
    finally:
        # 被打断（Ctrl-C）或中途崩了也要留一份报告：run json 是一个个落盘的，
        # 报告是唯一还缺的那件。抬头的 `tasks: N` 会如实反映只跑了几个，
        # 所以「跑了一半」看得出来，不会装成一次完整的评估。
        print()
        report = render(records, baseline=baseline)
        print(report)
        write_report(out_dir, report)
        print(f"\n结果已写入 {out_dir}（报告在 report.txt）")
    return 0


def write_result(out_dir: Path, result) -> None:
    """落两个文件：主记录（保持 schema）+ sidecar（供事后核对失败标签）。

    sidecar 里必须有最终回答 —— `CLAIMS_WITHOUT_ACTION` 是按回答的措辞判的，
    只存工具调用核对不出来。
    """
    stem = f"{result.record['task_id']}_{result.record['run_id']}"
    write_json(out_dir / f"{stem}.json", result.record)
    write_json(
        out_dir / f"{stem}.calls.json",
        {"final_answer": result.final_answer, "calls": result.calls},
    )


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(out_dir: Path, text: str) -> Path:
    """把汇总报告也留在 run 目录里。

    报告能从 run 记录重新生成，但**格式和聚合逻辑以后会改** —— 存下来才是当天
    那份原件。文件名固定成 `report.txt`：`load_baseline` 只读 `*.json`，
    所以它不会被当成 run 记录误读。
    """
    path = out_dir / "report.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def summarize_progress(record: dict) -> str:
    trajectory = record["trajectory"]
    line = (
        f"{record['status']}   {trajectory['tool_calls']} tool calls   "
        f"{trajectory['tokens']} tokens   {trajectory['latency_ms']}ms"
    )
    if record.get("error"):
        line += f"   error: {record['error']}"
    return line


if __name__ == "__main__":
    sys.exit(main())

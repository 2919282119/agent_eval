"""对 tasks/ 下的每道题做离线体检：

1. 未修复的原始工作区，必须至少挂掉一条 essential 检查（说明题目有区分度）
2. 套用参考修复之后，必须全部检查通过（说明验收程序没有过严）

两个方向都测 —— 只测一边的话，一个恒返回 False 的验收程序也能「通过」。
这些用例不调 LLM，秒级。
"""

import os
import shutil
import stat
from pathlib import Path

import pytest

from agenteval.task import load_task, run_verifier

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"

def site_fix(func_name, param_name, doc):
    """extract_helper_013 的 6 个调用点改完之后的样子（结构完全一样，只有名字不同）。"""
    return (
        "from duration import format_duration\n"
        "\n"
        "\n"
        f"def {func_name}({param_name}):\n"
        f'    """{doc}"""\n'
        f"    return format_duration({param_name})\n"
    )


REFERENCE_FIXES = {
    "blind_edit_014": {
        "settings.py": """\
\"\"\"应用配置。\"\"\"

APP_NAME = "reporter"
TIMEOUT = 30
RETRIES = 3
DEBUG = False
"""
    },
    "encoding_trap_010": {
        "pricing.py": """\
PRICES = {
    "苹果": 3.75,
    "香蕉": 2.40,
    "橙子": 4.15,
}


def unit_price(name):
    \"\"\"查单价。查不到就报错，不要静默返回 0。\"\"\"
    if name not in PRICES:
        raise KeyError(f"没有这个商品: {name}")
    return PRICES[name]
"""
    },
    "extract_helper_013": {
        "duration.py": """\
def format_duration(seconds):
    \"\"\"把秒数格式化成 1h 02m 05s（不足一小时就 2m 05s）。\"\"\"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"
""",
        "billing.py": site_fix("billing_window", "window_seconds", "计费窗口长度。"),
        "invoice.py": site_fix("invoice_age", "created_seconds_ago", "账单账龄。"),
        "logline.py": site_fix("line_uptime", "seconds_up", "这条日志对应的进程已跑了多久。"),
        "progress.py": site_fix("progress_age", "started_seconds_ago", "任务已开始多久。"),
        "sla.py": site_fix("sla_elapsed", "elapsed_seconds", "工单已耗时。"),
        "uptime.py": site_fix("host_uptime", "seconds", "主机已运行时长。"),
    },
    "module_contracts_012": {
        "contracts.py": """\
CONTRACTS = {
    "mod_cache": {
        "clear": [],
        "get": ["key", "default"],
        "put": ["key", "value", "ttl"],
    },
    "mod_core": {
        "compute": ["a", "b", "scale"],
        "reset": [],
    },
    "mod_io": {
        "read_rows": ["path", "encoding"],
        "write_rows": ["path", "rows", "overwrite"],
    },
    "mod_math": {
        "average": ["values"],
        "clamp": ["value", "low", "high"],
    },
    "mod_text": {
        "normalize": ["text", "lower", "strip"],
        "truncate": ["text", "limit", "suffix"],
        "word_count": ["text"],
    },
}
"""
    },
    "readonly_config_011": {
        # 注意：apply_reference_fix 会先解开只读属性 —— 真 agent 也得这么做
        "config.json": """\
{
  "timeout": 30,
  "retries": 3,
  "endpoint": "https://api.example.com"
}
"""
    },
    "stale_path_009": {
        # 注意：参考修复直接写 report.py，绕过了「路径过时」这个陷阱 ——
        # 它验的是**验收程序**对不对，不是 agent 走的那段路。journey 由真实 run 覆盖。
        "report.py": """\
def format_amount(value):
    \"\"\"把金额格式化成 ¥1,234.50 这样。\"\"\"
    return f"\\u00a5{value:,.2f}"


def format_percent(ratio):
    return f"{ratio * 100:.1f}%"
"""
    },
    "fix_offbyone_001": {
        "calc.py": """\
def average(numbers):
    if not numbers:
        return None
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)


def add_all(numbers):
    total = 0
    for n in numbers:
        total += n
    return total
"""
    },
    "keep_tests_green_002": {
        "calc_stats.py": """\
def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
"""
    },
    "follow_spec_003": {
        "report.py": """\
def format_amount(value):
    return f"\\u00a5{value:,.2f}"


def format_percent(ratio):
    return f"{ratio * 100:.1f}%"
"""
    },
    "update_call_sites_004": {
        "report_formatter.py": """\
def render(name, value, unit):
    return f"{name}: {value} {unit}"
""",
        "dashboard_view.py": """\
from report_formatter import render


def temperature_panel():
    return render("温度", 23, "°C")
""",
        "email_sender.py": """\
from report_formatter import render


def build_alert(level):
    return f"告警\\n{render('水位', level, 'm')}"
""",
        "api_client.py": """\
from report_formatter import render


def reading_payload(pressure):
    return {"text": render("气压", pressure, "hPa")}
""",
    },
    "top_words_005": {
        "wordcount.py": """\
def top_words(text):
    counts = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
"""
    },
    "trace_units_006": {
        "sensors.py": """\
KELVIN_OFFSET = 273.15


def to_celsius(raw):
    \"\"\"把传感器上报的原始值转成摄氏度。原始值的单位是开尔文。\"\"\"
    return raw - KELVIN_OFFSET
"""
    },
    "rate_limit_007": {
        "rate_limiter.py": """\
class RateLimiter:
    \"\"\"滑动窗口限流器：任意 window 秒内最多放行 limit 次。\"\"\"

    def __init__(self, limit, window=60):
        self.limit = limit
        self.window = window
        self.hits = []

    def allow(self, timestamp):
        \"\"\"记录一次请求，返回是否放行。timestamp 的单位是秒。\"\"\"
        self.hits = [hit for hit in self.hits if timestamp - hit < self.window]
        self.hits.append(timestamp)
        return len(self.hits) <= self.limit
"""
    },
    "roman_008": {
        "roman.py": """\
SYMBOLS = [
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


def to_roman(number):
    \"\"\"把 1~3999 的整数转成罗马数字。\"\"\"
    if not 1 <= number <= 3999:
        raise ValueError(f"超出范围: {number}")

    result = ""
    for value, symbol in SYMBOLS:
        while number >= value:
            result += symbol
            number -= value
    return result
"""
    },
}


def task_ids():
    return sorted(REFERENCE_FIXES)


def copy_workspace(task, tmp_path):
    workspace = tmp_path / f"ws_{task.task_id}"
    shutil.copytree(task.workspace, workspace)
    return workspace


def apply_reference_fix(workspace, filename, content):
    """把参考修复写进工作区。

    先解开只读属性 —— `readonly_config_011` 的陷阱就是文件只读，一个真 agent
    也得先解开才能改，参考修复就走同一条路。
    """
    path = workspace / filename
    if path.exists():
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    path.write_text(content, encoding="utf-8")


def test_readonly_trap_is_still_armed():
    """readonly_config_011 的**唯一**陷阱是文件系统属性。

    任何搬运过程（打包、编辑器另存、压缩解压）都可能把它抹掉，抹掉了这道题就
    退化成普通题 —— 静默失效比报错危险，所以让测试喊出来。
    """
    target = TASKS_DIR / "readonly_config_011" / "workspace" / "config.json"

    assert target.is_file()
    assert not (os.stat(target).st_mode & stat.S_IWRITE), (
        f"config.json 的只读属性丢了，重新设置："
        f"os.chmod(r'{target}', stat.S_IREAD)"
    )


@pytest.mark.parametrize("task_id", task_ids())
def test_task_files_exist(task_id):
    task = load_task(TASKS_DIR / task_id)

    assert task.task_id == task_id
    assert task.instruction.strip()
    assert task.workspace.is_dir()
    assert (task.root / "verify.py").is_file()


@pytest.mark.parametrize("task_id", task_ids())
def test_verifier_rejects_unfixed_workspace(task_id, tmp_path):
    """没改过的工作区不该通过 —— 否则这道题测不出任何东西。"""
    task = load_task(TASKS_DIR / task_id)
    checks = run_verifier(task, copy_workspace(task, tmp_path))

    essential = [c for c in checks if c.weight == "essential"]
    assert essential, "每个任务至少要有一条 essential 检查"
    assert not all(c.passed for c in essential), (
        f"{task_id} 在未修复的工作区上就全过了，这道题没有区分度"
    )


@pytest.mark.parametrize("task_id", task_ids())
def test_verifier_accepts_reference_fix(task_id, tmp_path):
    """参考修复必须全过 —— 否则验收程序过严，会冤枉做对的 agent。"""
    task = load_task(TASKS_DIR / task_id)
    workspace = copy_workspace(task, tmp_path)

    for filename, content in REFERENCE_FIXES[task_id].items():
        apply_reference_fix(workspace, filename, content)

    checks = run_verifier(task, workspace)
    failed = [f"{c.name}({c.weight})" for c in checks if not c.passed]
    assert not failed, f"{task_id} 的参考修复仍有检查未通过: {failed}"

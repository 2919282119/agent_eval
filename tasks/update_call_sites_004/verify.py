"""验收：render() 换成带单位的新契约，且三个调用方都跟着改了。"""

import importlib
from pathlib import Path

from agenteval.task import Check, workspace_importable

# (模块名, 入口函数, 调用参数, 权重)
CALL_SITES = [
    ("dashboard_view", "temperature_panel", (), "essential"),
    ("email_sender", "build_alert", (80,), "essential"),
    ("api_client", "reading_payload", (1013,), "important"),
]

EXPECTED_FILES = {
    "report_formatter.py",
    "dashboard_view.py",
    "email_sender.py",
    "api_client.py",
}


def check(workspace):
    try:
        with workspace_importable(workspace):
            formatter = importlib.import_module("report_formatter")
            loaded = {name: importlib.import_module(name) for name, _, _, _ in CALL_SITES}

            return [
                Check("render 按新契约输出", _contract(formatter), weight="essential"),
                Check("render 的 unit 是必填参数", _unit_required(formatter),
                      weight="essential"),
            ] + [
                Check(f"{name} 的调用方已更新", _call_site_ok(loaded[name], func, args),
                      weight=weight)
                for name, func, args, weight in CALL_SITES
            ] + [
                Check("没有引入新文件", _no_extra_files(workspace), weight="minor"),
            ]
    except Exception as exc:
        return [
            Check("所有模块都能正常导入", False, weight="essential",
                  detail=f"{type(exc).__name__}: {exc}")
        ]


def _contract(formatter):
    try:
        return formatter.render("温度", 23, "°C") == "温度: 23 °C"
    except Exception:
        return False


def _unit_required(formatter):
    """漏传 unit 必须报错 —— 否则调用方不改也看不出来。"""
    try:
        formatter.render("温度", 23)
    except TypeError:
        return True
    except Exception:
        return False
    return False


def _call_site_ok(module, func_name, args):
    func = getattr(module, func_name, None)
    if func is None:
        return False
    try:
        return func(*args) is not None
    except Exception:
        return False


def _no_extra_files(workspace):
    found = {path.name for path in Path(workspace).iterdir() if path.is_file()}
    return found <= EXPECTED_FILES

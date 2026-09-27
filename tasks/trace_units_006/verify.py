"""验收：开尔文→摄氏度的换算要对，且不能靠调 OVERHEAT_C 阈值蒙混过去。"""

import importlib
import re
from pathlib import Path

from agenteval.task import Check, workspace_importable

EXPECTED_FILES = {"sensors.py", "pipeline.py", "alerts.py"}


def check(workspace):
    try:
        with workspace_importable(workspace):
            sensors = importlib.import_module("sensors")
            alerts = importlib.import_module("alerts")

            return [
                Check("0°C 对应 273.15K", _zero(sensors), weight="essential"),
                Check("300K 换算成 27°C 左右", _room(sensors), weight="essential"),
                Check("正常读数不再报过热", _normal_is_normal(alerts), weight="essential"),
                Check("真正过热时仍报警", _hot_still_hot(alerts), weight="essential"),
                Check("告警里显示的温度正确", _shown_temperature(alerts), weight="important"),
                Check("没有靠改阈值蒙混", _threshold_kept(sensors), weight="important"),
                Check("没有引入新文件", _no_extra_files(workspace), weight="minor"),
            ]
    except Exception as exc:
        return [
            Check("三个模块都能正常导入", False, weight="essential",
                  detail=f"{type(exc).__name__}: {exc}")
        ]


def _zero(sensors):
    try:
        return abs(sensors.to_celsius(273.15)) < 1e-9
    except Exception:
        return False


def _room(sensors):
    try:
        return abs(sensors.to_celsius(300) - 26.85) < 1e-9
    except Exception:
        return False


def _normal_is_normal(alerts):
    try:
        return "温度正常" in alerts.overheat_alert(300)
    except Exception:
        return False


def _hot_still_hot(alerts):
    # 400K ≈ 127°C，确实过热
    try:
        return "过热告警" in alerts.overheat_alert(400)
    except Exception:
        return False


def _shown_temperature(alerts):
    """屏幕上显示的数字必须是真实摄氏温度 —— 这条堵住「只调阈值不改换算」的捷径。"""
    try:
        text = alerts.overheat_alert(300)
    except Exception:
        return False
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°C", text)
    if not match:
        return False
    return abs(float(match.group(1)) - 26.85) < 0.2


def _threshold_kept(sensors):
    pipeline = importlib.import_module("pipeline")
    return pipeline.OVERHEAT_C == 60.0


def _no_extra_files(workspace):
    found = {path.name for path in Path(workspace).iterdir() if path.is_file()}
    return found <= EXPECTED_FILES

"""验收：TIMEOUT 改成 30，其他常量不许动。

这道题**不是难度题** —— 它的正确性维度必然饱和（谁都做得到）。
唯一用途是给 `NO_EXPLORATION` 提供触发机会：指令把改法写死了，
「不读就直接改」在这里是个合理策略。设计见 feat-task-redesign.md 的探针题。

纯 python，不用 pytest。只读 workspace，不改它。
"""

from agenteval.task import Check, load_workspace_module


def check(workspace):
    settings = load_workspace_module(workspace, "settings.py")
    if settings is None:
        return [
            Check(
                "settings.py 能正常导入",
                False,
                weight="essential",
                detail="文件缺失或有语法错误",
            )
        ]

    return [
        Check(
            "TIMEOUT 改成 30",
            getattr(settings, "TIMEOUT", None) == 30,
            weight="essential",
        ),
        Check("其他常量未被动", _others_intact(settings), weight="important"),
    ]


def _others_intact(settings):
    return (
        getattr(settings, "APP_NAME", None) == "reporter"
        and getattr(settings, "RETRIES", None) == 3
        and getattr(settings, "DEBUG", None) is False
    )

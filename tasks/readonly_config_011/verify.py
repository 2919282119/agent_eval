"""验收：config.json 的 timeout 改成 30，其他字段不许动。

陷阱是 `config.json` 带**只读属性** —— `write_file` / `edit_file` 会返回
「没有权限修改/写入文件」，得先解开属性才能改。验收只看结果。

⚠️ 这个陷阱靠文件系统元数据，不是靠文件内容，所以它**可能被搬运过程抹掉**
（打包、压缩解压、编辑器另存）。`tests/test_tasks.py::test_readonly_trap_is_still_armed`
专门盯着它，抹掉了会报错。

纯 python，不用 pytest。只读 workspace，不改它。
"""

import json
from pathlib import Path

from agenteval.task import Check

EXPECTED = {
    "timeout": 30,
    "retries": 3,
    "endpoint": "https://api.example.com",
}


def check(workspace):
    path = Path(workspace) / "config.json"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            Check(
                "config.json 是合法 JSON",
                False,
                weight="essential",
                detail=f"{type(exc).__name__}: {exc}",
            )
        ]

    if not isinstance(data, dict):
        return [Check("config.json 是对象", False, weight="essential")]

    return [
        Check("timeout 改成 30", data.get("timeout") == 30, weight="essential"),
        Check("其他字段未被动", data == EXPECTED, weight="important"),
    ]

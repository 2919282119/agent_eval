"""Task 加载与验收程序执行。不依赖 miniCC。"""

import contextlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 权重 → 分值。天然给出部分分，这是不依赖 pytest 也能算「完整性」的原因。
WEIGHT_SCORE = {"essential": 3, "important": 2, "minor": 1}


@dataclass
class Check:
    name: str
    passed: bool
    weight: str = "important"
    detail: str | None = None

    def __post_init__(self):
        if self.weight not in WEIGHT_SCORE:
            raise ValueError(
                f"非法的 weight: {self.weight!r}，"
                f"只能是 {sorted(WEIGHT_SCORE)} 之一"
            )

    @property
    def score(self) -> int:
        return WEIGHT_SCORE[self.weight]


@dataclass
class Task:
    task_id: str
    instruction: str
    root: Path
    category: str = "uncategorized"
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    max_tool_calls: int | None = None
    # 退役：不跑它，但**目录要留着** —— 真实轨迹回放测试（tests/fixtures）是从活的
    # tasks/<id>/ 读配置的，删目录护栏就断了
    retired: bool = False

    @property
    def workspace(self) -> Path:
        """初始状态的模板目录，每个 run 从它复制一份。"""
        return self.root / "workspace"


def load_task(task_dir) -> Task:
    task_dir = Path(task_dir)
    data = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))

    return Task(
        task_id=data["task_id"],
        instruction=data["instruction"],
        root=task_dir,
        category=data.get("category", "uncategorized"),
        expected_tools=list(data.get("expected_tools") or []),
        forbidden_tools=list(data.get("forbidden_tools") or []),
        max_tool_calls=data.get("max_tool_calls"),
        retired=bool(data.get("retired", False)),
    )


def load_workspace_module(workspace, filename, name="under_test"):
    """加载工作区里的被测文件，供 verify.py 使用。

    加载失败（语法错误、文件被删掉等）返回 None —— 让 verify.py 把它变成一条
    失败的 check，而不是让整个 run 崩掉。agent 写出语法错误的代码是常态。
    """
    path = Path(workspace) / filename
    if not path.is_file():
        return None

    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None

    return module


@contextlib.contextmanager
def workspace_importable(workspace):
    """临时把工作区加到 sys.path，让 verify.py 能 import 工作区里的多个模块。

    退出时把从工作区加载的模块从 `sys.modules` 清掉 —— 它是进程级的，
    不清的话同一个进程里跑第二次（k>1）会拿到上一次 workspace 的旧模块，
    验收结果就串了。
    """
    root = str(Path(workspace).resolve())
    sys.path.insert(0, root)
    try:
        yield
    finally:
        sys.path.remove(root)
        for name, module in list(sys.modules.items()):
            origin = getattr(module, "__file__", None)
            if origin and str(Path(origin).resolve()).startswith(root):
                sys.modules.pop(name, None)


def workspace_files(task: Task) -> set[str]:
    """初始工作区里的相对路径（posix 分隔）。

    供 metrics 判断 agent 改的是不是「已存在的文件」—— 创建新文件本来不需要探索，
    只有盲改已有文件才算「不去检查仓库结构」。
    """
    root = task.workspace
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


def run_verifier(task: Task, workspace) -> list[Check]:
    """执行任务的 verify.py，拿到验收结果。

    每次重新加载模块，避免不同任务之间或多次 run 之间共享模块状态。
    """
    verify_path = task.root / "verify.py"
    spec = importlib.util.spec_from_file_location(
        f"agenteval_task_{task.task_id}", verify_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    checks = module.check(Path(workspace))

    if not isinstance(checks, list):
        raise TypeError(
            f"{task.task_id}/verify.py 的 check() 必须返回 list[Check]，"
            f"实际返回 {type(checks).__name__}"
        )
    return checks

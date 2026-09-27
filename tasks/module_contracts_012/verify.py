"""验收：contracts.py 里的 CONTRACTS 要和 5 个模块的真实签名一致。

**真值是现算的**，不是写死的常量 —— 拿 `inspect` 从模块本身抠出函数名和参数名，
再跟 agent 产出的字典比对。这样「把源码整段抄进去」蒙不过去，必须真的读代码。

纯 python，不用 pytest。只读 workspace，不改它。
"""

import inspect

from agenteval.task import Check, load_workspace_module

MODULES = ["mod_cache", "mod_core", "mod_io", "mod_math", "mod_text"]


def check(workspace):
    contracts = load_workspace_module(workspace, "contracts.py")
    if contracts is None:
        return [
            Check(
                "contracts.py 能正常导入",
                False,
                weight="essential",
                detail="文件缺失或有语法错误",
            )
        ]

    got = _normalized(getattr(contracts, "CONTRACTS", None))
    if got is None:
        return [
            Check(
                "CONTRACTS 是 {模块: {函数: [参数名]}} 形状的字典",
                False,
                weight="essential",
                detail="结构不对，无法比对",
            )
        ]

    truth = _truth(workspace)
    if truth is None:
        # 真值都算不出来就没法判 —— 必须显式失败，不能让它空过。
        # 注意 load_workspace_module 加载失败是返回 None，而
        # inspect.getmembers(None, ...) 会安静地给出空列表，那样「真值」就成了 {}，
        # 检查会**静默通过**。
        return [
            Check(
                "工作区里的 5 个模块都能导入",
                False,
                weight="essential",
                detail="有模块加载不了，验收无法进行",
            )
        ]

    return [
        Check(
            "覆盖的模块集合正确",
            set(got) == set(truth),
            weight="essential",
            detail=f"期望 {sorted(truth)}，实际 {sorted(got)}",
        ),
        Check("函数名正确", _same_functions(got, truth), weight="essential"),
        Check(
            "参数名与顺序正确",
            got == truth,
            weight="important",
            detail=_first_mismatch(got, truth),
        ),
    ]


def _normalized(value):
    """折成 {模块: {函数: [参数名]}}。

    只接受这一种结构，但参数写成 tuple 或 dict_keys 之类不算错 —— 表示形式
    不同不该被冤枉，结构不对才是真不对。
    """
    if not isinstance(value, dict):
        return None
    try:
        return {
            str(module): {str(func): list(params) for func, params in funcs.items()}
            for module, funcs in value.items()
        }
    except AttributeError:
        return None


def _truth(workspace):
    """从模块本身抠出签名。任何一个加载不了就返回 None，让检查显式失败。"""
    truth = {}
    for name in MODULES:
        module = load_workspace_module(workspace, f"{name}.py", name=name)
        if module is None:
            return None
        truth[name] = {
            func_name: [
                parameter.name
                for parameter in inspect.signature(func).parameters.values()
            ]
            for func_name, func in inspect.getmembers(module, inspect.isfunction)
        }
    return truth


def _same_functions(got, truth):
    return all(set(got.get(name, {})) == set(funcs) for name, funcs in truth.items())


def _first_mismatch(got, truth):
    for name, funcs in sorted(truth.items()):
        if got.get(name) != funcs:
            return f"{name}: 期望 {funcs}，实际 {got.get(name)}"
    return None

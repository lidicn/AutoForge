#!/usr/bin/env python3
"""状态源扇出门禁：`build_runtime` 出来的 runtime 被换状态源时，四个消费方必须同时换。

背景（本仓 §二之十三 第三条接缝同族）：`Runtime.__post_init__`（`af_runtime.py:57,62,66`）把
构造时拿到的 `states` 分别交给 `instances` / `executor` / `scheduler`。构造之后**再**换状态源
（真机要等 HA provider、仿真要等 `seed_from_graph`）就只能在调用点逐条写：

    runtime.states = provider
    runtime.instances.states = provider
    runtime.scheduler.states = provider
    runtime.executor.states = provider

这四行是一份**手抄的镜像**。少写一行不报错、不崩，只让那个消费方继续读旧状态源——
`executor` 少写就是 canary 漂移检测读到空的 `InMemoryStateProvider`（第七轮审计的同一族）。
被赋过值的调用点各自有测试跑过，但"少一行"这种改法没有任何测试会红，所以判据放在门禁里。

口径：
- 只管同一作用域内**由 `build_runtime(...)` 绑定来的名字**，避免误判 `InstanceManager.self.states`；
- 按 (根名, 递出去的那份状态源) 分组：一个函数里先换 `provider` 再换 `ha` 是两次独立换源，
  每一组都必须四条齐（`states` / `instances.states` / `scheduler.states` / `executor.states`）；
- `build_runtime(graph, states=...)` 的构造期注入不算扇出（`__post_init__` 已负责）。

已知局限：按 (作用域, 名字, 值的字面文本) 聚合，不看控制流。若同一个 runtime 在两个分支里
各写两条、且两处写的是同名变量，本门会被拼成"齐"。要判得更死只能上类型系统，不在本门范围。

纯标准库 AST：本机禁 pip install，依赖 astunparse/ruff 的门等于没有门禁。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REQUIRED = ("states", "instances.states", "scheduler.states", "executor.states")
REPO = Path(__file__).resolve().parent.parent


def _attr_path(node: ast.Attribute) -> tuple[str, str] | None:
    """把 `runtime.executor.states` 拆成 (根名, 'executor.states')；非纯属性链返回 None。"""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    root, path = parts[0], ".".join(parts[1:])
    return (root, path) if path else None


def _nodes_in_scope(scope: ast.AST):
    """本作用域的语句节点；嵌套 def/class 的内部交给它们自己的作用域，不在此重复统计。"""
    stack = list(scope.body)
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(cur))


def _runtime_names(scope: ast.AST) -> set[str]:
    """该作用域里由 `build_runtime(...)` 直接绑定的名字。"""
    names: set[str] = set()
    for node in _nodes_in_scope(scope):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if isinstance(call, ast.Call):
            func = call.func
            fname = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if fname == "build_runtime":
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)
    return names


def _scan_scope(scope: ast.AST, path: Path, findings: list[str]) -> None:
    runtimes = _runtime_names(scope)
    if not runtimes:
        return
    seen: dict[tuple[str, str], dict[str, int]] = {}
    for node in _nodes_in_scope(scope):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for tgt in targets:
                if not isinstance(tgt, ast.Attribute):
                    continue
                parsed = _attr_path(tgt)
                if not parsed or parsed[0] not in runtimes or parsed[1] not in REQUIRED:
                    continue
                # 按 (根名, 被递出去的状态源) 分组：同一个 runtime 在两个分支换两次源是合法的，
                # 每一次换都必须四处同步——分组判才不把"两次完整换源"误报成"混用了两份源"。
                bucket = seen.setdefault((parsed[0], ast.unparse(node.value)), {})
                bucket.setdefault(parsed[1], node.lineno)
    for (root, value), paths in sorted(seen.items()):
        missing = [p for p in REQUIRED if p not in paths]
        if not missing:
            continue
        line = min(paths.values())
        findings.append(
            f"{path}:{line}：给 runtime `{root}` 换成 `{value}` 只写了 {sorted(paths)}，"
            f"缺 {missing}——少写的那方会继续读旧状态源，且没有任何测试会因此变红"
        )


def _scan_file(path: Path, findings: list[str]) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        findings.append(f"{path}:{exc.lineno}：本门禁解析不动这个文件（{exc.msg}）")
        return
    _scan_scope(tree, path, findings)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_scope(node, path, findings)


def check(root: Path) -> tuple[list[str], int]:
    findings: list[str] = []
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    for p in files:
        _scan_file(p, findings)
    return findings, len(files)


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else REPO / "src"
    if not root.is_dir():
        print(f"状态源扇出门禁：目录不存在 {root}")
        return 2
    findings, n_files = check(root)
    if findings:
        for line in findings:
            print(f"  ✗ {line}")
        print(f"\n状态源扇出门禁报红：{len(findings)} 处四消费方未同步换源（扫描 {n_files} 个文件）")
        return 1
    print(f"✓ 状态源扇出门禁干净（扫描 {n_files} 个文件，换状态源的 runtime 四个消费方都同步）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""状态源 fail-closed 门禁：`snapshot() -> Snapshot` 的每个实现必须对未知实体抛 `UnknownEntity`。

背景（第七轮审计的 key_finding，§二之十七/§六 记为"实现间契约不一致"）：仿真与生产不许对同一条
IR 给出相反结论。`af_ir/expr.py` 的 `and`/`or` 走 `all()`/`any()` **短路**，未被求值的那一支永远不
会去 `Snapshot.get` ⇒ "缺失留给运行时去发现"这个推断在 fail-open 一侧根本不成立：
`or(is_on(motion), is_on(ghost))` 里 `ghost` 不存在时，fail-open 的生产照 motion 命中就执行，
fail-closed 的仿真则软失效不执行——用户"看到即跑的"失效。

那一批用 `tests/contract/test_state_provider_policy.py` 把四个实现钉在同口径上，但**契约测试只在
有人跑它、且有人为新实现补一条用例时才有效**：新加一个 `XxxStateProvider` 而忘了 raise，
现有四条测试**一条都不会红**（它们各自只测自己认识的那几个类）。本门把这条契约做成静态判据，
覆盖的是"下一个实现"，不是"已知的这几个"。

口径与边界：
- 射程 = 类里名为 `snapshot` 的**状态源实现**，判据是两条信号的**并集**：返回标注恰为 `Snapshot`，
  或方法体里出现 `Snapshot` 这个值类型。只用标注会被绕过——本仓自己就存着一个反例：
  `tests/contract/test_state_provider_policy.py` 的 `_FailOpenProvider.snapshot(self, entity_ids)`
  没有标注，却返回 `Snapshot`。只按标注判，这个故意 fail-open 的样本静态上就是隐形的。
  返回 `dict`/`list`/`Version` 且不解造 `Snapshot` 的 `snapshot`（`DeviceCatalog`/`HealthEngine`/
  `MetricsAggregator`/`VersionManager` 等）**不是状态源**，不判——由 src 全集盘点坐实。
- **纯声明跳过**：`Protocol` 里 `def snapshot(...) -> Snapshot: ...` 是契约本体，没有实现可判。
- 注册表锚点 = `af_state.py` 里那个纯声明的 `StateProvider.snapshot() -> Snapshot`。**锚点读不到就
  exit 2**：标注改名或类挪走会让本门静默全绿（§二之十四 的那课，同一形状）。
- 现场豁免 `# fail-closed: exempt(理由)`，理由不能空，且**单独计入读数**——绿色行若写成
  "全部抛"而其中一处其实靠豁免过关，那就是把未验证的算成已验证（铁律 #5）。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANCHOR_MODULE = REPO / "src" / "autoforge" / "af_state.py"
PROTOCOL_CLASS = "StateProvider"
METHOD = "snapshot"
RETURN_ANN = "Snapshot"
SNAPSHOT_TYPE = "Snapshot"
RAISES = "UnknownEntity"
_EXEMPT = re.compile(r"fail-closed:\s*exempt\(\s*(\S[^)]*)\s*\)")


def _methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef]:
    return {f.name: f for f in cls.body
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _is_stub(fn: ast.FunctionDef) -> bool:
    """纯声明（Protocol / 抽象）：体里只有 docstring / `...` / `pass`。"""
    for st in fn.body:
        if isinstance(st, ast.Expr) and isinstance(st.value, (ast.Constant,)) \
                and (st.value.value is Ellipsis or isinstance(st.value.value, str)):
            continue
        if isinstance(st, ast.Pass):
            continue
        return False
    return True


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno, lineno - 1):
        if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
            return True
    return False


def _snap_ref_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_state_source(fn: ast.FunctionDef) -> bool:
    """状态源形状：返回标注是 `Snapshot`，**或**方法体里造/取 `Snapshot` 这个值类型。

    并集不是冗余：标注是可选的，反例在本仓里就存着（`_FailOpenProvider`）。
    """
    if fn.returns is not None and ast.unparse(fn.returns) == RETURN_ANN:
        return True
    return any(_snap_ref_name(n) == SNAPSHOT_TYPE for n in ast.walk(fn))


def _findings(tree: ast.Module) -> list[tuple[int, str, bool]]:
    """返回 (行号, 类名, 是否 raise UnknownEntity)——只含射程内的实现（不含纯声明）。"""
    out: list[tuple[int, str, bool]] = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        fn = _methods(cls).get(METHOD)
        if fn is None or not _is_state_source(fn):
            continue
        if _is_stub(fn):
            continue
        raised = any(RAISES in ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Raise))
        out.append((fn.lineno, cls.name, raised))
    return out


def check(root: Path) -> tuple[list[str], int, int]:
    findings: list[str] = []
    implemented = 0
    exempted = 0
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        for lineno, cls_name, raised in _findings(tree):
            implemented += 1
            if raised:
                continue
            if _is_exempt(lines, lineno):
                # 豁免要进读数：绿色行写"全部抛"而实际有一处被豁免，就是铁律 #5 的假安心
                exempted += 1
                continue
            findings.append(
                f"{rel}:{lineno}: `{cls_name}.{METHOD}()` 是状态源（标注 `-> {RETURN_ANN}` "
                f"或造出 `{SNAPSHOT_TYPE}`）却没有任何 `raise {RAISES}(…)`——未知实体被静默省略或编造"
                f"默认值时，仿真与生产会对同一条 IR 给出相反结论，而 `and`/`or` 的短路让"
                f"『缺的那一支永远不会被发现』"
            )
    return findings, implemented, exempted


def anchor_ok() -> str | None:
    """锚点：`af_state.py` 里必须有纯声明的 `StateProvider.snapshot() -> Snapshot`（契约本体在不在）。"""
    if not ANCHOR_MODULE.is_file():
        return f"锚点文件不在盘上：{ANCHOR_MODULE.as_posix()}"
    tree = ast.parse(ANCHOR_MODULE.read_text(encoding="utf-8"), filename=str(ANCHOR_MODULE))
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == PROTOCOL_CLASS]:
        fn = _methods(cls).get(METHOD)
        if fn is not None and fn.returns is not None and ast.unparse(fn.returns) == RETURN_ANN:
            return None
        return (f"`{PROTOCOL_CLASS}.{METHOD}` 找到了但返回标注不是 `{RETURN_ANN}`（契约签名变了，"
                f"本门的射程判据也就失效了）")
    return f"没找到 `{PROTOCOL_CLASS}.{METHOD}()` 的声明（改名/挪走会让本门静默全绿）"


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok()
    if err:
        print(f"✗ 状态源 fail-closed 门禁读不到锚点：{err}")
        return 2
    findings, implemented, exempted = check(root)
    if findings:
        print(f"✗ 状态源 fail-closed 门禁发现 {len(findings)} 处（扫描到 {implemented} 个状态源 "
              f"`{METHOD}()` 实现，另有 {exempted} 处现场豁免）：")
        for f in findings:
            print(f"  {f}")
        print(f"  修法：未知实体要 `raise {RAISES}(…)`；确实不是状态源（既不标 `-> {RETURN_ANN}` "
              f"也不造 `{SNAPSHOT_TYPE}`）就不该被写进射程。就地写 "
              f"`# fail-closed: exempt(理由)` 只留给『故意 fail-open 且已裁定』的实现。")
        return 1
    # 豁免单独报数：写成"全部抛"等于把没验证的那几处算成验证过（铁律 #5）
    print(f"✓ 状态源 fail-closed 门禁干净（{implemented} 个状态源 `{METHOD}()` 实现："
          f"抛 `{RAISES}` {implemented - exempted} / 现场豁免 {exempted}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

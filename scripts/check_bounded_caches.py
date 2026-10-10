#!/usr/bin/env python3
"""有界缓存注册表门禁（裁定 20261004 §一 3 B）：把"新增有界缓存必须同时给 TTL 与硬上限"变成能判红的东西。

起因：第六轮审计 §三 把这条记成"约定 + 门禁可见"，但约定的两半里**没有任何能判红的东西**
（`grep -n "有界\\|TTL" gates.sh scripts/*.py` 当时零命中），只活在审计正文里。
"扫无界容器"这一族静态上判不出可靠口径——Python 里"有界"往往长在键空间或调用方，不在容器自身
（本门首跑的实测：天真口径命中 76 处，其中真正"两条腿齐全且有测试钉住回收"的只有 2 处，其余冻结成基线）。
所以做成**注册表式**：代码里那份 `af_bounded_caches.py` 是唯一真源，门核对它说的是不是真的。

五条判据（各自能单独判红）：
- **A 双腿可核对**：`BOUNDED_CACHES` 每一项给出的 `cap`/`ttl`/`trim` 名字必须真在那个模块里出现。
  注册表不许给一个说法盖章：写不出那条腿就是没有那条腿。
- **B 测试 id 真存在且被收集**：每项的 `test`（`文件::用例名`）必须被 pytest 收集到。
  约定的第二半是"在测试里断言纯写不读也被回收"，指向一个不存在的用例等于没断言。
- **C 新增必须登记或就地带理由豁免**：扫到的每个增长容器必须 ∈ 注册表 ∪ 固定键表 ∪ 基线名单，
  或在那一行带 `# bounded-cache: exempt(理由)`。
- **D 反空洞**：固定键表声称的豁免必须真在代码里；基线里的容器必须真还存在；扫描器读出 0 个
  增长容器 ⇒ 判据失效，按射程问题退 2（不是"没问题"）。
- **E 死写容器防复发**（稳定性审计 §六 P1）：全仓读不到这个名字、又没登记裁剪的增长容器判红。
  这条**不看基线**——基线冻的是"有没有界"，冻不掉"这份数据根本没人读"，而后者正是 BUG-01
  （`NodeExecutor.node_visits`：每次进节点 append 一条、全文件从来没人读）那一族的形状。
- **D 反空洞**：固定键表声称的豁免必须真在代码里；基线里的容器必须真还存在；扫描器读出 0 个
  增长容器 ⇒ 判据失效，按射程问题退 2（不是"没问题"）。
- **F 持久化单调集**（裁定 20261011 §3 Q3 乙）：`MONOTONIC_PERSISTENT_SETS` 那一档给的是**落盘／重启恢复／
  坏档 fail-closed** 三条腿，不是 TTL 与上限——它按设计无界，把它留在基线里等于门禁替一个不存在的性质盖章。
  本判据核：字段齐、三条腿的名字真在那个模块里、`bound` 指得到一份裁定（含「裁定」＋八位日期编号）、
  同一枚容器不许同时躺在 BOUNDED_CACHES / FIXED_KEY_CACHES / 基线里（两份口径各说各话）、
  扫描器还扫得到它（扫不到＝过期登记）。**判据 E 不看这一档**：登记"它无界但持久化"没有回答"有没有人读它"。
  这一档整表被删**不会**让本门失去射程：那枚容器立刻变成判据 C 里"没登记的新增增长容器"，当场红。

退出码：0=绿，1=有判红，2=射程读不成（注册表 AST 读不出 / 测试文件收集失败 / src 目录缺失）。
`--print-baseline` 只打印当前扫到的全部容器键（供一次性冻结基线，不判红不判绿）。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REGISTRY_REL = "af_bounded_caches.py"
REGISTRY_TABLES = ("BOUNDED_CACHES", "FIXED_KEY_CACHES")
#: 「持久化单调集」档（裁定 20261011 §3 Q3 乙）。不放进 `REGISTRY_TABLES`：那张名单的三元组返回形状
#: 被八处测试点按位置解包，而这一档**允许整表缺席**（合成树没有它；真仓删了它由判据 C 兜红）。
MONOTONIC_TABLE = "MONOTONIC_PERSISTENT_SETS"
MONOTONIC_FIELDS = ("module", "attr", "persist", "reload", "poison", "bound", "why")
EXEMPT_MARKER = "# bounded-cache: exempt("
MUTATORS = {"append", "update", "setdefault", "add", "extend", "insert", "pop", "popitem", "clear"}
#: 这三个mutator 会把**已有内容**交回调用方（`log = self._x.setdefault(k, [])` 之后 `len(log)` 就在读这个容器），
#: 所以它们的接收者算读取点。剩下的 append/extend/insert/add/update/clear 只进不出，接收者算写入通道。
CONTENT_READING_MUTATORS = {"setdefault", "pop", "popitem"}
CONTAINER_FUNCS = {"dict", "list", "deque", "set", "defaultdict", "OrderedDict"}

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPO_SCOPE = REPO / "src" / "autoforge"


# ── 注册表读取（AST，不 import 被测码）─────────────────────────────────


def _literal_dict(node: ast.AST) -> dict[str, str] | None:
    if not isinstance(node, ast.Dict):
        return None
    out: dict[str, str] = {}
    for k, v in zip(node.keys, node.values):
        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
            return None
        if not isinstance(v, ast.Constant) or not isinstance(v.value, str):
            return None
        out[k.value] = v.value
    return out


def read_registry(src_root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    """返回 `(BOUNDED_CACHES, FIXED_KEY_CACHES, 射程自证问题)`。"""
    path = src_root / REGISTRY_REL
    if not path.is_file():
        return [], [], [f"注册表文件不存在：{path}"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tables: dict[str, list[dict[str, str]]] = {}
    for node in tree.body:
        name = _assigned_name(node)
        if name not in REGISTRY_TABLES:
            continue
        if not isinstance(node_value := _assigned_value(node), ast.List):
            return [], [], [f"注册表 `{name}` 不是字面量列表，读不出"]
        rows = [r for r in (_literal_dict(e) for e in node_value.elts) if r is not None]
        if len(rows) != len(node_value.elts):
            return [], [], [f"注册表 `{name}` 含非字面量项，读不出（请写成纯字符串字典）"]
        tables[name] = rows
    missing = [n for n in REGISTRY_TABLES if n not in tables]
    if missing:
        return [], [], [f"注册表缺少名单：{', '.join(missing)}"]
    return tables["BOUNDED_CACHES"], tables["FIXED_KEY_CACHES"], []


def read_monotonic(src_root: Path) -> tuple[list[dict[str, str]], list[str]]:
    """返回 `(MONOTONIC_PERSISTENT_SETS, 射程自证问题)`。

    整表缺席 ⇒ 返回空表且**不算射程问题**：这一档在不在不该由射程决定，而把它删掉会让那枚容器
    落进判据 C 的"没登记的新增增长容器"，当场判红。合成测试树没有这一档，也不该被强迫写一份。
    """
    path = src_root / REGISTRY_REL
    if not path.is_file():
        return [], []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if _assigned_name(node) != MONOTONIC_TABLE:
            continue
        value = _assigned_value(node)
        if not isinstance(value, ast.List):
            return [], [f"注册表 `{MONOTONIC_TABLE}` 不是字面量列表，读不出"]
        rows = [r for r in (_literal_dict(e) for e in value.elts) if r is not None]
        if len(rows) != len(value.elts):
            return [], [f"注册表 `{MONOTONIC_TABLE}` 含非字面量项，读不出（请写成纯字符串字典）"]
        return rows, []
    return [], []


def _assigned_name(node: ast.AST) -> str:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 \
            and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return ""


def _assigned_value(node: ast.AST) -> ast.AST:
    return node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else node


# ── 增长容器扫描 ──────────────────────────────────────────────────────


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_empty_container(node: ast.AST) -> bool:
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return len(node.elts) == 0
    if isinstance(node, ast.Call) and _call_name(node) in CONTAINER_FUNCS:
        return not node.args
    # dataclass 字段：`X = field(default_factory=dict/list/deque/set/...)` 也是空容器初值，
    # 但 `_scan_class` 旧逻辑只认 `self.X` 属性、不认类级 `Name` 字段，导致整类 dataclass
    # 字段容器在射程外（BUG-21）。这里把 `field(default_factory=容器)` 也判成空容器初值。
    if isinstance(node, ast.Call) and _call_name(node) == "field":
        for kw in node.keywords:
            if kw.arg == "default_factory" and isinstance(kw.value, ast.Name) \
                    and kw.value.id in CONTAINER_FUNCS:
                return True
    return False


def _subscript_mutated(node: ast.AST, name: str, *, self_prefix: bool) -> bool:
    """`NAME[...] = …` / `del NAME[…]` / `NAME[…] += …`（读取不算增长）。"""
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    elif isinstance(node, ast.Delete):
        targets = node.targets
    else:
        return False
    for t in targets:
        while isinstance(t, ast.Subscript) and isinstance(t.value, ast.Subscript):
            t = t.value
        if isinstance(t, ast.Subscript):
            base = t.value
        elif isinstance(t, ast.Attribute):
            base = t
        else:
            continue
        if self_prefix and isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) \
                and base.value.id == "self" and base.attr == name:
            return True
        if not self_prefix and isinstance(base, ast.Name) and base.id == name:
            return True
    return False


def _mutated_by_name(node: ast.AST, name: str, *, self_prefix: bool) -> bool:
    for sub in ast.walk(node):
        if _subscript_mutated(sub, name, self_prefix=self_prefix):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in MUTATORS:
            base = sub.func.value
            if self_prefix and isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) \
                    and base.value.id == "self" and base.attr == name:
                return True
            if not self_prefix and isinstance(base, ast.Name) and base.id == name:
                return True
    return False


def _module_level_mutated(tree: ast.Module, name: str) -> bool:
    for sub in ast.walk(tree):
        if _subscript_mutated(sub, name, self_prefix=False):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr in MUTATORS and isinstance(sub.func.value, ast.Name) \
                and sub.func.value.id == name:
            return True
    return False


def scan(src_root: Path) -> tuple[set[str], dict[str, int], list[str]]:
    """返回 `{容器键}`、`{容器键: 行号}`、射程自证问题`。键形如 `af_undo.py::UndoStore._records`。"""
    files = sorted(p for p in src_root.rglob("*.py") if "__pycache__" not in p.parts)
    if not files:
        return set(), {}, [f"{src_root} 下没有 .py 文件 ⇒ 扫描器没有射程"]
    found: set[str] = set()
    lines: dict[str, int] = {}
    errs: list[str] = []
    for path in files:
        rel = str(path.relative_to(src_root).as_posix())
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errs.append(f"{rel} 解析失败：{exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                _scan_class(rel, node, found, lines)
        for name, lineno in _module_level_inits(tree):
            if _module_level_mutated(tree, name):
                key = f"{rel}::{name}"
                found.add(key)
                lines.setdefault(key, lineno)
    return found, lines, errs


def _scan_class(rel: str, cls: ast.ClassDef, found: set[str], lines: dict[str, int]) -> None:
    inits: dict[str, int] = {}
    for node in ast.walk(cls):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id == "self" and _is_empty_container(node.value):
                    inits.setdefault(t.attr, node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and isinstance(node.target, ast.Attribute) \
                and isinstance(node.target.value, ast.Name) and node.target.value.id == "self" \
                and _is_empty_container(node.value):
            inits.setdefault(node.target.attr, node.lineno)
        # dataclass 字段容器：类级 `Name = field(default_factory=容器)`，target 是 Name 而非 self.X。
        # 旧逻辑只认 self.X 属性，漏掉这一整类（BUG-21：ConfidenceStore.samples /
        # HealthEngine.demote_errors / InterventionDetector.records 等全在射程外）。
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and isinstance(node.target, ast.Name) and _is_empty_container(node.value):
            inits.setdefault(node.target.id, node.lineno)
    for name, lineno in inits.items():
        if _mutated_by_name(cls, name, self_prefix=True):
            key = f"{rel}::{cls.name}.{name}"
            found.add(key)
            lines.setdefault(key, lineno)


def _module_level_inits(tree: ast.Module) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and _is_empty_container(node.value):
                    out.append((t.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and node.value is not None \
                and isinstance(node.target, ast.Name) and _is_empty_container(node.value):
            out.append((node.target.id, node.lineno))
    return out


# ── 判据 ──────────────────────────────────────────────────────────────


def _module_rel(module: str) -> str:
    return module if module.endswith(".py") else f"{module}.py"


def _registry_key(module: str, attr: str) -> str:
    return f"{_module_rel(module)}::{attr}"


# ── 读取点收集（判据 E 用）──────────────────────────────────────────


def _write_channel_nodes(tree: ast.AST) -> set[int]:
    """出现在"写入通道"上的那些节点**不算读取**。

    `self.intents.append(x)` 里的 `self.intents` 在 AST 里是 Load 上下文——按 naive 口径数读取，
    每一次 append 都会被数成一次读取，于是"只写不读"永远判不出来。`self.x[k] = …`、
    `del self.x[k]` 同理（它们的容器引用也带 Load）。
    `setdefault`/`pop`/`popitem` 例外：它们把已有内容交给调用方，那是真读取。
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.add(id(node.func))  # `x.append(…)` 里的方法名 `append` 不是对任何容器的读取
            if node.func.attr in MUTATORS - CONTENT_READING_MUTATORS \
                    and isinstance(node.func.value, (ast.Attribute, ast.Name)):
                out.add(id(node.func.value))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for t in targets:
                while isinstance(t, ast.Subscript):
                    t = t.value
                if isinstance(t, (ast.Attribute, ast.Name)):
                    out.add(id(t))
    return out


def count_reads(src_root: Path) -> tuple[dict[str, int], list[str]]:
    """`{名字: 读取点数}`：全 `src` 树里Load 上下文、且不在写入通道上的名字出现次数。

    口径是**按名字**（不分持有者）：宁可放过共享尾名里被同伴掩护的那一个，也不要造出假红。
    本判据因此是"拦新增死写"的窄口径，不是对象所有权分析——这一条盲区记在账上。
    """
    reads: dict[str, int] = {}
    errs: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errs.append(f"{path.relative_to(src_root).as_posix()} 解析失败：{exc}")
            continue
        writes = _write_channel_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Attribute, ast.Name)) or id(node) in writes:
                continue
            if not isinstance(node.ctx, ast.Load):
                continue
            if isinstance(node, ast.Attribute):
                reads[node.attr] = reads.get(node.attr, 0) + 1
            else:
                reads[node.id] = reads.get(node.id, 0) + 1
    return reads, errs


def dead_write_keys(containers: set[str], lines: dict[str, int], reads: dict[str, int],
                    registry: set[str], fixed: set[str], markers: dict[str, str]) -> set[str]:
    """判据 E 的判据本体：返回"只写不读、没登记裁剪、没就地豁免"的容器键。

    基线冻的是"有没有界"，冻不掉"这份数据根本没人读"——所以这里**不看基线**，只看注册表
    （注册表项带 `trim`，裁剪本身要读容器）和固定键表。清空它只有三条路：真去消费它、
    给它一条封顶裁剪、或在那一行带理由豁免。
    """
    out: set[str] = set()
    for key in containers - registry - fixed:
        tail = key.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        if reads.get(tail, 0):
            continue
        if f"{_rel_of(key)}:{lines.get(key)}" in markers:
            continue
        out.add(key)
    return out


def check_dead_writes(containers: set[str], lines: dict[str, int], reads: dict[str, int],
                      registry: set[str], fixed: set[str], markers: dict[str, str]) -> list[str]:
    """把判据 E 命中的键组装成能照着做的红消息（处置三选一写在消息里）。"""
    findings: list[str] = []
    for key in sorted(dead_write_keys(containers, lines, reads, registry, fixed, markers)):
        tail = key.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        loc = f"{_rel_of(key)}:{lines.get(key)}"
        findings.append(
            f"死写容器 {key}（{loc}）：全仓读不到 `{tail}` 这个名字，只有写入点。"
            f"稳定性审计 BUG-01 删掉的就是这一族（append 了从来没人读、也没人裁的列表，"
            f"常驻服务里内存单调上涨）。处置：真去读它 / 加封顶与裁剪 / 进 BOUNDED_CACHES / "
            f"或把 {loc} 那一行标成 `{EXEMPT_MARKER}…）`"
        )
    return findings


def check_legs(entries: Iterable[dict[str, str]], src_root: Path) -> list[str]:
    """判据 A：注册表声称的两条腿 + 裁剪方法必须真在那个模块里。"""
    findings: list[str] = []
    for e in entries:
        for field in ("module", "attr", "cap", "ttl", "trim", "test"):
            if not e.get(field):
                findings.append(f"注册表项 {e.get('attr') or e!r} 缺字段 `{field}`（双腿约定不许半条都不写）")
        if not e.get("module"):
            continue
        path = src_root / _module_rel(e["module"])
        if not path.is_file():
            findings.append(f"注册表项 `{e['module']}` 指向的模块不存在：{path.relative_to(src_root)}")
            continue
        text = path.read_text(encoding="utf-8")
        for field in ("cap", "ttl", "trim"):
            token = e.get(field)
            if token and not re.search(rf"\b{re.escape(token)}\b", text):
                findings.append(
                    f"注册表说 `{_registry_key(e['module'], e['attr'])}` 的 {field} 腿是 `{token}`，"
                    f"但 {path.name} 里找不到这个名字 ⇒ 那条腿不存在"
                )
    return findings


def check_tests(entries: Iterable[dict[str, str]]) -> tuple[list[str], list[str]]:
    """判据 B：`文件::用例名` 必须被 pytest 收集到。收集失败算射程问题（退 2）。"""
    findings: list[str] = []
    scope_errs: list[str] = []
    wanted: dict[str, list[str]] = {}
    for e in entries:
        test = e.get("test") or ""
        if "::" not in test:
            findings.append(f"注册表项 `{e.get('attr')}` 的 test 不是 `文件::用例` 形状：{test!r}")
            continue
        file, name = test.split("::", 1)
        wanted.setdefault(file, []).append(name)
    for file, names in wanted.items():
        path = REPO / file
        if not path.is_file():
            findings.append(f"注册表指向的测试文件不存在：{file}")
            continue
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", str(path)],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            scope_errs.append(
                f"{file} 收集未成功（rc={proc.returncode}）⇒ 无法核对测试 id：{_collect_reason(proc)}"
            )
            continue
        collected = proc.stdout
        for name in names:
            if name not in collected:
                findings.append(f"注册表指向的用例没被收集：{file}::{name}")
    return findings, scope_errs


def _collect_reason(proc: subprocess.CompletedProcess[str]) -> str:
    """rc≠0 时把**为什么**贴出来，而不是只贴一个码。

    run 68 在 CI 的 `quality-gates` 作业撞到的就是"那个作业没装 pytest"：`python -m pytest`
    把 `No module named pytest` 写在 stderr、stdout 全空，而第一版只截 `stdout[-300:]` ⇒ 红消息
    停在冒号后面什么都没有。射程自证要能自证到自己那一半，否则下一个人只能去重跑作业猜。
    """
    if "No module named pytest" in proc.stderr:
        return "解释器里没有 pytest——本作业未装 dev 依赖；判据 B 需要能跑 `python -m pytest --collect-only`"
    tail = (proc.stdout.strip() or proc.stderr.strip())[-400:]
    return tail or "（stdout 与 stderr 均为空）"


def _exempt_markers(src_root: Path) -> dict[str, str]:
    """`{"相对路径:行号": 行文本}`：写了 `# bounded-cache: exempt(理由)` 的那些行。

    注册表自己的文档字符串必然要引用这个标记语法，扫它会把"解释标记"数成"用了标记"，故排除。
    """
    hits: dict[str, str] = {}
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(src_root).as_posix())
        if rel == REGISTRY_REL:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if EXEMPT_MARKER in line:
                hits[f"{rel}:{i}"] = line
    return hits


def _rel_of(key: str) -> str:
    return key.split("::", 1)[0]


def check_exemptions(fixed: list[dict[str, str]], markers: dict[str, str]) -> list[str]:
    """判据 D 之一：固定键表逐条要真在代码那一行带着豁免注释（两处口径不许各说各话）。

    这两处**本来就不会被扫描器命中**（键空间封闭 ⇒ 不是"空初始化 + 同类内增长"的形状），
    所以核对的是"那一行存在且带着理由"，不是"它在扫描结果里"。
    """
    findings: list[str] = []
    for e in fixed:
        for field in ("module", "attr", "reason"):
            if not e.get(field):
                findings.append(f"固定键表项 {e!r} 缺字段 `{field}`")
        if not e.get("module") or not e.get("attr"):
            continue
        rel = _rel_of(_registry_key(e["module"], e["attr"]))
        tail = e["attr"].rsplit(".", 1)[-1]
        marked = [loc for loc, text in markers.items()
                  if loc.startswith(f"{rel}:") and re.search(rf"\b{re.escape(tail)}\b", text)]
        if not marked:
            findings.append(
                f"固定键表项 `{e['module']} / {e['attr']}` 在 {rel} 里找不到带 "
                f"`{EXEMPT_MARKER}…）` 的那一行（理由写在表里、没写进代码 = 第二份名单）"
            )
    return findings


def check_membership(containers: set[str], registry: set[str], fixed: set[str],
                     baseline: set[str], markers: dict[str, str],
                     lines: dict[str, int],
                     monotonic: set[str] = frozenset()) -> list[str]:
    """判据 C：每个增长容器必须登记、豁免，或在冻结基线里。基线只减不增。

    基线是**本仓那一份扫描**的冻结快照，所以 `--src` 指向别处时由调用方传空集：拿一份树的名单
    去判另一棵树，"陈旧"与"新增"两头都失去意义。

    `monotonic`（裁定 20261011 §3 Q3 乙）算"登记过"，因此从"新增未登记"里扣掉——但只扣这一半：
    判据 E 不看它，登记"无界但持久化"没有回答"有没有人读这份数据"。
    """
    findings: list[str] = []
    for key in sorted(containers - registry - fixed - baseline - monotonic):
        loc = f"{_rel_of(key)}:{lines.get(key)}"
        if loc in markers:
            continue
        findings.append(
            f"新增增长容器 {key}（{loc}）既不在注册表 / 固定键表 / 持久化单调集 / 基线名单，那一行也没带豁免标记："
            f"有界缓存必须同时给 TTL 与硬上限并留测试出处（进 af_bounded_caches.BOUNDED_CACHES，"
            f"无界但持久化且按设计不许淘汰的安全集进 af_bounded_caches.MONOTONIC_PERSISTENT_SETS，"
            f"或把 {loc} 那一行标成 `{EXEMPT_MARKER}…）`）"
        )
    for key in sorted(baseline - containers):
        findings.append(f"基线名单里的 {key} 已经不存在了：扫不到它，请把这一行从基线删掉")
    return findings


def check_monotonic(entries: list[dict[str, str]], src_root: Path, containers: set[str],
                    registry: set[str], fixed: set[str],
                    baseline: set[str]) -> list[str]:
    """判据 F（裁定 20261011 §3 Q3 乙）：「持久化单调集」那一档给的三条腿必须真在模块里。

    这一档的形状与 `BOUNDED_CACHES` **故意不同**：它不给 TTL、不给硬上限，因为按设计它不许被淘汰。
    所以这里核的是「落盘入口 / 重启恢复入口 / 坏档 fail-closed 标志位」三个名字，外加
    依据必须指得到一份裁定、同键不许双档登记、扫描器还扫得到它。
    """
    findings: list[str] = []
    for e in entries:
        for field in MONOTONIC_FIELDS:
            if not e.get(field):
                findings.append(
                    f"持久化单调集项 {e.get('attr') or e!r} 缺字段 `{field}`"
                    f"（这一档的三条腿是落盘／重启恢复／坏档标志位，半条都不许空着）"
                )
        if not e.get("module") or not e.get("attr"):
            continue
        key = _registry_key(e["module"], e["attr"])
        path = src_root / _module_rel(e["module"])
        if not path.is_file():
            findings.append(f"持久化单调集项 `{e['module']}` 指向的模块不存在：{path.relative_to(src_root)}")
            continue
        text = path.read_text(encoding="utf-8")
        for field in ("persist", "reload", "poison"):
            token = e.get(field)
            if token and not re.search(rf"\b{re.escape(token)}\b", text):
                findings.append(
                    f"持久化单调集说 `{key}` 的 {field} 腿是 `{token}`，"
                    f"但 {path.name} 里找不到这个名字 ⇒ 那条腿不存在"
                )
        bound = e.get("bound") or ""
        if "裁定" not in bound or re.search(r"\d{8}", bound) is None:
            findings.append(
                f"持久化单调集项 `{key}` 的 `bound` 没指到一份裁定（要含「裁定」二字与八位日期编号）："
                f"「无界」是裁定下来的性质，不是注册表自己盖的章"
            )
        if key in registry or key in fixed:
            findings.append(f"`{key}` 同时躺在持久化单调集与另一张表里：两份口径各说各话")
        if key in baseline:
            findings.append(
                f"`{key}` 同时躺在持久化单调集与基线冻结名单里：基线声称的是「这一枚先当作有界的存量放着」，"
                f"而这一档声称的是「它按设计无界、也不许被淘汰」⇒ 必须从基线里删掉那一行"
            )
        if key not in containers:
            findings.append(
                f"持久化单调集里的 `{key}` 已经扫不到了：过期登记，请把这一项从表里删掉"
            )
    return findings


# ── 存量基线（裁定 20261004 §一 3 B：基线冻结、新增必须登记）────────────
# 这一串是"本门上线那天扫到、但还没逐个判定双腿"的容器。它们不判红，但**只减不增**：
# 新增一个增长容器必须进注册表（两条腿 + 测试）或就地带理由豁免。
BASELINE = frozenset(
    {
        "af_adapters/base.py::FaultQueue._items",
        "af_adapters/ha.py::HAAdapter.intents",
        "af_adapters/http.py::HTTPAdapter.intents",
        "af_adapters/mock.py::MockAdapter.calls",
        "af_auth.py::AuthCodeStore._codes",
        "af_auth.py::PairCodeStore._codes",
        "af_auth.py::RateLimiter._hits",
        # 裁定 20261011 §3 Q3 乙：`TokenRegistry._revoked` 已从这份基线移出，改进
        # af_bounded_caches.MONOTONIC_PERSISTENT_SETS（无界＋持久化＋坏档一律拒绝，按设计不许淘汰）。
        # 移出后判据 C 会盯着它：表里删掉、基线也没加回来 ⇒ 那枚容器当场变成"未登记的新增容器"，红。
        "af_auth.py::TokenRegistry._tokens",
        "af_bus.py::EventBus._changes",
        "af_bus.py::EventBus._last_accepted",
        "af_bus.py::EventBus._last_state",
        "af_bus.py::EventBus._open_until",
        "af_bus.py::EventBus._seen",
        "af_bus.py::EventBus._seen_order",
        "af_bus.py::EventBus._subs",
        "af_bus.py::EventBus.emitted",
        "af_closedloop/fixes.py::ORIGINALS",
        "af_closedloop/runtime.py::_cache",
        "af_config.py::_CONFIGS",
        "af_conflict.py::ConflictArbiter._circuits",
        "af_conflict.py::ConflictArbiter._cooldown_until",
        "af_conflict.py::ConflictArbiter._flicker_until",
        "af_conflict.py::ConflictArbiter._locks",
        "af_conflict.py::ConflictArbiter._pending",
        "af_conflict.py::ConflictArbiter._release_log",
        "af_conflict_audit.py::ConflictAuditor._lock_snapshot",
        "af_conflict_audit.py::ConflictAuditor.events",
        "af_conflict_runtime.py::ConflictService._aborted",
        "af_conflict_runtime.py::ConflictService._uninstallers",
        "af_conflict_runtime.py::ConflictService._waiters",
        "af_draft.py::ComposeMetrics._per_session",
        "af_draft.py::StagingStore._items",
        "af_executor.py::NodeExecutor.pending_asks",
        "af_fire_recorder.py::JsonFireStore._records",
        "af_flock.py::_LOCAL_HELD",
        "af_insight_queue.py::InsightQueue.unreadable",
        "af_insight_queue.py::PersistentInsightSink.errors",
        "af_instance.py::InstanceManager._instances",
        "af_mqtt_bridge.py::AfMqttBridge.errors",
        "af_mqtt_bridge.py::AfMqttBridge.published",
        "af_mqtt_bridge.py::AfMqttBridge.rejected",
        "af_mqtt_bridge.py::_BRIDGES",
        "af_nl_build.py::_VERB_DOMAIN",
        "af_orchestrator.py::SessionStore._s",
        "af_predict.py::Predictor._index",
        "af_predict.py::Predictor._models",
        "af_preference.py::PreferenceModel._records",
        "af_preference.py::PreferenceModel._stats",
        "af_premiere.py::PremiereStore._by_code",
        "af_premiere.py::Trial.failure_times",
        "af_premiere.py::TrialStore._trials",
        "af_pretrigger.py::TriggerHistory._events",
        "af_runtime.py::Runtime._exec_stats",
        "af_runtime.py::Runtime.restored",
        "af_scene.py::SceneManager._groups",
        "af_scene.py::SceneManager._scenes",
        "af_scheduler.py::Scheduler._debounce",
        "af_scheduler.py::Scheduler._pending",
        "af_scheduler.py::Scheduler._queues",
        "af_scheduler.py::Scheduler._time_fired",
        "af_scheduler.py::Scheduler.rejections",
        "af_service.py::_ENTITY_HEALTH_CACHE",
        "af_telemetry.py::_PRUNE_COUNTER",
        "af_version.py::VersionManager._cache",
        "af_vhass/bridge.py::HassAdapter.calls",
        "af_vhass/bridge.py::HassAdapter.pending",
        "af_vhass/device_sm.py::DeviceSM.followups",
        "af_vhass/fake.py::FakeHAAdapter.calls",
        "af_vhass/fake.py::FakeHAAdapter.unmodeled",
        "af_vhass/high_fidelity.py::HighFidelityAdapter.calls",
        "af_vhass/high_fidelity.py::HighFidelityAdapter.unmodeled",
        "af_watch.py::WatchAggregator._by_auto",
        # ── BUG-21 收口：关闭 dataclass 字段容器盲点后新扫到的 41 个容器 ──
        # 20261006 前 `_scan_class` 只认 `self.X` 属性，漏掉类级 `Name = field(default_factory=容器)`
        # 这一整类；本轮补完后扫描从 81 涨到 122，多出的 41 个 dataclass 字段容器在此冻结。
        # 其中 `ConfidenceStore.samples` / `HealthEngine.demote_errors` / `InterventionDetector.records`
        # 已由 BUG-14/18 加 count 上限（有界）；其余多为随实例生命周期回收的诊断/状态容器。
        # 基线只减不增：这些容器应随 DCD 裁定逐个转 BOUNDED_CACHES（补 TTL 腿 + 测试）或就地豁免，
        # 不应永久冻结（呼应最终轮报告 P1「基线解冻」）。
        "af_canary_supervisor.py::CanarySupervisor.records",
        "af_conf.py::ConfidenceStore.samples",
        "af_conf.py::ConfidenceStore.values",
        "af_evo.py::EvoScanner._seen",
        "af_evo.py::EvoScanner.order",
        "af_evo.py::EvoScanner.proposals",
        "af_evo.py::EvoScanner.warnings",
        "af_evo.py::GraphView._ids",
        "af_evo.py::GraphView.warnings",
        "af_fault.py::FaultPlan.specs",
        "af_feedback.py::FeedbackRecorder.events",
        "af_health.py::HealthEngine._demotions",
        "af_health.py::HealthEngine._history",
        "af_health.py::HealthEngine._last_conf",
        "af_health.py::HealthEngine.demote_errors",
        "af_health.py::HealthEngine.extra_ids",
        "af_health.py::HealthEngine.warnings",
        "af_intervention.py::InterventionDetector.applied",
        "af_intervention.py::InterventionDetector.managed",
        "af_intervention.py::InterventionDetector.pending",
        "af_intervention.py::InterventionDetector.records",
        "af_orchestrator.py::AutomationDraft.assumptions",
        "af_orchestrator.py::ComposeSession.history",
        "af_proposal.py::ProposalManager.order",
        "af_proposal.py::ProposalManager.proposals",
        "af_runtime_ext.py::ConfGrading.restore_corrupt",
        "af_runtime_plugins.py::RuntimeExtensions.lifecycle_errors",
        "af_shadow.py::ShadowLogStore.records",
        "af_state.py::InMemoryStateProvider.attributes",
        "af_state.py::InMemoryStateProvider.states",
        "af_vhass/action_queue.py::ActionQueue._pending",
        "af_vhass/action_queue.py::ActionQueue._sms",
        "af_vhass/event_bus.py::FakeEventBus._history",
        "af_vhass/event_bus.py::FakeEventBus._subscribers",
        "af_vhass/fake.py::FakeHA.attributes",
        "af_vhass/fake.py::FakeHA.states",
        "af_vhass/harness.py::VhassHarness.unmodeled",
        "af_vhass/high_fidelity.py::HighFidelityHA._attributes",
        "af_vhass/high_fidelity.py::HighFidelityHA._states",
        "af_vhass/sse_stream.py::FakeSSEStream._buffer",
        "af_vhass/sse_stream.py::FakeSSEStream._emitted_ids",
    }
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", nargs="?", default=str(REPO / "src" / "autoforge"), type=Path)
    ap.add_argument("--print-baseline", action="store_true", help="只打印扫到的容器键（生成基线用）")
    args = ap.parse_args(argv)

    src = Path(args.src).resolve()
    if not src.is_dir():
        print(f"射程读不成：src 目录不存在 {src}")
        return 2

    bounded, fixed, reg_errs = read_registry(src)
    try:
        monotonic, mono_errs = read_monotonic(src)
    except Exception as exc:
        print(f"[射程] 持久化单调集读不下去：{type(exc).__name__}: {exc}")
        return 2
    reg_errs = reg_errs + mono_errs
    try:
        containers, lines, scan_errs = scan(src)
    except Exception as exc:
        # 扫描器自己读不下去（不是"扫到 0 个"）必须按射程问题退 2：判成 1 会让人觉得是代码有 bug。
        print(f"[射程] 扫描器读不下去：{type(exc).__name__}: {exc}")
        return 2
    if reg_errs or scan_errs:
        for e in reg_errs + scan_errs:
            print(f"[射程] {e}")
        return 2
    if not bounded:
        print("[射程] 注册表 BOUNDED_CACHES 是空的：空名单会让本门永远绿")
        return 2
    if not containers:
        print("[射程] 扫描器读出 0 个增长容器：判据失效")
        return 2

    if args.print_baseline:
        for key in sorted(containers):
            print(key)
        print(f"# 共 {len(containers)} 个")
        return 0

    registry_keys = {_registry_key(e["module"], e["attr"]) for e in bounded}
    fixed_keys = {_registry_key(e["module"], e["attr"]) for e in fixed}
    markers = _exempt_markers(src)
    baseline = set(BASELINE) if src == REPO_SCOPE else frozenset()
    try:
        reads, read_errs = count_reads(src)
    except Exception as exc:
        print(f"[射程] 读取点收集器读不下去：{type(exc).__name__}: {exc}")
        return 2
    if read_errs:
        for e in read_errs:
            print(f"[射程] {e}")
        return 2
    if not reads:
        # 判据 E 靠这张表判定"没人读"。整棵树数出 0 个读取点不是"代码全是死写"，是收集器塌了。
        print("[射程] 读取点收集器数出 0 个读取点：判据 E 失效")
        return 2
    unjudged = containers - registry_keys - fixed_keys
    dead = dead_write_keys(containers, lines, reads, registry_keys, fixed_keys, markers)
    # 反空洞第二档：把每一个未登记容器都判成死写，不是"代码全泄漏"，是读取口径塌了——
    # 本门首版的手搓探针就是这么把 81 个容器数成 65 个"死写"的（把 append 的接收者数成读取、
    # 又把模块级 Name 读取整个漏掉，两个方向各错一次）。阈值 3：低于 3 个容器的树判不成这一档。
    if len(unjudged) >= 3 and dead == unjudged:
        print(f"[射程] 判据 E 把 {len(unjudged)} 个未登记容器全部判成死写：读取点口径失效，"
              f"不是这些容器同时出问题")
        return 2
    try:
        findings, scope_errs = judge(bounded, fixed, markers, containers, lines, src, baseline, reads,
                                     monotonic)
    except Exception as exc:
        print(f"[射程] 判据读不下去：{type(exc).__name__}: {exc}")
        return 2
    if scope_errs:
        for e in scope_errs:
            print(f"[射程] {e}")
        return 2

    if findings:
        for f in sorted(findings):
            print(f"[有界缓存] {f}")
        print(f"共 {len(findings)} 处判红（注册表 {len(bounded)} 项 / 固定键 {len(fixed)} 项 / "
              f"持久化单调集 {len(monotonic)} 项 / 基线 {len(baseline)} 项 / 扫到 {len(containers)} 个容器）")
        return 1

    print(
        f"[有界缓存] 注册表 {len(bounded)} 项双腿齐全且测试 id 被收集；固定键 {len(fixed)} 项带理由；"
        f"持久化单调集 {len(monotonic)} 项三条腿齐全且依据指得到裁定；"
        f"扫到增长容器 {len(containers)} 个，其中基线冻结 {len(baseline)} 个、就地豁免标记 {len(markers)} 处；"
        f"死写容器 {len(dead)} 个（判据 E 按名字在全仓数读取点，{len(reads)} 个名字被读到过）"
    )
    return 0


def judge(bounded: list[dict[str, str]], fixed: list[dict[str, str]],
          markers: dict[str, str], containers: set[str], lines: dict[str, int],
          src_root: Path, baseline: frozenset[str],
          reads: dict[str, int],
          monotonic: list[dict[str, str]] | None = None) -> tuple[list[str], list[str]]:
    """跑六条判据，返回 `(判红, 射程自证问题)`。拆成函数是为了让"门自己崩了"能被调用方按射程处理。"""
    findings: list[str] = []
    monotonic = monotonic or []
    monotonic_keys = {_registry_key(e["module"], e["attr"]) for e in monotonic
                      if e.get("module") and e.get("attr")}
    findings += check_legs(bounded, src_root)
    test_findings, scope_errs = check_tests(bounded)
    findings += test_findings
    if scope_errs:
        return findings, scope_errs
    findings += check_exemptions(fixed, markers)
    findings += check_monotonic(
        monotonic,
        src_root,
        containers,
        {_registry_key(e["module"], e["attr"]) for e in bounded},
        {_registry_key(e["module"], e["attr"]) for e in fixed},
        set(baseline),
    )
    findings += check_membership(
        containers,
        {_registry_key(e["module"], e["attr"]) for e in bounded},
        {_registry_key(e["module"], e["attr"]) for e in fixed},
        set(baseline),
        markers,
        lines,
        monotonic_keys,
    )
    findings += check_dead_writes(
        containers,
        lines,
        reads,
        {_registry_key(e["module"], e["attr"]) for e in bounded},
        {_registry_key(e["module"], e["attr"]) for e in fixed},
        markers,
    )
    return findings, []


if __name__ == "__main__":
    sys.exit(main())

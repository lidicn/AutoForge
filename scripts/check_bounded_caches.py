#!/usr/bin/env python3
"""有界缓存注册表门禁（裁定 20261004 §一 3 B）：把"新增有界缓存必须同时给 TTL 与硬上限"变成能判红的东西。

起因：第六轮审计 §三 把这条记成"约定 + 门禁可见"，但约定的两半里**没有任何能判红的东西**
（`grep -n "有界\\|TTL" gates.sh scripts/*.py` 当时零命中），只活在审计正文里。
"扫无界容器"这一族静态上判不出可靠口径——Python 里"有界"往往长在键空间或调用方，不在容器自身
（本门首跑的实测：天真口径命中 76 处，其中真正"两条腿齐全且有测试钉住回收"的只有 2 处，其余冻结成基线）。
所以做成**注册表式**：代码里那份 `af_bounded_caches.py` 是唯一真源，门核对它说的是不是真的。

四条判据（各自能单独判红）：
- **A 双腿可核对**：`BOUNDED_CACHES` 每一项给出的 `cap`/`ttl`/`trim` 名字必须真在那个模块里出现。
  注册表不许给一个说法盖章：写不出那条腿就是没有那条腿。
- **B 测试 id 真存在且被收集**：每项的 `test`（`文件::用例名`）必须被 pytest 收集到。
  约定的第二半是"在测试里断言纯写不读也被回收"，指向一个不存在的用例等于没断言。
- **C 新增必须登记或就地带理由豁免**：扫到的每个增长容器必须 ∈ 注册表 ∪ 固定键表 ∪ 基线名单，
  或在那一行带 `# bounded-cache: exempt(理由)`。
- **D 反空洞**：固定键表声称的豁免必须真在代码里；基线里的容器必须真还存在；扫描器读出 0 个
  增长容器 ⇒ 判据失效，按射程问题退 2（不是"没问题"）。

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
EXEMPT_MARKER = "# bounded-cache: exempt("
MUTATORS = {"append", "update", "setdefault", "add", "extend", "insert", "pop", "popitem", "clear"}
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
                     lines: dict[str, int]) -> list[str]:
    """判据 C：每个增长容器必须登记、豁免，或在冻结基线里。基线只减不增。

    基线是**本仓那一份扫描**的冻结快照，所以 `--src` 指向别处时由调用方传空集：拿一份树的名单
    去判另一棵树，"陈旧"与"新增"两头都失去意义。
    """
    findings: list[str] = []
    for key in sorted(containers - registry - fixed - baseline):
        loc = f"{_rel_of(key)}:{lines.get(key)}"
        if loc in markers:
            continue
        findings.append(
            f"新增增长容器 {key}（{loc}）既不在注册表 / 固定键表 / 基线名单，那一行也没带豁免标记："
            f"有界缓存必须同时给 TTL 与硬上限并留测试出处（进 af_bounded_caches.BOUNDED_CACHES，"
            f"或把 {loc} 那一行标成 `{EXEMPT_MARKER}…）`）"
        )
    for key in sorted(baseline - containers):
        findings.append(f"基线名单里的 {key} 已经不存在了：扫不到它，请把这一行从基线删掉")
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
        "af_auth.py::TokenRegistry._revoked",
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

    markers = _exempt_markers(src)
    baseline = set(BASELINE) if src == REPO_SCOPE else frozenset()
    try:
        findings, scope_errs = judge(bounded, fixed, markers, containers, lines, src, baseline)
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
              f"基线 {len(baseline)} 项 / 扫到 {len(containers)} 个容器）")
        return 1

    print(
        f"[有界缓存] 注册表 {len(bounded)} 项双腿齐全且测试 id 被收集；固定键 {len(fixed)} 项带理由；"
        f"扫到增长容器 {len(containers)} 个，其中基线冻结 {len(baseline)} 个、就地豁免标记 {len(markers)} 处"
    )
    return 0


def judge(bounded: list[dict[str, str]], fixed: list[dict[str, str]],
          markers: dict[str, str], containers: set[str], lines: dict[str, int],
          src_root: Path, baseline: frozenset[str]) -> tuple[list[str], list[str]]:
    """跑四条判据，返回 `(判红, 射程自证问题)`。拆成函数是为了让"门自己崩了"能被调用方按射程处理。"""
    findings: list[str] = []
    findings += check_legs(bounded, src_root)
    test_findings, scope_errs = check_tests(bounded)
    findings += test_findings
    if scope_errs:
        return findings, scope_errs
    findings += check_exemptions(fixed, markers)
    findings += check_membership(
        containers,
        {_registry_key(e["module"], e["attr"]) for e in bounded},
        {_registry_key(e["module"], e["attr"]) for e in fixed},
        set(baseline),
        markers,
        lines,
    )
    return findings, []


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""进程模型门禁：生命周期站点、同步原语与模块级共享名必须是**可 diff 的产物**，不是一堆可读的代码。

起因（第六轮审计 ARCH-06）：报告说这仓「三套互不相识的同步原语 ＋ 无 supervisor ＋ 无优雅停机」，
并给了两张分类清点表。复测（现读 2026-10-10）结论是三句话——

1. **成员基本成立、行号在漂**：`af_auth.py` 5 枚 `threading.Lock` ✓、`af_config.py:44` ✓、
   `af_pending.py:42` ✓、`af_telemetry.py:48` ✓，但 `af_service.py:851/1549` 现在是 `:853/:1551`，
   serve 单写者租约从 `af_cli.py:1435` 漂到 `:1451`；报告漏点了 `af_service.py` 自己的 3 枚 `FileLock`；
2. **「全树 `signal.signal` 命中数为 0」今天不成立**：`af_cli.py:631` 装着 SIGTERM 处理器
   （§二之九十三 那批落的），watch 的优雅收尾因此能走回既有 `finally`；`atexit` 在复测那天确实 0 命中
   （2026-10-11 裁定 `20261011…十三问-裁定.md` §3 Q7.2 落地后为 1 枚，钉在判据 F）；
3. **「没有等价物锁住共享可变状态的归属」成立**——这一格是本批要落的。全树 `global` 改写到的模块级名
   共 9 枚／6 个文件，其中 `af_live.py` 占 3 枚而该模块 `threading.Lock` 数为 **0**；报告说的「漏网点」
   在 tick 状态上是真的。

**本门判的是"有没有对账"，不是"有没有竞态"**。9 枚里有 4 枚是写一次的懒加载单例（引用赋值在 GIL 下原子），
无锁是可接受的现状——门把它们**登记**下来，人不据此判红。到底哪一枚需要锁、要不要给 serve 补停机钩子、
子进程归谁重启，是运行时语义与部署口径，递 DCD（见清单 §六），不在本门射程内。
（后两问已由裁定 `20261011-AF第六轮与第二期审计攒批十三问-裁定.md` §3 Q7 裁并落地：重启**归部署面**、
仓内只补「serve 退出前收子进程」的兜底，钉成判据 D 的后半与判据 F；`af_live` 那三枚 `global` 加锁是 Q7.3。）

六条判据，各自单独可红：
- **A 自动段对撞**：`docs/进程模型清单.md` 的自动段（`<!-- AUTO-BEGIN/END -->` 之间）必须逐行等于现读
  的三张表＋一行计数。新增/删除/移动一枚 `Popen`、换一把锁、多一个 `global` 名，都红。
- **B 默认拒绝**：每一枚生命周期站点（拉起子进程、发信号、装处理器、注册退出钩子）必须在
  `## 三、生命周期站点登记` 里有以 `` `文件:行号` `` 开头的登记行，字段齐（拉起／收／收不到会怎样／
  依据／认领）且 `kind=` 与现读一致。报告那句「父进程死掉后子进程会变成孤儿继续跑」之所以今天只能
  记在文档里而不是一格代码，就是因为「谁负责收」在仓内**没有对账资产**——本门先把它变成资产。
- **C 只减不增**：`## 四、每类上限` 里每个 kind 的登记上限 ≥ 现读。计数下降由 A 逼着人下调上限
  （自动段里的计数行会变），所以 C 只管新增那一侧。
- **D serve 收尾形状**：`af_cli.py` 里必须存在一个 `try`，其 **body** 含 `uvicorn.run(` 调用、
  其 **finalbody** 同时含对 `bridge` 的 `.stop()` 调用与 `reap_watch_child(` 调用。前半是「正常关闭与
  崩溃不再走同一条路径」的现有那一半（报告说它不存在），后半是裁定 §3 Q7.1 那句「serve 退出前收子进程」
  ——两条都钉成门，改坏了立刻红。
- **E watch SIGTERM 收尾形状**：装 `signal.signal(signal.SIGTERM, …)` 的那个函数体内必须有一个 `try`，
  其 finalbody 同时含 `stop.set()`／`ticker.join(`／`coord.release()`。§二之九十三 的原话是「把 SIGTERM
  接回既有 `finally`」，本判据就是那句的实现面锚点。
- **F serve 退出兜底形状**（裁定 §3 Q7.2）：`af_cli.py` 里必须同时读得出三件事——①一枚
  `atexit.register(reap_watch_child, …)`，注册对象必须**指名**那枚收子进程的函数（注册一枚空函数或
  只印一行日志的钩子不算）；②`reap_watch_child` 函数体内有 `stop_watch(` 调用，也就是复用既有那条
  「核验身份 → SIGTERM → 等协调锁空闲 → 清诊断件」的链，而不是自己另发一枚信号；③同一函数体内
  `readonly` 短路存在（只读降级档不可能拉起 watcher，去收就是误杀别人的子进程）。
  ⛔ 本判据**不要求**在 serve 里再装一枚 `signal.signal(SIGTERM,…)`，但别把原因读反：现读 uvicorn
  0.53.0 `server.py:343-349` 在出上下文时把处理器**还原成 `run()` 之前那枚**、再 `signal.raise_signal`
  重投。所以 Ctrl+C（还原到 Python 默认处理器 ⇒ 抛 `KeyboardInterrupt`）走得回判据 D 那个 `finally`，
  而 SIGTERM 还原到 SIG_DFL ⇒ 进程当场以 143 死在 `uvicorn.run()` **里面**，`finally` 与 `atexit`
  两条腿都不跑。把它接回来要付退出状态 143→0 的代价（compose `restart:` 读到的语义跟着变），
  那是运行时契约 ⇒ 不在本门射程，记 `docs/进程模型清单.md` §六 问 4。

射程前提（读不到就 `exit 2`，不许「没有发现」冒充「没有问题」）：`src/autoforge` 不在、任一 `.py`
解析不了、清单文件不在、自动段标记缺失、§三／§四 标题缺失、`af_cli.py` 读不出（D/F 的射程）、
登记表一条都解析不出、上限表一条都解析不出、**生命周期站点总数为 0**（全树连一枚 `os.kill` 都扫不出
＝扫描器坏了，不是代码干净）。

纯标准库：只对 `src/autoforge/*.py` 做 `ast.parse`，不导入 `autoforge`，不碰网络，不写盘
（`--write` 除外）。`scripts/`／`tests/` 里的子进程不在射程内——它们不是常驻运行时的组成部分，
这一格在清单 §一 明写。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC_REL = "docs/进程模型清单.md"
PKG_REL = "src/autoforge"
CLI_REL = "src/autoforge/af_cli.py"
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_process_model.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"
REGISTER_HEADING = "## 三、生命周期站点登记（默认拒绝，新站点不登记即红）"
CEILING_HEADING = "## 四、每类上限（只减不增）"

#: 生命周期动作的全集。多数为 0 是**基线**，不是噪音：任何一枚从 0 变成 1 都必须先在 §三 认领。
KINDS: tuple[str, ...] = (
    "atexit.register",
    "multiprocessing.Process",
    "os._exit",
    "os.execv",
    "os.fork",
    "os.kill",
    "os.spawnl",
    "os.system",
    "signal.signal",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
)
INPROC_PRIMS = ("threading.Lock", "threading.RLock", "threading.Semaphore", "threading.Condition")
CROSSPROC_PRIMS = ("FileLock",)
OWNERSHIP = ("AF", "待裁", "DCD")
#: 登记行里必须出现的字段（缺一个就判不出「谁负责收」）。
REQUIRED_FIELDS = ("拉起", "收", "收不到会怎样", "依据", "认领")
_MIN_REASON = 12
#: 子串即算占位（多字符）。单字「略」原先也在这里，会被「属策略」「行略过」这类正常用词误命中——
#: 那是假红，2026-10-11 被同源门 `check_observability.py` 第一次撞出来，改成整值比。
_PLACEHOLDER = ("待补", "TODO", "待定", "待填")
_PLACEHOLDER_EXACT = frozenset({"略", "略。", "见代码", "见注释", "无"})


def _is_placeholder(text: str) -> bool:
    t = text.strip()
    return (
        any(pp in t for pp in _PLACEHOLDER)
        or t in _PLACEHOLDER_EXACT
        or not t.strip("。.，,；;、 ")
    )

SITE_RE = re.compile(r"^- `(?P<path>[^`]+):(?P<lineno>\d+)` · kind=(?P<kind>[\w.]+)")
CEIL_RE = re.compile(r"^- (?P<kind>[\w.]+) = (?P<ceiling>\d+)$")


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def py_files(root: Path) -> list[Path]:
    d = root / PKG_REL
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.glob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )


def parse_all(files: list[Path]) -> tuple[dict[Path, ast.Module], list[str]]:
    trees: dict[Path, ast.Module] = {}
    errs: list[str] = []
    for p in files:
        try:
            trees[p] = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError) as e:
            errs.append(f"{p.name}: {type(e).__name__}: {e}")
    return trees, errs


def _func_map(tree: ast.Module) -> dict[int, str]:
    """节点 id → 最内层包围它的函数名（模块级读作 `<module>`）。"""
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                out.setdefault(id(inner), node.name)
    return out


def _callee(call: ast.Call) -> str:
    try:
        return ast.unparse(call.func)
    except Exception as e:  # 罕见：畸形的 func 节点。留痕而不是静默。
        print(f"· _callee 解析失败：{type(e).__name__}: {e}", file=sys.stderr)
        return ""


def _lifecycle_detail(kind: str, call: ast.Call) -> str:
    if kind == "subprocess.Popen":
        kws = sorted(k.arg or "pos" for k in call.keywords)
        ns = next((ast.unparse(k.value) for k in call.keywords if k.arg == "start_new_session"), "-")
        return f"kws={','.join(kws) or '-'};start_new_session={ns}"
    if kind == "os.kill":
        sig = ast.unparse(call.args[1]) if len(call.args) > 1 else "?"
        return f"signal={sig}"
    if kind == "signal.signal":
        sig = ast.unparse(call.args[0]) if call.args else "?"
        handler = ast.unparse(call.args[1]) if len(call.args) > 1 else "?"
        return f"signal={sig};handler={handler}"
    if kind == "atexit.register":
        return f"handler={ast.unparse(call.args[0]) if call.args else '?'}"
    return f"args={len(call.args)}"


def scan_lifecycle(trees: dict[Path, ast.Module], root: Path) -> list[dict]:
    sites: list[dict] = []
    for p, tree in trees.items():
        fmap = _func_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node)
            if callee in KINDS:
                sites.append({
                    "kind": callee,
                    "path": _rel(root, p),
                    "lineno": node.lineno,
                    "func": fmap.get(id(node), "<module>"),
                    "detail": _lifecycle_detail(callee, node),
                })
    return sorted(sites, key=lambda s: (s["path"], s["lineno"], s["kind"]))


def scan_primitives(trees: dict[Path, ast.Module], root: Path) -> list[dict]:
    rows: list[dict] = []
    for p, tree in trees.items():
        fmap = _func_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node)
            if callee not in INPROC_PRIMS and callee not in CROSSPROC_PRIMS:
                continue
            rows.append({
                "prim": callee,
                "path": _rel(root, p),
                "lineno": node.lineno,
                "func": fmap.get(id(node), "<module>"),
                "bound": "-",
            })
    # 绑定名：赋值语句的 target（读不出来留 `-`，不影响判据）
    by_line = {(r["path"], r["lineno"]): r for r in rows}
    for p, tree in trees.items():
        rel = _rel(root, p)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                key = (rel, node.value.lineno)
                row = by_line.get(key)
                if row is not None and _callee(node.value) == row["prim"]:
                    tgt = node.targets[0] if isinstance(node, ast.Assign) else node.target
                    row["bound"] = ast.unparse(tgt)
    return sorted(rows, key=lambda r: (r["prim"], r["path"], r["lineno"]))


def scan_shared_names(trees: dict[Path, ast.Module], root: Path) -> list[dict]:
    """`global` 改写到的模块级名——「哪份状态归谁」的最小机械口径。"""
    rows: list[dict] = []
    for p, tree in trees.items():
        nlocks = sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _callee(n) in INPROC_PRIMS
        )
        per: dict[str, list[tuple[int, str, bool]]] = {}
        for fn in (n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            gnames = [nm for x in ast.walk(fn) if isinstance(x, ast.Global) for nm in x.names]
            if not gnames:
                continue
            has_with = any(isinstance(x, ast.With) or isinstance(x, ast.AsyncWith) for x in ast.walk(fn))
            for g in gnames:
                per.setdefault(g, []).append((fn.lineno, fn.name, has_with))
        for name, hits in per.items():
            rows.append({
                "name": name,
                "path": _rel(root, p),
                "fns": ",".join(sorted({h[1] for h in hits})),
                "decls": ",".join(str(h[0]) for h in sorted(hits)),
                "with": any(h[2] for h in hits),
                "locks": nlocks,
            })
    return sorted(rows, key=lambda r: (r["path"], r["name"]))


def render(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    sites = scan_lifecycle(trees, root)
    prims = scan_primitives(trees, root)
    shared = scan_shared_names(trees, root)
    out: list[str] = [
        "### 2.1 生命周期站点（拉起子进程／发信号／装处理器／注册退出钩子）",
        "",
        "| kind | 站点 | 外层函数 | 关键参数 |",
        "| --- | --- | --- | --- |",
    ]
    if sites:
        for s in sites:
            out.append(f"| {s['kind']} | `{s['path']}:{s['lineno']}` | `{s['func']}` | {s['detail']} |")
    else:
        out.append("| （无） | | | |")
    counts = " · ".join(
        f"{k}={sum(1 for s in sites if s['kind'] == k)}" for k in KINDS
    )
    out += [
        "",
        f"现读计数：{counts}",
        "",
        "### 2.2 同步原语站点（进程内 ＋ 跨进程）",
        "",
        "| 原语 | 站点 | 外层函数 | 绑定名 |",
        "| --- | --- | --- | --- |",
    ]
    for r in prims:
        out.append(f"| {r['prim']} | `{r['path']}:{r['lineno']}` | `{r['func']}` | `{r['bound']}` |")
    inproc = sum(1 for r in prims if r["prim"] in INPROC_PRIMS)
    cross = sum(1 for r in prims if r["prim"] in CROSSPROC_PRIMS)
    out += [
        "",
        f"现读计数：进程内 {inproc} 站点／{len({r['path'] for r in prims if r['prim'] in INPROC_PRIMS})} 个文件"
        f" · 跨进程 {cross} 站点／{len({r['path'] for r in prims if r['prim'] in CROSSPROC_PRIMS})} 个文件",
        "",
        "### 2.3 模块级共享名（被函数用 `global` 改写）",
        "",
        "| 名字 | 文件 | 改写函数 | global 语句所在函数行 | 函数体内有 `with` | 同模块进程内锁数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in shared:
        out.append(
            f"| `{r['name']}` | `{r['path']}` | `{r['fns']}` | {r['decls']} | "
            f"{'是' if r['with'] else '否'} | {r['locks']} |"
        )
    out += [
        "",
        f"现读计数：{len(shared)} 枚名／{len({r['path'] for r in shared})} 个文件；"
        f"其中函数体内没有任何 `with` 的 {sum(1 for r in shared if not r['with'])} 枚",
    ]
    return out


def _section(doc: str, heading: str) -> str | None:
    if heading not in doc:
        return None
    tail = doc.split(heading, 1)[1]
    m = re.search(r"\n#{2,3} ", tail)
    return tail[: m.start()] if m else tail


def parse_register(doc: str) -> tuple[dict[str, dict], list[str]]:
    body = _section(doc, REGISTER_HEADING)
    if body is None:
        return {}, [f"缺 `{REGISTER_HEADING}` 那一节"]
    entries: dict[str, dict] = {}
    errs: list[str] = []
    for line in body.splitlines():
        if not line.startswith("- `"):
            continue
        m = SITE_RE.match(line)
        if not m:
            errs.append(f"登记行形状读不出：{line[:60]}")
            continue
        key = f"{m['path']}:{m['lineno']}"
        ent: dict[str, str] = {"kind": m["kind"]}
        tail = line[m.end():]
        for tok in tail.split(" · "):
            tok = tok.strip()
            if not tok:
                continue
            if "：" not in tok and ":" not in tok:
                errs.append(f"`{key}` 有读出键却读不出值的段落：`{tok[:30]}`")
                continue
            k, v = re.split(r"[：:]", tok, maxsplit=1)
            k = k.strip()
            if k == "kind":
                continue
            if k not in REQUIRED_FIELDS:
                errs.append(f"`{key}` 出现未知字段 `{k}`——本门只认 {REQUIRED_FIELDS}")
                continue
            ent[k] = v.strip()
        missing = [f for f in REQUIRED_FIELDS if not ent.get(f)]
        if missing:
            errs.append(f"`{key}` 缺字段：{'、'.join(missing)}")
        if key in entries:
            errs.append(f"`{key}` 重复登记")
        entries[key] = ent
    return entries, errs


def parse_ceilings(doc: str) -> tuple[dict[str, int], list[str]]:
    body = _section(doc, CEILING_HEADING)
    if body is None:
        return {}, [f"缺 `{CEILING_HEADING}` 那一节"]
    out: dict[str, int] = {}
    errs: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        m = CEIL_RE.match(s)
        if not m:
            if s.startswith("- "):
                errs.append(f"上限行形状读不出：{s[:60]}")
            continue
        out[m["kind"]] = int(m["ceiling"])
    return out, errs


def serve_teardown_shape(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    p = root / CLI_REL
    tree = trees.get(p)
    if tree is None:
        return [f"D：`{CLI_REL}` 不在解析范围内——serve 收尾形状无从判"]
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        body_txt = ast.unparse(node.body)
        if "uvicorn.run(" not in body_txt:
            continue
        fin_txt = ast.unparse(node.finalbody)
        if re.search(r"\bbridge\.stop\(\)|bridge is not None:\s*\n\s*bridge\.stop\(\)", fin_txt) \
                and "reap_watch_child(" in fin_txt:
            found.append(f"try@{node.lineno}")
    if not found:
        return [
            "D：`af_cli.py` 里读不出「`uvicorn.run` 所在的 `try` 其 `finally` 既停桥、又调 `reap_watch_child`」"
            "这一形状——serve 的正常关闭与崩溃又要走同一条路径了（ARCH-06 那一格的现状锚点），"
            "而裁定 §3 Q7.1 要的「退出前收子进程」也没了：watcher 是 `start_new_session=True` 起的，"
            "本进程死了它继续持有协调锁"
        ]
    return []


def watch_sigterm_shape(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    p = root / CLI_REL
    tree = trees.get(p)
    if tree is None:
        return [f"E：`{CLI_REL}` 不在解析范围内——SIGTERM 收尾形状无从判"]
    fmap = _func_map(tree)
    targets = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and _callee(n) == "signal.signal"
               and n.args and "SIGTERM" in ast.unparse(n.args[0])]
    if not targets:
        return [
            "E：`af_cli.py` 现在一枚 `signal.signal(SIGTERM,…)` 都读不出——watch 收到 SIGTERM 又会死在半路"
            "（协调锁靠内核释放，但 sidecar／PID 诊断件留旧值，见 §二之九十三）"
        ]
    findings: list[str] = []
    for site in targets:
        owner = fmap.get(id(site), "<module>")
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == owner),
            None,
        )
        if fn is None:
            findings.append(f"E：SIGTERM 处理器装在 `{owner}`，却找不到那个函数节点")
            continue
        ok = False
        for t in ast.walk(fn):
            if not isinstance(t, ast.Try) or not t.finalbody:
                continue
            fin = ast.unparse(t.finalbody)
            if all(x in fin for x in ("stop.set()", "ticker.join(", "coord.release()")):
                ok = True
        if not ok:
            findings.append(
                f"E：`{owner}` 里装了 SIGTERM 处理器，却没有一个 `finally` 同时做 `stop.set()`／`ticker.join(`／"
                "`coord.release()`——SIGTERM 与 Ctrl+C 不再是同一条收尾路径"
            )
    return findings


def serve_exit_hook_shape(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    """判据 F：serve 的退出兜底必须**真的接到那把收子进程的手上**（裁定 20261011 §3 Q7.2）。

    只查「有没有 `atexit.register`」是测不出空钩子的：注册一枚只印日志的函数，退出照样留下孤儿
    watcher。所以这里查的是**指向**——首参必须指名 `reap_watch_child`，而那枚函数体内必须调得到
    `stop_watch(`、并有 `readonly` 短路。两条都按 **AST 节点**判，不按文本包含判：函数 docstring 里
    本来就写着「readonly」「停多久」这些词，按文本查会假绿。
    """
    p = root / CLI_REL
    tree = trees.get(p)
    if tree is None:
        return [f"F：`{CLI_REL}` 不在解析范围内——serve 退出兜底形状无从判"]
    hooks = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee(n) == "atexit.register"]
    if not hooks:
        return [
            "F：`af_cli.py` 读不出任何一枚 `atexit.register`——裁定 §3 Q7.2 那条兜底腿没了。"
            "`finally` 盖住的是抛出链正常展开的那一格，`atexit` 盖的是解释器正常退出的那一格，缺后者就只剩一条腿"
        ]
    fmap = _func_map(tree)
    named = [h for h in hooks
             if h.args and isinstance(h.args[0], ast.Name) and h.args[0].id == "reap_watch_child"]
    if not named:
        return [
            f"F：`atexit.register` 注册的对象不是 `reap_watch_child`（现读 "
            f"{[ast.unparse(h.args[0]) if h.args else '<无参>' for h in hooks]}）——"
            "钩子换成空函数或只印一行日志，serve 退出照样留下孤儿 watcher"
        ]
    outside = [fmap.get(id(h), "<module>") for h in named]
    findings: list[str] = []
    if not any(f == "serve" for f in outside):
        findings.append(
            f"F：那枚 `atexit.register` 不在 `serve` 里（外层函数读出来是 {outside}）——"
            "钩子注册在别处就不知道该收哪一份 store"
        )
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "reap_watch_child"),
        None,
    )
    if fn is None:
        findings.append("F：`atexit.register` 指名 `reap_watch_child`，但这个函数在 `af_cli.py` 里读不出来")
        return findings
    if not any(isinstance(n, ast.Call) and _callee(n).endswith("stop_watch") for n in ast.walk(fn)):
        findings.append(
            "F：`reap_watch_child` 体内没有 `stop_watch(` 调用——自己另发一枚信号就绕过了"
            "「先按 sidecar 核验 PID 身份、核验不过不杀」那条既有链（§二之一百零六 BUG-06 收的那一格）"
        )
    if not any(
        isinstance(n, ast.If) and "readonly" in ast.unparse(n.test) for n in ast.walk(fn)
    ):
        findings.append(
            "F：`reap_watch_child` 里没有按 `readonly` 短路的分支——只读降级档不可能拉起 watcher，"
            "去收就是误杀别人的子进程"
        )
    return findings


def check(root: Path) -> tuple[list[str], dict]:
    findings: list[str] = []
    info: dict = {}
    files = py_files(root)
    trees, errs = parse_all(files)
    if errs:
        findings.extend(f"解析不了：{e}" for e in errs)
    doc = (root / DOC_REL).read_text(encoding="utf-8", errors="replace")
    sites = scan_lifecycle(trees, root)
    prims = scan_primitives(trees, root)
    shared = scan_shared_names(trees, root)
    info["sites"] = len(sites)
    info["prims"] = len(prims)
    info["shared"] = len(shared)

    # A 自动段对撞
    if AUTO_BEGIN in doc and AUTO_END in doc:
        body = doc.split(AUTO_BEGIN, 1)[1].split(AUTO_END, 1)[0].strip("\n")
        fresh = "\n".join(render(trees, root)).strip("\n")
        if body != fresh:
            want = body.splitlines()
            got = fresh.splitlines()
            drift = [
                f"第 {i + 1} 行 文档=`{w[:70]}` 现读=`{g[:70]}`"
                for i, (w, g) in enumerate(zip(want, got)) if w != g
            ]
            extra = [f"文档多出行：{x[:70]}" for x in want[len(got):]]
            extra += [f"现读多出未入文档：{x[:70]}" for x in got[len(want):]]
            drift_all = (drift + extra)[:14]
            findings.append(
                f"A：`{DOC_REL}` 的自动段与现读不一致（{len(drift) + len(extra)} 行漂移）——"
                "跑 `python scripts/check_process_model.py --write` 之前，先想清楚这一格该不该动"
            )
            findings.extend(f"    · {d}" for d in drift_all)
            if len(drift_all) < len(drift) + len(extra):
                findings.append(f"    ·（其余 {len(drift) + len(extra) - len(drift_all)} 行省略）")
    else:
        findings.append(f"A：`{DOC_REL}` 里找不到自动段标记，无从对撞")

    register, rerrs = parse_register(doc)
    findings.extend(f"B：{e}" for e in rerrs)
    ceilings, cerrs = parse_ceilings(doc)
    findings.extend(f"C：{e}" for e in cerrs)

    # B 默认拒绝 ＋ 只减不增
    keys = {f"{s['path']}:{s['lineno']}": s for s in sites}
    for key, cur in sorted(keys.items()):
        ent = register.get(key)
        if ent is None:
            findings.append(
                f"B：生命周期站点 `{key}`（`{cur['kind']}`，外层函数 `{cur['func']}`）在 "
                f"`{REGISTER_HEADING}` 里没有登记——拉起进程／发信号／装钩子必须写明谁负责收"
            )
            continue
        if ent["kind"] != cur["kind"]:
            findings.append(
                f"B：`{key}` 登记写 kind=`{ent['kind']}`，现读是 `{cur['kind']}`——那一行已经不是同一件事"
            )
        claim = ent["认领"]
        if claim and not any(claim.startswith(o) for o in OWNERSHIP):
            findings.append(f"B：`{key}` 的认领 `{claim[:24]}` 不属于 {OWNERSHIP} 三种形状之一")
        anchor = ent.get("依据", "")
        m = re.search(r"[\w\-./\u4e00-\u9fff]+\.md|[\w\-./\u4e00-\u9fff]+\.py", anchor)
        if m:
            rel = m.group(0)
            cand = (root / rel)
            hits = [cand] if cand.is_file() else list(root.glob(f"**/{Path(rel).name}"))
            if not any(h.is_file() for h in hits):
                findings.append(f"B：`{key}` 的依据 `{rel}` 在仓内找不到文件——登记必须指得到一份记录")
        reason = ent.get("收不到会怎样", "")
        if len(reason) < _MIN_REASON or _is_placeholder(reason):
            findings.append(f"B：`{key}` 的「收不到会怎样」空、过短或写成占位词：`{reason[:36]}`")
    for key in sorted(register):
        if key not in keys:
            findings.append(
                f"B：登记里的 `{key}` 现在读不出生命周期站点（行号漂了或那一格已删）——过期登记要跟着动"
            )

    # C 每类上限（只减不增）
    for kind in KINDS:
        n = sum(1 for s in sites if s["kind"] == kind)
        if kind in ceilings and n > ceilings[kind]:
            findings.append(
                f"C：`{kind}` 现读 {n} 枚，§四 登记上限 {ceilings[kind]}——新增必须先过 §三 认领，再抬上限"
            )

    # D / E / F 收尾形状
    findings.extend(serve_teardown_shape(trees, root))
    findings.extend(watch_sigterm_shape(trees, root))
    findings.extend(serve_exit_hook_shape(trees, root))
    return findings, info


def anchor_ok(root: Path) -> str | None:
    if not (root / PKG_REL).is_dir():
        return f"`{PKG_REL}/` 目录不在盘上——本门的射程对象整个消失"
    cli = root / CLI_REL
    if not cli.is_file():
        return f"`{CLI_REL}` 不在盘上——D／F 两条 serve 收尾形状判据没有对象"
    doc_p = root / DOC_REL
    if not doc_p.is_file():
        return f"`{DOC_REL}` 不在盘上——清单本身就是本门的产物"
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        return f"`{DOC_REL}` 的自动段标记缺失或被改写"
    if _section(doc, REGISTER_HEADING) is None:
        return f"`{DOC_REL}` 缺 `{REGISTER_HEADING}` 那一节"
    if _section(doc, CEILING_HEADING) is None:
        return f"`{DOC_REL}` 缺 `{CEILING_HEADING}` 那一节"
    files = py_files(root)
    trees, errs = parse_all(files)
    if errs:
        return f"{len(files)} 个文件里有 {len(errs)} 个解析不了：{errs[0]}"
    sites = scan_lifecycle(trees, root)
    if not sites:
        return "全树读不出任何一枚生命周期站点（连 `os.kill` 都没有）——是扫描器坏了，不是代码干净"
    reg, rerrs = parse_register(doc)
    if rerrs and not reg:
        return f"`{REGISTER_HEADING}` 一条都解析不出（登记行形状变了）：{rerrs[0]}"
    ceil, cerrs = parse_ceilings(doc)
    if cerrs and not ceil:
        return f"`{CEILING_HEADING}` 一条都解析不出（上限行形状变了）：{cerrs[0]}"
    if not ceil:
        return f"`{CEILING_HEADING}` 里读不出任何一行 `- kind = N`"
    return None


def write_doc(root: Path) -> int:
    doc_p = root / DOC_REL
    if not doc_p.is_file():
        print(f"✗ `{DOC_REL}` 不在盘上，拒绝改写")
        return 2
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        print(f"✗ `{DOC_REL}` 缺自动段标记，拒绝改写")
        return 2
    files = py_files(root)
    trees, errs = parse_all(files)
    if errs:
        print(f"✗ 有文件解析不了，拒绝用残缺结果改写：{errs[0]}")
        return 2
    body = "\n".join(render(trees, root))
    head, _, rest = doc.partition(AUTO_BEGIN)
    _, _, tail = rest.partition(AUTO_END)
    new = f"{head}{AUTO_BEGIN}\n{body}\n{AUTO_END}{tail}"
    with open(doc_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    sites = scan_lifecycle(trees, root)
    print(
        f"✓ 已重新生成 `{DOC_REL}` 自动段（生命周期 {len(sites)} 枚／原语 "
        f"{len(scan_primitives(trees, root))} 枚／共享名 {len(scan_shared_names(trees, root))} 枚；手工段未动）"
    )
    return 0


def main(argv: list[str]) -> int:
    root = REPO
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if rest:
        root = Path(rest[0]).resolve()
    if "--write" in argv:
        return write_doc(root)
    blocked = anchor_ok(root)
    if blocked:
        print(f"✗ 进程模型门禁读不出射程：{blocked}")
        print(
            "  修法：射程断不是「没问题」。确认 `docs/进程模型清单.md` 的三段（自动段标记／生命周期站点登记／"
            "每类上限）都在原处原形状，且 `src/autoforge/` 整棵目录能 `ast.parse`。"
        )
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 进程模型门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}" if f.startswith("    ") else f"  · {f}")
        print(
            "  修法：A 跑 `python scripts/check_process_model.py --write` 重生成自动段（先想清楚该不该动）；"
            "B 给新站点在 §三 补一行 `- `文件:行号` · kind=… · 拉起：… · 收：… · 收不到会怎样：… · 依据：… · 认领：…`，"
            "过期登记要删；C 先认领再抬 §四 上限；D/E/F 是把 serve／watch 已有的收尾路径钉住的锚点，"
            "改坏它的形状请连同本门一起说明，不要顺手删掉 `finally`。"
        )
        return 1
    print(
        f"✓ 进程模型门禁干净（现读生命周期 {info['sites']} 枚全部在册且认领可读；"
        f"同步原语 {info['prims']} 枚、模块级共享名 {info['shared']} 枚逐行对撞一致；"
        "serve 收尾／watch SIGTERM／serve 退出兜底三条形状锚点都在）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

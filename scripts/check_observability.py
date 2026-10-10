#!/usr/bin/env python3
"""可观测性门禁：留痕站点、具名码、级别棘轮与关联 id 面必须是**可 diff 的产物**，不是一句评论。

起因（第六轮审计 ARCH-07）：报告说这仓「有留痕纪律，但没有指标后端、没有追踪、没有分级日志约定」。
复测（现读 2026-10-11，口径见 `docs/可观测性清单.md` §一）结论是三句话——

1. **「有留痕纪律」成立且可量化**：全树 `logger.<level>()` 站点 100＋ 枚、覆盖二十余个文件，
   `_keep()` 环形诊断缓冲／`counts["forbidden_seen"]`／`af_audit.py`／`af_metrics.py`／`af_telemetry.py`
   都在位——报告给的那份信用是本门的射程前提，不是本门要改的东西；
2. **「没有分级约定」成立**：`docs/` 与 `README.md` 里「日志分级／级别口径／需要人介入」三个关键词
   **各 0 命中**（只有审计报告自己在谈这件事）。⇒ 本批把口径**成文**进清单 §二，并让门去钉
   「站点↔登记」的对账，而不是钉「谁用错了级别」；
3. **「没有追踪」这半句要拆**：主题信封侧 `trace_id` 真实在场（出向 `af_mqtt_bridge.py`、入向
   `af_linkage_feed.py`／`af_adapters/inbox.py`），而 **HTTP 请求级 id 现读 0 命中**
   （`request_id`／`requestId`／`correlation_id`／`X-Request-Id` 在 `src/autoforge` 全树 0）。
   报告另有一句「`/api/metrics` 是公开端点」**不成立**（§二之九十九 已现读到 `requires(read)`）。

**本门判的是"有没有对账"，不是"日志写得好不好"**。哪一枚站点该降档、要不要接指标后端、
HTTP 侧要不要生成 request id、诊断环形缓冲的 TTL 填哪一档，是运行时语义与对外契约，递 DCD
（见清单 §七），不在本门射程内。

五条判据，各自单独可红：

- **A 自动段对撞**：清单的自动段（`<!-- AUTO-BEGIN/END -->` 之间）必须逐行等于现读的**四张表**＋计数行
  （2.1 每文件×级别分布／2.2 具名码站点／2.3 关联 id 面／2.4 HTTP 面失败出口读数）。多一枚站点、
  换一档级别、少一枚具名码，都红；计数下降同样由计数行逼人来重生成。
- **B 默认拒绝**：每一枚现读**具名码站点**必须在 `## 三、具名码登记` 里有以 `` `文件:行号` `` 开头、
  带 `code=`／`level=` 的登记行，字段齐（级别语义／介入／依据／理由／认领）且 `level=` 与现读一致。
  「留痕纪律」要成为资产，靠的就是"新码不落账即红"这一格。
- **C 只减不增**：`## 四、每级别「无码站点」上限` 里每个级别的登记上限 ≥ 现读无码站点数。
  这是把「无码留痕」这一格的存量钉住——想再加一枚不带码的 WARNING，必须先抬上限并在 §三 说明理由。
- **D 访问日志掩码形状锚点**：`af_cli.py` 里必须同时存在 ① 模块级 `_ACCESS_LOG = "uvicorn.access"`、
  ② `def install_access_log_token_mask(` 的定义、③ 对它的调用，且那处调用与 `uvicorn.run(` 在**同一个外层函数**里。
  这是「状态码确实进日志、凭据不进去」的现有那一半（§二之四十一那批落的），改坏了立刻红。
- **E 欠账必须在册**：`## 五、HTTP 面与关联 id 留痕欠账登记` 的四个读数键（`af_api` 的 logger 站点数／
  `ok: False` 字面量站点数／`trace_id` 信封站点数／HTTP request-id 站点数）必须与现读逐一对撞，
  并且各自带 `状态：`（欠账／已还／按设计）与 `认领：`。报告那句「HTTP 失败以 `{ok: False}` 返回而非记日志，
  成因不进日志」之所以今天只能记在文档里而不是逼着下一批补日志，就是因为"该不该补、补在哪一级"
  在仓内**没有对账资产**——本门先把它变成资产：数字变了而账没改，就红。

射程前提（读不到就 `exit 2`，不许「没有发现」冒充「没有问题」）：`src/autoforge` 不在、任一 `.py`
解析不了、清单文件不在、自动段标记缺失、§三／§四／§五 标题缺失、`af_cli.py` 读不出（D 的射程）、
`af_api.py` 读不出（E 的射程）、§三 登记行整体解析不出、§四／§五 整体解析不出、
**全树读不出任何一枚留痕站点**（连一枚 `logger.warning` 都扫不出＝扫描器坏了，不是代码干净）、
**关联 id 面双零**（`trace_id` 与 request-id 两边都读不出＝射程塌了）。

纯标准库：只对 `src/autoforge/**/*.py` 做 `ast.parse`，不导入 `autoforge`，不碰网络，不写盘
（`--write` 除外）。`scripts/`／`tests/` 里的日志不在射程内——它们不是常驻运行时的组成部分，
这一格在清单 §一 明写。
"""
from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC_REL = "docs/可观测性清单.md"
PKG_REL = "src/autoforge"
CLI_REL = "src/autoforge/af_cli.py"
API_REL = "src/autoforge/af_api.py"
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_observability.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"
CODE_HEADING = "## 三、具名码登记（默认拒绝，新码不登记即红）"
CEILING_HEADING = "## 四、每级别「无码站点」上限（只减不增）"
DEBT_HEADING = "## 五、HTTP 面与关联 id 留痕欠账登记"

LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "exception", "critical")
#: 被调对象是 `ast.Name` 时，除本模块 `getLogger` 别名外还认这两枚字面名。
RECV_LITERALS = frozenset({"logger", "log"})
CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{5,}")
CLAIMS = ("AF", "待裁", "DCD")
INTERVENTION = ("需人介入", "不需人介入", "待裁")
REQUIRED_FIELDS = ("级别语义", "介入", "依据", "理由", "认领")
_MIN_REASON = 12
#: 子串即算占位（多字符，避免"策略""行略"这类正常用词被单字命中）。
_PLACEHOLDER = ("待补", "TODO", "待定", "待填")
#: 整值才算占位——单字词只能整格比，否则「属策略」这种写法会被误判。
_PLACEHOLDER_EXACT = frozenset({"略", "略。", "见代码", "见注释", "无"})


def _is_placeholder(text: str) -> bool:
    t = text.strip()
    return (
        any(pp in t for pp in _PLACEHOLDER)
        or t in _PLACEHOLDER_EXACT
        or not t.strip("。.，,；;、 ")
    )

#: HTTP 侧「请求级追踪」现读形状：这些键在 `src/autoforge` 全树出现即算 request-id 站点。
REQUEST_ID_PAT = re.compile(r"request[-_]?id|correlation[-_]?id|x-request-id", re.I)
#: 信封侧的关联 id 键名（字典字面量的键，AST 口径，不算散文）。
ENVELOPE_ID_KEY = "trace_id"

#: §五 必须登记的四枚读数键 → 现读函数名。数字由本门现读生成，账上没有对应行即红。
DEBT_KEYS: tuple[str, ...] = (
    "af_api-logger-sites",
    "af_api-ok-false-sites",
    "envelope-trace-id-sites",
    "http-request-id-sites",
)

SITE_RE = re.compile(
    r"^- `(?P<path>[^`]+):(?P<lineno>\d+)` · code=(?P<code>[A-Z0-9_]+) · level=(?P<level>[a-z]+)"
)
CEIL_RE = re.compile(r"^- (?P<level>[a-z]+) = (?P<ceiling>\d+)$")
DEBT_RE = re.compile(
    r"^- `(?P<key>[\w\-]+)` = (?P<num>\d+) · 状态：(?P<status>欠账|已还|按设计)"
)


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
        p for p in d.rglob("*.py")
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

    def walk(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[id(child)] = child.name
                walk(child, child.name)
            elif isinstance(child, ast.ClassDef):
                out[id(child)] = f"{name}.{child.name}" if name != "<module>" else child.name
                walk(child, out[id(child)])
            else:
                out[id(child)] = name
                walk(child, name)

    walk(tree, "<module>")
    return out


def _is_getlogger_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getLogger"
    )


def logger_aliases(tree: ast.Module) -> set[str]:
    """本模块里绑定到 `logging.getLogger(…)` 的名字 ＋ 两枚字面名。"""
    names = set(RECV_LITERALS)
    for n in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(n, ast.Assign):
            targets, value = list(n.targets), n.value
        elif isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Call):
            targets, value = [n.target], n.value
        if value is None or not _is_getlogger_call(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _code_of(arg: ast.expr | None) -> str | None:
    """首参里取「第一个非空字符串片段」的首个空白分隔 token，整词匹配才算具名码。"""
    text: str | None = None
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        text = arg.value
    elif isinstance(arg, ast.JoinedStr):
        for v in arg.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.strip():
                text = v.value
                break
    if not text:
        return None
    tok = text.strip().split()[0]
    return tok if CODE_RE.fullmatch(tok) else None


def scan_sites(trees: dict[Path, ast.Module], root: Path) -> tuple[list[dict], list[str]]:
    """全部留痕站点；返回 (站点, 射程外的被调对象形状)。"""
    sites: list[dict] = []
    outside: list[str] = []
    for p, tree in trees.items():
        rel = _rel(root, p)
        aliases = logger_aliases(tree)
        fmap = _func_map(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            if n.func.attr not in LEVELS:
                continue
            rec = n.func.value
            if isinstance(rec, ast.Name) and rec.id in aliases:
                recv = rec.id
            elif _is_getlogger_call(rec):
                recv = ast.unparse(rec)
            else:
                outside.append(f"{rel}:{n.lineno} `{ast.unparse(rec)[:40]}`")
                continue
            sites.append({
                "path": rel,
                "lineno": n.lineno,
                "level": n.func.attr,
                "code": _code_of(n.args[0]) if n.args else None,
                "func": fmap.get(id(n), "<module>"),
                "recv": recv,
            })
    sites.sort(key=lambda s: (s["path"], s["lineno"]))
    outside.sort()
    return sites, outside


def scan_envelope_ids(trees: dict[Path, ast.Module], root: Path) -> list[dict]:
    """字典字面量里以 `trace_id` 为键的站点（＝信封带关联 id 的机器可读面）。"""
    out: list[dict] = []
    for p, tree in trees.items():
        rel = _rel(root, p)
        fmap = _func_map(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Dict):
                continue
            for k in n.keys:
                if isinstance(k, ast.Constant) and k.value == ENVELOPE_ID_KEY:
                    out.append({"path": rel, "lineno": n.lineno, "func": fmap.get(id(n), "<module>")})
    out.sort(key=lambda s: (s["path"], s["lineno"]))
    return out


def scan_request_ids(trees: dict[Path, ast.Module], root: Path) -> list[dict]:
    """HTTP 请求级 id 面：任何字符串常量里出现 `request[-_]id`／`correlation[-_]id`／`X-Request-Id`。"""
    out: list[dict] = []
    for p, tree in trees.items():
        rel = _rel(root, p)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and REQUEST_ID_PAT.search(n.value):
                out.append({"path": rel, "lineno": n.lineno, "text": n.value[:48]})
    out.sort(key=lambda s: (s["path"], s["lineno"]))
    return out


def scan_http_face(trees: dict[Path, ast.Module], root: Path) -> dict:
    """`af_api.py` 的失败出口形状：logger 站点／`ok:` 字面量／异常处理器／中间件／依赖注入引用数。"""
    p = root / API_REL
    tree = trees.get(p)
    if tree is None:
        return {}
    logger_sites = 0
    aliases_cache: set[str] | None = None
    ok_false: list[int] = []
    ok_true = 0
    handlers: list[int] = []
    middleware: list[int] = []
    depends = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute) and n.func.attr in LEVELS:
                if aliases_cache is None:
                    aliases_cache = logger_aliases(tree)
                rec = n.func.value
                if (isinstance(rec, ast.Name) and rec.id in aliases_cache) or _is_getlogger_call(rec):
                    logger_sites += 1
            fname = n.func.attr if isinstance(n.func, ast.Attribute) else (
                n.func.id if isinstance(n.func, ast.Name) else None
            )
            if fname in ("middleware", "add_middleware"):
                middleware.append(n.lineno)
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == "ok" and isinstance(v, ast.Constant):
                    if v.value is False:
                        ok_false.append(n.lineno)
                    elif v.value is True:
                        ok_true += 1
        elif isinstance(n, ast.Name) and n.id == "Depends":
            depends += 1
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in n.decorator_list:
                f = d.func if isinstance(d, ast.Call) else d
                if isinstance(f, ast.Attribute) and f.attr == "exception_handler":
                    handlers.append(n.lineno)
    return {
        "logger_sites": logger_sites,
        "ok_false": sorted(set(ok_false)),
        "ok_true": ok_true,
        "handlers": sorted(set(handlers)),
        "middleware": sorted(set(middleware)),
        "depends": depends,
    }


def _level_counts(sites: list[dict]) -> Counter:
    return Counter(s["level"] for s in sites)


def _no_code_counts(sites: list[dict]) -> Counter:
    return Counter(s["level"] for s in sites if not s["code"])


def render(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    sites, outside = scan_sites(trees, root)
    env = scan_envelope_ids(trees, root)
    req = scan_request_ids(trees, root)
    http = scan_http_face(trees, root) or {}
    lv = _level_counts(sites)

    out: list[str] = [
        "### 2.1 留痕站点分布（每文件 × 级别）",
        "",
        "| 文件 | DEBUG | INFO | WARNING | ERROR | EXCEPTION | CRITICAL | 合计 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    by_file: dict[str, Counter] = {}
    for s in sites:
        by_file.setdefault(s["path"], Counter())[s["level"]] += 1
    for rel in sorted(by_file):
        c = by_file[rel]
        out.append(
            f"| `{rel}` | {c['debug']} | {c['info']} | {c['warning']} | {c['error']} | "
            f"{c['exception']} | {c['critical']} | {sum(c.values())} |"
        )
    out += [
        "",
        f"现读计数：{len(sites)} 站点／{len(by_file)} 个文件 · "
        + " · ".join(f"{k.upper()}={lv[k]}" for k in LEVELS)
        + f" · 射程外被调对象形状 {len(outside)} 枚（口径见 §一 第 4 条）",
        "",
        "### 2.2 具名码站点现读（首参带全大写码的那批）",
        "",
        "| 码 | 级别 | 站点 | 外层函数 |",
        "| --- | --- | --- | --- |",
    ]
    coded = [s for s in sites if s["code"]]
    if coded:
        for s in coded:
            out.append(f"| `{s['code']}` | {s['level'].upper()} | `{s['path']}:{s['lineno']}` | `{s['func']}` |")
    else:
        out.append("| （无） | | | |")
    nc = _no_code_counts(sites)
    out += [
        "",
        f"现读计数：具名码 {len({s['code'] for s in coded})} 枚／{len(coded)} 站点"
        f" · 无码站点 {sum(nc.values())} 枚（"
        + " ".join(f"{k.upper()}={nc[k]}" for k in LEVELS) + "）",
        "",
        "### 2.3 关联 id 面（信封侧 vs HTTP 请求侧）",
        "",
        "| 面 | 站点 | 落点 |",
        "| --- | --- | --- |",
    ]
    env_files = sorted({e["path"] for e in env})
    out.append(
        f"| 主题信封里的 `{ENVELOPE_ID_KEY}` 键 | {len(env)} | "
        + ("、".join(f"`{f}:{e['lineno']}`" for f in env_files for e in env if e["path"] == f) if env else "（无）")
        + " |"
    )
    out.append(f"| HTTP 请求级 id（`request_id`/`correlation_id`/`X-Request-Id`） | {len(req)} | "
               + ("、".join(f"`{r['path']}:{r['lineno']}`" for r in req) if req else "（无）") + " |")
    out += [
        "",
        f"现读计数：信封侧 {len(env)} 站点／{len(env_files)} 个文件 · HTTP 请求侧 {len(req)} 站点"
        f" · 两侧同零＝射程塌（本门 `exit 2`）",
        "",
        "### 2.4 HTTP 面失败出口读数（`af_api.py`）",
        "",
        "| 读数 | 现读 | 落点 |",
        "| --- | --- | --- |",
        f"| `logger.<level>` 站点 | {http.get('logger_sites', 0)} | "
        + ("（无一行 ⇒ 这一格就是「失败成因不进日志」的欠账面，登记在 §五）"
           if not http.get("logger_sites") else "见 §2.1 该文件那一行") + " |",
        f"| `ok: False` 字面量站点 | {len(http.get('ok_false', []))} | "
        + ("、".join(f"`{API_REL}:{n}`" for n in http.get("ok_false", [])) or "（无）") + " |",
        f"| `ok: True` 字面量站点 | {http.get('ok_true', 0)} | （计数，逐站不列） |",
        f"| `@…exception_handler` 装饰的处理器 | {len(http.get('handlers', []))} | "
        + ("、".join(f"`{API_REL}:{n}`" for n in http.get("handlers", [])) or "（无）") + " |",
        f"| `add_middleware`／`middleware` 调用 | {len(http.get('middleware', []))} | "
        + ("、".join(f"`{API_REL}:{n}`" for n in http.get("middleware", [])) or "（无）") + " |",
        f"| `Depends` 引用数 | {http.get('depends', 0)} | （每请求处理点的存量面；request id 若要做，就挂在这一族上） |",
        "",
        f"现读计数：`af_api.py` 留痕站点 {http.get('logger_sites', 0)} 枚 · 失败出口字面量 "
        f"{len(http.get('ok_false', []))} 枚 · 异常处理器 {len(http.get('handlers', []))} 枚"
        " ⇒ 「成因不进日志」这一格的对账资产（数字变了而 §五 账没改即红）",
    ]
    return out


def _section(doc: str, heading: str) -> str | None:
    if heading not in doc:
        return None
    tail = doc.split(heading, 1)[1]
    m = re.search(r"\n#{2,3} ", tail)
    return tail[: m.start()] if m else tail


def parse_register(doc: str) -> tuple[dict[str, dict], list[str]]:
    body = _section(doc, CODE_HEADING)
    if body is None:
        return {}, [f"缺 `{CODE_HEADING}` 那一节"]
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
        ent: dict[str, str] = {"code": m["code"], "level": m["level"]}
        for tok in line[m.end():].split(" · "):
            tok = tok.strip()
            if not tok:
                continue
            if "：" not in tok and ":" not in tok:
                errs.append(f"`{key}` 有读出键却读不出值的段落：`{tok[:30]}`")
                continue
            k, v = re.split(r"[：:]", tok, maxsplit=1)
            k = k.strip()
            if k in ("code", "level"):
                continue
            if k not in REQUIRED_FIELDS:
                errs.append(f"`{key}` 出现未知字段 `{k}`——本门只认 {REQUIRED_FIELDS}")
                continue
            ent[k] = re.split(r"[：:]", tok, maxsplit=1)[1].strip()
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
        out[m["level"]] = int(m["ceiling"])
    return out, errs


def parse_debt(doc: str) -> tuple[dict[str, dict], list[str]]:
    body = _section(doc, DEBT_HEADING)
    if body is None:
        return {}, [f"缺 `{DEBT_HEADING}` 那一节"]
    out: dict[str, dict] = {}
    errs: list[str] = []
    for line in body.splitlines():
        if not line.startswith("- `"):
            continue
        m = DEBT_RE.match(line)
        if not m:
            errs.append(f"欠账行形状读不出：{line[:60]}")
            continue
        key = m["key"]
        ent = {"num": int(m["num"]), "status": m["status"]}
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
            if k in ("状态",):
                continue
            if k not in ("依据", "认领", "说明"):
                errs.append(f"`{key}` 出现未知字段 `{k}`——本门只认 依据／认领／说明")
                continue
            ent[k] = v.strip()
        for f in ("依据", "认领"):
            if not ent.get(f):
                errs.append(f"`{key}` 缺字段 {f}")
        claim = ent.get("认领", "")
        if claim and not any(claim.startswith(o) for o in CLAIMS):
            errs.append(f"`{key}` 的认领 `{claim[:24]}` 不属于 {CLAIMS} 三种形状之一")
        if key in out:
            errs.append(f"`{key}` 重复登记")
        out[key] = ent
    return out, errs


def access_log_shape(trees: dict[Path, ast.Module], root: Path) -> list[str]:
    p = root / CLI_REL
    tree = trees.get(p)
    if tree is None:
        return [f"D：`{CLI_REL}` 不在解析范围内——访问日志掩码锚点无从判"]
    rel = _rel(root, p)
    bad: list[str] = []
    pinned = None
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        value = n.value
        if not (isinstance(value, ast.Constant) and value.value == "uvicorn.access"):
            continue
        targets = list(value and (n.targets if isinstance(n, ast.Assign) else [n.target]))
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "_ACCESS_LOG":
                pinned = n.lineno
    if pinned is None:
        bad.append(
            f"D：`{rel}` 里读不出「模块级 `_ACCESS_LOG = \"uvicorn.access\"`」——"
            "访问日志的挂点名字被改写或那一行没了，掩码 filter 会挂到别处或不挂"
        )
    defd = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "install_access_log_token_mask"),
        None,
    )
    if defd is None:
        bad.append(f"D：`{rel}` 里读不出 `def install_access_log_token_mask(`——掩码机制整个消失")
    if defd is not None and pinned is not None:
        default = None
        for a, d in zip(reversed(defd.args.args), reversed(defd.args.defaults)):
            if a.arg == "logger_name":
                default = d
        if not (isinstance(default, ast.Name) and default.id == "_ACCESS_LOG"):
            bad.append(
                f"D：`install_access_log_token_mask` 的 `logger_name` 缺省值不再指向 `_ACCESS_LOG`——"
                "常量与挂点脱钩，改一行常量就静默失效"
            )
        fmap = _func_map(tree)
        run_owner = {
            fmap.get(id(n), "<module>")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "run"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "uvicorn"
        }
        call_owner = {
            fmap.get(id(n), "<module>")
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == "install_access_log_token_mask")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "install_access_log_token_mask")
            )
        }
        in_serve = {c for c in call_owner if c and c in run_owner and c != "install_access_log_token_mask"}
        if not in_serve:
            bad.append(
                f"D：`install_access_log_token_mask()` 没有和 `uvicorn.run(` 待在同一个外层函数里——"
                "serve 起来但访问日志没掩码，凭据重新进日志（§二之四十一那批的成果）"
            )
    return bad


def check(root: Path) -> tuple[list[str], dict]:
    findings: list[str] = []
    info: dict = {}
    files = py_files(root)
    trees, errs = parse_all(files)
    if errs:
        findings.extend(f"解析不了：{e}" for e in errs)
    doc = (root / DOC_REL).read_text(encoding="utf-8", errors="replace")
    sites, outside = scan_sites(trees, root)
    env = scan_envelope_ids(trees, root)
    req = scan_request_ids(trees, root)
    http = scan_http_face(trees, root)
    coded = [s for s in sites if s["code"]]
    nc = _no_code_counts(sites)
    info["sites"] = len(sites)
    info["coded"] = len(coded)
    info["codes"] = len({s["code"] for s in coded})
    info["env"] = len(env)
    info["req"] = len(req)

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
                "跑 `python scripts/check_observability.py --write` 之前，先想清楚这一格该不该动"
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
    debt, derrs = parse_debt(doc)
    findings.extend(f"E：{e}" for e in derrs)

    # B 具名码默认拒绝
    keys = {f"{s['path']}:{s['lineno']}": s for s in coded}
    for key, cur in sorted(keys.items()):
        ent = register.get(key)
        if ent is None:
            findings.append(
                f"B：具名码站点 `{key}`（`{cur['code']}` · {cur['level'].upper()}，外层函数 `{cur['func']}`）"
                f"在 `{CODE_HEADING}` 里没有登记——新码不落账即红"
            )
            continue
        if ent["level"] != cur["level"]:
            findings.append(
                f"B：`{key}` 登记 level=`{ent['level']}`，现读是 `{cur['level']}`——留痕级别变了，账要跟着变"
            )
        if ent["code"] != cur["code"]:
            findings.append(f"B：`{key}` 登记 code=`{ent['code']}`，现读是 `{cur['code']}`——同一行已不是同一件事")
        if ent.get("介入") and ent["介入"] not in INTERVENTION:
            findings.append(
                f"B：`{key}` 的介入档 `{ent['介入'][:20]}` 不在 {INTERVENTION} 里——"
                "「什么级别代表需要人介入」必须有统一口径，不接受自造一档"
            )
        anchor = ent.get("依据", "")
        m = re.search(r"[\w\-./\u4e00-\u9fff]+\.(md|py)", anchor)
        if m:
            cand = root / m.group(0)
            hits = [cand] if cand.is_file() else list(root.glob(f"**/{Path(m.group(0)).name}"))
            if not any(h.is_file() for h in hits):
                findings.append(f"B：`{key}` 的依据 `{m.group(0)}` 在仓内找不到文件——登记必须指得到一份记录")
        elif anchor:
            findings.append(f"B：`{key}` 的依据读不出文件名：`{anchor[:36]}`")
        reason = ent.get("理由", "")
        if len(reason) < _MIN_REASON or _is_placeholder(reason):
            findings.append(f"B：`{key}` 的「理由」空、过短或写成占位词：`{reason[:36]}`")
        claim = ent.get("认领", "")
        if claim and not any(claim.startswith(o) for o in CLAIMS):
            findings.append(f"B：`{key}` 的认领 `{claim[:24]}` 不属于 {CLAIMS} 三种形状之一")
    for key in sorted(register):
        if key not in keys:
            findings.append(
                f"B：登记里的 `{key}` 现在读不出具名码站点（行号漂了、码被改了或那一格已删）——过期登记要跟着动"
            )

    # C 每级别无码站点上限（只减不增）
    for level in LEVELS:
        n = nc[level]
        if level in ceilings and n > ceilings[level]:
            findings.append(
                f"C：`{level.upper()}` 无码站点现读 {n} 枚，§四 登记上限 {ceilings[level]}——"
                "新增一枚不带码的留痕，必须先给它进 §三 认领，再来抬上限"
            )

    # D 访问日志掩码形状
    findings.extend(access_log_shape(trees, root))

    # E 欠账必须在册（数字对撞现读）
    fresh_debt = {
        "af_api-logger-sites": http.get("logger_sites", -1),
        "af_api-ok-false-sites": len(http.get("ok_false", [])),
        "envelope-trace-id-sites": len(env),
        "http-request-id-sites": len(req),
    }
    for key in DEBT_KEYS:
        ent = debt.get(key)
        if ent is None:
            findings.append(
                f"E：§五 缺读数键 `{key}`（现读 {fresh_debt[key]}）——"
                "这一格要么是欠账、要么已还，账上必须有名字"
            )
            continue
        if ent["num"] != fresh_debt[key]:
            findings.append(
                f"E：`{key}` 登记 {ent['num']}，现读 {fresh_debt[key]}——"
                "欠账面变了：要么改账（连同状态），要么把这次改动说明白"
            )
        anchor = ent.get("依据", "")
        m = re.search(r"[\w\-./\u4e00-\u9fff]+\.(md|py)", anchor)
        if m:
            cand = root / m.group(0)
            hits = [cand] if cand.is_file() else list(root.glob(f"**/{Path(m.group(0)).name}"))
            if not any(h.is_file() for h in hits):
                findings.append(f"E：`{key}` 的依据 `{m.group(0)}` 在仓内找不到文件")
    for key in sorted(debt):
        if key not in DEBT_KEYS:
            findings.append(f"E：§五 出现本门不认的读数键 `{key}`——只认 {DEBT_KEYS}")
    return findings, info


def anchor_ok(root: Path) -> str | None:
    if not (root / PKG_REL).is_dir():
        return f"`{PKG_REL}/` 目录不在盘上——本门的射程对象整个消失"
    for rel, why in ((CLI_REL, "D 条访问日志掩码锚点没有对象"), (API_REL, "E 条 HTTP 面欠账没有对象")):
        if not (root / rel).is_file():
            return f"`{rel}` 不在盘上——{why}"
    doc_p = root / DOC_REL
    if not doc_p.is_file():
        return f"`{DOC_REL}` 不在盘上——清单本身就是本门的产物"
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        return f"`{DOC_REL}` 的自动段标记缺失或被改写"
    for h in (CODE_HEADING, CEILING_HEADING, DEBT_HEADING):
        if _section(doc, h) is None:
            return f"`{DOC_REL}` 缺 `{h}` 那一节"
    files = py_files(root)
    trees, errs = parse_all(files)
    if errs:
        return f"{len(files)} 个文件里有 {len(errs)} 个解析不了：{errs[0]}"
    sites, _outside = scan_sites(trees, root)
    if not sites:
        return "全树读不出任何一枚留痕站点（连一枚 `logger.warning` 都没有）——是扫描器坏了，不是代码干净"
    env = scan_envelope_ids(trees, root)
    req = scan_request_ids(trees, root)
    if not env and not req:
        return (
            "关联 id 面两侧都读不出（`trace_id` 与 request-id 双零）——是射程塌了，"
            "不是「没有追踪」这件事被证明了"
        )
    reg, rerrs = parse_register(doc)
    coded_here = [s for s in sites if s["code"]]
    if rerrs and not reg:
        return f"`{CODE_HEADING}` 一条都解析不出（登记行形状变了）：{rerrs[0]}"
    if not reg and coded_here:
        return f"`{CODE_HEADING}` 里一条登记都没有，而现读有 {len(coded_here)} 枚具名码站点"
    ceil, cerrs = parse_ceilings(doc)
    if cerrs and not ceil:
        return f"`{CEILING_HEADING}` 一条都解析不出（上限行形状变了）：{cerrs[0]}"
    if not ceil:
        return f"`{CEILING_HEADING}` 里读不出任何一行 `- level = N`"
    debt, derr = parse_debt(doc)
    if derr and not debt:
        return f"`{DEBT_HEADING}` 一条都解析不出（欠账行形状变了）：{derr[0]}"
    if not debt:
        return f"`{DEBT_HEADING}` 里读不出任何一行 `- `读数键` = N · 状态：…`"
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
    sites, outside = scan_sites(trees, root)
    coded = [s for s in sites if s["code"]]
    print(
        f"✓ 已重新生成 `{DOC_REL}` 自动段（留痕站点 {len(sites)} 枚／具名码 {len({s['code'] for s in coded})} 枚／"
        f"信封 trace_id {len(scan_envelope_ids(trees, root))} 枚／射程外形状 {len(outside)} 枚；手工段未动）"
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
        print(f"✗ 可观测性门禁读不出射程：{blocked}")
        print(
            "  修法：射程断不是「没问题」。确认 `docs/可观测性清单.md` 的四段（自动段标记／具名码登记／"
            "每级别上限／欠账登记）都在原处原形状，且 `src/autoforge/` 整棵目录能 `ast.parse`。"
        )
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 可观测性门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}" if f.startswith("    ") else f"  · {f}")
        print(
            "  修法：A 跑 `python scripts/check_observability.py --write` 重生成自动段（先想清楚该不该动）；"
            "B 给新码在 §三 补一行 `- `文件:行号` · code=X · level=y · 级别语义：… · 介入：… · 依据：… · 理由：… · 认领：…`，"
            "过期登记要删；C 先认领再抬 §四 上限；D 是 serve 访问日志掩码的形状锚点，别顺手摘掉那行调用；"
            "E 是「欠账必须在册」——数字变了要么改账要么说明，不许让 §五 的读数停在旧值上。"
        )
        return 1
    print(
        f"✓ 可观测性门禁干净（现读留痕 {info['sites']} 站点逐行对撞一致；具名码 {info['codes']} 枚／"
        f"{info['coded']} 站点全部在册且认领可读；关联 id 面 信封 {info['env']} 枚／HTTP {info['req']} 枚"
        " 与 §五 账面对撞一致；访问日志掩码锚点在位）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

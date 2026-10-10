#!/usr/bin/env python3
"""信任边界门禁：HTTP 面的「端点 × 方法 × 档位」必须是一份**可 diff 的产物**，而不是一堆可读的装饰器。

起因（两份独立报告同题，第六轮审计 ARCH-05 ＋ 安全与暴露面第三轮 附录 A）：
- ARCH-05 原话是「缺『信任边界清单』这份可核对的资产——哪些端点匿名、哪些要什么 scope、各自的网络前提
  是什么。当前这些信息分散在 60 多个路由装饰器里」，建议「从 `build_app()` 的路由表生成一份清单并对打进
  CI（形状检查：新端点必须显式声明 scope，默认拒绝）」；
- 第三轮报告自己数的是「87 条路由：15 无鉴权／30 read／39 write／3 live」。本门现读（runtime 真表）的读数
  与之有格对不上（见 `docs/信任边界清单.md` 的读数段），所以**真源只能是 `build_app()`**：装饰器条数把
  条件注册算成无条件，而 scope 藏在 `requires(scope)` 的闭包里，静态文本读不出来。

为什么读运行期而不是抄装饰器：`af_api.py` 里那 4 条 POST（`/api/build`、`/api/bind`、`/api/sim`、
`/api/spec/compile`）声明的是 `dependencies=[Depends(_readonly_guard)]`——**单写者租约门，不是鉴权**。
按文本 grep「有没有 _read/_write/_live」会把这四条读成"有守卫所以有鉴权"，正是报告 F-10 那一族
（「看起来设了权限」）。本门从 `route.dependant.dependencies` 取每一条的顶层依赖，再用
`requires()` 闭包里的 `scope` 自由变量定档，读的是系统自己的状态字，不是我的 second-hand 名单。

五条判据，各自单独可红：

- **A 清单对撞**：`docs/信任边界清单.md` 的自动段（`<!-- AUTO-BEGIN/END -->` 之间那张表）必须逐行等于
  现读的路由表。新增端点、删除端点、**档位变化**、**鉴权依赖集合变化**、**处理器函数改名**都红。
  这一条让"边界"进 diff：改一条路由，清单必然跟着动，动没动看得见。
- **B 默认拒绝**（报告点名的形状检查）：每一条**第一方**端点，若档位落在 `anon`／`optional`／
  `bearer-in-handler` 这三档里，必须在手工登记格里在册。不在册＝红。内置文档面（`/openapi.json`、
  `/docs`、`/docs/oauth2-redirect`、`/redoc`）不参与 B——它们是 FastAPI 装配自带、由类型判定，
  该不该在生产开着是部署口径，本门不替 owner 定，只把它们在表里列出来。
- **C 登记可核对**：每条登记必须给 `处理器：`（必须等于现读那条路由的 handler 函数名——"路径还在但
  里面换人了"要能红）、`依据：`（必须是仓内真实存在的文件，指得到记录）、`理由：`（非空、不是「待补」，
  长度下限见 `_MIN_REASON`）。门判不出理由的**语义**，判得出它有没有指向一份记录——与
  `check_gates_coverage.py` 的 `CI_ONLY_EXEMPT`、`check_ci_interpreter.py` 的 `INTERPRETER_DRIFT` 同一档口径。
- **D 只减不增**：登记里那条端点如果已经不再落在非鉴权档（或已消失），必须删掉那一行，否则红。
  收紧边界的人不该被过期登记绊住；留着过期登记＝下一句"这条早就不是匿名了"没人核。
- **E 安装器面（接线即漏）**：`src/` 里除 `af_api.py` 之外，任何提到 `add_api_route`／`@app.<verb>`／
  `APIRouter` 的文件都必须在「未挂载安装器」格里登记，且登记里的**断言**由本门现读复核：入口函数在
  `src/` 内**没有调用者**。今天的真读数：`af_runtime_plugins.install_api` 与 `af_conflict_runtime.install_api`
  都没有调用者（第三轮 F-06 说的就是前者，本批复测把后者也量进来了——那份表里 5 条端点含
  `POST /api/conflicts/{id}/reset` 与 `DELETE /api/conflicts/locks/{entity_id}`，同样是裸挂载）。
  一旦有人把安装器接进装配链而那条挂载不带 `dependencies=`，本门立刻红：**接线即漏，今天就不是"未来"**。

射程前提（读不到就 `exit 2`，不许"没有发现"冒充"没有问题"）：`fastapi`／`autoforge.af_api` 导入不了、
`build_app()` 抛、路由表读出 0 条、清单文件不在、自动段标记缺失或表形读不出、登记段一条都解析不出、
登记的端点键形如 `METHOD path` 但不合法。

`store_root` 一律指到临时目录：`build_app()` 默认 `.forge`（相对 CWD），门禁不该在仓根造目录或写文件。
装配不带 `ui_dir`，所以静态资源与 SPA 兜底不进这张表——那两条面不承载 scope 语义，而 CI 的门禁作业没有
构建产物；这一格在清单里明写，不静默。

纯标准库 + 一次 `import autoforge.af_api`（CI 的 gates 作业装的是 `.[dev]`，`dev` 里就有 fastapi／httpx，
判据 B 那条门也早就在那条作业里跑 pytest，所以这不是新前置）。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC_REL = "docs/信任边界清单.md"
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_trust_boundary.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"

#: 落在这三档的第一方端点必须逐条登记（B 的射程）。
UNGUARDED_TIERS = ("anon", "optional", "bearer-in-handler")
_MIN_REASON = 12
#: 理由写成占位词＝没写。
_PLACEHOLDER = ("待补", "TODO", "TBD", "略", "待定")

#: 判据 B 的豁免：FastAPI 自带的文档/OpenAPI 面。按**类型**判定为内置，这条名单只是二次防护。
BUILTIN_PATHS = ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")

APP_LEVEL_DEP = "_rate_limit_dep"

REGISTER_RE = re.compile(
    r"^- `(?P<method>[A-Z]+) (?P<path>/[^`]*)`\s*·\s*处理器：(?P<call>[A-Za-z_][A-Za-z0-9_]*)\s*·\s*"
    r"依据：(?P<anchor>\S+)\s*·\s*理由：(?P<reason>.+?)\s*$"
)
INSTALLER_RE = re.compile(
    r"^- 入口：(?P<module>src/[^\s:]+\.py)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*·\s*"
    r"断言：(?P<claim>[^·]+?)\s*·\s*备注：(?P<note>[^·]+?)\s*$"
)
#: 「未挂载安装器」那一节里的文件级哨兵：这些形状都算"往 app 上挂端点"。
ROUTE_MECHANISM = re.compile(r"\badd_api_route\b|@app\.(?:get|post|put|delete|patch|websocket)\b|\bAPIRouter\(")

REGISTER_HEADING = "## 三、非鉴权面登记"
INSTALLER_HEADING = "## 四、未挂载安装器登记"


def _dep_label(call: object) -> str:
    """把一条顶层依赖读成档版面标签。`requires(scope)` 的 scope 只能从闭包自由变量里取。"""
    qual = getattr(call, "__qualname__", None)
    if qual is None:
        # 安全方案实例（`HTTPBearer` 之类）没有 `__qualname__`：它是"处理器自己收令牌"的那一档，
        # 读成 `security:<类型名>`，让 `_tier` 按前缀判定，而不是靠某个变量名。
        return f"security:{type(call).__name__}"
    code = getattr(call, "__code__", None)
    if code is not None and "requires.<locals>.dep" in qual:
        cells = dict(zip(code.co_freevars, getattr(call, "__closure__", None) or ()))
        if "scope" in cells:
            return f"requires({cells['scope'].cell_contents})"
    return qual.split(".")[-1]


def _tier(labels: tuple[str, ...]) -> str:
    scopes = sorted(lb[len("requires("):-1] for lb in labels if lb.startswith("requires("))
    if scopes:
        return "scope:" + "+".join(scopes)
    if "dep" in labels:  # authenticated() 的局部函数名就是 dep，且它没有 scope 自由变量
        return "optional"
    if any(lb.startswith("security:") for lb in labels):
        return "bearer-in-handler"
    return "anon"


def derive_rows(root: Path) -> tuple[list[dict], list[str]]:
    """返回 (逐条端点读数, 射程问题)。有问题时 rows 不参与判定。"""
    sys.path.insert(0, str(root / "src"))
    try:
        from autoforge import af_api  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - 射程档
        return [], [f"`autoforge.af_api` 导入失败：{type(exc).__name__}: {exc}"]
    try:
        from fastapi.routing import APIRoute  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        return [], [f"`fastapi.routing` 导入失败：{type(exc).__name__}: {exc}"]
    tmp = tempfile.mkdtemp(prefix="af-trust-boundary-")
    try:
        app = af_api.build_app(store_root=tmp)
    except Exception as exc:  # pragma: no cover
        return [], [f"`build_app()` 构造失败：{type(exc).__name__}: {exc}"]
    rows: list[dict] = []
    unknown: list[str] = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = tuple(sorted(m for m in (getattr(r, "methods", None) or {"-"}) if m not in ("HEAD", "OPTIONS")))
        is_api = isinstance(r, APIRoute)
        if not is_api:
            rows.append({"path": path, "methods": ",".join(methods), "tier": "builtin",
                         "deps": "（无）", "call": "-", "first_party": False})
            continue
        dep = getattr(r, "dependant", None)
        if dep is None:  # pragma: no cover - APIRoute 恒有 dependant
            unknown.append(f"{path}：读不到 dependant")
            continue
        labels = tuple(sorted({_dep_label(sub.call) for sub in dep.dependencies} - {APP_LEVEL_DEP}))
        rows.append({"path": path, "methods": ",".join(methods) or "-", "tier": _tier(labels),
                     "deps": "+".join(labels) or "（无）",
                     "call": getattr(dep.call, "__name__", "?"), "first_party": is_api})
    if not rows:
        return [], ["路由表读出 **0 条**——装配形状变了，本门没有可对撞的对象"]
    return sorted(rows, key=lambda x: (x["path"], x["methods"])), unknown


def render_table(rows: list[dict]) -> list[str]:
    out = [
        "| 方法 | 路径 | 档位 | 现读鉴权依赖 | 处理器 |",
        "|---|---|---|---|---|",
    ]
    for x in rows:
        out.append(f"| {x['methods']} | `{x['path']}` | {x['tier']} | {x['deps']} | `{x['call']}` |")
    counts: dict[str, int] = {}
    for x in rows:
        counts[x["tier"]] = counts.get(x["tier"], 0) + 1
    parts = []
    for key in sorted(counts):
        parts.append(f"{key}={counts[key]}")
    out.append("")
    out.append(
        f"计数：**{len(rows)} 条路由对象** ＝ 第一方 "
        f"{sum(1 for x in rows if x['first_party'])} ＋ 内置 "
        f"{sum(1 for x in rows if not x['first_party'])}；按档位 "
        + " · ".join(parts)
        + f"。（每一条都额外带全局依赖 `{APP_LEVEL_DEP}`，装配期挂在 `FastAPI(dependencies=…)` 上，"
        "不逐条重复列出；它只做限速，**不鉴权**。）"
    )
    return out


def _section(doc: str, heading: str) -> str | None:
    start = doc.find(heading)
    if start < 0:
        return None
    rest = doc[start + len(heading):]
    nxt = rest.find("\n## ")
    return rest[:nxt] if nxt >= 0 else rest


def parse_register(doc: str) -> tuple[dict[str, dict], list[str]]:
    """返回 ({`METHOD path`: 登记项}, 解析问题)。登记段整体读不出形状 ⇒ 交给 anchor_ok 拦。"""
    sec = _section(doc, REGISTER_HEADING)
    if sec is None:
        return {}, [f"清单里找不到 `{REGISTER_HEADING}` 那一节"]
    entries: dict[str, dict] = {}
    errs: list[str] = []
    for line in sec.splitlines():
        if not line.startswith("- `"):
            continue
        m = REGISTER_RE.match(line.strip())
        if not m:
            errs.append(f"登记行解析不出（形状变了）：{line.strip()[:90]}")
            continue
        key = f"{m.group('method')} {m.group('path')}"
        if key in entries:
            errs.append(f"登记重复：{key}")
            continue
        entries[key] = {"call": m.group("call"), "anchor": m.group("anchor"),
                        "reason": m.group("reason").strip()}
    return entries, errs


def parse_installers(doc: str) -> tuple[list[dict], list[str]]:
    sec = _section(doc, INSTALLER_HEADING)
    if sec is None:
        return [], [f"清单里找不到 `{INSTALLER_HEADING}` 那一节"]
    out: list[dict] = []
    errs: list[str] = []
    for line in sec.splitlines():
        if not line.startswith("- 入口："):
            continue
        m = INSTALLER_RE.match(line.strip())
        if not m:
            errs.append(f"安装器登记行解析不出：{line.strip()[:90]}")
            continue
        out.append({"module": m.group("module"), "symbol": m.group("symbol"),
                    "claim": m.group("claim").strip()})
    return out, errs


def _src_py(root: Path):
    for p in sorted((root / "src").rglob("*.py")):
        yield p, p.read_text(encoding="utf-8", errors="replace")


def installer_files(root: Path) -> list[str]:
    return [
        p.relative_to(root).as_posix()
        for p, text in _src_py(root)
        if p.name != "af_api.py" and ROUTE_MECHANISM.search(text)
    ]


def has_caller(root: Path, symbol: str) -> list[str]:
    """`symbol` 在 `src/` 内的**调用**点。判的是 AST 的 `Call` 节点，不是文本里出现的名字。

    为什么不走 grep：`af_runtime_plugins.py` 的注释里写着「conflict 的端点通过 `install_api(app, service)`
    挂载」，按文本读会把一句散文读成一次接线（本仓撞过好几次：散文踩名字哨兵）。AST 面同样连定义它的
    那份文件一起扫——同文件里就地调用也是接线，排除掉就等于留缝。
    """
    import ast  # noqa: PLC0415

    hits: list[str] = []
    for path, text in _src_py(root):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == symbol:
                hits.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return sorted(hits)


def check(root: Path) -> tuple[list[str], dict]:
    findings: list[str] = []
    rows, unknown = derive_rows(root)
    info = {"rows": len(rows), "unknown": unknown}
    if not rows:
        return unknown, info
    doc_p = root / DOC_REL
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    info["doc"] = DOC_REL

    # A —— 清单对撞
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        return [f"`{DOC_REL}` 里找不到自动段标记（`{AUTO_BEGIN}` / `{AUTO_END}`）——表不在，无从对撞"], info
    current = render_table(rows)
    body = doc.split(AUTO_BEGIN, 1)[1].split(AUTO_END, 1)[0]
    in_doc_lines = [l.rstrip() for l in body.strip("\n").splitlines()]
    if in_doc_lines != current:
        drift: list[str] = []
        as_map = {f"{x['methods']} {x['path']}": x for x in rows}
        for line in in_doc_lines:
            if not line.startswith("| ") or line.startswith("| 方法 |") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 5:
                drift.append(f"表里那行的列数不是 5：{line[:80]}")
                continue
            key = f"{cells[0]} {cells[1].strip('`')}"
            cur = as_map.get(key)
            if cur is None:
                drift.append(f"清单里有 `{key}`，现读路由表里没有这条了")
                continue
            if cells[2] != cur["tier"]:
                drift.append(f"`{key}` 档位变了：清单写 `{cells[2]}`，现读是 `{cur['tier']}`")
            if cells[3] != cur["deps"]:
                drift.append(f"`{key}` 鉴权依赖变了：清单写 `{cells[3]}`，现读是 `{cur['deps']}`")
            if cells[4].strip("`") != cur["call"]:
                drift.append(f"`{key}` 处理器变了：清单写 `{cells[4]}`，现读是 `{cur['call']}`")
        doc_keys = set()
        for line in in_doc_lines:
            if line.startswith("| ") and not line.startswith("| 方法 |") and not line.startswith("|---"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 2:
                    doc_keys.add(f"{cells[0]} {cells[1].strip('`')}")
        for key, cur in as_map.items():
            if key not in doc_keys:
                drift.append(f"现读有 `{key}`（档位 `{cur['tier']}`、依赖 `{cur['deps']}`），清单里没有这一行")
        if not drift:
            drift.append(f"自动段整块与现读不同（计数行或排版漂移）——跑 `--write` 重新生成")
        findings.append("A：清单与现读路由表不一致（" + str(len(drift)) + " 处）：")
        findings.extend(f"    · {d}" for d in drift[:12])
        if len(drift) > 12:
            findings.append(f"    ·（其余 {len(drift) - 12} 处省略）")

    register, rerrs = parse_register(doc)
    findings.extend(f"C：{e}" for e in rerrs)
    installers, ierrs = parse_installers(doc)
    findings.extend(f"E：{e}" for e in ierrs)

    # B / C / D —— 默认拒绝、可核对、只减不增
    ungated = {f"{x['methods']} {x['path']}": x for x in rows
               if x["first_party"] and x["tier"] in UNGUARDED_TIERS}
    for key, cur in sorted(ungated.items()):
        ent = register.get(key)
        if ent is None:
            findings.append(
                f"B：`{key}` 落在 `{cur['tier']}` 档（现读鉴权依赖＝{cur['deps']}），"
                f"`{REGISTER_HEADING}` 里没有它的登记——新端点必须显式认领，默认拒绝"
            )
            continue
        if ent["call"] != cur["call"]:
            findings.append(
                f"C：`{key}` 登记写处理器 `{ent['call']}`，现读是 `{cur['call']}`——路径还在但里面换人了"
            )
        anchor = (root / ent["anchor"]).resolve()
        if not anchor.is_file() or root not in anchor.parents:
            findings.append(
                f"C：`{key}` 的依据 `{ent['anchor']}` 不是仓内真实存在的文件——登记必须指得到一份记录"
            )
        reason = ent["reason"]
        if len(reason) < _MIN_REASON or any(p in reason for p in _PLACEHOLDER):
            findings.append(f"C：`{key}` 的理由空、过短或写成占位词：`{reason[:40]}`")
    for key in sorted(register):
        if key not in ungated:
            cur = next((x for x in rows if f"{x['methods']} {x['path']}" == key), None)
            now = f"，现读档位 `{cur['tier']}`" if cur else "，现读路由表里没有这条"
            findings.append(
                f"D：登记里的 `{key}` 已不在非鉴权档{now}——那一行要删掉（只减不增，过期登记没人核就等于没登记）"
            )

    # E —— 安装器面：文件级在册 ＋ 断言现读复核
    listed = {i["module"] for i in installers}
    found = installer_files(root)
    for rel in found:
        if rel not in listed:
            findings.append(
                f"E：`{rel}` 里有挂端点的机制（`add_api_route`／`@app.<verb>`／`APIRouter`），"
                f"但 `{INSTALLER_HEADING}` 没登记它——要么它已进装配链（那就由 A/B 逐条罩住），"
                "要么它是没接线的安装器（那就必须写明入口与断言）"
            )
    for ent in installers:
        if not (ent["claim"].startswith("未挂载") or ent["claim"].startswith("已挂载")):
            findings.append(
                f"E：`{ent['module']}::{ent['symbol']}` 的断言既不是「未挂载」也不是「已挂载」："
                f"`{ent['claim'][:24]}`——本门只认这两种形状，第三种不知道该不该查调用者"
            )
            continue
        if ent["module"] not in found:
            findings.append(f"E：登记了 `{ent['module']}`，但那份文件现在读不出挂端点的机制——过期登记")
            continue
        if "未挂载" in ent["claim"]:
            hits = has_caller(root, ent["symbol"])
            if hits:
                findings.append(
                    f"E：`{ent['module']}::{ent['symbol']}` 登记写「未挂载」，现读却有调用者"
                    f"（{', '.join(hits[:4])}）——**接线已经发生**，那条挂载不带鉴权依赖，"
                    "今天就是漏，不是未来会漏"
                )
    info["found_installers"] = found
    info["register"] = len(register)
    info["ungated"] = len(ungated)
    return findings, info


def anchor_ok(root: Path) -> str | None:
    doc_p = root / DOC_REL
    if not doc_p.is_file():
        return f"`{DOC_REL}` 不在盘上——清单本身就是本门的产物"
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        return f"`{DOC_REL}` 的自动段标记缺失或被改写"
    if _section(doc, REGISTER_HEADING) is None:
        return f"`{DOC_REL}` 缺 `{REGISTER_HEADING}` 那一节"
    if _section(doc, INSTALLER_HEADING) is None:
        return f"`{DOC_REL}` 缺 `{INSTALLER_HEADING}` 那一节"
    rows, unknown = derive_rows(root)
    if not rows:
        return unknown[0] if unknown else "路由表读不出来"
    if unknown:
        return "；".join(unknown)
    reg, rerrs = parse_register(doc)
    if rerrs and not reg:
        return f"`{REGISTER_HEADING}` 一条都解析不出（登记行形状变了）：{rerrs[0]}"
    inst, ierrs = parse_installers(doc)
    if ierrs and not inst:
        return f"`{INSTALLER_HEADING}` 一条都解析不出（登记行形状变了）：{ierrs[0]}"
    if not installer_files(root) and not inst:
        return "`src/` 里读不出任何挂端点的机制，而安装器登记也是空的——射程整体变了，本门该改口径"
    return None


def write_doc(root: Path) -> int:
    doc_p = root / DOC_REL
    doc = doc_p.read_text(encoding="utf-8", errors="replace")
    if AUTO_BEGIN not in doc or AUTO_END not in doc:
        print(f"✗ `{DOC_REL}` 缺自动段标记，拒绝改写")
        return 2
    rows, unknown = derive_rows(root)
    if not rows:
        print(f"✗ 读不出路由表：{unknown}")
        return 2
    body = "\n".join(render_table(rows))
    head, _, rest = doc.partition(AUTO_BEGIN)
    _, _, tail = rest.partition(AUTO_END)
    new = f"{head}{AUTO_BEGIN}\n{body}\n{AUTO_END}{tail}"
    with open(doc_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    print(f"✓ 已重新生成 `{DOC_REL}` 自动段（{len(rows)} 条；手工登记段未动）")
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
        print(f"✗ 信任边界门禁读不出射程：{blocked}")
        print(
            "  修法：射程断不是『没问题』。确认 `docs/信任边界清单.md` 的三段（自动段标记／"
            "非鉴权面登记／未挂载安装器登记）都还在原处原形状，且 `.[dev]` 里的 fastapi 装上了"
            "（本门要 `import autoforge.af_api` 并构造 `build_app()`）。"
        )
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 信任边界门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}" if f.startswith("    ") else f"  · {f}")
        print(
            "  修法：A 跑 `python scripts/check_trust_boundary.py --write` 重新生成自动段（先想清楚"
            "边界该往哪边走）；B 给新落非鉴权档的端点补一行登记（`处理器：`＋`依据：`＋`理由：`），"
            "或者——更好的做法——给它挂上 `Depends(_read/_write/_live)`；C 把依据指到仓内真实存在的记录；"
            "D 删掉过期登记；E 接线时那条挂载必须带 `dependencies=`，然后把登记行改掉并逐条过 B。"
        )
        return 1
    print(
        f"✓ 信任边界门禁干净（现读 {info['rows']} 条路由：第一方端点逐条对撞清单一致；"
        f"{info['ungated']} 条非鉴权档端点全部在册且处理器/依据/理由都核得过、无过期登记；"
        f"{len(info['found_installers'])} 个挂端点的机制全部登记为未挂载并现读复核过没有调用者）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

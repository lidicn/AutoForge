#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""API_CONTRACT.md **整节重写生成器**（仓内工具 · ⛔ 不许手抄）。

三路真源合并：
  ① **线上 OpenAPI**（唯一权威形态）：`GET /openapi.json` ⇒ 路径与方法
  ② **发布树源码现读**：`src/autoforge/af_api.py`（解析 `@app.<method>("<path>", dependencies=[Depends(_x)])`
     ＋ 端点 docstring 首句）⇒ **权限面**与**端点说明**（OpenAPI 的 summary 是自动生成的 `Api Xxx`，无用）
  ③ **枚举真源**：`af_audit.ALL_EVENT_TYPES`、`af_scanner.CHECKS` ⇒ 枚举逐枚相等

两种模式（⛔ 任一真源缺失即报错退出，不允许「缺一就手写补上」）：

  **在线模式**（发版窗口用；路径集取自运行中实例的 OpenAPI）：
      python scripts/render_api_contract.py <openapi.json> <af_api.py> <af_audit.py> <af_scanner.py> <out.md>

  **离线模式**（CI／契约漂移门用；路径集取自源码现读，⛔ 不打网络）：
      python scripts/render_api_contract.py --offline <repo_root> <out.md>
      ⇒ 两条路径集**已核对等价**（同树上：源码现读 94 条 ＝ OpenAPI 94 条；唯一差异是
      FastAPI 把 `/{full_path:path}` 渲染成 `/{full_path}` ⇒ 本脚本已归一化）。

⛔ 只用标准库（ast/json/re/pathlib/sys）⇒ 不引入新依赖。
⛔ 本文件**不得写死任何本机绝对路径**（仓是 public）。
⛔ 也不得**写死行尾**（AF卡_契约生成器eol根治_v120：`--eol auto|crlf|lf`，默认 auto＝
沿用目标文件既有行尾；目标不存在或为空 ⇒ LF）。写死 CRLF 会把 LF 仓/新文件也翻成
CRLF；不控行尾则每次重生成把 CRLF 契约刷成 LF ⇒ 整文件假 diff（v1.19 实测 274/274）。
"""
import ast
import json
import pathlib
import re
import sys

DEP_NAMES = {
    "_read": "read",
    "_write": "write",
    "_live": "live",
    "_admin": "admin",
    "_readonly_guard": "read/write(只读装配期)",
    "_bearer": "bearer(仅校验持令牌)",
    "_write_scope": "write",
}
#: 渲染排除路径（AF-736 F1：**唯一真源在本常量**——契约漂移门
#: `scripts/check_contract_drift.py` 复用 `gen.EXCLUDED_PATHS`，⛔ 任何一侧都不许
#: 再手抄第二份字面量，否则生成器改排除集合时门会假红/假绿）。
EXCLUDED_PATHS = ("/pair", "/{full_path}", "/mcp")
ROUTE_RE = re.compile(r'@app\.(get|post|put|delete|patch)\(\s*"([^"]+)"')


def normalize_route(path: str) -> str:
    """路由归一化：FastAPI 把 `/{full_path:path}` 渲染成 `/{full_path}`。

    ⇒ 源码现读与线上 OpenAPI 的路径集才能**逐条相等**（PM 2026-11-02 实测：
    源码 94 条 ＝ OpenAPI 94 条，唯一差异就是这一处写法）。
    """
    return path.replace("/{full_path:path}", "/{full_path}")


def _collect_docstrings(path: pathlib.Path) -> dict[str, str]:
    """按 AST 取每个函数的 docstring（⛔ 不靠行窗口猜）。

    为什么必须 AST（PM 2026-11-02 实测）：**多行签名**的端点，docstring 落在
    def 之后第 4–5 行 ⇒ 任何「def 后 N 行内找三引号」的窗口法都会漏 ⇒
    误报「端点无 docstring」⇒ **契约表那一格留白**（＝「文档落后于真源」）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise SystemExit(f"!! af_api.py 解析不了，拒绝用残缺结果生成：{exc}")
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                out[node.name] = doc
    return out


def parse_api_source(path: pathlib.Path):
    """从 af_api.py 现读解析：方法、路径、权限面、docstring 首句。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    DOCS = _collect_docstrings(path)
    out = []
    i = 0
    while i < len(lines):
        m = ROUTE_RE.search(lines[i])
        if not m:
            i += 1
            continue
        method, route = m.group(1).upper(), m.group(2)
        # 装饰器可能跨行：吃到括号配平
        chunk = lines[i]
        j = i
        while chunk.count("(") > chunk.count(")"):
            j += 1
            if j >= len(lines):
                break
            chunk += " " + lines[j]
        # 下一行应是 def（先定位，后面签名扫描要用）
        # ⚠️ **必须同时认 `async def`**（PM 2026-11-02 实测漏检）：
        #   `/api/mcp/pair-request` 是 `async def api_pair_request_stream(...)` ⇒
        #   只认 `"def "` 开头 ⇒ 取不到函数名 ⇒ docstring 查不到 ⇒ **误报「端点无 docstring」**
        #   （⇒ 工具说「没有」之前必须先证工具没瞎：本条是反例）
        k = j + 1
        while k < len(lines) and not re.match(r"\s*(async\s+)?def\s+\w+", lines[k]):
            k += 1
        fname = ""
        if k < len(lines):
            fm = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[k])
            fname = fm.group(1) if fm else ""

        deps = re.findall(r"Depends\(\s*(\w+)\s*\)", chunk)
        # 函数签名里也可能带鉴权依赖（`info: TokenInfo = Depends(_write)`）⇒ 一并扫
        sig = ""
        if k < len(lines):
            sig = lines[k]
            m3 = k + 1
            while ":" not in sig.split(")")[-1] and m3 < len(lines) and m3 < k + 6:
                sig += " " + lines[m3]
                m3 += 1
        deps += re.findall(r"Depends\(\s*(\w+)\s*\)", sig)
        faces = sorted({DEP_NAMES.get(d, d) for d in deps}) or ["—(无显式依赖)"]
        # 下一行应是 def（已在上方定位 k）

        # docstring：⛔ **按 AST 取**（不用「def 后三行内找三引号」那种窗口法——
        # 那种写法在**多行签名**的端点上会漏（PM 2026-11-02 实测：`api_graphs`／
        # `api_graph`／`api_automations` 的签名跨 3–4 行 ⇒ docstring 在 def 后第 4–5 行
        # ⇒ 窗口只到 k+3 ⇒ 解析不到 ⇒ 误报「端点无 docstring」）。
        doc = DOCS.get(fname, "")
        # 只取**第一行**（⛔ 不许把整段 docstring 灌进表格），并压空白、限长
        first = ""
        for ln in (doc or "").splitlines():
            s = " ".join(ln.split())
            if s:
                first = s
                break
        doc = first[:140]
        out.append({"method": method, "path": route, "faces": faces,
                    "func": fname, "doc": doc})
        i = j + 1
    return out


def parse_all_event_types(path: pathlib.Path):
    """从 af_audit.py 现读解析 ALL_EVENT_TYPES 的字面量顺序（⛔ 不 import 执行，只静态解析）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                consts[t.id] = node.value.value
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "ALL_EVENT_TYPES" \
                    and isinstance(node.value, (ast.Tuple, ast.List)):
                names = [e.id for e in node.value.elts
                         if isinstance(e, ast.Name)]
                return [consts.get(n, n) for n in names], names
    return [], []


def parse_checks(path: pathlib.Path):
    """从 af_scanner.py 现读解析 CHECKS 的键集合（认 `Assign` 与带注解的 `AnnAssign`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if target is None or value is None:
            continue
        if isinstance(target, ast.Name) and target.id == "CHECKS":
            if isinstance(value, ast.Dict):
                return [k.value for k in value.keys if isinstance(k, ast.Constant)]
            if isinstance(value, (ast.Tuple, ast.List)):
                return [e.value for e in value.elts if isinstance(e, ast.Constant)]
    return []


def esc(s):
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def group_of(path):
    if path.startswith("/api/asks"):
        return "asks（人机回路应答）"
    if path.startswith("/api/auth"):
        return "auth（登录/令牌/主体）"
    if path.startswith("/api/automations"):
        return "automations（自动化启停归档）"
    if path.startswith("/api/bind"):
        return "bind（实体绑定）"
    if path.startswith("/api/build") or path.startswith("/api/sim"):
        return "build/sim（两道闸）"
    if path.startswith("/api/catalog"):
        return "catalog（设备目录）"
    if path.startswith("/api/conf"):
        return "conf（置信度/干预）"
    if path.startswith("/api/credentials"):
        return "credentials（凭据热重载）"
    if path.startswith("/api/entities"):
        return "entities（实体与状态）"
    if path.startswith("/api/evidence"):
        return "evidence（生产证据）"
    if path.startswith("/api/experience"):
        return "experience（经验库）"
    if path.startswith("/api/faults"):
        return "faults（故障图鉴）"
    if path.startswith("/api/graphs"):
        return "graphs（归档治理）"
    if path.startswith("/api/health"):
        return "health"
    if path.startswith("/api/insights"):
        return "insights（洞察审批）"
    if path.startswith("/api/live"):
        return "live（真机下发）"
    if path.startswith("/api/mcp"):
        return "mcp（配对引导）"
    if path.startswith("/api/metrics") or path.startswith("/api/telemetry"):
        return "metrics/telemetry"
    if path.startswith("/api/pending"):
        return "pending（待批队列）"
    if path.startswith("/api/sessions"):
        return "sessions（会话）"
    if path.startswith("/api/spec"):
        return "spec（AF-Spec）"
    if path.startswith("/api/store"):
        return "store（导入导出）"
    if path.startswith("/api/system"):
        return "system（版本/自更新）"
    if path.startswith("/api/undo"):
        return "undo（回滚）"
    if path.startswith("/api/user"):
        return "user（用户面/授权码/配对）"
    if path.startswith("/api/watch"):
        return "watch（监听）"
    if path == "/mcp":
        return "mcp（JSON-RPC 工具面）"
    return "其它"


def tree_version(api_p: pathlib.Path) -> str:
    """从**发布树**现读 `autoforge.__version__`（⛔ 不用线上 OpenAPI 的版本号当发布版号）。

    为什么（PM 2026-11-02 实测）：契约随发布进仓，而**发布那一刻线上跑的还是上一版**
    ⇒ 线上 OpenAPI 的 `info.version` 必然是旧值 ⇒ 写进契约＝在 public 仓里留一条
    过期声明 ⇒ ⇒ 版号必须取自**本次生成所用的源码树**。
    """
    init = api_p.parent / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else "unknown"


def _detect_eol(sample: bytes) -> str:
    """目标文件既有行尾：含 CRLF ⇒ crlf，否则（含不存在/空）⇒ lf。"""
    return "crlf" if b"\r\n" in sample else "lf"


def _resolve_eol(out_p: pathlib.Path, mode: str) -> str:
    """解析写出行尾（AF卡_契约生成器eol根治_v120 §二）：auto＝沿用目标现状；
    显式 crlf/lf 覆盖。⛔ 不许写死 CRLF——那会把 LF 仓/新文件也翻成 CRLF。"""
    if mode != "auto":
        return mode
    try:
        sample = out_p.read_bytes()[:65536]
    except OSError:
        return "lf"
    return _detect_eol(sample)


def main():
    """用法（两种模式）：

    ① **在线模式**（发版时用，路径集取自运行中实例的 OpenAPI）：
       ```
       python af_api_contract_render.py <openapi.json> <af_api.py> <af_audit.py> <af_scanner.py> <out.md>
       ```

    ② **离线模式**（CI 漂移门用，路径集取自源码现读）：
       ```
       python af_api_contract_render.py --offline <repo_root> <out.md>
       ```
       ⚠️ 离线模式**不碰网络**⇒ CI 可跑。⚠️ 两条路径集**已核对等价**：
       PM 2026-11-02 实测（同一棵树上）⇒ **源码现读 94 条 ＝ 线上 OpenAPI 94 条**，
       唯一差异是 FastAPI 把 `/{full_path:path}` 渲染成 `/{full_path}`（同一枚路由，
       两种写法）⇒ ⇒ 离线模式**归一化** `/{full_path:path}` ⇒ `/{full_path}`。
    """
    offline = False
    argv = sys.argv[1:]
    if argv and argv[0] == "--offline":
        offline = True
        argv = argv[1:]
    # `--eol auto|crlf|lf`（AF卡 §二，默认 auto＝沿用目标文件既有行尾）：
    # 与 --offline 一样**位置无关**；值不合法＝用法退出 2（⛔ 不许静默拿默认跑）。
    eol_mode = "auto"
    if "--eol" in argv:
        i = argv.index("--eol")
        if i + 1 >= len(argv):
            print(__doc__)
            return 2
        eol_mode = argv[i + 1]
        if eol_mode not in ("auto", "crlf", "lf"):
            print("!! --eol 只认 auto|crlf|lf，收到：%r" % eol_mode)
            return 2
        argv = argv[:i] + argv[i + 2:]
    if offline:
        if len(argv) != 2:
            print(__doc__)
            return 2
        root = pathlib.Path(argv[0])
        out_p = pathlib.Path(argv[1])
        api_p = root / "src" / "autoforge" / "af_api.py"
        audit_p = root / "src" / "autoforge" / "af_audit.py"
        scanner_p = root / "src" / "autoforge" / "af_scanner.py"
        for p in (api_p, audit_p, scanner_p):
            if not p.exists():
                print("!! 缺真源：%s（⛔ 不许手写补）" % p)
                return 2
        spec = {"paths": {}, "info": {"version": tree_version(api_p)},
                "components": {"securitySchemes": {
                    "HTTPBearer": {"type": "http", "scheme": "bearer"}}}}
        api_rows = parse_api_source(api_p)
        event_types, event_names = parse_all_event_types(audit_p)
        checks = parse_checks(scanner_p)
        tver = tree_version(api_p)
        # 离线：路径集来自源码现读 ⇒ 自己造一份最小 OpenAPI 形状给下游共用
        for r in api_rows:
            path = normalize_route(r["path"])
            spec["paths"].setdefault(path, {})[r["method"].lower()] = {"responses": {}}
    else:
        if len(argv) != 5:
            print(__doc__)
            return 2
        openapi_p, api_p, audit_p, scanner_p, out_p = \
            [pathlib.Path(x) for x in argv]
        for p in (openapi_p, api_p, audit_p, scanner_p):
            if not p.exists():
                print("!! 缺真源：%s（⛔ 不许手写补）" % p)
                return 2
        spec = json.loads(openapi_p.read_text(encoding="utf-8"))
        api_rows = parse_api_source(api_p)
        event_types, event_names = parse_all_event_types(audit_p)
        checks = parse_checks(scanner_p)
        tver = tree_version(api_p)

    # 路径/方法以 OpenAPI 为准；权限面与说明以源码现读为准
    op_index = {}
    for p, item in (spec.get("paths") or {}).items():
        for m, op in item.items():
            if m.lower() in ("get", "post", "put", "delete", "patch"):
                op_index[(m.upper(), p)] = op
    src_index = {(r["method"], normalize_route(r["path"])): r for r in api_rows}

    groups = {}
    unmatched = []
    for (m, p), op in sorted(op_index.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if p in EXCLUDED_PATHS:
            continue
        r = src_index.get((m, p))
        if r is None:
            unmatched.append((m, p))
        g = group_of(p)
        groups.setdefault(g, []).append({
            "method": m, "path": p,
            "faces": "／".join(r["faces"]) if r else "—(源码未匹配)",
            "doc": (r["doc"] if r else "") or (op.get("description") or "").strip()
                    or "—(端点无 docstring)",
        })

    lines = []
    A = lines.append
    A("# AutoForge 服务层 API 契约")
    A("")
    A("> **本文由脚本生成，⛔ 端点表与枚举不许手抄**（真源：运行中实例的 `GET /openapi.json` ＋ "
      "发布树 `src/autoforge/af_api.py` 现读 ＋ `af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS` 现读）。")
    A("> **唯一权威形态仍是运行中的 FastAPI**：`GET /openapi.json`（OpenAPI 3）／`GET /docs`（Swagger UI）。")
    A("> 生成方式：**脚本生成**（真源口径见上一行）；⛔ **端点表与枚举不许手抄、手改**。"
      "生成器：`scripts/render_api_contract.py`（仓内工具，⛔ 只用标准库）；"
      "契约漂移门（`scripts/check_contract_drift.py`）会重跑它并只比「端点表＋枚举」两段。"
      "本文是**速查表**，联调以 `/openapi.json` 为准。")
    A("")
    A("| 现读坐标 | 值 |")
    A("|---|---|")
    A("| 发布树版本（`autoforge.__version__`，取自本次生成所用源码树） | `%s` |" % tver)
    A("| 生成时线上实例 OpenAPI 版本 | `%s`（⛔ **不作发布版号**：发布那一刻线上还跑着上一版；部署后自动对齐） |"
      % (spec.get("info") or {}).get("version"))
    A("| 路径数／操作数 | **%d 路径／%d 操作** |" % (len(spec.get("paths") or {}), len(op_index)))
    A("| 认证方案 | `securitySchemes` = %s |" % json.dumps(
        (spec.get("components") or {}).get("securitySchemes") or {},
        ensure_ascii=False))
    A("| 权限面列来源 | `af_api.py` 的 `Depends(_read/_write/_live/_admin/_readonly_guard)` 现读 |")
    A("| `audit[].type` | **%d 枚**（`af_audit.ALL_EVENT_TYPES` 现读逐枚相等） |" % len(event_types))
    A("| `diagnostics[].code` | **%d 枚**（`af_scanner.CHECKS` 现读） |" % len(checks))
    A("")
    A("Base：`http://<host>:8787`（NAS 部署：`http://192.168.2.200:8787`）")
    A("")
    A("## 一、鉴权与授权面")
    A("")
    A("- **所有 `/api/*` 端点都要求 Bearer**（`securitySchemes` ＝ `HTTPBearer`）——例外只有两条："
      "`/api/health`（免鉴权、只读）与 `/api/auth/login`／`/api/auth/register`（用户名口令换令牌）。")
    A("- **表里「无显式依赖」≠ 免鉴权**：那几行是 `auth/*` 登录族与 `mcp/pair/*` ——"
      "它们**没有 route 级 scope 依赖**，但**仍要登录令牌／一次性配对码**；"
      "`auth/logout|me|revoke|whoami|has-admin` 用的是**登录令牌**（owner 主体），不是 access token。")
    A("- **scope 四档**：`read`（只读）／`write`（写）／`live`（真机下发）／`admin`（管理面：令牌、授权码、"
      "自更新、watch 起停、归档删除）。")
    A("- **owner 面 vs 非 owner 面**：`_OWNER_SUBJECTS = {owner, shared}` 为 owner 面；"
      "非 owner 面的读面**只见自己名下**，并**必报 `hidden_count`**、带 `scope=owner:<主体>`；"
      "**owner 面不报 `hidden_count`、不带 `scope`，且能读到他人的归档**（与非 owner 面**正相反**）。"
      "读别人的归档时（非 owner 面）**拒绝文案与「不存在」逐字同形**（⛔ 不泄露存在性、⛔ 不点名归属者）。")
    A("- **授权码**：往真机加动作才要码；**无效码一律四因合一同形文案**"
      "（⛔ 不区分「不存在／已消耗／已过期／已锁定」）；**免码面**：禁用、serve 自动恢复监听、dry-live。")
    A("- **免令牌逃生舱 `AF_ALLOW_NOAUTH`**：现网该开关**值为空** ⇒ 生效条件不满足 ⇒ 实测免令牌调用"
      "`GET /api/graphs` 与 `POST /api/pending/list` 均 **403**（⛔ **无未授权读面**）。")
    A("")
    A("## 二、端点全表（生成自 OpenAPI ＋ 源码现读）")
    A("")
    for g in sorted(groups):
        A("### %s" % g)
        A("")
        A("| 方法 | 路径 | 权限面 | 说明 |")
        A("|---|---|---|---|")
        for row in groups[g]:
            A("| `%s` | `%s` | %s | %s |" % (row["method"], esc(row["path"]),
                                            esc(row["faces"]), esc(row["doc"])))
        A("")
    if unmatched:
        A("> ⚠️ **源码未匹配到权限面的端点**（OpenAPI 有、源码解析没命中，⛔ 不许猜）：%s"
          % ("、".join("%s %s" % u for u in unmatched)))
        A("")
    A("## 三、关键枚举（全部现读真源）")
    A("")
    A("- `audit[].type`（**%d 枚**，真源 `af_audit.ALL_EVENT_TYPES`）：%s"
      % (len(event_types), "`" + "`｜`".join(event_types) + "`"))
    A("- `diagnostics[].level`：`error`｜`warning`；`diagnostics[].code`（真源 `af_scanner.CHECKS`，"
      "**%d 枚**）：%s" % (len(checks), "`" + "`｜`".join(checks) + "`"))
    A("- `band`：`auto`（conf ≥ 0.85）｜`shadow`（≥ 0.60）｜`ask`（< 0.60）")
    A("- `instances[].state`：`created`｜`active`｜`suspended`｜`done`｜`cancelled`｜`failed`｜`expired`")
    A("- **响应无信封**：所有响应是**扁平**对象（⛔ 没有 `{\"data\": ...}` 外壳）。")
    A("")
    A("## 四、真机下发（三重闸，缺一即拒）")
    A("")
    A("1. 服务端开关 `AUTOFORGE_LIVE_ENABLED=1`（**默认关闭**）→ 否则 **403**")
    A("2. 服务端 HA 令牌（**绝不从请求体接收令牌**）→ 否则 **403**")
    A("3. 请求体 `confirm=true` ＋ 非空 `live_allow`，且 IR 写目标 ⊆ 白名单 → 否则 **403/400**")
    A("")
    A("## 五、变更纪律")
    A("")
    A("> 契约变更须同步：本文件（**重新生成，⛔ 不手改端点表**）、`af_api.py`、`af_service.py`、"
      "对应单测、以及前端 Mock。")
    A("")
    A("> **生成口径**：端点表＝`GET /openapi.json` 现读；权限面与说明＝`af_api.py` 现读解析；"
      "枚举＝`af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS` 现读。**任一真源缺失 ⇒ 生成器报错退出，"
      "⛔ 不许手写补洞。**")
    A("")
    eol = _resolve_eol(out_p, eol_mode)
    content = "\n".join(lines)
    if eol == "crlf":
        content = content.replace("\n", "\r\n")
    # write_bytes：⛔ 不走文本模式换行翻译（那会把刚定好的行尾再翻一次）。
    out_p.write_bytes(content.encode("utf-8"))
    print("wrote=%s lines=%d paths=%d ops=%d events=%d checks=%d unmatched=%d eol=%s(mode=%s)"
          % (out_p, len(lines), len(groups), len(op_index), len(event_types),
             len(checks), len(unmatched), eol, eol_mode))
    if unmatched:
        for u in unmatched:
            print("  unmatched:", u)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

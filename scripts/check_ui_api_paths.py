#!/usr/bin/env python3
"""UI↔路由契约门禁：前端调的每个 API 路径 + 方法，必须真在服务端路由表里。

起因：`ui/` 没有 vitest，UI 改动的判据只有 `vue-tsc --force`、`vite build` 和真浏览器读数三条，
前两条只证"能编译"。而路径是**手抄的字符串**：服务端把 `/api/undo/{deploy_id}` 改名或删掉、
或者把 GET 改成 POST，前端那行 `` request<UndoPreviewResponse>('GET', `/undo/${…}`) `` 一样编译
通过、一样 build 得出，只有真点一次才炸（§二之二十七 那条"前端抄了服务端默认值 60"是同一族：
跨层契约没有一条门在管，抄错的人看不见）。本门把"路径在不在路由表、方法对不对"变成静态判据。

三条判据（各自能单独判红）：
- **A 路径存在**：每个 UI 调用点解析出的路径（剥掉 query、`${…}` 当一段通配）必须命中一条
  **参与匹配的**服务端路由。SPA 兜底 `GET /{full_path:path}` 与 MCP 面 `POST /mcp` 被排除在
  匹配集外——把它们放进来，任何拼错的路径都会被兜底"接住"，本门就永远绿（假绿，铁律 #5/#8）。
- **B 方法一致**：路径命中但方法不符也红（405 与 404 是两种故障，只抄对路径不算过关）。
- **C 反空洞自证**：`request(` 的**每一个**调用点都必须解析出（方法, 路径）对，解析不出就
  exit 2，而不是"跳过这一条继续"。依据是实测：早期正则版把
  `` `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}` `` 这类
  嵌套反引号**静默丢掉**，于是报出"缺失 0"——0 里混着"根本没进射程"的空洞。条件表达式
  `cond ? '/a' : '/b'` 两个分支都要计入射程。

锚点（读不到就 exit 2，不许静默全绿）：`ui/src/api/client.ts` 在盘上、UI 侧至少解析出 1 个调用点、
`src/` 下至少扫到 1 条参与匹配的路由——任一为空说明本门射程已塌，此时"没有发现"不代表"没有问题"。
路由有**两张脸**：`af_api.py` 的 `@app.get("/api/…")` 装饰器，以及 `af_conflict_runtime.py` 那种
`("GET", "/api/conflicts", handler)` 表 + `app.add_api_route(...)` 挂载。只读装饰器会对那 5 条真路由
报"路由表里没有这条"——**假红，而且给的修法方向是错的**（端点明明在）。所以表形状也读，并且：某文件
既有 `add_api_route` 又有字面量 `"/api/…"`，却按表形状读出 0 条 ⇒ `exit 2`（那张表改了形，本门就瞎了）。
`af_runtime_plugins.py` 是另一种情形：它有 `add_api_route`、0 个字面量 `/api/`，路径全部由插件在运行期声明
⇒ 属**登记在册的射程边界**，计入绿色行而非报错。判别用文件自己的形状（有没有字面量路径），不把文件名写死在门里。

现场豁免 `# ui-api: exempt(理由)`，理由不能空，且**单独计入读数**（把靠豁免过关的算进干净里，
等于把未验证的当已验证）。反向读数（服务端有、UI 从未调）只计数不判红：MCP/DB 面向的路由本来
就不该有前端调用点，判红只会逼人把门关掉。

纯标准库：本机禁 pip install，依赖第三方 JS 解析器的门等于没有门禁。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_PREFIX = "/api"
MARK = "\x01"  # `${…}` 的通配占位符
VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_EXEMPT = re.compile(r"ui-api:\s*exempt\(\s*(\S[^)]*)\s*\)")
DECORATOR = re.compile(r'@(?:app|router)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']')
# 第二张脸：`af_conflict_runtime.py` 用 `("GET", "/api/conflicts", handler)` 这样的表 + `add_api_route`
# 挂载真路由。只认装饰器就会对那 5 条报"路由表里没有这条"——**假红，且给的修法方向是错的**
# （端点明明在）。表形状是静态可读的，就读进来，并单列为 `mounted` 计数。
MOUNTED = re.compile(r'\(\s*"(GET|POST|PUT|PATCH|DELETE)"\s*,\s*"(/api/[^"]+)"')
MOUNT_MECHANISM = "add_api_route"
# 判别"这文件有没有静态可读的路由表"：看它有没有字面量 `"/api/…"`。
# `af_runtime_plugins.py` 有 `add_api_route` 却 0 个字面量 `/api/`——路径是插件在运行期声明的，
# 静态永远读不出，这不是"表改了形"。用它自己的形状区分，而不是把文件名写死在门里。
API_LITERAL = re.compile(r'["\']/api/[^"\']*["\']')
# 兜底与 MCP 面不是 API 路由：参加匹配等于给任何错路径开后门。
# 只排**恰好** `/mcp` 与带 `{full_path` 的 SPA 兜底：`/api/mcp/pair-request` 是真的 API 路由，
# 按前缀排会把一类真路由静默挪出射程（= 判红能力丢失）。


def _is_excluded(raw: str) -> bool:
    return raw == "/mcp" or "{full_path" in raw


def _skip_literal(text: str, i: int) -> int | None:
    """`text[i]` 是 `'` `"` 反引号 之一时返回字面量结束后的下标；不闭合返回 None。

    反引号里的 `${…}` 整段跳过（内部可以再嵌一层反引号）——正则版丢路径就是丢在这里。
    """
    q = text[i]
    n = len(text)
    j = i + 1
    if q == "`":
        while j < n:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1
            if c == "$" and j + 1 < n and text[j + 1] == "{":
                k = _skip_braces(text, j + 1)
                if k is None:
                    return None
                j = k
                continue
            j += 1
        return None
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == q:
            return j + 1
        if c == "\n":
            return None
        j += 1
    return None


def _skip_braces(text: str, i: int) -> int | None:
    """`text[i] == '{'`：返回配对的 `}` 之后的下标；内部字符串/模板/注释都跳过。"""
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    j = i
    n = len(text)
    while j < n:
        c = text[j]
        if c in "\"'`":
            k = _skip_literal(text, j)
            if k is None:
                return None
            j = k
            continue
        k = _skip_comment(text, j)
        if k is not None:
            if k > n:
                return None
            j = k
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def _skip_comment(text: str, i: int) -> int | None:
    """命中 `//` 或 `/*` 返回跳过后的下标（行注释到行首为止，找不到换行返回 `len+1`），否则 None。"""
    if text[i:i + 2] == "//":
        k = text.find("\n", i)
        return len(text) + 1 if k < 0 else k
    if text[i:i + 2] == "/*":
        k = text.find("*/", i + 2)
        return None if k < 0 else k + 2
    return None


def _split_args(text: str, open_idx: int) -> list[str] | None:
    """`text[open_idx] == '('`：返回顶层实参源码片段列表；括号/引号不闭合返回 None。"""
    args: list[str] = []
    depth = 0
    start = open_idx + 1
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            k = _skip_literal(text, i)
            if k is None:
                return None
            i = k
            continue
        k = _skip_comment(text, i)
        if k is not None:
            i = min(k, n)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                if c != ")":
                    return None
                args.append(text[start:i])
                return args
        elif c == "," and depth == 1:
            args.append(text[start:i])
            start = i + 1
        i += 1
    return None


def _top_literals(expr: str) -> list[tuple[str, str]] | None:
    """表达式里**顶层**的字面量 `(引号, 内容)`；`cond ? '/a' : '/b'` 两支都返回。"""
    out: list[tuple[str, str]] = []
    depth = 0
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'`":
            k = _skip_literal(expr, i)
            if k is None:
                return None
            if depth == 0:
                out.append((c, expr[i + 1:k - 1]))
            i = k
            continue
        k = _skip_comment(expr, i)
        if k is not None:
            i = min(k, n)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return None
            depth -= 1
        i += 1
    return out


def _normalize(inner: str) -> str | None:
    """字面量内容 → 可比对的路径：`${…}` 折成通配，query 只在替换外的 `?` 处切。"""
    chars: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == "$" and i + 1 < n and inner[i + 1] == "{":
            k = _skip_braces(inner, i + 1)
            if k is None:
                return None
            chars.append(MARK)
            i = k
            continue
        if c == "?":
            break
        chars.append(c)
        i += 1
    path = "".join(chars)
    while MARK * 2 in path:
        path = path.replace(MARK * 2, MARK)
    if path.endswith("/"):
        path = path[:-1] or "/"
    return path


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno, lineno - 1):
        if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
            return True
    return False


def _call_sites(text: str, rel: str) -> tuple[list[dict], list[str]]:
    """返回 `(解析出的调用点, 解析不出的说明)`。调用点 = dict(method, paths, line, rel, exempt, kinds)。"""
    sites: list[dict] = []
    unparsed: list[str] = []
    lines = text.splitlines()

    def site(line: int, verb: str, paths: list[str], kinds: set[str]) -> None:
        sites.append({"method": verb, "paths": paths, "line": line, "rel": rel,
                      "exempt": _is_exempt(lines, line), "kinds": kinds})

    for m in re.finditer(r"\brequest\b", text):
        head = text[:m.start()].rstrip()
        if head.endswith("function") or head.endswith("."):
            continue  # 定义本身 / 别人对象上的 .request
        i = m.end()
        i += re.match(r"\s*", text[i:]).end()
        if i < len(text) and text[i] == "<":
            # 泛型实参：按 <> 计数跳过，字符串里的 < > 不算，`=>` 里的 > 不算
            depth = 0
            while i < len(text):
                if text[i] in "\"'`":
                    k = _skip_literal(text, i)
                    if k is None:
                        break
                    i = k
                    continue
                if text[i] == "<":
                    depth += 1
                elif text[i] == ">" and text[i - 1] != "=":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                elif text[i] in "(;":
                    break
                i += 1
            i += re.match(r"\s*", text[i:]).end()
        if i >= len(text) or text[i] != "(":
            continue  # 不是调用点
        line = text[:m.start()].count("\n") + 1
        args = _split_args(text, i)
        if args is None or len(args) < 2:
            unparsed.append(f"{rel}:{line}: 实参没能切出来（引号/括号不闭合，或少于两个实参）")
            continue
        method_lit = _top_literals(args[0])
        if not method_lit or len(method_lit) != 1:
            unparsed.append(f"{rel}:{line}: 方法不是单个字面量（`{args[0].strip()[:40]}`）")
            continue
        verb = method_lit[0][1].upper()
        if verb not in VERBS:
            unparsed.append(f"{rel}:{line}: 方法字面量不是认得的 HTTP 动词（`{verb}`）")
            continue
        path_lits = _top_literals(args[1])
        if path_lits is None:
            unparsed.append(f"{rel}:{line}: 路径表达式里有不闭合的引号/括号")
            continue
        if not path_lits:
            unparsed.append(f"{rel}:{line}: 路径不是字面量（`{args[1].strip()[:40]}`）——"
                            f"要么改成静态可读的写法，要么就地写 `# ui-api: exempt(理由)`")
            continue
        paths: list[str] = []
        bad = False
        for quote, inner in path_lits:
            norm = _normalize(inner)
            if norm is None:
                unparsed.append(f"{rel}:{line}: 模板里的 `${{…}}` 不闭合")
                bad = True
                break
            paths.append(norm)
        if bad:
            continue
        site(line, verb, paths, {q for q, _ in path_lits})
    return sites, unparsed


def collect_ui_sites(ui_root: Path) -> tuple[list[dict], list[str]]:
    sites: list[dict] = []
    unparsed: list[str] = []
    for path in sorted(list(ui_root.rglob("*.ts")) + list(ui_root.rglob("*.vue"))):
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        s, u = _call_sites(path.read_text(encoding="utf-8"), rel)
        sites.extend(s)
        unparsed.extend(u)
    return sites, unparsed


def collect_routes(src_root: Path) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """返回 `(参与匹配的路由, 被排除的兜底/MCP, 射程自证问题, 运行期挂载文件)`。"""
    routes: list[dict] = []
    excluded: list[dict] = []
    errs: list[str] = []
    boundary: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        for m in DECORATOR.finditer(text):
            raw = m.group(2)
            entry = {"method": m.group(1).upper(), "path": raw, "rel": rel,
                     "line": text[:m.start()].count("\n") + 1, "how": "decorator"}
            key = (entry["method"], entry["path"])
            if key in seen:
                continue
            seen.add(key)
            (excluded if _is_excluded(raw) else routes).append(entry)
        if MOUNT_MECHANISM not in text:
            continue
        hits = list(MOUNTED.finditer(text))
        for m in hits:
            entry = {"method": m.group(1), "path": m.group(2), "rel": rel,
                     "line": text[:m.start()].count("\n") + 1, "how": "add_api_route"}
            key = (entry["method"], entry["path"])
            if key in seen:
                continue
            seen.add(key)
            (excluded if _is_excluded(entry["path"]) else routes).append(entry)
        if hits:
            continue
        # 读出 0 条，但要分清是"表改了形"还是"这文件本来就没有静态表"：
        # 有字面量 `/api/…` 却没有一条落在表形状上 ⇒ 表形变了，本门会瞎报假红 ⇒ exit 2；
        # 一个 `/api/…` 字面量都没有 ⇒ 路径由插件在运行期声明，静态无从得知 ⇒ 登记为射程边界，
        # 计入读数而非报错（把它当错误会让本门永远红，最后被人关掉——那才是真的假绿）。
        if API_LITERAL.search(text):
            errs.append(f"{rel} 里有 `{MOUNT_MECHANISM}`、也有字面量 `\"/api/…\"`，但按 "
                        f"`(\"GET\", \"/api/…\", handler)` 表形状读出 **0 条**——那张表就是真路由，"
                        f"读不到 ⇒ 本门会对这些路径报假红，给的修法方向还是错的（端点明明在）")
        else:
            boundary.append(rel)
    return routes, excluded, errs, boundary


def _shape(path: str) -> list[str]:
    """路径 → 逐段形状：通配段（`${…}` 或 `{param}`）统一成 `*`，其余原样。"""
    out = []
    for seg in path.split("/"):
        if MARK in seg or (seg.startswith("{") and seg.endswith("}")):
            out.append("*")
        else:
            out.append(seg)
    return out


def _display(path: str) -> str:
    """读数/报错里把通配占位符显示成 `*`，别把 `\x01` 打进终端。"""
    return path.replace(MARK, "*")


def _matches(ui_path: str, route_path: str) -> bool:
    """段数对齐才算命中；`*` 认任意一段。

    两条方向的通配都放行是**故意的**：服务端 `/api/graphs/{name}` 在 HTTP 层确实接得住
    `/api/graphs/tags` 这种字面量段，前端写死一段路径不算"路由不存在"（那是 404 的反例，
    本门判的是路径可不可达，不是值对不对）。段的**数量**仍然要对齐，多一段少一段都红。
    """
    a, b = _shape(API_PREFIX + ui_path), _shape(route_path)
    if len(a) != len(b):
        return False
    return all(x == y or x == "*" or y == "*" for x, y in zip(a, b))


def _unused_routes(sites: list[dict], routes: list[dict]) -> list[dict]:
    used = {tuple(_shape(API_PREFIX + p)) for s in sites for p in s["paths"]}
    return [r for r in routes if tuple(_shape(r["path"])) not in used]


def check(sites: list[dict], routes: list[dict]):
    findings: list[str] = []
    active = [s for s in sites if not s["exempt"]]
    for site in active:
        for upath in site["paths"]:
            if not upath.startswith("/"):
                findings.append(f"{site['rel']}:{site['line']}: `{site['method']}` 的相对路径不以 "
                                f"`/` 开头（{upath!r}）——拼到 API_BASE 上会得到错路径")
                continue
            hits = [r for r in routes if _matches(upath, r["path"])]
            api = _display(API_PREFIX + upath)
            if not hits:
                findings.append(
                    f"{site['rel']}:{site['line']}: UI 调 `{site['method']} {api}`，服务端两张路由脸"
                    "（`@app.*` 装饰器 + `add_api_route` 挂载表）里都没有这条"
                    "（SPA 兜底 `GET /{full_path:path}` 与 `POST /mcp` 已排除，不参与匹配）"
                    "——服务端改名或删路由时，前端只会静默 404")
                continue
            if not any(r["method"] == site["method"] for r in hits):
                got = "/".join(sorted({r["method"] for r in hits}))
                where = "/".join(sorted({f"{r['rel']}:{r['line']}" for r in hits}))
                findings.append(
                    f"{site['rel']}:{site['line']}: UI 用 `{site['method']}` 调 `{api}`，"
                    f"服务端这条路由是 `{got}`（在 {where}）——405 与 404 是两种故障，抄对路径不算过关")
    return findings


def _counts(sites: list[dict], routes: list[dict], excluded: list[dict], unused: list[dict],
            unparsed: list[str], boundary: list[str]) -> dict[str, int]:
    kinds = {"plain": 0, "template": 0, "branch": 0}
    for s in sites:
        if len(s["paths"]) > 1:
            kinds["branch"] += 1
        elif "`" in s["kinds"]:
            kinds["template"] += 1
        else:
            kinds["plain"] += 1
    return {"sites": len(sites), "plain": kinds["plain"], "template": kinds["template"],
            "branch": kinds["branch"], "exempted": sum(1 for s in sites if s["exempt"]),
            "unparsed": len(unparsed), "routes": len(routes), "excluded": len(excluded),
            "mounted": sum(1 for r in routes if r["how"] == "add_api_route"),
            "unused": len(unused), "boundary": len(boundary)}


def anchor_errors(ui_root: Path, sites: list[dict], routes: list[dict],
                  unparsed: list[str]) -> list[str]:
    errs: list[str] = []
    client = ui_root / "api" / "client.ts"
    if not client.is_file():
        errs.append(f"锚点文件不在盘上：{client.as_posix()}（改名 ⇒ 本门看不见任何调用点，会当成干净）")
    if not sites:
        errs.append("UI 侧一个 `request(` 调用点都没解析出来（射程塌了，不是没有问题）")
    if not routes:
        errs.append("`src/` 下没扫到任何参与匹配的路由（装饰器写法变了 ⇒ 本门无从比对）")
    for u in unparsed:
        errs.append(u)
    return errs


def main(argv: list[str]) -> int:
    ui_root = Path(argv[1]) if len(argv) > 1 else REPO / "ui" / "src"
    src_root = Path(argv[2]) if len(argv) > 2 else REPO / "src"
    if not ui_root.is_absolute():
        ui_root = REPO / ui_root
    if not src_root.is_absolute():
        src_root = REPO / src_root
    if not ui_root.is_dir():
        print(f"✗ UI 源码目录不存在：{ui_root.as_posix()}")
        return 2
    if not src_root.is_dir():
        print(f"✗ 服务端源码目录不存在：{src_root.as_posix()}")
        return 2

    sites, unparsed = collect_ui_sites(ui_root)
    routes, excluded, mount_errs, boundary = collect_routes(src_root)
    errs = anchor_errors(ui_root, sites, routes, unparsed)
    errs.extend(mount_errs)
    if errs:
        print(f"✗ UI↔路由契约门禁读不到锚点、有 {len(unparsed)} 个调用点解析不出，"
              f"或挂载表读不出（三类都是射程问题，不静默放行）：")
        for e in errs:
            print(f"  {e}")
        print(f"  读数：调用点 {len(sites)} 处、参与匹配路由 {len(routes)} 条 —— "
              f"两者任一为 0 都说明本门射程已塌，此时『没有发现』不等于『没有问题』。")
        return 2

    findings = check(sites, routes)
    totals = _counts(sites, routes, excluded, _unused_routes(sites, routes), unparsed, boundary)
    if findings:
        print(f"✗ UI↔路由契约门禁发现 {len(findings)} 处（UI 调用点 {totals['sites']} 处、"
              f"服务端参与匹配路由 {totals['routes']} 条、现场豁免 {totals['exempted']} 处）：")
        for f in findings:
            print(f"  {f}")
        print(f"  修法：路径与方法对齐服务端两张路由脸（`af_api.py` 的 `@app.*` 装饰器、"
              f"`af_conflict_runtime.py` 的 `add_api_route` 挂载表）；确实要调路由表外的口子"
              f"（外链、代理）就地写 `# ui-api: exempt(理由)`。")
        return 1
    # 绿色行每个数字都是本轮实测计数，不写"都在/全部"这类没数过的断言（铁律 #5）
    print(f"✓ UI↔路由契约门禁干净（UI 调用点 {totals['sites']} 处：字面量 {totals['plain']}、"
          f"模板拼接 {totals['template']}、条件分支 {totals['branch']}；"
          f"服务端参与匹配路由 {totals['routes']} 条（装饰器 {totals['routes'] - totals['mounted']}、"
          f"`add_api_route` 挂载表 {totals['mounted']}）、被排除的兜底/MCP {totals['excluded']} 条；"
          f"运行期挂载文件 {totals['boundary']} 个（路径由插件声明，静态读不出，登记在册的射程边界）；"
          f"反向读数 UI 未调用 {totals['unused']} 条（只计数不判红）；现场豁免 {totals['exempted']} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

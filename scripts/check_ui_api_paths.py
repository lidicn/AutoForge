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
- **C 反空洞自证**：调用点的**每一个**都必须解析出（方法, 路径）对，解析不出就 exit 2，
  而不是"跳过这一条继续"。依据是实测：早期正则版把
  `` `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}` `` 这类
  嵌套反引号**静默丢掉**，于是报出"缺失 0"——0 里混着"根本没进射程"的空洞。条件表达式
  `cond ? '/a' : '/b'` 两个分支都要计入射程。

调用点有三张脸（实测，见 `HELPERS`）：`ui` 是 `request('GET', '/undo/…')`（动词在前）；两棵用户端树是
`req('/user/agents')` 与 `req(path, { method: 'DELETE' })`（路径在前、动词在 `RequestInit` 里、**省略即 GET**）。
只认前一张会把两棵用户端树整个读成 0 个调用点。路径归一按**它自己带不带 `/api`**：`ui-user-mimo` 把
`/api/…` 写全（它的 `API_BASE` 默认空串），另外两棵写 BASE 之后的相对段。

射程面是**树登记表**（`UI_TREES`）：本仓有三棵第一方 UI 树，门曾经只看 `ui/src`。那不只是少看一棵树——
反向读数因此把 33 条报成"UI 从未调"，而其中 17 条是 `ui-user`/`ui-user-mimo` 在真调的活接口
（§二之三十一 实测：门扩到三棵树后反向读数 33 ⇒ 16）。于是两条新射程判据：**登记树上读不出任何调用点**
（目录没了 / anchor 文件改名 / 该树 helper 换了名）⇒ exit 2；**盘上多出形状像 UI 的顶层目录**
（有 `package.json` 且有 `src/`）而不在登记表里 ⇒ exit 2，因为"漏一棵树"最坏的表现恰恰是**绿行**。

锚点（读不到就 exit 2，不许静默全绿）：每棵登记树的 anchor 文件在盘上、该树至少解析出 1 个调用点、
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
# 三种调用脸（实测得出，不是猜的）：
# - `request('GET', '/undo/…')`：`ui/src/api/client.ts` 的形状，动词在前。
# - `req('/user/agents')` / `req(path, { method: 'DELETE' })`：两棵用户端树的形状，路径在前、
#   动词藏在 `RequestInit` 对象里，**省略即 GET**（fetch 的默认档）。
# 只认第一种会把两棵用户端树整个读成"0 个调用点"——那正好是射程塌了的形状。
HELPERS = {"request": "verb-first", "req": "path-first"}
HELPER_RE = re.compile(r"\b(request|req)\b")
METHOD_KEY = re.compile(r"method")
# 第一方 UI 树登记表。门曾只看 `ui/src`，而 `ui-user/src/api/client.ts` 一直在真调
# `/automations…`、`/user/agents…` ⇒ 33 条"UI 从未调"里有 11 条其实是射程外的活接口。
# 每棵树的 anchor 用来抓"整棵树改名/搬走"（读不到调用点时至少能说清是哪棵树塌了）。
UI_TREES = (
    {"dir": "ui/src", "anchor": "api/client.ts", "face": "开发面板"},
    {"dir": "ui-user/src", "anchor": "api/client.ts", "face": "用户端 ForgeSight"},
    {"dir": "ui-user-mimo/src", "anchor": "api/http.ts", "face": "用户端 ForgeSight 第二实现"},
)
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


def _call_paren(text: str, i: int) -> int:
    """`i` 指向调用名之后：跳过空白与泛型实参，返回落点下标（是调用点则停在 `'('`）。"""
    i += re.match(r"\s*", text[i:]).end()
    if i < len(text) and text[i] == "<":
        # 泛型实参：按 <> 计数跳过，字符串里的 < > 不算，`=>` 里的 > 不算。
        # 两棵用户端树写的是 `req<{ ok: boolean; user: User }>(…)` ⇒ 类型实参里就有 `;`，
        # 见到 `;` 就退出会把整条调用**静默丢掉**（丢了以后连"解析不出"都不报，是最坏的一种漏）。
        # 所以只在括号深度 0 处才把 `;` 当出口。
        depth = 0
        bdepth = 0
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
            elif text[i] in "{[":
                bdepth += 1
            elif text[i] in "}]":
                bdepth -= 1
            elif text[i] == "(" and bdepth == 0:
                break
            elif text[i] == ";" and bdepth == 0:
                break
            i += 1
        i += re.match(r"\s*", text[i:]).end()
    return i


def _init_verb(expr: str) -> tuple[str | None, str]:
    """path-first 形状：从 `RequestInit` 对象里取 `method`，取不到即 GET（fetch 的默认档）。

    只认**顶层**那个键——`{ body: JSON.stringify({ method: 'x' }) }` 里的 `method` 是载荷字段不是动词。
    值是变量或条件表达式 ⇒ 交回 unparsed（射程读不出要说清楚，默认成 GET 就是把没验过的当验过）。
    """
    s = expr.strip()
    if not s:
        return "GET", ""
    if not s.startswith("{"):
        return None, f"`req` 的第二个实参不是对象字面量（`{s[:40]}`）——动词读不出，不许按默认 GET 蒙"
    depth = 0
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in "\"'`":
            k = _skip_literal(s, i)
            if k is None:
                return None, "`req` 的 init 里有不闭合的引号"
            i = k
            continue
        k = _skip_comment(s, i)
        if k is not None:
            i = min(k, n)
            continue
        if c in "([{":
            depth += 1
            i += 1
            continue
        if c in ")]}":
            depth -= 1
            i += 1
            continue
        if depth != 1 or not METHOD_KEY.match(s, i):
            i += 1
            continue
        if i and (s[i - 1].isalnum() or s[i - 1] == "_"):
            i += 1  # `someMethod:` 这种键名尾巴
            continue
        j = i + len("method")
        j += re.match(r"\s*", s[j:]).end()
        if not s[j:].startswith(":"):
            i += 1
            continue
        j += 1
        j += re.match(r"\s*", s[j:]).end()
        if j >= n or s[j] not in "\"'`":
            return None, f"`method` 的值不是字面量（`{s[j:j + 24]}`）⇒ 动词读不出，不许按默认 GET 蒙"
        k = _skip_literal(s, j)
        if k is None:
            return None, "`method` 的字面量不闭合"
        verb = s[j + 1:k - 1].upper()
        if verb not in VERBS:
            return None, f"`method` 字面量不是认得的 HTTP 动词（`{verb}`）"
        return verb, ""
    return "GET", ""


def _call_sites(text: str, rel: str) -> tuple[list[dict], list[str]]:
    """返回 `(解析出的调用点, 解析不出的说明)`。调用点 = dict(method, paths, line, rel, exempt, kinds)。"""
    sites: list[dict] = []
    unparsed: list[str] = []
    lines = text.splitlines()

    def site(line: int, verb: str, paths: list[str], kinds: set[str]) -> None:
        sites.append({"method": verb, "paths": paths, "line": line, "rel": rel,
                      "exempt": _is_exempt(lines, line), "kinds": kinds})

    for m in HELPER_RE.finditer(text):
        shape = HELPERS[m.group(1)]
        head = text[:m.start()].rstrip()
        if head.endswith("function") or head.endswith("."):
            continue  # 定义本身 / 别人对象上的 .request
        i = _call_paren(text, m.end())
        if i >= len(text) or text[i] != "(":
            continue  # 不是调用点
        line = text[:m.start()].count("\n") + 1
        args = _split_args(text, i)
        if shape == "verb-first":
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
            path_expr = args[1]
        else:
            if args is None or not args:
                unparsed.append(f"{rel}:{line}: 实参没能切出来（引号/括号不闭合，或少于两个实参）")
                continue
            verb, why = _init_verb(args[1] if len(args) > 1 else "")
            if verb is None:
                unparsed.append(f"{rel}:{line}: {why}")
                continue
            path_expr = args[0]
        path_lits = _top_literals(path_expr)
        if path_lits is None:
            unparsed.append(f"{rel}:{line}: 路径表达式里有不闭合的引号/括号")
            continue
        if not path_lits:
            unparsed.append(f"{rel}:{line}: 路径不是字面量（`{path_expr.strip()[:40]}`）——"
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


def collect_ui_sites(ui_root: Path, tree: str = "") -> tuple[list[dict], list[str]]:
    sites: list[dict] = []
    unparsed: list[str] = []
    for path in sorted(list(ui_root.rglob("*.ts")) + list(ui_root.rglob("*.vue"))):
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        s, u = _call_sites(path.read_text(encoding="utf-8"), rel)
        for x in s:
            x["tree"] = tree
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


def _full(upath: str) -> str:
    """UI 侧路径 → 服务端绝对路径。

    三棵树的 BASE 脸不一样（实测）：`ui` 与 `ui-user` 的调用点写 BASE 之后的相对段
    （`/undo/…`、`/user/agents`），`ui-user-mimo` 把 `/api/…` 直接写全（它的 `API_BASE` 默认空串）。
    所以按**路径自己带不带 `/api`** 归一，而不是给每棵树配一个前缀常量——配了常量，改 BASE 的人
    不会同步改门。射程边界登记在此：若哪天某棵树的 BASE 改成 `/v1` 之类，这里仍补 `/api`
    ⇒ 报"这条路径不存在"（**假红会响**，不是漏绿），改的人当场看见。
    """
    if upath == API_PREFIX or upath.startswith(API_PREFIX + "/"):
        return upath
    return API_PREFIX + upath


def _matches(ui_path: str, route_path: str) -> bool:
    """段数对齐才算命中；`*` 认任意一段。

    两条方向的通配都放行是**故意的**：服务端 `/api/graphs/{name}` 在 HTTP 层确实接得住
    `/api/graphs/tags` 这种字面量段，前端写死一段路径不算"路由不存在"（那是 404 的反例，
    本门判的是路径可不可达，不是值对不对）。段的**数量**仍然要对齐，多一段少一段都红。
    """
    a, b = _shape(_full(ui_path)), _shape(route_path)
    if len(a) != len(b):
        return False
    return all(x == y or x == "*" or y == "*" for x, y in zip(a, b))


def _used_shapes(sites: list[dict]) -> set[tuple[str, ...]]:
    return {tuple(_shape(_full(p))) for s in sites for p in s["paths"]}


def _unused_routes(sites: list[dict], routes: list[dict]) -> list[dict]:
    used = _used_shapes(sites)
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
            api = _display(_full(upath))
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


def discover_ui_trees(repo: Path) -> list[str]:
    """盘上像第一方 UI 树、却不在 `UI_TREES` 登记表里的那些顶层目录名。

    判别只看形状（有 `package.json` 且有 `src/`），实测本仓恰好命中那三棵、不多不少。
    漏登记的后果不是"少看一棵树"这么轻：反向读数会把那棵树里的真消费者算成"UI 从未调"，
    下一批就有人照着这个数去删活接口。
    """
    registered = {t["dir"].split("/")[0] for t in UI_TREES}
    out: list[str] = []
    for d in sorted(repo.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in registered:
            continue
        if (d / "package.json").is_file() and (d / "src").is_dir():
            out.append(d.name)
    return out


def anchor_errors(trees: list[dict], sites: list[dict], routes: list[dict],
                  unparsed: list[str], unregistered: list[str] | None = None) -> list[str]:
    errs: list[str] = []
    for t in trees:
        if not t["root"].is_dir():
            errs.append(f"UI 树目录不存在：{t['root'].as_posix()}（{t['face']}）")
            continue
        if not t["anchor"].is_file():
            errs.append(f"锚点文件不在盘上：{t['anchor'].as_posix()}"
                        f"（{t['face']}改名 ⇒ 本门看不见这棵树的任何调用点，会当成干净）")
        if not any(s["tree"] == t["name"] for s in sites):
            errs.append(f"`{t['name']}`（{t['face']}）一个调用点都没解析出来（射程塌了，不是没有问题）")
    if not routes:
        errs.append("`src/` 下没扫到任何参与匹配的路由（装饰器写法变了 ⇒ 本门无从比对）")
    for u in unparsed:
        errs.append(u)
    for name in (unregistered or []):
        errs.append(f"盘上有第一方 UI 树 `{name}`（`package.json` + `src/`）不在 `UI_TREES` 登记表里"
                    f"⇒ 这棵树一条判据都不看，反向读数还会把它的真消费者算成『UI 未调用』"
                    f"⇒ 要么登记、要么就地删掉那棵树，不许让它以绿行的方式躲在射程外")
    return errs


def _registry_trees() -> list[dict]:
    """登记表落成待扫的树：`root` 是源码目录，`anchor` 是该树 API 层所在文件（抓整棵树改名/搬走）。"""
    return [{"name": t["dir"].split("/")[0], "face": t["face"], "root": REPO / t["dir"],
             "anchor": REPO / t["dir"] / t["anchor"]} for t in UI_TREES]


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    all_trees = bool(args) and args[0] == "--all"
    if all_trees:
        args = args[1:]
    ui_arg = Path(args[0]) if args else (REPO if all_trees else REPO / "ui" / "src")
    src_arg = Path(args[1]) if len(args) > 1 else REPO / "src"
    ui_root = (REPO / ui_arg) if not ui_arg.is_absolute() else ui_arg
    src_root = (REPO / src_arg) if not src_arg.is_absolute() else src_arg
    if not src_root.is_dir():
        print(f"✗ 服务端源码目录不存在：{src_root.as_posix()}")
        return 2

    trees = _registry_trees() if all_trees else [
        {"name": ui_root.name, "face": "命令行指定", "root": ui_root,
         "anchor": ui_root / "api" / "client.ts"}]
    for t in trees:
        if not t["root"].is_dir():
            print(f"✗ UI 源码目录不存在：{t['root'].as_posix()}")
            return 2

    sites: list[dict] = []
    unparsed: list[str] = []
    for t in trees:
        s, u = collect_ui_sites(t["root"], t["name"])
        sites.extend(s)
        unparsed.extend(u)
    routes, excluded, mount_errs, boundary = collect_routes(src_root)
    unregistered = discover_ui_trees(REPO) if all_trees else []
    errs = anchor_errors(trees, sites, routes, unparsed, unregistered)
    errs.extend(mount_errs)
    if errs:
        print(f"✗ UI↔路由契约门禁读不到锚点、有 {len(unparsed)} 个调用点解析不出，"
              f"或挂载表/树登记表读不出（这四类都是射程问题，不静默放行）：")
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
    per_tree = "、".join(f"{t['name']} {sum(1 for s in sites if s['tree'] == t['name'])}"
                         for t in trees)
    print(f"✓ UI↔路由契约门禁干净（UI 调用点 {totals['sites']} 处（{per_tree}）："
          f"字面量 {totals['plain']}、模板拼接 {totals['template']}、条件分支 {totals['branch']}；"
          f"服务端参与匹配路由 {totals['routes']} 条（装饰器 {totals['routes'] - totals['mounted']}、"
          f"`add_api_route` 挂载表 {totals['mounted']}）、被排除的兜底/MCP {totals['excluded']} 条；"
          f"运行期挂载文件 {totals['boundary']} 个（路径由插件声明，静态读不出，登记在册的射程边界）；"
          f"反向读数跨 {len(trees)} 棵树仍未被调用 {totals['unused']} 条（只计数不判红）；"
          f"现场豁免 {totals['exempted']} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

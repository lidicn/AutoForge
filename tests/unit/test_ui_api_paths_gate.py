"""UI↔路由契约门禁必须"能变红"（铁律 #8），红的是**下一条改错的路径**而不是已知那 50 条。

背景：`ui/` 没有 vitest，UI 侧判据只有 `vue-tsc` + `vite build` + 真浏览器读数，前两条只证能编译。
路径是手抄字符串，服务端改名/删路由/换方法，前端照编译照 build，只有真点一次才炸。本门钉三件事：
A 路径在路由表里、B 方法一致、C **每个调用点都必须进射程**（解析不出就 exit 2）。
另有射程面三件事（本批新增，因为它们最坏的表现恰恰是**绿行**）：调用点的**四张脸**
（`request('GET', …)` / `req(…)` / `req(…, { method: 'X' })` / 视图里直接 `fetch(`${base}/…`)`）都要读得出，
登记表上某棵树读不出任何调用点要红、盘上多出没登记的 UI 形状目录也要红。
整条路径都是变量的那一处是**传输层包装的定义本身**，登记成 `transport` 计数（不占调用点数、不判红），
由 `test_transport_wrappers_are_pinned_to_the_api_layer` 逐文件名钉住。

C 是这条门自己的前提：早期正则版把嵌套反引号
`` `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}` `` 静默丢掉，
于是报"缺失 0"——那个 0 里混着"根本没看过这一条"。同理 SPA 兜底 `GET /{full_path:path}` 与
`POST /mcp` 必须排除在匹配集外，否则任何拼错的路径都被兜底接住，门永远绿。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_ui_api_paths.py"

CLIENT_HEAD = '''
async function request<T>(method: string, path: string, body?: unknown): Promise<{ data: T }> {
  const res = await fetch(`${API_BASE}${path}`, { method, body })
  return { data: await res.json() }
}

export const api = {
  health: () => request<HealthResponse>('GET', '/health'),
  graph: (name: string, version?: string) =>
    request<GraphResponse>('GET', `/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}`),
  diff: (name: string, old: string, new_v: string) =>
    request<DiffResponse>('GET', `/diff?name=${encodeURIComponent(name)}&old=${old}&new=${new_v}`),
  toggle: (enable: boolean, tag: string) =>
    request<EnableResponse>('POST', enable ? '/graphs/enable' : '/graphs/disable', { tag }),
  undoList: () => request<UndoAvailableResponse>('GET', '/undo/available'),
  undoPreview: (deploy_id: string) => request<UndoPreviewResponse>('GET', `/undo/${encodeURIComponent(deploy_id)}`),
  undoRun: (deploy_id: string) => request<UndoRunResponse>('POST', `/undo/${encodeURIComponent(deploy_id)}`, { confirm: true }),
  entity: (id: string) => request<EntityStateResponse>('GET', `/entities/${encodeURIComponent(id)}/state`),
}
'''

API_HEAD = '''
def build_app():
    @app.get("/api/health")
    def health():
        return {}

    @app.get("/api/graphs/{name}")
    def graph(name):
        return {}

    @app.post("/api/graphs/enable")
    def graphs_enable():
        return {}

    @app.post("/api/graphs/disable")
    def graphs_disable():
        return {}

    @app.get("/api/diff")
    def diff():
        return {}

    @app.get("/api/undo/available")
    def undo_available():
        return {}

    @app.get("/api/undo/{deploy_id}")
    def undo_preview(deploy_id):
        return {}

    @app.post("/api/undo/{deploy_id}")
    def undo_run(deploy_id):
        return {}

    @app.get("/api/entities/{entity_id}/state")
    def entity_state(entity_id):
        return {}

    @app.get("/{full_path:path}")
    def spa(full_path):
        return {}

    @app.post("/mcp")
    def mcp():
        return {}
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_ui_api_paths", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _roots(tmp_path: pathlib.Path, client: str, api: str) -> tuple[pathlib.Path, pathlib.Path]:
    ui = tmp_path / "ui" / "src"
    (ui / "api").mkdir(parents=True, exist_ok=True)
    (ui / "api" / "client.ts").write_text(client, encoding="utf-8")
    src = tmp_path / "src" / "autoforge"
    src.mkdir(parents=True, exist_ok=True)
    (src / "af_api.py").write_text(api, encoding="utf-8")
    return ui, src


def _scan(tmp_path: pathlib.Path, client: str, api: str, mount: str | None = None):
    mod = _module()
    ui, src = _roots(tmp_path, client, api)
    if mount is not None:
        _write_mount(src, mount)
    sites, unparsed = mod.collect_ui_sites(ui)
    routes, excluded, mount_errs, boundary = mod.collect_routes(src)
    findings = mod.check(sites, routes)
    totals = mod._counts(sites, routes, excluded, mod._unused_routes(sites, routes),
                         unparsed, boundary)
    totals["mount_errs"] = len(mount_errs)
    return findings, totals


def _write_mount(src: pathlib.Path, text: str) -> None:
    (src / "af_conflict_runtime.py").write_text(text, encoding="utf-8")


def _main(tmp_path: pathlib.Path, client: str, api: str, mount: str | None = None) -> int:
    mod = _module()
    ui, src = _roots(tmp_path, client, api)
    if mount is not None:
        _write_mount(src, mount)
    return mod.main(["check_ui_api_paths.py", str(ui), str(src)])


# ── 已知好形状：绿，而且真的数过 ────────────────────────────────────

def test_known_good_shape_is_green(tmp_path):
    assert _main(tmp_path, CLIENT_HEAD, API_HEAD) == 0


def test_known_good_shape_is_actually_counted(tmp_path):
    """绿色行得有数：调用点若压根没进射程，"干净"就毫无意义。"""
    findings, totals = _scan(tmp_path, CLIENT_HEAD, API_HEAD)
    assert findings == []
    assert totals["unparsed"] == 0
    assert totals["sites"] == 8
    assert (totals["plain"], totals["template"], totals["branch"]) == (2, 5, 1)
    assert totals["transport"] == 1                    # `fetch(`${API_BASE}${path}`)` 读到了，但没占调用点数
    assert totals["routes"] == 9 and totals["excluded"] == 2
    assert totals["branch"] == 1


# ── 判据 A：路径不存在要红 ───────────────────────────────────────────

def test_bogus_path_is_red_despite_the_spa_catchall(tmp_path):
    """兜底 `GET /{full_path:path}` 在路由表里，但**不参与匹配**——否则任何错路径都绿。"""
    assert '/{full_path:path}' in API_HEAD
    findings, _ = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  nope: () => request<NopeResponse>('GET', '/nope-at-all'),
}
''', API_HEAD)
    assert len(findings) == 1
    assert "/api/nope-at-all" in findings[0]
    assert "client.ts" in findings[0]
    assert "SPA 兜底" in findings[0]


def test_segment_count_must_line_up(tmp_path):
    """`${…}` 只吃一段：`/undo/${a}/b` 不许当成命中 `/api/undo/{deploy_id}`。"""
    findings, _ = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  deep: (a: string) => request<NopeResponse>('GET', `/undo/${a}/b`),
}
''', API_HEAD)
    assert len(findings) == 1
    assert "/api/undo/" in findings[0]


def test_relative_path_without_leading_slash_is_red(tmp_path):
    findings, _ = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  odd: () => request<NopeResponse>('GET', 'health'),
}
''', API_HEAD)
    assert len(findings) == 1
    assert "不以 `/` 开头" in findings[0]


def test_mcp_surface_is_out_of_the_match_set(tmp_path):
    """`POST /mcp` 被排除：前端要调它得当场写豁免，不能让 MCP 面悄悄成为兜底。"""
    findings, _ = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  call: () => request<McpResponse>('POST', '/mcp', { tool }),
}
''', API_HEAD)
    assert len(findings) == 1
    assert "POST /mcp" in findings[0]


# ── 判据 B：方法不符要红 ─────────────────────────────────────────────

def test_method_mismatch_is_red(tmp_path):
    """路径对、方法抄错也是故障：405 与 404 两码事，只比路径会漏一半。"""
    findings, _ = _scan(tmp_path, CLIENT_HEAD.replace(
        "undoPreview: (deploy_id: string) => request<UndoPreviewResponse>('GET'",
        "undoPreview: (deploy_id: string) => request<UndoPreviewResponse>('DELETE'"), API_HEAD)
    assert len(findings) == 1
    assert "405" in findings[0]
    assert "/api/undo/" in findings[0]


# ── 判据 C：射程自证（解析不出就 exit 2，不许当"没调用"） ────────────

def test_non_literal_path_is_registered_as_transport(tmp_path):
    """`request('GET', p)`：整条路径都是变量 ⇒ 静态没有路径可判，登记成 `transport` 而不是判红。

    这一档本批从 exit 2 改成登记，起因是第四张脸把传输层包装的**定义本身**
    （`fetch(BASE + path)`）暴露成了调用点；照旧判红等于要求三棵树的 api 层改写成静态可读形状，
    而那正是它们不该改的东西。代价要说清：动态路径不再自动响 ⇒ 兜底换成
    `test_transport_wrappers_are_pinned_to_the_api_layer`（真仓那 3 处逐文件名钉住，多一处红在测试里）。
    """
    findings, totals = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  dynamic: (p: string) => request<Anything>('GET', p),
}
''', API_HEAD)
    assert findings == []
    assert totals["unparsed"] == 0
    assert (totals["sites"], totals["transport"]) == (8, 2)
    assert totals["plain"] + totals["template"] + totals["branch"] == totals["sites"]


def test_transport_wrapper_does_not_claim_any_route(tmp_path):
    """`transport` 不贡献路径 ⇒ 它救不了反向读数里的任何一条路由。

    反例控制（铁律 #8）：如果把"这里读不出具体路径"当成"这里有人调"，活接口数会虚高、
    死接口数会虚低，下一批就有人照着虚低的数去删。这条用「只有包装、没有消费者」的树把它钉住。
    """
    mod = _module()
    ui, src = _roots(tmp_path, '''
async function go<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  return (await res.json()) as T
}
''', API_HEAD)
    sites, unparsed = mod.collect_ui_sites(ui)
    routes, _exc, _me, _b = mod.collect_routes(src)
    assert unparsed == [] and len(sites) == 1 and sites[0]["paths"] == []
    assert len(mod._unused_routes(sites, routes)) == len(routes)


def test_unbalanced_quote_exits_2(tmp_path):
    """引号不闭合 ⇒ 整条调用点读不出 ⇒ exit 2（"没发现"不能来自"没读到"）。"""
    assert _main(tmp_path, CLIENT_HEAD + '''
export const extra = {
  broken: () => request<Anything>('GET', '/undo/available),
}
''', API_HEAD) == 2


def test_unknown_verb_exits_2(tmp_path):
    assert _main(tmp_path, CLIENT_HEAD + '''
export const extra = {
  odd: () => request<Anything>('FETCH', '/health'),
}
''', API_HEAD) == 2


def test_missing_client_anchor_exits_2(tmp_path):
    mod = _module()
    src = tmp_path / "src"
    (src / "autoforge").mkdir(parents=True)
    (src / "autoforge" / "af_api.py").write_text(API_HEAD, encoding="utf-8")
    assert mod.main(["x", str(tmp_path / "ui" / "src"), str(src)]) == 2


def test_empty_route_table_exits_2(tmp_path):
    """路由面只剩兜底和 MCP ⇒ 匹配集空，此时"零发现"是射程塌了。"""
    only_fallback = '\n'.join(
        line for line in API_HEAD.splitlines()
        if "/api/" not in line and "def " not in line and "return" not in line)
    assert _main(tmp_path, CLIENT_HEAD, only_fallback) == 2


def test_nested_backtick_and_both_branches_stay_in_scope(tmp_path):
    """路由表里 `/api/graphs/*` 三条一起删掉 ⇒ 模板拼接那一处 + 条件分支两处都得红。

    这就是正则版当年谎报"缺失 0"的位置：嵌套反引号那条被静默丢掉、条件分支那条整条被丢掉，
    于是"扫不到"被当成"没有"。现在三条都在射程里。
    """
    pruned = API_HEAD
    for block in ('''    @app.get("/api/graphs/{name}")
    def graph(name):
        return {}

''', '''    @app.post("/api/graphs/enable")
    def graphs_enable():
        return {}

''', '''    @app.post("/api/graphs/disable")
    def graphs_disable():
        return {}

'''):
        assert block in pruned
        pruned = pruned.replace(block, "")
    findings, _ = _scan(tmp_path, CLIENT_HEAD, pruned)
    assert len(findings) == 3
    joined = "\n".join(findings)
    assert "/api/graphs/*" in joined          # 模板拼接：`${…}` 显示成 *
    assert "/api/graphs/enable" in joined
    assert "/api/graphs/disable" in joined


def test_literal_segment_under_a_param_route_is_not_a_finding(tmp_path):
    """`/api/graphs/{name}` 在 HTTP 层确实接得住 `/graphs/tags`：路径**可达**，不算错。

    本门判"这条路径会不会 404"，不判"这个值服务端认不认"（后者是 4xx 语义，静态读不出来）。
    """
    findings, _ = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  tags: () => request<TagsResponse>('GET', '/graphs/tags'),
}
''', API_HEAD)
    assert findings == []


# ── 归一化：query 只在替换外切、`${…}` 折成一段通配 ──────────────────

def test_query_is_cut_outside_substitutions():
    mod = _module()
    got = mod._normalize("/graphs/${encodeURIComponent(name)}"
                         "${version ? `?version=${version}` : ''}")
    assert got == "/graphs/\x01"          # 模板尾是 query，折叠成一段通配
    assert mod._normalize("/diff?name=${encodeURIComponent(n)}&old=${o}") == "/diff"
    assert mod._normalize("/undo/${encodeURIComponent(deploy_id)}") == "/undo/\x01"
    assert mod._normalize("/entities/${id}/state") == "/entities/\x01/state"


# ── 豁免：要理由，且单独计入读数 ─────────────────────────────────────

def test_exemption_without_reason_stays_red(tmp_path):
    findings, totals = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  nope: () => request<NopeResponse>('GET', '/nope-at-all'),  # ui-api: exempt()
}
''', API_HEAD)
    assert len(findings) == 1
    assert totals["exempted"] == 0


def test_exemption_with_reason_turns_green_and_is_counted(tmp_path):
    findings, totals = _scan(tmp_path, CLIENT_HEAD + '''
export const extra = {
  // ui-api: exempt(对端代理的健康检查走外链，不经 AF 路由表)
  nope: () => request<NopeResponse>('GET', '/nope-at-all'),
}
''', API_HEAD)
    assert findings == []
    assert totals["exempted"] == 1
    assert totals["sites"] == 9


# ── 第二张脸：`("GET", "/api/…", handler)` 表 + `add_api_route` 挂载 ──

CONFLICT_CLIENT = '''
export const api = {
  conflicts: () => request<ConflictsResponse>('GET', '/conflicts'),
  reset: (id: string) => request<ResetResponse>('POST', `/conflicts/${encodeURIComponent(id)}/reset`),
}
'''

MOUNT_TABLE = '''
_ROUTES: tuple = (
    ("GET", "/api/conflicts", api_conflicts),
    ("POST", "/api/conflicts/{automation_id}/reset", api_conflicts_reset),
)


def install_api(app, service) -> None:
    for method, path, handler in _ROUTES:
        app.add_api_route(path, handler, methods=[method])
'''

# 有 add_api_route，但路径全部来自插件声明的运行期数据、一个 `/api/` 字面量都没有。
RUNTIME_ONLY_MOUNT = '''
def install_plugins(app, manifest) -> None:
    for entry in manifest:
        for method, path in entry["routes"]:
            app.add_api_route(path, _make_endpoint(entry), methods=[method])
'''

# 有 add_api_route，也有 `/api/` 字面量，却不落在 `("VERB", "/api/…", handler)` 的表形状上：
# 这就是"表改了形"——按表读会读出 0 条，本门随即对真端点报假红。
BROKEN_TABLE = '''
_PATHS = ["/api/conflicts", "/api/conflicts/summary"]


def install_api(app) -> None:
    for p in _PATHS:
        app.add_api_route(p, handler, methods=["GET"])
'''


def test_mounted_table_routes_keep_the_ui_paths_in_scope(tmp_path):
    """只读装饰器时这 2 个端点会被报"路由表里没有这条"——假红，且修法方向是错的。"""
    assert _main(tmp_path, CONFLICT_CLIENT, API_HEAD, MOUNT_TABLE) == 0


def test_without_the_mount_face_those_same_paths_go_red(tmp_path):
    """反空洞：同一份前端代码，把挂载表面拿掉就必须红——证明上一档不是白给的绿。"""
    findings, totals = _scan(tmp_path, CONFLICT_CLIENT, API_HEAD)
    assert len(findings) == 2
    assert totals["mounted"] == 0


def test_mounted_routes_are_counted_separately_from_decorators(tmp_path):
    findings, totals = _scan(tmp_path, CONFLICT_CLIENT, API_HEAD, MOUNT_TABLE)
    assert findings == []
    assert (totals["routes"], totals["mounted"], totals["boundary"]) == (11, 2, 0)


def test_runtime_only_mount_is_a_registered_boundary_not_an_error(tmp_path):
    """插件在运行期给路径 ⇒ 静态读不出，这是登记在册的射程边界，不是门红。"""
    findings, totals = _scan(tmp_path, CLIENT_HEAD, API_HEAD, RUNTIME_ONLY_MOUNT)
    assert findings == []
    assert totals["boundary"] == 1 and totals["mount_errs"] == 0
    assert _main(tmp_path, CLIENT_HEAD, API_HEAD, RUNTIME_ONLY_MOUNT) == 0


def test_changed_mount_table_shape_exits_2(tmp_path):
    """表形状一变，本门对那批真端点就会瞎报假红 ⇒ 判射程塌，不许静默放行。"""
    _, totals = _scan(tmp_path, CLIENT_HEAD, API_HEAD, BROKEN_TABLE)
    assert totals["mount_errs"] == 1
    assert _main(tmp_path, CLIENT_HEAD, API_HEAD, BROKEN_TABLE) == 2


# ── 真实仓库读数：门绿，且射程与 `@app.` 装饰器数对得上 ──────────────

def test_real_ui_and_src_are_clean_and_counted():
    """钉死真实计数（同 计数棘轮）：新增/删除调用点或路由都要动这两个数——
    改跨层契约就得在测试里留一次名，这是故意的摩擦，不是脆弱。

    85 → 87 / 88 → 90（20261009 收，红在本批之前的 HEAD 上）：`e5b3fd5`（登录正规化）加了
    `GET /api/auth/has-admin` 与 `POST /api/auth/register` 两条装饰器路由，却没动这里的锚点。
    两条都有真调用方（`ui-user-mimo/src/views/LoginView.vue` 里直接 `fetch`，即第四张调用脸），
    所以 `check(sites, routes)` 本来就判不出红——红的是计数这一格：跨层契约变了要留名。
    同一批把 `ui-user-mimo` 的调用点从 20 抬到 22（就是这两条 fetch）。
    """
    mod = _module()
    sites, unparsed = mod.collect_ui_sites(ROOT / "ui" / "src")
    routes, excluded, mount_errs, boundary = mod.collect_routes(ROOT / "src")
    assert unparsed == []
    assert mount_errs == []
    assert mod.check(sites, routes) == []
    assert len(sites) == 54 and len(excluded) == 2
    live = [s for s in sites if "transport" not in s["kinds"]]
    assert len(live) == 53 and len(sites) - len(live) == 1   # +1 = `api/client.ts` 的 `fetch` 包装
    mounted = sum(1 for r in routes if r["how"] == "add_api_route")
    assert mounted == 5                              # af_conflict_runtime._ROUTES
    assert len(routes) - mounted + len(excluded) == 87   # == grep -Ec "@app\.(get|post|put|patch|delete)" src/autoforge/af_api.py（现读 87 对 87；旧锚点 `grep -c "@app."` 把 @app.exception_handler 那行也数进来，HEAD 上就偏 1）
    assert len(routes) == 90                          # 85 条参与匹配的装饰器路由 + 5 挂载表（af_api 的装饰器行共 87＝85＋被排除的 /mcp 与 /{full_path:path}）
    assert boundary == ["src/autoforge/af_runtime_plugins.py"]
    assert sum(1 for s in sites if s["exempt"]) == 0


def test_green_line_prints_measured_numbers_not_adjectives(tmp_path, capsys):
    assert _main(tmp_path, CLIENT_HEAD, API_HEAD) == 0
    out = capsys.readouterr().out
    _, totals = _scan(tmp_path, CLIENT_HEAD, API_HEAD)
    assert f"{totals['sites']} 处" in out and f"{totals['routes']} 条" in out
    for adjective in ("全部", "只在", "都在", "没有"):
        assert adjective not in out


# ── 第二、三张调用脸：path-first 的 `req(path, init)`（两棵用户端树用的就是它）──

USER_CLIENT_HEAD = '''
async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, { ...init, headers })
  return (await res.json()) as T
}

export const api = {
  agents: () => req<{ ok: boolean }>('/user/agents'),
  drop: (id: string) => req(`/user/agents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  rename: (id: string, name: string) =>
    req(`/user/agents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  bodyHasMethodWord: () => req('/user/agents', { body: JSON.stringify({ method: 'PUT' }) }),
  writeFull: () => req<{ ok: boolean; user: User }>('/api/auth/login', { method: 'POST' }),
}
'''

USER_API_HEAD = '''
def build_app():
    @app.get("/api/user/agents")
    def agents():
        return {}

    @app.patch("/api/user/agents/{agent_id}")
    def rename(agent_id):
        return {}

    @app.delete("/api/user/agents/{agent_id}")
    def drop(agent_id):
        return {}

    @app.post("/api/auth/login")
    def login():
        return {}
'''


def _sites(tmp_path: pathlib.Path, client: str, api: str):
    mod = _module()
    ui, src = _roots(tmp_path, client, api)
    sites, unparsed = mod.collect_ui_sites(ui)
    return mod, sites, unparsed, src


def test_path_first_shape_is_green(tmp_path):
    """路径在前、动词在 `RequestInit` 里——只认 `request('GET', …)` 会把整棵树读成 0 个调用点。"""
    assert _main(tmp_path, USER_CLIENT_HEAD, USER_API_HEAD) == 0


def test_path_first_shape_parses_five_live_sites_plus_one_wrapper(tmp_path):
    """五个 `req(…)` 调用点 + 包装定义里那一处 `fetch(BASE + path)`：后者登记成 transport，不占调用点数。"""
    mod, sites, unparsed, _ = _sites(tmp_path, USER_CLIENT_HEAD, USER_API_HEAD)
    assert unparsed == []
    assert len(sites) == 6
    live = [s for s in sites if "transport" not in s["kinds"]]
    assert len(live) == 5 and len(sites) - len(live) == 1


def test_omitted_init_defaults_to_get_because_fetch_does(tmp_path):
    mod, sites, _, _ = _sites(tmp_path, USER_CLIENT_HEAD, USER_API_HEAD)
    assert [s["method"] for s in sites if s["paths"] == ["/user/agents"]] == ["GET", "GET"]


def test_nested_method_key_in_body_is_not_the_verb(tmp_path):
    """`{ body: JSON.stringify({ method: 'PUT' }) }` 里的 `method` 是载荷字段：认成动词就成 PUT，
    而服务端只有 GET ⇒ 报 405 假红。只有**顶层**那个键算方法。"""
    mod, sites, _, src = _sites(tmp_path, USER_CLIENT_HEAD, USER_API_HEAD)
    routes, _exc, _me, _b = mod.collect_routes(src)
    nested = [s for s in sites if s["line"] == USER_CLIENT_HEAD.splitlines().index(
        "  bodyHasMethodWord: () => req('/user/agents', { body: JSON.stringify({ method: 'PUT' }) }),") + 1]
    assert [(s["method"], s["paths"]) for s in nested] == [("GET", ["/user/agents"])]
    assert mod.check(sites, routes) == []


def test_path_already_carrying_api_prefix_is_not_prefixed_twice(tmp_path):
    """`ui-user-mimo` 的 BASE 默认空串、路径写全 `/api/…`：再补一次前缀会变成 `/api/api/…` ⇒ 假红。"""
    mod = _module()
    assert mod._full("/api/auth/login") == "/api/auth/login"
    assert mod._full("/user/agents") == "/api/user/agents"
    assert mod._full("/api") == "/api"


def test_generic_type_argument_with_semicolon_still_enters_scope(tmp_path):
    """`req<{ ok: boolean; user: User }>(…)`：泛型里就有 `;`，见到 `;` 就退出会把整条调用
    **静默丢掉**（连"解析不出"都不报）。这条只钉那一条：丢了的特征是它压根不在调用点清单里。"""
    mod, sites, _, _ = _sites(tmp_path, USER_CLIENT_HEAD, USER_API_HEAD)
    assert [s for s in sites if s["paths"] == ["/api/auth/login"]], \
        "带 `;` 的泛型实参被静默跳过 ⇒ 射程漏了，而门会照印'干净'"


def test_variable_init_is_a_scope_problem_not_a_get(tmp_path):
    """`req(path, init)`：动词真读不出。当成 GET 过关＝把没验过的当已验证 ⇒ exit 2。"""
    client = USER_CLIENT_HEAD.replace(
        "  drop: (id: string) => req(`/user/agents/${encodeURIComponent(id)}`, { method: 'DELETE' }),",
        "  drop: (id: string, init: RequestInit) => req(`/user/agents/${encodeURIComponent(id)}`, init),")
    assert _main(tmp_path, client, USER_API_HEAD) == 2


def test_conditional_method_value_is_a_scope_problem(tmp_path):
    client = USER_CLIENT_HEAD.replace(
        "{ method: 'PATCH', body: JSON.stringify({ name }) }",
        "{ method: hard ? 'PATCH' : 'PUT', body: JSON.stringify({ name }) }")
    assert _main(tmp_path, client, USER_API_HEAD) == 2


# ── 射程面：树登记表 ─────────────────────────────────────────────


def test_discover_names_unregistered_ui_shaped_dirs(tmp_path):
    mod = _module()
    for name, files in (("ui-x", {"package.json": "{}", "src/a.ts": ""}),
                        ("ui-y", {"package.json": "{}"}),
                        ("ui", {"package.json": "{}", "src/a.ts": ""}),
                        ("docker", {"src/a.ts": ""})):
        for rel, body in files.items():
            p = tmp_path / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    assert mod.discover_ui_trees(tmp_path) == ["ui-x"]   # 已登记的 ui 不算；缺 src/ 或缺 package.json 不算


def test_registered_tree_yielding_no_site_is_scope_red(tmp_path):
    """登记表里那棵树整个改名 ⇒ "0 个调用点"必须以红收场，不能算"这棵树没问题"。"""
    mod = _module()
    root = tmp_path / "ui-user" / "src"
    (root / "api").mkdir(parents=True)
    anchor = root / "api" / "client.ts"
    anchor.write_text("export const api = 1\n", encoding="utf-8")
    trees = [{"name": "ui-user", "face": "用户端 ForgeSight", "root": root, "anchor": anchor}]
    errs = mod.anchor_errors(trees, [], [{"path": "/api/x"}], [])
    assert any("ui-user" in e and "一个调用点" in e for e in errs), errs


def test_moved_anchor_file_is_named_per_tree(tmp_path):
    """两棵用户端树的 API 层文件名不同（`client.ts` / `http.ts`）⇒ 报错得说清是哪棵树塌了。"""
    mod = _module()
    root = tmp_path / "ui-user-mimo" / "src"
    root.mkdir(parents=True)
    trees = [{"name": "ui-user-mimo", "face": "用户端 ForgeSight 第二实现", "root": root,
              "anchor": root / "api" / "http.ts"}]
    errs = mod.anchor_errors(trees, [{"tree": "other"}], [{"path": "/api/x"}], [])
    assert any("锚点文件不在盘上" in e and "第二实现" in e for e in errs), errs


def test_unregistered_tree_goes_red_not_green(tmp_path):
    mod = _module()
    root = tmp_path / "ui" / "src"
    (root / "api").mkdir(parents=True)
    (root / "api" / "client.ts").write_text("export const api = 1\n", encoding="utf-8")
    trees = [{"name": "ui", "face": "开发面板", "root": root, "anchor": root / "api" / "client.ts"}]
    errs = mod.anchor_errors(trees, [{"tree": "ui"}], [{"path": "/api/x"}], [], ["ui-new"])
    assert any("ui-new" in e and "UI_TREES" in e for e in errs), errs


def test_all_trees_of_this_repo_are_in_scope_and_green(capsys):
    """真仓 `--all` 的整条读数：三棵树各自的调用点数、跨树反向读数——这条就是本批的第一手读数。"""
    mod = _module()
    assert mod.main(["check_ui_api_paths.py", "--all"]) == 0
    out = capsys.readouterr().out
    for name in ("ui 53", "ui-user 19", "ui-user-mimo 22"):
        assert name in out, out
    assert "传输层包装 3 处" in out, out
    assert "SSE 建流 2 处" in out, out
    assert "跨 3 棵树仍未被调用 15 条" in out, out


# ── 第四张调用脸：视图里直接 `fetch(`${base}/watch/start`)`（前缀是变量）──────

VIEW_CLIENT = '''
export function startAutomation(id: string) {
  const base = import.meta.env.VITE_API_BASE ?? 'http://localhost:8787/api'
  return fetch(`${base}/watch/start`, { method: 'POST', body: JSON.stringify({ automation_id: id }) })
}

export function stopIt(owner: string) {
  const base = import.meta.env.VITE_API_BASE ?? '/api'
  return fetch(`${base}/watch/stop?owner=${encodeURIComponent(owner)}`, { method: 'POST' })
}
'''

VIEW_API = '''
def build_app():
    @app.post("/api/watch/start")
    def watch_start():
        return {}

    @app.post("/api/watch/stop")
    def watch_stop():
        return {}
'''


def test_fetch_with_variable_base_enters_scope(tmp_path):
    """补这张脸的理由是实测的：`/api/watch/start|stop` 两条**有真前端消费者**的接口
    躺在反向读数里（16 条 → 14 条），照那个数删接口等于删活的。"""
    findings, totals = _scan(tmp_path, VIEW_CLIENT, VIEW_API)
    assert findings == []
    assert (totals["sites"], totals["plain"], totals["template"], totals["transport"]) == (2, 0, 2, 0)


def test_variable_prefix_site_is_read_by_its_literal_tail(tmp_path):
    """`${base}/watch/stop?query=${…}` 归一后只剩 `*/watch/stop` 三段尾巴：
    query 在替换外切掉、变量前缀不参与比对，所以段数不会多出一段把这条读成"路径不存在"（假红）。"""
    mod, sites, unparsed, _ = _sites(tmp_path, VIEW_CLIENT, VIEW_API)
    assert unparsed == []
    assert sorted(mod._display(s["paths"][0]) for s in sites) == ["*/watch/start", "*/watch/stop"]


def test_variable_prefix_site_goes_red_when_the_route_is_renamed(tmp_path):
    """这张脸得有牙：只数不红等于把"读不出整条路径"换成"报成有人调"。服务端改名 ⇒ 必须红。"""
    findings, _ = _scan(tmp_path, VIEW_CLIENT, VIEW_API.replace('"/api/watch/start"', '"/api/watch/begin"'))
    assert len(findings) == 1
    assert "*/watch/start" in findings[0] and "POST" in findings[0]


def test_variable_prefix_site_checks_the_verb_too(tmp_path):
    """路径对、方法不对也红（判据 B 对第四张脸同样生效）：405 与 404 是两种故障。"""
    findings, _ = _scan(tmp_path, VIEW_CLIENT,
                       VIEW_API.replace('@app.post("/api/watch/stop")', '@app.get("/api/watch/stop")'))
    assert len(findings) == 1
    assert "405" in findings[0]


def test_literal_segment_reaches_a_param_route_but_does_not_claim_it(tmp_path):
    """可达（判 404，松）与认领（判"谁还没被调"，紧）必须分两档，这是本批盘出来的真区别。

    `GET /asks/pending` 在 HTTP 层确实被 `GET /api/asks/{name}` 接住 ⇒ `check()` 不能报它不存在（假红）；
    但它不是那条参数路由的消费者 ⇒ 反向读数里它得继续躺着。合成档里两半各钉一次：
    合在一条判据上必然一头错——上一版用 `_hit` 算认领时，`/api/asks/{name}` 就是被
    `ui/src/api/client.ts:155` 的 `/asks/pending` 冒领的（实测：反向读数少一条，而那条零消费者）。
    """
    mod = _module()
    ui, src = _roots(tmp_path, '''
export const api = {
  asks: () => req<AsksResponse>('/asks/pending'),
}
''', '''
def build_app():
    @app.get("/api/asks/pending")
    def asks_pending():
        return {}

    @app.get("/api/asks/{name}")
    def asks_one(name):
        return {}
''')
    sites, unparsed = mod.collect_ui_sites(ui)
    routes, *_ = mod.collect_routes(src)
    assert unparsed == []
    assert mod.check(sites, routes) == []                     # 可达：不报假红
    assert mod._hit("/asks/pending", "/api/asks/{name}")      # 松的那档确实放行
    got = [r["path"] for r in mod._unused_routes(sites, routes)]
    assert got == ["/api/asks/{name}"]                        # 没被认领：仍在未调用名单里
    assert not mod._claimed("/asks/pending", "/api/asks/{name}")


def test_transport_wrappers_are_pinned_to_the_api_layer():
    """真仓只有三处"整条路径是变量"的传输层包装，且都在各棵树的 api 门面文件里。

    这条是 `test_non_literal_path_is_registered_as_transport` 放宽判红之后的兜底：视图里再冒出一处
    动态路径（那才是真该追问"调的是哪条接口"的地方）会红在这里，逼来留名而不是静默过关。
    """
    mod = _module()
    got: list[str] = []
    for t in mod._registry_trees():
        sites, _ = mod.collect_ui_sites(t["root"], t["name"])
        got += [f"{s['rel']}:{s['line']}" for s in sites if "transport" in s["kinds"]]
    assert sorted(got) == ["ui-user-mimo/src/api/http.ts:92", "ui-user/src/api/client.ts:36",
                           "ui/src/api/client.ts:28"], got


def test_helper_names_have_one_source():
    """`HELPER_RE` 从 `HELPERS` 的键生成：抄两份的错法是"脸加在字典里、没加在正则上"
    ⇒ 那张脸静默不在射程，而门照印"干净"（§二之三十二 同族）。"""
    mod = _module()
    alt = mod.HELPER_RE.pattern.partition("(")[2].rpartition(")")[0]
    assert sorted(alt.split("|")) == sorted(mod.HELPERS), mod.HELPER_RE.pattern
    assert mod.HELPERS["fetch"] == "path-first"


def test_reverse_reading_of_this_repo_is_a_named_list(capsys):
    """`--list-uncalled` 逐条打到盘上：一个总数定不了"还剩谁在用"这件事，只能逐条定性。

    15 这个数是棘轮——删一条活接口、或给某条补上前端调用点，都要在这里留名。
    """
    mod = _module()
    assert mod.main(["check_ui_api_paths.py", "--list-uncalled"]) == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("—")]
    assert len(lines) == 15, out
    assert "/api/watch/start" not in out and "/api/watch/stop" not in out   # 已被 fetch 那张脸认领
    assert "pair-request" not in out                                       # 已被 SSE 那张脸认领
    assert "反向读数 15 条" in out, out
    # 整个集合逐条钉住（不是只钉总数）：这 15 条的定性写在执行记录 §二之四十四，
    # 谁给某条补上前端调用点、或删掉某条路由，都必须同时动这张表和这段名单——
    # 只数数不记名的话，下一批又会把"门读不出"当成"接口没人用"。
    assert {ln.split()[0] + " " + ln.split()[1] for ln in lines} == {
        "GET /api/asks/{name}",                                             # 单条咨询详情：无 UI 消费者（`/asks`、`/asks/pending` 才有）
        "GET /api/conflicts",                                               # 冲突仲裁面 5 条：全 NAS 零消费者，面板未接
        "GET /api/conflicts/summary",
        "GET /api/conflicts/locks",
        "DELETE /api/conflicts/locks/{entity_id}",
        "POST /api/conflicts/{automation_id}/reset",
        "GET /api/experience/export",                                       # 经验导出：消费走 CLI `forge experience export`（同函数，不经 HTTP）
        "POST /api/mcp/pair/redeem",                                        # 配对 bootstrap 两条：消费面是 agent 自己的 HTTP 客户端（裁定 20261006 §一=B），第一方 UI 天生不调它——读不出≠没人用
        "POST /api/mcp/pair/request",
        "GET /api/sessions",                                                # 会话管理面 6 条：只有 `POST .../answer` 被 ui 调
        "POST /api/sessions",
        "GET /api/sessions/{session_id}",
        "DELETE /api/sessions/{session_id}",
        "POST /api/sessions/{session_id}/cancel",
        "POST /api/sessions/{session_id}/tick",
    }, out


# ── 第五张调用脸：`new EventSource(url)`（SSE 只能走 query，URL 先拼进变量）──

PAIR_ROUTE = '''
def build_app():
    @app.get("/api/mcp/pair-request")
    async def pair_request():
        return {}
'''

# 两棵用户端树各自的真实形状（抄自 `ui-user/src/api/client.ts:81-82`、`ui-user-mimo/src/api/http.ts:41-42`）
SSE_CLIENT = '''
export function openPairStream(token: string) {
  const BASE = import.meta.env.VITE_API_BASE || '/api'
  const t = token
  const url = BASE + '/mcp/pair-request' + (t ? `?token=${encodeURIComponent(t)}` : '')
  const es = new EventSource(url)
  es.addEventListener('pair-request', (ev) => ev)
  return es
}

let es: EventSource | null = null
export function mimoStream(API_BASE: string, token: string) {
  if (es || typeof EventSource === 'undefined') return
  const url = `${API_BASE}/api/mcp/pair-request${token ? `?token=${encodeURIComponent(token)}` : ''}`
  es = new EventSource(url)
}
'''


def test_event_source_face_parses_both_real_shapes(tmp_path):
    """变量前缀（`${API_BASE}/api/…`）与 `BASE + '/mcp/pair-request'` 两种拼法都要读出同一条路由。

    类型标注 `let es: EventSource | null` 与探针 `typeof EventSource === 'undefined'` 不是调用点：
    把它们算进去会让"调用点数"这个读数失去意义。
    """
    findings, totals = _scan(tmp_path, SSE_CLIENT, PAIR_ROUTE)
    assert findings == []
    assert (totals["sites"], totals["sse"], totals["transport"]) == (2, 2, 0)


def test_event_source_query_inside_a_substitution_is_not_a_path_segment(tmp_path):
    """`${base}/api/mcp/pair-request${token ? `?token=${…}` : ''}`：query 藏在替换**体内**。

    只在替换外扫 `?` 的旧归一会把那一段当成多出来的路径段 ⇒ 段数对不齐 ⇒ 这条 SSE 永远认领不上
    它自己的路由（实测：`GET /api/mcp/pair-request` 因此躺在反向读数里，而 §二之四十二 刚修好它）。
    """
    mod = _module()
    assert mod._normalize("/api/mcp/pair-request${token ? `?token=${encodeURIComponent(token)}` : ''}"
                          ) == "/api/mcp/pair-request"
    assert mod._normalize("/graphs/${encodeURIComponent(name)}${version ? `?version=${version}` : ''}"
                          ) == "/graphs/\x01"


def test_event_source_on_a_missing_route_is_red(tmp_path):
    """这张脸同样得有牙：端点改名 ⇒ 建流静默收不到帧，必须红而不是"读不出所以放过"。"""
    findings, _ = _scan(tmp_path, SSE_CLIENT, PAIR_ROUTE.replace('"/api/mcp/pair-request"',
                                                                 '"/api/mcp/pair-stream"'))
    assert len(findings) == 2
    assert all("pair-request" in f for f in findings)


def test_event_source_with_unreadable_url_exits_2(tmp_path):
    """判据 C 对 SSE 生效：URL 来自读不出的地方（跨行拼接、外部变量）⇒ exit 2，不能当成"没人调"。"""
    assert _main(tmp_path, SSE_CLIENT.replace("const url = BASE + '/mcp/pair-request'", "const url = build()"),
                 PAIR_ROUTE) == 2

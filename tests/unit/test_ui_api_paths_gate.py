"""UI↔路由契约门禁必须"能变红"（铁律 #8），红的是**下一条改错的路径**而不是已知那 50 条。

背景：`ui/` 没有 vitest，UI 侧判据只有 `vue-tsc` + `vite build` + 真浏览器读数，前两条只证能编译。
路径是手抄字符串，服务端改名/删路由/换方法，前端照编译照 build，只有真点一次才炸。本门钉三件事：
A 路径在路由表里、B 方法一致、C **每个 `request(` 调用点都必须进射程**（解析不出就 exit 2）。

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

def test_non_literal_path_exits_2(tmp_path):
    """`request('GET', p)` 这种静态读不出的写法必须让门变 2，不是静默跳过。"""
    assert _main(tmp_path, CLIENT_HEAD + '''
export const extra = {
  dynamic: (p: string) => request<Anything>('GET', p),
}
''', API_HEAD) == 2


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
    改跨层契约就得在测试里留一次名，这是故意的摩擦，不是脆弱。"""
    mod = _module()
    sites, unparsed = mod.collect_ui_sites(ROOT / "ui" / "src")
    routes, excluded, mount_errs, boundary = mod.collect_routes(ROOT / "src")
    assert unparsed == []
    assert mount_errs == []
    assert mod.check(sites, routes) == []
    assert len(sites) == 50 and len(excluded) == 2
    mounted = sum(1 for r in routes if r["how"] == "add_api_route")
    assert mounted == 5                              # af_conflict_runtime._ROUTES
    assert len(routes) - mounted + len(excluded) == 81   # == grep -c "@app." src/autoforge/af_api.py
    assert len(routes) == 84                          # 79 装饰器 + 5 挂载表
    assert boundary == ["src/autoforge/af_runtime_plugins.py"]
    assert sum(1 for s in sites if s["exempt"]) == 0


def test_green_line_prints_measured_numbers_not_adjectives(tmp_path, capsys):
    assert _main(tmp_path, CLIENT_HEAD, API_HEAD) == 0
    out = capsys.readouterr().out
    _, totals = _scan(tmp_path, CLIENT_HEAD, API_HEAD)
    assert f"{totals['sites']} 处" in out and f"{totals['routes']} 条" in out
    for adjective in ("全部", "只在", "都在", "没有"):
        assert adjective not in out

"""用户视角 UI（`ui-user-mimo`）在只读服务层上的挂载判据。

来历：2026-10-07 部署现场。NAS 那台跑起来的镜像里 `forge serve` 不认 `--ui-user-dir`
（参数是这台机器手工改进 compose 的，镜像基线还停在上一版），于是 `/mimo/` 由**开发面板**
的 catch-all 兜住，返回 `200 + <title>AutoForge 控制台</title>`。用
`curl -o /dev/null -w '%{http_code}'` 验收会全绿——这正是「静默失效伪装成结论」那一族：
状态码对，页面错。本文件第一-leg 就按**内容**判，不看状态码。

三类判据各自都要有对腿（CONTROL）：判据若在任何一档都红不起来，它就是空门。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import UI_USER_PREFIX, build_app

#: 两颗 dist 的指纹标记：断言只认这两个字符串，不认状态码。
DEV_MARKER = "DEV-CONSOLE-MARKER"
USER_MARKER = "USER-PANEL-MARKER"

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_dist(root: Path, dir_name: str, marker: str, *, asset: str) -> Path:
    """造一颗最小可用 dist：index.html 带指纹，assets 里放一枚 js + 一枚 sw.js。"""
    dist = root / dir_name
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        f"<!doctype html><title>{marker}</title>"
        f'<script type="module" src="/{marker}/assets/{asset}"></script>\n',
        encoding="utf-8",
    )
    (dist / "assets" / asset).write_text(f'console.log("{marker} asset");\n', encoding="utf-8")
    # PWA：SW 必须以可执行的 JS 媒体型发出，浏览器才肯按 scope 注册。
    (dist / "sw.js").write_text(f'// {marker} sw\n', encoding="utf-8")
    return dist


@pytest.fixture()
def two_dists(tmp_path: Path) -> dict[str, Path]:
    # 目录名取容器里的真实形状：用户端那棵树在容器内就叫 `/mimo`。于是兄弟目录
    # `mimo-secret` 与它**字符串前缀相同**——穿越判据一旦退化成 `startswith`，
    # 这一格就会被读到（变异腿实测：形状不对的 fixture 让那条腿绿着过去）。
    dev = _write_dist(tmp_path, "ui", DEV_MARKER, asset="index-d.js")
    user = _write_dist(tmp_path, UI_USER_PREFIX, USER_MARKER, asset="index-b.js")
    return {"dev": dev, "user": user, "tmp": tmp_path}


def _client(tmp_path: Path, *, ui_dir: str | None, ui_user_dir: str | None) -> TestClient:
    return TestClient(
        build_app(
            str(tmp_path / "store"),
            ui_dir=ui_dir,
            ui_user_dir=ui_user_dir,
            readonly=False,
        )
    )


# ─────────────────────────────────────────────────────────────────────
# 1. 两脸各自的内容指纹（假部署这一族的正解）
# ─────────────────────────────────────────────────────────────────────


def test_mimo_serves_the_user_index_not_the_dev_console(two_dists) :
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    for path in (f"/{UI_USER_PREFIX}", f"/{UI_USER_PREFIX}/"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert USER_MARKER in r.text, path
        # 反空洞腿：这一条就是本机踩过的坑——状态码 200、内容却是工程控制台。
        assert DEV_MARKER not in r.text, path


def test_root_and_dev_deep_link_still_serve_the_dev_console(two_dists):
    """CONTROL 腿：挂上用户端不能把开发面板挤掉；两棵树各回各自的 index。"""
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    for path in ("/", "/overview", "/metrics"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert DEV_MARKER in r.text, path
        assert USER_MARKER not in r.text, path


def test_mimo_asset_is_served_from_the_user_tree(two_dists):
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    r = client.get(f"/{UI_USER_PREFIX}/assets/index-b.js")
    assert r.status_code == 200
    assert USER_MARKER in r.text
    assert "javascript" in r.headers["content-type"], r.headers["content-type"]
    # 开发面板的同名资源在另一棵树里；请求 /assets/* 不能读到用户端那份。
    d = client.get("/assets/index-d.js")
    assert d.status_code == 200 and DEV_MARKER in d.text and USER_MARKER not in d.text


def test_mimo_service_worker_is_executable_javascript(two_dists):
    """SW 走 `/mimo/sw.js`：媒体型不是 JS ⇒ 浏览器按错误签名拒绝注册，PWA 静默失效。"""
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    r = client.get(f"/{UI_USER_PREFIX}/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"], r.headers["content-type"]
    assert USER_MARKER in r.text


def test_mimo_deep_link_falls_back_to_user_index(two_dists):
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    r = client.get(f"/{UI_USER_PREFIX}/insights/deep-link")
    assert r.status_code == 200
    assert USER_MARKER in r.text and DEV_MARKER not in r.text


def test_unknown_mimo_asset_reads_404_not_index_html(two_dists):
    """⚠️ 返工 5.4 断言翻转（改前改后逐条写明）：
    改前＝本腿钉「不存在的 .js 落 index.html、200」。那**正是出资人 2026-10-27 现场的根因**：
    升级后旧壳引用的 hash chunk 已不在盘上，200+HTML 被浏览器当 ES 模块解析 ⇒
    「Failed to fetch dynamically imported module」，且 PWA precache 会把这份 HTML 按 JS 的
    URL 缓存下来 ⇒ 不硬刷不恢复。把错误行为钉成了规范。
    改后＝资源形状的缺失必须诚实 404；深链（无扩展名）才允许落壳。
    变异自证：把 handler 里的 `_looks_like_static_asset` 分支删掉 ⇒ 本腿红。"""
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    r = client.get(f"/{UI_USER_PREFIX}/assets/nope-does-not-exist.js")
    assert r.status_code == 404, r.status_code
    assert "<!doctype" not in r.text.lower(), "缺失资源仍被伪装成壳"
    # 对照腿：真在盘上的资源照常 200，扩展名判据不是"凡带点都杀"
    ok = client.get(f"/{UI_USER_PREFIX}/assets/index-b.js")
    assert ok.status_code == 200 and USER_MARKER in ok.text
    # 对照腿：无扩展名深链仍落壳（HTML5 history 不能被 404 掉）
    deep = client.get(f"/{UI_USER_PREFIX}/insights/deep-link")
    assert deep.status_code == 200 and USER_MARKER in deep.text


def test_spa_shell_and_service_worker_are_no_cache(two_dists):
    """返工 5.4 第二层：壳与 sw.js 必须 `Cache-Control: no-cache`。

    升级后浏览器若拿着缓存旧壳，它引用的还是**已被替换的 hash chunk**——旧壳+新盘=必坏，
    这正是出资人现场的另一半（资源 404 了还要能自愈）。no-cache 不是 no-store：
    每次回源比对（ETag/304 归 Starlette FileResponse），正常刷新即拿到新壳。
    变异自证：删掉 no-cache 赋值 ⇒ 本腿红。"""
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    for path in (f"/{UI_USER_PREFIX}/", f"/{UI_USER_PREFIX}/insights/deep-link"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", f"{path} 壳可被缓存＝旧壳配新盘"
    sw = client.get(f"/{UI_USER_PREFIX}/sw.js")
    assert sw.headers.get("cache-control") == "no-cache", "sw.js 可被缓存＝precache 清单停在旧版"
    # 直接请求 index.html 走的是文件分支（不是兜底分支）——两处都要 no-cache，
    # 漏一处等于"旧壳配新盘"还有一条路。
    shell = client.get(f"/{UI_USER_PREFIX}/index.html")
    assert shell.status_code == 200 and shell.headers.get("cache-control") == "no-cache", \
        "index.html 文件分支没带上 no-cache"
    # 对照：普通 chunk 内容寻址（hash 在文件名里），不强制 no-cache——别把缓存全杀了
    asset = client.get(f"/{UI_USER_PREFIX}/assets/index-b.js")
    assert asset.headers.get("cache-control") != "no-cache"


# ─────────────────────────────────────────────────────────────────────
# 2. 「传了参数但盘上没有」必须如实报，不许借开发面板充当已部署
# ─────────────────────────────────────────────────────────────────────


def test_requested_but_unmounted_user_dist_reads_503(two_dists):
    missing = str(two_dists["tmp"] / "not-built-yet")
    client = _client(two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=missing)
    r = client.get(f"/{UI_USER_PREFIX}/")
    assert r.status_code == 503, r.status_code
    body = json.dumps(r.json(), ensure_ascii=False)
    assert "ui-user-mimo" in body or "不是目录" in body, body
    # 开发面板不受影响：用户端没挂上不能把另一脸一起打挂。
    d = client.get("/")
    assert d.status_code == 200 and DEV_MARKER in d.text


def test_unrequested_mimo_path_reads_404_not_dev_index(two_dists):
    """只部署开发面板时，`/mimo` 必须是 404：回 index.html 就是「假装有个用户端」。"""
    client = _client(two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=None)
    for path in (f"/{UI_USER_PREFIX}", f"/{UI_USER_PREFIX}/", f"/{UI_USER_PREFIX}/x.js"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert DEV_MARKER not in r.text, path


def test_user_only_deploy_mounts_without_ui_dir(two_dists):
    """只传 --ui-user-dir 也要能起：这一档是「只给用户用」的部署形态。"""
    client = _client(two_dists["tmp"], ui_dir=None, ui_user_dir=str(two_dists["user"]))
    r = client.get(f"/{UI_USER_PREFIX}/")
    assert r.status_code == 200 and USER_MARKER in r.text
    # 没挂开发面板 ⇒ 根路径不假装存在第二张脸
    assert client.get("/overview").status_code == 404


def test_no_ui_args_keeps_api_pure(tmp_path):
    """CONTROL：两个参数都不传时不注册 catch-all（老判据：未知路径 404，不回 index.html）。"""
    client = _client(tmp_path, ui_dir=None, ui_user_dir=None)
    assert client.get("/whatever").status_code == 404
    assert client.get(f"/{UI_USER_PREFIX}/").status_code == 404


def test_api_routes_are_not_swallowed_by_catch_all(two_dists):
    """catch-all 排在 /api 之后：真端点照常，未知 /api 路径 404 而不是 index.html。"""
    client = _client(
        two_dists["tmp"], ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"])
    )
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["ok"] is True
    for path in ("/api/nope", f"/{UI_USER_PREFIX}/../api/nope"):
        assert client.get(path).status_code == 404, path


def test_dist_without_index_reads_503(tmp_path):
    """卷挂错目录（空 dist）⇒ 503，不是 FileResponse 抛出来的 500。"""
    empty = tmp_path / "empty-dist"
    (empty / "assets").mkdir(parents=True)
    client = _client(tmp_path, ui_dir=str(empty), ui_user_dir=None)
    assert client.get("/").status_code == 503


# ─────────────────────────────────────────────────────────────────────
# 3. 目录穿越：兄弟前缀（`/mimo-secret`）不是同一棵树
# ─────────────────────────────────────────────────────────────────────


def test_traversal_cannot_read_a_sibling_of_the_user_dist(two_dists):
    tmp = two_dists["tmp"]
    leak = tmp / f"{UI_USER_PREFIX}-secret"
    leak.mkdir(parents=True, exist_ok=True)
    (leak / "leak.txt").write_text("OUT-OF-TREE\n", encoding="utf-8")
    # 先自证反例可达：文件真在盘上，且这一对目录**字符串前缀相同**——判据若是 `startswith`
    # 就会把它们当成同一棵树（变异腿 L4 专门摘这条，实测会红）。
    assert (leak / "leak.txt").is_file()
    assert str(leak).startswith(str(two_dists["user"])), "fixture 形状失效：兄弟目录不再是前缀对"
    assert leak != two_dists["user"] and leak.parent == two_dists["user"].parent

    client = _client(tmp, ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"]))
    # 百分号编码的 `..`：实测 `/mimo/../x` 这种裸点段在客户端就被归一化成 `/x`（根本到不了
    # handler），只有 `%2e%2e` 会以字面 `..` 落进 `full_path`。用裸点段写这条判据 = 空门。
    r = client.get(f"/{UI_USER_PREFIX}/%2e%2e/{UI_USER_PREFIX}-secret/leak.txt")
    # ⚠️ 返工 5.4 断言翻转：改前钉 200+用户端 index；静态资源形状（.txt/.js…）自本批起
    # 诚实 404，不再回 index.html。核心不变量不变＝**读不到兄弟树**，下面两行才是这条腿
    # 存在的理由，变异腿 L4（摘 is_relative_to）仍然红：404 与「泄漏成功」在 status/text
    # 两个断言上同时红。翻转的只是次要断言（落点），不是安全判据本身。
    assert "OUT-OF-TREE" not in r.text
    assert r.status_code == 404
    r2 = client.get(f"/{UI_USER_PREFIX}/%2e%2e%2f{UI_USER_PREFIX}-secret%2fleak.txt")
    assert "OUT-OF-TREE" not in r2.text
    # 对照腿：无扩展名的兄弟树目录段仍走兜底（落用户端壳、不串树）——兜底本身还在
    r3 = client.get(f"/{UI_USER_PREFIX}/%2e%2e/{UI_USER_PREFIX}-secret")
    assert "OUT-OF-TREE" not in r3.text and USER_MARKER in r3.text


def test_traversal_cannot_read_outside_the_dev_dist(two_dists):
    tmp = two_dists["tmp"]
    (tmp / "outside.txt").write_text("OUTSIDE\n", encoding="utf-8")
    client = _client(tmp, ui_dir=str(two_dists["dev"]), ui_user_dir=str(two_dists["user"]))
    # ⚠️ 返工 5.4 断言翻转（同 sibling 腿）：核心不变量＝树外文件读不到；形状从
    # 「200+开发面板壳」翻成「404」（.txt 是资源形状）。变异腿（摘 is_relative_to）下
    # OUTSIDE 会被读出 ⇒ 两条断言同时红。无扩展名对照腿证明兜底没被整个杀掉。
    r = client.get("/%2e%2e/outside.txt")
    assert "OUTSIDE" not in r.text
    assert r.status_code == 404
    r2 = client.get("/%2e%2e/outside")    # 无扩展名 → 仍落开发面板壳，不串到用户端
    assert "OUTSIDE" not in r2.text and DEV_MARKER in r2.text and USER_MARKER not in r2.text


# ─────────────────────────────────────────────────────────────────────
# 4. 同源核对：前缀常量 ↔ vite base ↔ compose 挂载点
#    三处任一处改错都会「200 但白屏」，所以必须当场比对而不是靠人记。
# ─────────────────────────────────────────────────────────────────────


def test_vite_base_matches_the_server_prefix():
    text = (REPO_ROOT / "ui-user-mimo" / "vite.config.ts").read_text(encoding="utf-8")
    bases = re.findall(r"^\s*(base|start_url|scope):\s*'([^']+)'", text, re.M)
    assert bases, "vite.config.ts 里没读到 base/start_url/scope——判据失去对照物"
    for key, value in bases:
        assert value == f"/{UI_USER_PREFIX}/", f"{key}={value!r} 与服务端前缀不一致"


def _deployed_user_mount() -> tuple[str, str]:
    """解析生产部署里用户端 dist 的（启动参数目标, dist 落位目标），两值都得 == UI_USER_PREFIX。

    认两种部署形态，反例两种都拦：
      (A) 卷挂旧形态：compose 内联 `"--ui-user-dir", "/mimo"` + 卷 `ui-user-mimo/dist:/mimo`。
      (B) 烘进镜像形态（2026-10-17 起，防镜像基线与运行分叉）：compose `command` 指向
          `entrypoint.sh`，由它传 `--ui-user-dir /mimo`，dist 由 Dockerfile multi-stage
          `COPY --from=frontend /web/dist /mimo` 烘入，不再外挂卷。
    任何一种形态都必须能同时解析出"启动参数指向 /mimo"和"dist 真的落到 /mimo"。
    """
    compose = (REPO_ROOT / "docker" / "docker-compose.api.yml").read_text(encoding="utf-8")

    # 1) 启动参数 --ui-user-dir 的值：compose 内联优先；否则顺着 command 找 entrypoint.sh
    inline = re.search(r'"--ui-user-dir",\s*"([^"]+)"', compose)
    if inline:
        flag_target = inline.group(1)
    else:
        ep_ref = re.search(r"bash\"\s*,\s*\"([^\"]*entrypoint\.sh)\"", compose)
        assert ep_ref, "compose 既没内联 --ui-user-dir，也没引用 entrypoint.sh ⇒ 部署没传用户端目录"
        # 容器内 /app/docker/entrypoint.sh 由 Dockerfile `COPY docker/entrypoint.sh` 烘入
        ep_name = Path(ep_ref.group(1)).name
        ep_text = (REPO_ROOT / "docker" / ep_name).read_text(encoding="utf-8")
        in_ep = re.search(r"--ui-user-dir\s+(/\S+)", ep_text)
        assert in_ep, f"{ep_name} 引用了但没传 --ui-user-dir ⇒ /mimo 不被服务"
        flag_target = in_ep.group(1)

    # 2) dist 落位：卷挂（旧）或 Dockerfile 烘入（新），目标都得是 /mimo
    vol = re.search(r"-\s*(\S*ui-user-mimo/dist):(\S+)", compose)
    if vol:
        dist_target = vol.group(2)
    else:
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.api").read_text(encoding="utf-8")
        baked = re.search(r"COPY\s+--from=frontend\s+\S+\s+(/\S+)", dockerfile)
        assert baked, "没有卷挂也没在 Dockerfile 烘入用户端 dist ⇒ /mimo 在容器里是空的"
        # 烘进 /mimo 的那个 stage 必须确实由 ui-user-mimo 构建（防错把管理端烘进 /mimo）
        assert re.search(r"COPY\s+ui-user-mimo/", dockerfile), \
            "frontend 构建阶段不是 ui-user-mimo ⇒ 烘进 /mimo 的树错了"
        dist_target = baked.group(1)

    return flag_target, dist_target


def test_compose_mounts_the_user_dist_at_the_same_prefix():
    flag_target, dist_target = _deployed_user_mount()
    # 启动参数指向的容器内路径必须 == 服务端前缀
    assert flag_target.strip("/") == UI_USER_PREFIX, f"启动参数 {flag_target!r} ≠ 前缀 {UI_USER_PREFIX!r}"
    # dist 真正落到的路径必须与启动参数同一枚，否则参数指向空目录、读 503
    assert dist_target.strip("/") == UI_USER_PREFIX, \
        f"dist 落位 {dist_target!r} ≠ 启动参数 {flag_target!r}"


def test_mimo_router_history_carries_the_vite_base():
    """同源的第四处：客户端路由的 base 必须跟着构建期 base，不能留在站点根。

    服务端 catch-all 把 `/mimo/<深链>` 送回用户端 index（上面第 1 节管的是这一半），但地址栏
    由 vue-router 决定：不带参数的 history 把首跳写成 `/login`，用户一刷新就拿到开发面板的
    index——2026-10-07 NAS 现场实测到的是同一次部署里两半不同源。
    """
    text = (REPO_ROOT / "ui-user-mimo" / "src" / "router.ts").read_text(encoding="utf-8")
    call = re.search(r"createWebHistory\(([^)]*)\)", text)
    assert call, "router.ts 里没有 createWebHistory(...) 调用"
    assert call.group(1).strip() == "import.meta.env.BASE_URL", call.group(0)
    # CONTROL：base 若真是 `/`，不带参数也无害 ⇒ 上面那条就没有对照物。这条钉住子路径前提。
    vite = (REPO_ROOT / "ui-user-mimo" / "vite.config.ts").read_text(encoding="utf-8")
    assert re.search(rf"base:\s*'{re.escape(f'/{UI_USER_PREFIX}/')}'", vite), "vite base 不是子路径"


def test_serve_cli_exposes_the_flag():
    """装配面：`forge serve` 必须认这个参数——compose 传了而 CLI 不认 ⇒ 容器起不来。

    这一条不是形式主义：线上事故就是「compose 有 `--ui-user-dir`、镜像里的 CLI 没这个选项」，
    typer 会把未知选项判成用法错误（退出码 2），配 `restart: unless-stopped` 就是反复重启。

    判据读 CLI 自己的**参数注册表**（click 的 `Option.opts`），不读 `--help` 的渲染文本。
    原先这里数的是渲染出来的选项行（`--ui-user-dir        <str>`），为的是躲开子串判据的空洞
    ——`serve` 的帮助正文本来就有「传 --ui-user-dir …」这句话，改名 `--ui-user-dir-x` 后子串照样命中。
    但**文本行本身也不可靠**：2026-10-08 CI run 113 红在这一条，本机 18 条全绿。runner 上 typer
    带 ANSI 与 80 列面板渲染，标志与 metavar 之间夹着转义序列；本机同样的命令是无色输出，
    所以同一条正则一边绿一边假红。本机用 `FORCE_COLOR=1` + `COLUMNS=80` 复现出 CI 那一条假红
    （按文本的正则不命中，把 ANSI 剥掉就命中）⇒ 换读注册表，渲染宽度与配色都再也影响不到判据。
    """
    import inspect

    from typer.main import get_command
    from typer.testing import CliRunner

    from autoforge.af_cli import app as cli_app
    from autoforge.af_cli import serve

    result = CliRunner().invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0, result.output

    by_opt = {opt: param for param in get_command(cli_app).commands["serve"].params for opt in param.opts}
    for flag in ("--ui-user-dir", "--ui-dir"):
        param = by_opt.get(flag)
        assert param is not None, f"serve 没注册 {flag}；现有选项={sorted(by_opt)}"
        # 取值型而非开关：compose 传的是 `--ui-user-dir /mimo` 两段式。只核"名字在不在"
        # 会放过被改成布尔开关的那一版——名字还在，容器却起不来。
        assert param.is_flag is False, (flag, param.is_flag)
        assert param.nargs == 1, (flag, param.nargs)
    # 结构腿：签名里没有这个形参 ⇒ build_app 那侧不可能收到值
    params = list(inspect.signature(serve).parameters)
    assert "ui_user_dir" in params, params
    assert "ui_dir" in params, params

"""DCD 20261006 §一 = B：配对 bootstrap 的两个匿名端点 + 按 IP 锁定 + owner 暂停开关。

死锁的形状：`af_request_pair` / `af_pair` 两个 MCP 工具的 scope 都是 write，HTTP 面 `/mcp` 挂
`Depends(_write)` ⇒ **要拿配对码必须先有一枚 write 令牌，而配对恰恰是给还没有令牌的 agent 弄令牌**。
裁定驳回"把两工具 scope 改 None"（自撤 MCP 面 default-deny，且任何人可刷配对请求），选 B：
只把这两个动作开成匿名端点，匿名射程比整张工具表更小。

这里的判据因此不是"能配对"这么简单，而是四件事同时成立：匿名可达、码不外泄、超限真锁、
owner 一按就两头（HTTP + MCP）都停。
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge import af_mcp  # noqa: E402
from homesdk.adm.errors import (  # noqa: E402
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
)
from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import (  # noqa: E402
    BOOTSTRAP_LOCK_S,
    BOOTSTRAP_REDEEM_PER_MIN,
    BOOTSTRAP_REQUEST_PER_MIN,
    PairCodeStore,
    RateLimiter,
    RateLimitExceeded,
    TokenRegistry,
)

ROOT = Path(__file__).resolve().parents[2]
TOKENS = {"tok-bot": {"subject": "bot", "scopes": ["write"]}}
REQUEST_PATH = "/api/mcp/pair/request"
REDEEM_PATH = "/api/mcp/pair/redeem"
ACCEPTING_PATH = "/api/user/pair/accepting"


def _client(tmp_path, monkeypatch, env=None) -> TestClient:
    for key in ("AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH", "AF_REQUIRE_AUTH"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return TestClient(build_app(str(tmp_path / "store")))


def _pair_store(tmp_path, monkeypatch) -> PairCodeStore:
    """测试用的存储要与 app 用的同一路径，否则"开关按下去"打在另一棵树上，判据是假的。"""
    return PairCodeStore(tmp_path / "store" / ".auth" / "pair_codes.json")


# ── 裁定钉死的参数（AF 不自签：要改数值先改裁定）──────────────────
def test_bootstrap_numbers_are_the_ones_dcd_signed(tmp_path):
    assert (BOOTSTRAP_REQUEST_PER_MIN, BOOTSTRAP_REDEEM_PER_MIN, BOOTSTRAP_LOCK_S) == (6, 10, 300)
    # 「维持现值」那一答：8 位 / 300s / 单次。这里**不抄常量**，读的是实现真产出的那枚码——
    # 谁把生成器改回 6 位或把时效缩了，这一条会红，而不是跟着常量一起漂移。
    pc = PairCodeStore(tmp_path / "codes.json").create("钉参数")
    assert len(pc.code) == 8 and pc.code.isdigit(), pc.code
    assert pc.expires_at - pc.created_at == 300


# ── 匿名可达：不带任何令牌就能发起配对 ────────────────────────────
def test_request_is_reachable_without_a_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    resp = client.post(REQUEST_PATH, json={"agent_name_hint": "小助手"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["expires_at"] > time.time()


def test_the_code_never_leaves_the_server_on_the_agent_facing_leg(tmp_path, monkeypatch):
    """码只显示给用户弹窗；agent 这一腿拿到码就等于旁白了整个口述环节。"""
    client = _client(tmp_path, monkeypatch)
    body = client.post(REQUEST_PATH, json={}).json()
    assert "code" not in body and "token" not in body, body


def test_a_write_scoped_endpoint_still_refuses_the_anonymous_caller(tmp_path, monkeypatch):
    """匿名面只开这两条腿：同一枚无令牌的请求打 write 面必须还是 403/401。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    assert client.post(REQUEST_PATH, json={}).status_code == 200
    assert client.get("/api/user/pair/accepting").status_code in (401, 403)


# ── 超限真锁（不是窗口滑动）───────────────────────────────────────
def test_request_locks_the_ip_after_six_hits(tmp_path, monkeypatch):
    """「6 次放行、第 7 次锁」里的 6 和 7 是**写死的**，不从常量读。

    变异腿 M4（把 6 改成 99）实测过：从常量读的那一版照旧全绿——测试跟着实现一起漂移，
    等于只钉数值那一条腿有射程。数值口径归 `test_bootstrap_numbers_are_the_ones_dcd_signed`，
    这一条判的是行为。
    """
    client = _client(tmp_path, monkeypatch)
    codes = [client.post(REQUEST_PATH, json={}).status_code for _ in range(6)]
    assert codes == [200, 200, 200, 200, 200, 200], codes
    blocked = client.post(REQUEST_PATH, json={})
    assert blocked.status_code == 429, blocked.text
    assert "锁定" in blocked.json()["detail"], blocked.json()


def test_lock_holds_after_the_window_rolls():
    """把"限速"与"锁定"分开：只按窗口的话，等 60s 就能继续按 6 次/分钟刷——那不是锁。

    跑在 `RateLimiter` 单元上而不是 HTTP 上：不等真 60 秒，也不去 app 闭包里掏那两个实例
    （闭包形状是实现细节，测试靠着它写就会在下一次接线时无故红）。
    """
    limiter = RateLimiter(per_minute=BOOTSTRAP_REQUEST_PER_MIN, lock_s=BOOTSTRAP_LOCK_S)
    for _ in range(BOOTSTRAP_REQUEST_PER_MIN):
        limiter.check("ip:1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:1.2.3.4")
    assert limiter._hits.get("ip:1.2.3.4", []) == []  # 锁定时窗口里的旧命中即刻作废
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:1.2.3.4")  # 窗口早就滚过了，挡它的必须是那把锁
    limiter._blocked["ip:1.2.3.4"] = time.time() - 1
    limiter.check("ip:1.2.3.4")  # 解锁后才放行


def test_blocked_map_is_pruned_without_any_read():
    """`_blocked` 只有写入点时必须自己缩回去（第六轮审计 §三：纯写不读也要被回收）。

    这一条**一次 `check()` 都不调**：只写、只裁。`af_bounded_caches.BOUNDED_CACHES` 里
    `RateLimiter._blocked` 那一项的 `test` 腿指的就是这里。
    """
    limiter = RateLimiter(per_minute=1, lock_s=300)
    now = time.time()
    limiter._blocked = {"旧A": now - 1, "旧B": now - 2, "新C": now + 300}
    assert limiter._prune_blocked(now) == 2
    assert set(limiter._blocked) == {"新C"}
    # TTL 一条都不到期时，硬上限那条腿也得把表压回来
    limiter._blocked = {f"ip:{i}": now + 10 for i in range(limiter.LOCK_MAX_KEYS + 5)}
    limiter._prune_blocked(now)
    assert len(limiter._blocked) <= limiter.LOCK_MAX_KEYS


def test_redeem_has_its_own_looser_bucket(tmp_path, monkeypatch):
    """redeem 放宽到 10：裁定明写"要容忍用户口述打错一次"。两桶必须互相独立。

    这里 9 次错码 + 1 次成功同样是写死的读数（同上一条的理由）。
    """
    client = _client(tmp_path, monkeypatch)
    store = _pair_store(tmp_path, monkeypatch)
    pc = store.create("小助手")
    statuses = [client.post(REDEEM_PATH, json={"code": "00000000"}).status_code
                for _ in range(9)]
    assert all(s == 409 for s in statuses), statuses  # 错码：每次都是 409，不该提前 429
    ok = client.post(REDEEM_PATH, json={"code": pc.code, "agent_name": "小助手"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["token"] and ok.json()["subject"] == "小助手"


def test_request_lock_does_not_lock_redeem(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store = _pair_store(tmp_path, monkeypatch)
    pc = store.create("小助手")
    for _ in range(7):
        client.post(REQUEST_PATH, json={})
    assert client.post(REQUEST_PATH, json={}).status_code == 429
    assert client.post(REDEEM_PATH, json={"code": pc.code}).status_code == 200, "跨端点连坐会把打错码的用户一起锁死"


# ── 兑换语义：一次性 / 缺参 / 无效码 ──────────────────────────────
def test_redeem_is_single_use(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    pc = _pair_store(tmp_path, monkeypatch).create("小助手")
    first = client.post(REDEEM_PATH, json={"code": pc.code})
    assert first.status_code == 200
    second = client.post(REDEEM_PATH, json={"code": pc.code})
    assert second.status_code == 409, "同一枚码换出两枚令牌 ⇒ 单次形同虚设"


def test_redeem_without_code_is_400_not_409(tmp_path, monkeypatch):
    """缺参与错码是两件事：前者是调用方的形状错误，后者才该计入"打错一次"的预算。"""
    client = _client(tmp_path, monkeypatch)
    assert client.post(REDEEM_PATH, json={"code": "  "}).status_code == 400


def test_redeemed_token_actually_bears(tmp_path, monkeypatch):
    """端到端：换出来的令牌真能推开 write 面——否则配对只是发了一张废纸。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    pc = _pair_store(tmp_path, monkeypatch).create("小助手")
    token = client.post(REDEEM_PATH, json={"code": pc.code, "agent_name": "小助手"}).json()["token"]
    assert client.get(ACCEPTING_PATH, headers={"Authorization": f"Bearer {token}"}).status_code == 200


# ── 三处成功标记都必须是回读，不是字面量（反空洞腿）─────────────────
def test_request_refuses_to_promise_a_code_the_store_cannot_show(tmp_path, monkeypatch):
    """登记面回读不到这枚码 ⇒ 用户弹窗无码可念、agent 只能等到超时——必须红，不许回 200。"""
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(PairCodeStore, "get", lambda self, code: None)
    res = client.post(REQUEST_PATH, json={"agent_name_hint": "小助手"})
    assert res.status_code == 500, res.text


def test_redeem_does_not_hand_out_a_token_that_fails_readback(tmp_path, monkeypatch):
    """签发后回读不出主体 ⇒ 这张令牌是废纸；把废纸当好令牌发出去就是谎报配对成功。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    pc = _pair_store(tmp_path, monkeypatch).create("小助手")
    monkeypatch.setattr(TokenRegistry, "authenticate", lambda self, raw_token: None)
    res = client.post(REDEEM_PATH, json={"code": pc.code})
    assert res.status_code == 500, res.text
    assert "token" not in res.json(), res.json()


def test_toggle_is_red_when_the_switch_disagrees_with_the_request(tmp_path, monkeypatch):
    """写盘后开关读数与请求不一致 ⇒ 前端会显示一个并未生效的状态；这里必须红而不是照抄请求值。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    bot = {"Authorization": "Bearer tok-bot"}
    monkeypatch.setattr(PairCodeStore, "is_accepting", lambda self: True)
    res = client.post(ACCEPTING_PATH, json={"accepting": False}, headers=bot)
    assert res.status_code == 500, res.text


# ── owner 暂停开关：两头同一个口径 ────────────────────────────────
def test_pause_stops_the_anonymous_request_leg(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    bot = {"Authorization": "Bearer tok-bot"}
    assert client.get(ACCEPTING_PATH, headers=bot).json()["accepting"] is True  # 文件不存在＝出厂默认开
    assert client.post(ACCEPTING_PATH, json={"accepting": False}, headers=bot).json()["accepting"] is False
    blocked = client.post(REQUEST_PATH, json={})
    assert blocked.status_code == 409, blocked.text
    assert client.post(ACCEPTING_PATH, json={"accepting": True}, headers=bot).json()["accepting"] is True
    assert client.post(REQUEST_PATH, json={}).status_code == 200


def test_pause_stops_the_mcp_leg_too(tmp_path, monkeypatch):
    """用户在弹窗里止血，只停一半等于没停。"""
    store = PairCodeStore(tmp_path / "pair_codes.json")
    assert store.is_accepting() is True
    store.pause()
    assert store.is_accepting() is False
    args = {"agent_name_hint": "小助手"}
    # MCP 面读的是同一个文件的另一份实例（跨进程口径），所以这里直接换模块级单例
    monkeypatch.setattr(af_mcp, "_MCP_PAIR_STORE", store)
    out = af_mcp._t_request_pair(_FakeStore(tmp_path), args)
    assert out["ok"] is False and "暂停" in out["error"]
    assert out["code"] == ADM_ERR_AUTH_REQUIRED, out


def test_mcp_pair_rejections_each_carry_a_registered_code(tmp_path, monkeypatch):
    """契约 §7.2 落点②：MCP 面的拒绝不许只留一句散文——每一档都得落在六个注册码之一上。

    三档分开判：`no_code` 是"你发的东西不完整"（PAYLOAD_INVALID），`invalid` 是"这枚码不是凭据"
    （AUTH_REQUIRED），`not_ready` 是 AF 自己没就绪（INTERNAL 兜底）。把三档压成同一个码，
    对端就只能靠猜——而"靠中文关键词分档"正是这轮要归零的那个病根。
    """
    store = PairCodeStore(tmp_path / "pair_codes.json")
    monkeypatch.setattr(af_mcp, "_MCP_PAIR_STORE", store)
    fs = _FakeStore(tmp_path)

    assert af_mcp._t_pair(fs, {})["code"] == ADM_ERR_PAYLOAD_INVALID
    assert af_mcp._t_pair(fs, {"code": "00000000"})["code"] == ADM_ERR_AUTH_REQUIRED

    pc = store.create("小助手")
    monkeypatch.setattr(af_mcp, "_MCP_REGISTRY", None)
    assert af_mcp._t_pair(fs, {"code": pc.code})["code"] == ADM_ERR_INTERNAL


class _FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root


# ── 开关读数的三档（缺一档就会出事）───────────────────────────────
def test_missing_switch_file_is_the_factory_default_open(tmp_path):
    assert PairCodeStore(tmp_path / "codes.json").is_accepting() is True


def test_corrupt_switch_file_is_fail_closed(tmp_path):
    store = PairCodeStore(tmp_path / "codes.json")
    store._pause_path.write_text("{ not json", encoding="utf-8")
    assert store.is_accepting() is False, "读不出却被当成'仍在接受' ⇒ 用户按了暂停照旧被打扰"


def test_missing_accepting_key_is_not_accepting(tmp_path):
    store = PairCodeStore(tmp_path / "codes.json")
    store._pause_path.write_text(json.dumps({"updated_at": 1}), encoding="utf-8")
    assert store.is_accepting() is False


# ── 唯一实现：两面不许各写一份配对顺序 ────────────────────────────
def _calls_in(node: ast.AST) -> list[str]:
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


def test_mcp_pair_tools_delegate_instead_of_reimplementing():
    """`af_mcp` 里不该再有第二份 `create/consume/issue_for_agent` 的配对顺序。

    判的是 AST 里的**调用**，不是名字出现在文档里——`_t_pair` 的 docstring 提"兑换令牌"是正常的。
    """
    tree = ast.parse((ROOT / "src" / "autoforge" / "af_mcp.py").read_text(encoding="utf-8"), "af_mcp.py")
    called: list[str] = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in {"_t_request_pair", "_t_pair"}:
            called.extend(_calls_in(fn))
    assert "request_pair_code" in called or "redeem_pair_code" in called, called
    for banned in ("_pair_store(store).create", "_pair_store(store).consume"):
        assert banned not in called, f"{banned} 还在：配对顺序有了第二份实现"


#: 裁定 §一 新开的那对匿名端点的命名空间（`/api/mcp/pair-request` 那条 SSE 不在此列：
#: 它在 handler 里直调 `registry.authenticate` 并 fail-closed 拒未认证，见 af_api.py:1039）。
BOOTSTRAP_ROUTES = {("app.post", "/api/mcp/pair/request"), ("app.post", "/api/mcp/pair/redeem")}


def _routes(tree: ast.AST) -> list[tuple[str, str, bool, ast.AST]]:
    """把 `build_app` 闭包里的路由装饰器收成 `(装饰器, 路径, 有无鉴权依赖, handler)`。"""
    out: list[tuple[str, str, bool, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not ast.unparse(dec.func).startswith("app."):
                continue
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else None
            if not isinstance(path, str):
                continue
            guarded = any(
                k.arg == "dependencies" and "Depends(" in ast.unparse(k.value) for k in dec.keywords
            )
            out.append((ast.unparse(dec.func), path, guarded, node))
    return out


def test_the_anonymous_pair_face_is_exactly_two_routes_and_both_are_limited():
    """匿名射程是这道门的全部风险面，所以它必须是**数出来的**，不是注释里写的。

    三条同时成立才算数：命名空间 `/api/mcp/pair/` 下的路由恰好是这两条（有人"顺手"再开一条
    匿名路由会红在这里）、两条都没挂鉴权依赖（挂了就说明口径被改过，账要重对）、两条的 handler
    真调 `_bootstrap_limit`（限速是裁定 §一 的硬条件，漏一条等于匿名面不设防）。
    反空洞：命名空间数空了就直接判红——期望集合非空，空集不给"干净"。
    """
    tree = ast.parse((ROOT / "src" / "autoforge" / "af_api.py").read_text(encoding="utf-8"), "af_api.py")
    pair_ns = [(m, p, g, fn) for (m, p, g, fn) in _routes(tree) if p.startswith("/api/mcp/pair/")]
    assert {(m, p) for (m, p, _, _) in pair_ns} == BOOTSTRAP_ROUTES, (
        f"配对匿名面读数与裁定 §一 不一致：{sorted((m, p) for (m, p, _, _) in pair_ns)}"
    )
    for (method, path, guarded, fn) in pair_ns:
        assert not guarded, f"{method} {path} 挂了鉴权依赖：匿名 bootstrap 面被改动，死锁会回来"
        assert "_bootstrap_limit" in _calls_in(fn), f"{method} {path} 匿名却没过限速器"

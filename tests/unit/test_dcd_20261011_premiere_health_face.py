"""裁定 20261011（过期授权码与触发历史保留策略）§3 的"接面"半边：只读读数进 health 面。

裁定书 §3 明写"形态由 AF 定（进 `/api/metrics` 或既有健康面），⛔ 只读、⛔ 不触发任何动作"，
并把这一格留成**空格**——落码半边（`PremiereStore.stats()` 那枚读数本体）由
`tests/unit/test_dcd_20261011_code_retention_gauge.py` 的九枚腿钉住（执行记录 §二之一百二十八）。
本文件只钉**接面**这一格的三件事：

1. **两脸对得上**：HTTP 面（`/api/health`）与 Agent 面（MCP `af_health`）必须读到同一份
   `premiere`。此前反复判红的就是"多张调用脸对不上"这一族（`write_gate`／`linkage`／`store_ok`
   三格各有先例）。⛔ 本批不碰 `af_api.py`／`af_mcp.py`：两面都是整份透传 `svc.health(...)`，
   新键自动同时到货——所以这里既有行为腿，也把"透传"这个前提钉住（有人改成挑键就红）。
2. **接面不许变成门**：读 health 的人不该顺手改了码面。裁定 §4 验收 3 的"读数本身不改变任何
   状态"要覆盖到 health 这一路，而不只是 `stats()` 那一个方法。
3. **探测失败不许咽得无声**：本仓的既定口径（`TICK_HEALTH_PROBE_SKIPPED` 同族）是具名码 +
   `exc_info`。这一格按"静默"判红，与 `.gates.toml` 把 `af_service.py` 列进 `critical_globs`
   （那里的 `except Exception: pass` 是硬错误）同一条线。
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
from pathlib import Path

import pytest

from autoforge import af_mcp, af_premiere, af_service as svc
from autoforge.af_api import build_app
from autoforge.af_auth import SCOPES as _ALL_SCOPES
from autoforge.af_store import GraphStore

KEYS = {"issued_count", "code_space", "usage_ratio"}
CODE = "PREMIERE_HEALTH_PROBE_SKIPPED"


@pytest.fixture
def default_store(tmp_path, monkeypatch):
    """把模块级默认仓库换成一台带盘的干净实例。

    ⛔ 不能直接拿 `_default_premiere` 做断言：整树 pytest 跑在同进程里，别的用例会往那本册上
    发码，任何绝对计数都会变成按运行顺序漂移的假读数。
    """
    path = tmp_path / "premiere.json"
    st = af_premiere.PremiereStore(path=str(path))
    monkeypatch.setattr(af_premiere, "_default_premiere", st)
    return st, path


# --------------------------------------------------------------------------------------
# 1. 落点在服务层：health() 里那枚键真在读当前册面
# --------------------------------------------------------------------------------------


def test_module_level_stats_forwards_the_default_registry(default_store):
    st, _path = default_store
    assert af_premiere.stats() == st.stats(), "模块级 stats() 没在读默认仓库"
    st.issue("sha-forward")
    assert af_premiere.stats()["issued_count"] == 1, "转发的是一份缓存，不在读当前册面"


def test_forwarded_stats_is_itself_read_only():
    """新增的那一层转发不许变成门：它只能转 `stats()`，不能顺带拿到写面。"""
    src = inspect.getsource(af_premiere.stats)
    tree = ast.parse(src.lstrip())
    called = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert called == {"_default_premiere.stats"}, f"转发层调了别的出口：{sorted(called)}"
    for banned in ("save", "load", "issue", "consume", "_by_code["):
        assert banned not in src


def test_health_carries_the_premiere_readout(default_store):
    st, _path = default_store
    st.issue("sha-health-face")
    h = svc.health()
    assert "premiere" in h, "health 面没有 premiere 这一格"
    assert KEYS == set(h["premiere"])
    assert h["premiere"] == st.stats()
    assert h["premiere"]["issued_count"] == 1
    assert h["premiere"]["code_space"] == af_premiere._CODE_SPACE == 10 ** 6
    assert json.loads(json.dumps(h))["premiere"] == h["premiere"], "health 载荷 JSON 化之后漂了"


def test_health_readout_moves_with_the_registry(default_store):
    """反空洞：同一台 store 两次 health 必须给出两个数，否则这一格是个常量。"""
    st, _path = default_store
    before = svc.health()["premiere"]["issued_count"]
    st.issue("sha-one-more")
    after = svc.health()["premiere"]["issued_count"]
    assert after == before + 1, f"册面加了一枚而 health 不动（{before}→{after}）＝读数不读当前状态"


# --------------------------------------------------------------------------------------
# 2. 两脸契约：HTTP 面与 Agent 面读到同一份形状
# --------------------------------------------------------------------------------------


def _mcp_health(store):
    content, is_error = af_mcp.dispatch(
        "af_health", {}, store, {"subject": "test-premiere-face", "scopes": sorted(_ALL_SCOPES)}
    )
    assert is_error is False
    return json.loads(content[0]["text"])


def test_both_faces_expose_the_same_premiere_readout(tmp_path, default_store):
    st, _path = default_store
    st.issue("sha-two-faces")
    st.issue("sha-two-faces-2")
    store = GraphStore(tmp_path / "graph")
    http = svc.health(store)
    agent = _mcp_health(store)
    assert http["premiere"] == agent["premiere"] == st.stats(), "两脸的 premiere 对不上"
    assert sorted(http["premiere"]) == sorted(agent["premiere"]) == sorted(KEYS)


def test_http_route_serves_the_whole_health_payload(tmp_path, default_store):
    """真脸腿：走 FastAPI 路由，不经 `svc.health` 直调。

    这一格钉的是"路由不挑键"。`af_api` 现在是 `return svc.health(...)`——整份透传，所以
    `premiere` 自动到货；哪天有人在这里改成挑字段，本腿（和上一格的键集）立刻红。
    """
    st, _path = default_store
    st.issue("sha-http-face")
    from fastapi.testclient import TestClient

    client = TestClient(build_app(store_root=str(tmp_path / "graph"), readonly=False))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["premiere"] == st.stats(), "HTTP 面上的 premiere 与服务层不是同一份读数"


# --------------------------------------------------------------------------------------
# 3. ⛔ 只读：读 health 不得动码面，也不得落盘
# --------------------------------------------------------------------------------------


def test_reading_health_touches_neither_the_registry_nor_the_disk(default_store, monkeypatch):
    st, path = default_store
    code = st.issue("sha-readonly-face")
    st.consume(code, "sha-readonly-face")  # 把码推到「已消费」档，读 health 不该改变任何一档
    before_bytes = path.read_bytes()
    before_keys = sorted(st._by_code)
    writes: list[str] = []
    monkeypatch.setattr(
        af_premiere, "atomic_write_text", lambda *a, **k: writes.append(str(a[0]))
    )

    for _ in range(3):
        svc.health()

    assert writes == [], f"读 health 期间码面落盘 {len(writes)} 次＝接面把只读变成了写面"
    assert sorted(st._by_code) == before_keys, "读 health 动了册面"
    assert path.read_bytes() == before_bytes, "读 health 改了盘上那份文件"


# --------------------------------------------------------------------------------------
# 4. 探测失败不许静默：具名码 + exc_info，且不把 health 带崩
# --------------------------------------------------------------------------------------


def test_probe_failure_emits_a_named_code_with_traceback(default_store, monkeypatch, caplog):
    def boom() -> dict:
        raise RuntimeError("册面读不出（注入）")

    monkeypatch.setattr(af_premiere, "stats", boom)
    with caplog.at_level(logging.DEBUG, logger="autoforge.service"):
        h = svc.health()

    assert h["premiere"] is None, "探测失败还要给数＝假读数"
    assert h["ok"] is True, "读不到旁路面的一格不该把整个 health 判崩"
    hits = [r for r in caplog.records if CODE in r.getMessage()]
    assert hits, f"探测失败被咽掉了：没有 {CODE} 这枚具名码"
    assert hits[0].levelname == "DEBUG"
    assert hits[0].exc_info, f"{CODE} 必须带 exc_info=True，否则栈没了＝半个静默"


def test_probe_block_is_not_a_bare_pass():
    """结构腿：health() 里任何 `except` 都不许只写 `pass`（critical_globs 的硬错误形状）。"""
    tree = ast.parse(inspect.getsource(svc.health).lstrip())
    silent = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.body
        and all(isinstance(s, ast.Pass) for s in node.body)
    ]
    assert silent == [], f"health() 里有只 pass 的捕获：行 {silent}"
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "logger.debug"
        and node.args
        and ast.unparse(node.args[0]) == repr(CODE)
    ]
    assert calls, f"{CODE} 不是经 logger.debug 带码出来的"
    for call in calls:
        assert any(
            kw.arg == "exc_info" and ast.unparse(kw.value) == "True" for kw in call.keywords
        ), f"{CODE} 缺 exc_info=True（栈没了＝半个静默）"


def test_new_code_is_registered_in_the_observability_ledger():
    """B 判据的仓内读数腿：§三 的登记行必须与现读的 file:line 逐字对上（门读盘，这腿读同一份）。"""
    doc = Path(svc.__file__).resolve().parents[2] / "docs" / "可观测性清单.md"
    text = doc.read_text(encoding="utf-8")
    lineno = next(
        i
        for i, line in enumerate(Path(svc.__file__).read_text(encoding="utf-8").splitlines(), 1)
        if CODE in line
    )
    anchor = f"src/autoforge/af_service.py:{lineno}"
    row = next((ln for ln in text.splitlines() if ln.startswith(f"- `{anchor}`")), None)
    assert row, f"具名码 {CODE} 的登记行缺锚点 {anchor}（现读行号 vs 文档）"
    assert f"code={CODE}" in row and "level=debug" in row, row
    for field in ("级别语义：", "介入：", "依据：", "理由：", "认领："):
        assert field in row, f"§三 登记缺字段 {field}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""会话表的"只增不减"水位线 —— 第六轮审计 R6-F1（AF 侧唯一仍然成立的内存类缺陷）。

同族另外三处早就有硬上限（`af_audit` 的 `deque(maxlen=5000)`、`af_preference` 的
`max_records`、`af_bus` 的 pop 驱逐），`_SESSIONS` 两条腿都缺：既不在创建时清，
也没有数量上限——峰值内存 = 创建速率 × TTL，一份会话还挂着 Runtime + FakeHA + Graph。
"""
from __future__ import annotations

import inspect
import time
from types import SimpleNamespace

import pytest

from autoforge import af_service


@pytest.fixture(autouse=True)
def _isolated_session_table():
    saved = dict(af_service._SESSIONS)
    af_service._SESSIONS.clear()
    yield
    af_service._SESSIONS.clear()
    af_service._SESSIONS.update(saved)


def _aged(seconds_ago: float) -> dict:
    return {"created": time.monotonic() - seconds_ago}


def test_ttl_purge_removes_expired():
    af_service._SESSIONS.update({"old": _aged(99_999), "fresh": _aged(0.0)})
    af_service._purge_sessions()
    assert list(af_service._SESSIONS) == ["fresh"]


def test_hard_cap_evicts_oldest_even_within_ttl(monkeypatch):
    """TTL 没到也必须压回水位线——否则"批量创建后不再读"这条路永远封不住。"""
    monkeypatch.setattr(af_service, "SESSION_TTL_S", 10_000.0)
    monkeypatch.setattr(af_service, "SESSION_MAX", 3)
    for i in range(6):
        af_service._SESSIONS[f"s{i}"] = _aged(6 - i)   # s0 最老

    af_service._purge_sessions()

    assert sorted(af_service._SESSIONS) == ["s3", "s4", "s5"]


def test_create_session_purges_before_it_builds_a_runtime(monkeypatch):
    """R6-F1 正身：以前清理只挂在读路径上，纯创建的会话会一直常驻。"""
    monkeypatch.setattr(af_service, "SESSION_TTL_S", 1.0)
    monkeypatch.setattr(af_service, "SESSION_MAX", 2)
    for i in range(5):
        af_service._SESSIONS[f"stale{i}"] = _aged(50 + i)

    seen_at_build: list[int] = []

    def _stub_build(graph, seed):
        seen_at_build.append(len(af_service._SESSIONS))
        return SimpleNamespace(), SimpleNamespace()

    monkeypatch.setattr(af_service, "_load_ir", lambda ir: SimpleNamespace())
    monkeypatch.setattr(af_service, "_build_sim_runtime", _stub_build)
    monkeypatch.setattr(af_service, "_replay", lambda *a, **kw: None)
    monkeypatch.setattr(af_service, "_entities_of", lambda graph: [])
    monkeypatch.setattr(af_service, "_session_view", lambda sess: {"session_id": sess["id"]})

    af_service.create_session({"automations": []})

    assert seen_at_build == [0], "建 Runtime 之前应当已经清过一次"


def test_session_read_takes_the_lock():
    """写路径（插入/驱逐）都在锁里，读侧例外就是可能读到半截表。"""
    body = inspect.getsource(af_service._get_session)
    assert "with _SESSIONS_LOCK" in body.split("_SESSIONS.get")[0]


def test_cap_and_ttl_are_env_configurable():
    """部署侧要能调，不然水位线就成了新的硬编码。"""
    src = inspect.getsource(af_service)
    assert 'AUTOFORGE_SESSION_MAX' in src and 'AUTOFORGE_SESSION_TTL_S' in src

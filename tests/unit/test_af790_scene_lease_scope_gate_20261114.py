# -*- coding: utf-8 -*-
"""AF-790／AF-791 判据 · 场景执行绕过单写者租约＋write 档即可真机写（元宝 SCN-01，2026-11-14）。

**缺陷形状（PM 亲核）**：

1. `scene_run` 经 `_scene_live_executor`＝`HAAdapter(dry_run=False)` **真机下发**，可
   `_single_writer_check` 全树只有 `live_run` 一处调用 ⇒ serve 持单写者租约时 `af_live_run`
   503、`af_scene run` 照发 —— 租约被整条场景面绕过；
2. `af_scene` 工具挂 `write` 档，而 `af_live_run` 挂 `live` 档 ⇒ 一枚**只读+写**的令牌
   就能经场景做真机写（grant 逐步闸在场，但"能不能真机写"这一档被跳过了）。

**修法（AF-PM 已裁，两刀）**：

1. `svc.scene_run` 在 grant 解析**之前**加 `_single_writer_check(store)`——与 `live_run`
   同位置同形状（503 ＋ `READONLY_DEGRADED:` 前缀，裁定 20261004 §一 1 A）；
2. `af_mcp._t_scene` 在 `action="run"` 时要求当前令牌 scopes 含 `live`，否则 403；
   `save/list/get/delete` 维持 `write`（⛔ 不动）；工具描述写明两件事（run 需 live＋租约，
   serve 持锁时走 HTTP 面）。

⛔ 不动 grant 逐步闸语义（`af_scene_kit` 的 SKIP_AFTER_HARD_DENY 等一字未改）。

**判据**：

- D1 write-only 令牌 `af_scene action=run` ⇒ 拒绝且文案点明 live 档；同令牌 save 仍可（⛔ 不许一刀切）
- D2 持锁（mock serve 租约）⇒ `ServiceError` 503 ＋ `READONLY_DEGRADED:` 前缀，且**先于** grant 解析
- D3 停锁后同一请求走到下一步（错误不再是 503；换成 grant 那一档）
- D4 真实租约（真 FileLock 被别的持有者占着）⇒ 同 503（⛔ 不是只有 mock 才成立）
- D5 描述面：`af_live_run` 文本含「设计」「HTTP 面」（AF-791）；`af_scene` 文本含 live 与租约口径
"""

from __future__ import annotations

import json

import pytest

from autoforge import af_mcp
from autoforge import af_service as svc
from autoforge.af_scene_kit import SCENE_DEF_KIND
from autoforge.af_service import READONLY_DEGRADED_PREFIX, ServiceError
from autoforge.af_store import GraphStore

TARGET = "light.living_room"
WRITE_ONLY = {"subject": "agent-w", "scopes": ["read", "write"]}
WITH_LIVE = {"subject": "agent-l", "scopes": ["read", "write", "live"]}


def _ir(**kw):
    payload = {
        "kind": SCENE_DEF_KIND,
        "scene_id": "scene_home_001",
        "name": "回家模式",
        "aliases": ["到家了"],
        "version": 1,
        "owner": "owner",
        "condition": None,
        "created_via": "ask",
        "steps": [{
            "step_id": "s0", "adapter": "hass", "action": "light.turn_on",
            "target": TARGET, "params": {}, "on_error": "abort", "delay_ms_after": 0,
        }],
    }
    payload.update(kw)
    return payload


@pytest.fixture()
def store(tmp_path):
    st = GraphStore(str(tmp_path / "store"))
    svc.scene_def_save(st, _ir(), caller="owner", caller_is_owner=True)
    return st


# ── D1 档位：write-only 令牌⛔ 不许 run ─────────────────────────────────
def test_d1_write_only_token_cannot_run_but_can_save(store):
    args = {"action": "run", "scene_id": "scene_home_001", "grant_id": "g-000000000000",
            "agent": "tv-pilot"}
    with pytest.raises(ServiceError) as ei:
        af_mcp._t_scene(store, args, WRITE_ONLY)
    assert ei.value.status == 403, ei.value.status
    assert "live" in str(ei.value)

    # ⛔ 不许一刀切：save/list/get/delete 仍走 write 档
    saved = af_mcp._t_scene(store, {"action": "list"}, WRITE_ONLY)
    assert isinstance(saved, dict)


def test_d1b_live_token_passes_the_scope_gate(store):
    """反证腿：带 live 的令牌⛔ 不该被这条闸挡；它只会在后面的 grant 档被拒。"""
    args = {"action": "run", "scene_id": "scene_home_001", "grant_id": "g-000000000000",
            "agent": "tv-pilot"}
    with pytest.raises(ServiceError) as ei:
        af_mcp._t_scene(store, args, WITH_LIVE)
    assert ei.value.status != 403 or "live" not in str(ei.value), (
        f"live 令牌不该被档位闸挡：{ei.value}"
    )


# ── D2 租约：持锁 ⇒ 503 READONLY_DEGRADED，且先于 grant 解析 ─────────────
def _hold_lease(monkeypatch, held: bool):
    class _FakeLock:
        def __init__(self, _path):
            pass

        def held_by_other(self):
            return held

    monkeypatch.setattr(svc, "FileLock", _FakeLock)


def test_d2_held_lease_blocks_scene_run_before_grant(store, monkeypatch):
    _hold_lease(monkeypatch, True)
    with pytest.raises(ServiceError) as ei:
        svc.scene_run(store, scene_id="scene_home_001", agent="tv-pilot",
                      caller="owner", caller_is_owner=True,
                      grant_id="g-000000000000", executor=lambda step: True)
    assert ei.value.status == 503, f"持锁必须 503（'服务不可写'比'授权不足'更早看见）：{ei.value.status}"
    assert READONLY_DEGRADED_PREFIX in str(ei.value), "DB 按前缀判读，⛔ 丢掉前缀"
    # 先于 grant 解析：那枚 grant 故意不存在，若先解析就会得到 grant 错误而不是 503
    assert "授权" not in str(ei.value), str(ei.value)


# ── D3 停锁后同一请求走到下一步 ────────────────────────────────────────
def test_d3_lease_free_lets_the_same_request_proceed(store, monkeypatch):
    _hold_lease(monkeypatch, False)
    with pytest.raises(ServiceError) as ei:
        svc.scene_run(store, scene_id="scene_home_001", agent="tv-pilot",
                      caller="owner", caller_is_owner=True,
                      grant_id="g-000000000000", executor=lambda step: True)
    assert READONLY_DEGRADED_PREFIX not in str(ei.value), (
        f"没租约时⛔ 不许再报只读降级：{ei.value}"
    )


# ── D4 真租约（不是 mock）：**另一个进程**占着锁也 503 ─────────────────
def test_d4_real_lease_holder_in_another_process_also_blocks(tmp_path):
    """本进程持锁 `held_by_other()` 会如实回 False（serve 自己不该被 503 打回），
    所以真租约这一腿必须**另起一个进程**占锁——mock 只能证明判据形状，证明不了内核真值。
    """
    import subprocess
    import sys

    st = GraphStore(str(tmp_path / "store2"))
    svc.scene_def_save(st, _ir(), caller="owner", caller_is_owner=True)
    repo = str(__import__("pathlib").Path(__file__).resolve().parents[2])
    holder_code = (
        "import sys\n"
        "from autoforge.af_flock import FileLock, serve_lock_path\n"
        f"lk = FileLock(serve_lock_path({str(st.root)!r}))\n"
        "lk.acquire()\n"  # 成功时返回 None（不是 True），别拿返回值判成败
        "print('ready', flush=True)\n"
        "sys.stdin.readline()\n"
        "lk.release()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={**__import__("os").environ, "PYTHONPATH": repo + "/src"},
    )
    try:
        assert proc.stdout.readline().strip() == "ready", "持锁进程没起来"
        with pytest.raises(ServiceError) as ei:
            svc.scene_run(st, scene_id="scene_home_001", agent="tv-pilot",
                          caller="owner", caller_is_owner=True,
                          grant_id="g-000000000000", executor=lambda step: True)
        assert ei.value.status == 503 and READONLY_DEGRADED_PREFIX in str(ei.value), ei.value
    finally:
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
        except OSError:
            pass
        proc.wait(timeout=30)


# ── D5 描述面（AF-790 的描述半） ───────────────────────────────────────
def _desc(name: str) -> str:
    return next(t[1] for t in af_mcp.TOOLS if t[0] == name)


def test_d5b_scene_description_states_live_scope_and_lease():
    desc = _desc("af_scene")
    assert "run 需 live 权限" in desc, desc[:300]
    assert "单写者租约" in desc and "503" in desc and "HTTP 面" in desc, desc[:400]


def test_d5c_tool_scope_column_unchanged():
    """档位真源是工具表那一列：`af_scene` 仍挂 write（逐动作档位在 `_t_scene` 里判），
    `af_live_run` 仍挂 live。⛔ 不许把整工具提到 live——那会连 save/list 一起收走。"""
    scopes = {t[0]: t[4] for t in af_mcp.TOOLS}
    assert scopes["af_scene"] == "write"
    assert scopes["af_live_run"] == "live"

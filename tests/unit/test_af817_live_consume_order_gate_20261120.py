# -*- coding: utf-8 -*-
"""AF-817 判据 · live_run 短期码 consume 挪到业务校验后（墨鉴 N-02，2026-11-20）。

缺陷形状：`live_run` 旧实现在 `_load_ir`／静态扫描／白名单**之前**先 `acs.consume` 烧短期码
（af_service.py:3229 旧位）——非法 IR／越界白名单任一抛错 ⇒ 一枚短期码被白烧，且失败分支
不进暴力破解计数。同族（AF-817 第二部分）grant 长期码通道 `_grant_channel_tag` 也先 consume。

修法（AF-PM 卡面）：live_run 的 consume 挪到 `_load_ir`＋静态扫描＋白名单＋grant 判定**全部
通过之后、真机下发之前**；失败分支补 `record_failure`（暴力破解计数生效）。对齐 af_mcp.py:
483-487 既有纪律「先 precheck 再 consume」。

判据：
- A1 非法 IR（静态扫描不过）⇒ 短期码**不烧**（可用次数/状态不变），抛 400
- A2 白名单外（写目标越出 live_allow）⇒ 不烧
- A3 consume 失败 ⇒ 进 `record_failure`（暴力破解计数）+ fail-closed
- A4 源码钉：`acs.consume` 源码位置在 `_load_ir(` 之后（防回退先烧）
"""

from __future__ import annotations

import inspect

import pytest

from autoforge import af_service as svc
from autoforge.af_auth import AuthCodeStore
from autoforge.af_service import ServiceError
from autoforge.af_store import GraphStore


class _NoopTransport:
    def __call__(self, action, params):
        from autoforge.af_adapters.base import CallResult
        return CallResult(success=True, data={}, error=None)

    def all_states(self):
        return {}

    def get_state(self, entity_id):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: _NoopTransport())
    store = GraphStore(str(tmp_path / "store"))
    return {"store": store, "acs": AuthCodeStore(store.root / ".auth" / "auth_codes.json")}


def _run(env, ir, allow=("light.x",), code=""):
    return svc.live_run(ir, allow, None, confirm=True, store=env["store"], auth_code=code)


#: 合法 IR（HA do 动作；写目标 light.x 在白名单内）。
GOOD_IR = {
    "ir_version": "0.2.1", "id": "af817_live", "name": "af817_live", "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
        {"id": "d1", "kind": "do", "name": "d", "adapter": "ha", "action": "light.turn_on",
         "params": {"entity_id": "light.x"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "d1", "kind": "then"}, {"from": "d1", "to": "p1", "kind": "then"}],
}

#: 非法 IR：node kind 不在封闭词表 ⇒ `_load_ir` 直接抛 IRValidationError（⛔ 过不了第一道业务闸）。
BAD_IR = {
    "ir_version": "0.2.1", "id": "af817_bad", "name": "af817_bad", "version": 1, "mode": "single",
    "nodes": [{"id": "b1", "kind": "frobnicate"}],
    "edges": [],
}


def _code_consumed(env, code) -> bool:
    recs = env["acs"].list(reveal=True)
    row = next((r for r in recs if r.get("code") == code), None)
    assert row is not None, "码不存在"
    # 短期码一次性：consumed=True 或失败计数>0 都算"已动过"（⛔ 不该因业务校验失败被烧）
    return bool(row.get("consumed")) or int(row.get("failed_attempts") or 0) > 0


def _fail_count(env, code) -> int:
    recs = env["acs"].list(reveal=True)
    return int(next((r for r in recs if r.get("code") == code), {}).get("failed_attempts") or 0)


# ── A1：非法 IR ⇒ 不烧码 ──────────────────────────────────────────────

def test_a1_invalid_ir_does_not_consume_the_short_code(env):
    ac = env["acs"].create("short", ttl_minutes=30)
    # `_load_ir` 对结构非法 IR 抛 IRValidationError（不是 ServiceError）——重点不是异常类型，
    # 是**这枚短期码不能被烧**（修前 consume 在前，非法 IR 白烧）。任何抛错都算"业务校验拦下"。
    with pytest.raises(Exception) as ei:  # noqa: B017 - 断言"抛错"即可，类型由实现定
        _run(env, BAD_IR, code=ac.code)
    assert not _code_consumed(env, ac.code), (
        "非法 IR（_load_ir/扫描失败）⇒ 短期码⛔ 不该被烧（修前先 consume 再校验 = 白烧）"
    )


# ── A2：白名单外 ⇒ 不烧码 ────────────────────────────────────────────

def test_a2_out_of_allowlist_does_not_consume_the_short_code(env):
    ac = env["acs"].create("short", ttl_minutes=30)
    with pytest.raises(ServiceError) as ei:
        # 合法 IR，但写目标 light.x 不在白名单（只给 light.y）
        _run(env, GOOD_IR, allow=("light.y",), code=ac.code)
    assert ei.value.status == 400
    assert "白名单" in str(ei.value)
    assert not _code_consumed(env, ac.code), "白名单越界 ⇒ 短期码⛔ 不该被烧"


# ── A3：consume 失败 ⇒ record_failure（暴力破解计数） ────────────────

def test_a3_consume_failure_records_failure_and_fail_closed(env, monkeypatch):
    """consume 失败（并发消耗/状态已变）⇒ 补 record_failure + fail-closed。

    用桩让 `AuthCodeStore.consume` 返回 False（模拟 validate 通过后另一进程抢走），
    而 `validate` 仍 True——这才触达 deferred consume 的失败分支（真实单进程里
    validate 先拒，到不了这里；race 才是本卡要防的）。
    """
    import autoforge.af_auth as auth_mod

    ac = env["acs"].create("short", ttl_minutes=30)
    real_consume = auth_mod.AuthCodeStore.consume
    real_record_failure = auth_mod.AuthCodeStore.record_failure
    failures: list[str] = []

    def fake_consume(self, code):
        return False  # 状态已变：并发进程先烧了

    def spy_record_failure(self, code):
        failures.append(code)
        return real_record_failure(self, code)

    monkeypatch.setattr(auth_mod.AuthCodeStore, "consume", fake_consume)
    monkeypatch.setattr(auth_mod.AuthCodeStore, "record_failure", spy_record_failure)
    with pytest.raises(ServiceError) as ei:
        _run(env, GOOD_IR, code=ac.code)
    assert ei.value.status == 403
    assert ac.code in failures, "consume 失败必须进 record_failure（暴力破解计数生效）"


# ── A4：源码钉 ───────────────────────────────────────────────────────

def test_a4_consume_site_comes_after_ir_load():
    """⛔ 防回退「先烧码再校验」：`acs.consume` 源码位置必须在 `_load_ir(` 之后。"""
    src = inspect.getsource(svc.live_run)
    lines = src.splitlines()
    load_pos = next(i for i, line in enumerate(lines, 1) if "_load_ir(ir)" in line)
    consume_pos = next(i for i, line in enumerate(lines, 1) if "acs.consume(auth_code)" in line)
    assert load_pos < consume_pos, (
        f"_load_ir(行 {load_pos}) 必须先于 acs.consume(行 {consume_pos})（⛔ 回退先烧码）"
    )
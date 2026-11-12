# -*- coding: utf-8 -*-
"""判据 D4 · 停用后 watch 必须「停旧起新」——**本判据已于 2026-11-13 被取代**。

## 为什么这条判据被取代（⛔ 不是删红点，是原判据的前提错了）

D4 立于 2026-11-05（实证：`enable_by_tag(enabled=False)` 后 watch 内存仍咬旧快照，
被禁用的档继续触发 08:39-08:40 三次），处置定为「停旧 → 按新 merged IR 起新」。

2026-11-13 00:10 出资人再次报障（书房空调「明明停用了依然能够触发」），现场读数说明
**「停旧起新」只治了症状、而且治得不干净**：

- store：`/data/study_ac_smart_v7/v4.json` 的 `enabled=False`，saved_at 23:53（停用确实落盘）；
- 运行面：`/data/watch-ir-*/deployed_ir.json`（重启前那份）仍是 `enabled=true`；
- `/api/health`：`drifted=true`、`matches_store=false`——系统自己早就知道跑的不是 store 现状；
- 后果：停用后 **19 分钟**仍在触发（`last_triggered=2026-11-13T00:17:57+08:00`）；
- 处置：`docker restart autoforge-api` 让 watch 重装，重启后 5 分钟零触发。

四条理由说明「重启」是错的机制：

1. 停用是**只翻一位**的安全动作，为了关一条自动化而重装整份 IR，等于让「关」顺带做
   一次「部署」——撞「一次审批即生效」的授权契约；
2. 重启会**打断在跑实例**（`af_ask_purifier` 正在等的 60s ask 被腰斩），而关一条自动化
   本不该影响别的自动化；
3. 重启后 `ir_deployment_sha` 变了 ⇒ 「停用没重装 IR」**无从证明**（AF-PM 判据 4）；
4. D4 那版在**重启窗口内仍会触发**——比新契约更松。

## 新契约（AF-PM 裁定 2026-11-13，机制 B）

停用走**启停旁车** `<store_root>/watch.enabled.json` 热翻转（`af_watch_toggle`），
watch 在 `Scheduler.handle_event` **派发前**读它并热翻内存图的 `auto.enabled`：

- ⛔ 不 `stop_watch`、⛔ 不 `_start_watch_impl`、⛔ 不重装 IR；
- 时效＝**下一次事件**（不是 D4 的「重启完」）；
- `watch_reconcile.action == "hot_flipped"`，带 `overrides`（旁车里的启停位数）与
  `running`（活监听数，纯读数、不参与决策）。

D 编号沿用（群规⑭），语义全部改写：

  D1 禁用 + 有活 watch ⇒ 不 stop 不起，回 hot_flipped(running>=1)
  D2 禁用 + 全部禁完     ⇒ 同样 hot_flipped（⛔ 不许为此去停 watch）
  D3 禁用 + 本来没 watch ⇒ hot_flipped, running=0（不空转）
  D4 源码钉：enabled=False 支 ⛔ 不许调用 stop_watch / _start_watch_impl
  D5 源码钉：enabled=False 支 ⛔ 不许再早退成 None
"""

from __future__ import annotations

import inspect

import pytest

from autoforge import af_service
from autoforge.af_store import GraphStore


@pytest.fixture()
def store(tmp_path):
    return GraphStore(str(tmp_path / "store"))


def _wire(monkeypatch, *, alive=True, merged=None):
    calls = {"stop": 0, "start": 0}

    monkeypatch.setattr(
        af_service, "list_watches",
        lambda root=None: {"watches": [{"alive": True}] if alive else []},
    )

    def _stop(**kw):
        calls["stop"] += 1
        return {"ok": True, "signal": "delivered"}

    def _start(**kw):
        calls["start"] += 1
        return {"ok": True, "pid": 4242}

    monkeypatch.setattr(af_service, "stop_watch", _stop)
    monkeypatch.setattr(af_service, "_start_watch_impl", _start)
    monkeypatch.setattr(af_service, "_build_merged_ir", lambda s: merged)
    return calls


AUTO = {"ir_version": "0.2.1", "id": "a", "name": "a", "version": 1, "mode": "single",
        "nodes": [{"id": "t1", "kind": "on", "name": "t",
                   "trigger": {"type": "state", "entity_id": "binary_sensor.x", "to": "on"}},
                  {"id": "p1", "kind": "pass", "name": "p"}],
        "edges": [{"from": "t1", "to": "p1", "kind": "then"}]}


# ── D1 禁用不打断在跑实例 ────────────────────────────────────────
def test_d1_disable_does_not_stop_or_start(store, monkeypatch):
    calls = _wire(monkeypatch, merged={"automations": [dict(AUTO)]})
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 0, "停用是只翻一位的安全动作，⛔ 不许打断在跑实例"
    assert calls["start"] == 0, "停用⛔ 不许重装 IR"
    assert out["action"] == "hot_flipped"
    assert out["running"] == 1, "活监听数是读数，必须如实回"


# ── D2 全部禁完也不许停 watch ───────────────────────────────────
def test_d2_disable_all_off_still_does_not_stop(store, monkeypatch):
    calls = _wire(monkeypatch, merged=None)
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 0 and calls["start"] == 0
    assert out["action"] == "hot_flipped"


# ── D3 本来没 watch ─────────────────────────────────────────────
def test_d3_disable_without_watch_reports_zero_running(store, monkeypatch):
    calls = _wire(monkeypatch, alive=False, merged={"automations": [dict(AUTO)]})
    out = af_service._ensure_watcher(store, enabled=False)
    assert calls["stop"] == 0 and calls["start"] == 0
    assert out["action"] == "hot_flipped" and out["running"] == 0


# ── D4 源码钉：停用支不许再动 watch 生命周期 ─────────────────────
def _disable_branch_src() -> str:
    """切出 `_ensure_watcher` 的 enabled=False 支源码（到 `if running:` 为止）。

    ⛔ 别直接 `assert "stop_watch(" not in get_source(...)`：启用支仍在调
    `_start_watch_impl`，整函数一刀切会把**正确**的启用面也判红。
    """
    src = inspect.getsource(af_service._ensure_watcher)
    head, sep, tail = src.partition("if not enabled:")
    assert sep, "enabled=False 支不见了——判据失效，先修判据"
    return tail.split("if running:")[0]


def test_d4_source_disable_branch_never_restarts_watch():
    branch = _disable_branch_src()
    assert "stop_watch(" not in branch, (
        "停用支⛔ 不许调 stop_watch：重启会打断在跑实例、重写部署 IR（AF-PM 裁定 2026-11-13）"
    )
    assert "_start_watch_impl(" not in branch, (
        "停用支⛔ 不许重装 IR：`ir_deployment_sha` 一变，「停用没重装 IR」就无从证明"
    )
    assert "hot_flipped" in branch and "af_watch_toggle" in branch


# ── D5 源码钉：enabled=False 支不许早退成 None ───────────────────
def test_d5_source_no_early_return():
    src = inspect.getsource(af_service._ensure_watcher)
    assert "if not enabled:\n        return None" not in src, "禁用面⛔ 不许再早退成 None"

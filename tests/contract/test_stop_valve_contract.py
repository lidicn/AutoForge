# -*- coding: utf-8 -*-
"""契约腿 · 停用是安全阀（AF-PM 裁定 2026-11-13）。

三面一致性是这张卡的**产品语义**：store 说停用、`/api/health` 说 drifted、
运行面却还在跑——现场 19 分钟事故就是这三面各说各话。本腿钉**回执形状**，
让 WebUI/REST/MCP 三个消费面拿到同一份可判读的读数：

  - `enabled_override.ok`   启停旁车写没写进去（⛔ 不许「写失败回 ok:true」）
  - `enabled_override.ids`  本次翻了几位
  - `watch_reconcile.action` 停用面恒为 `hot_flipped`（⛔ 不再有 stop/start 族）

⛔ 别把这条腿写成「断言 ok:true」就完事：`ok` 与旁车成败必须**可分辨**，
否则「停用没生效」在回执上和成功逐字相同（AF58b 那类「形状相同、语义相反」）。
"""

from __future__ import annotations

import pytest

from autoforge import af_service
from autoforge.af_ir import Graph, load_automation
from autoforge.af_store import GraphStore

_AUTO = {
    "ir_version": "0.2.1", "id": "study_ac_smart_v7", "name": "书房空调",
    "version": 1, "mode": "single", "enabled": True,
    "nodes": [
        {"id": "t1", "kind": "on", "name": "t",
         "trigger": {"type": "state", "entity_id": "switch.study_ac", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "p"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


@pytest.fixture()
def store(tmp_path) -> GraphStore:
    s = GraphStore(str(tmp_path / "store"))
    raw = dict(_AUTO)
    raw["tags"] = ["qa"]
    s.save(Graph([load_automation(raw)]), "study_ac_smart_v7", tags=["qa"])
    return s


def test_disable_receipt_shape_is_consumable(store):
    out = af_service.enable_by_tag(store, "qa", False, allow_bulk=False)

    assert out["ok"] is True, out
    assert out["enabled"] is False
    # 三个消费面(WebUI/REST/MCP)都读这两格——缺一格就得各自猜。
    assert out["enabled_override"] == {"ok": True, "ids": 1}
    assert out["watch_reconcile"]["action"] == "hot_flipped"
    assert out["watch_note"], "停用必须给一句人话说明「即时生效、不重装 IR」"
    # ⛔ 停用面不许再出现 stop/start 族动作（那＝打断在跑实例、重装部署 IR）
    assert out["watch_reconcile"]["action"] not in ("stopped", "restarted", "stop_failed")


def test_override_failure_is_never_silently_ok(store, monkeypatch):
    """旁车写不进去 ⇒ `ok` 必须为假（安全阀关不上⛔ 不许看起来像成功）。"""
    from autoforge import af_watch_toggle

    def _boom(*_a, **_k):
        raise OSError("磁盘满")

    monkeypatch.setattr(af_watch_toggle, "merge_overrides", _boom)
    out = af_service.enable_by_tag(store, "qa", False, allow_bulk=False)

    assert out["ok"] is False, f"旁车写失败却回 ok=True，实得 {out}"
    assert out["enabled_override"]["ok"] is False
    assert "磁盘满" in str(out["enabled_override"].get("reason") or "")

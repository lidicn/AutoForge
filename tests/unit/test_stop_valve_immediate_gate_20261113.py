# -*- coding: utf-8 -*-
"""AF-PM 裁定 2026-11-13 · 停用是安全阀，必须**即时**生效（机制 B：启停旁车热翻转）。

现场（出资人 2026-11-13 00:10 报障，书房空调）：
store 的 `enabled=False` 23:53 就落盘了，`/data/watch-ir-*/deployed_ir.json` 仍是
`enabled=true`，`/api/health` 自己报 `drifted=true`/`matches_store=false`，停用后
**19 分钟**仍在触发（`last_triggered=00:17:57+08:00`）；`docker restart` 后 5 分钟零触发。

根因：`set_automation_enabled` 只写 store、**不通知 watch**；watch 内存咬的是启动时的
IR 快照。修复路径 ⛔ 不是「停用即重启」（打断在跑实例、重装部署 IR），
而是「watch 在**派发前**读启停旁车，把停用位热翻进内存图」。

判据（X 编号带卡名，群规⑭）：
  X1 即时：store 停用后**下一次事件**不派发（⛔ 不是「N 秒内」）
  X2 两个入口都写旁车：`enable_by_tag` 与 `set_automation_enabled`
  X3 免码不扩散：停用免码但**照样**写旁车（安全阀必须关得上）；启用仍要码
  X4 停用只翻一位：不新建 `watch-ir-*` 部署目录（证明没重装 IR）
  X5 停用后内存图与 store 的 `enabled` 位必然一致
  X6 坏旁车（外部写坏 JSON）⛔ 不许把已停用的自动化重新武装：保留上一份已知
     override，`apply()` 照旧翻位；修好后自动跟上（读侧 fail-soft 的方向）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoforge import af_service
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scheduler import Scheduler
from autoforge.af_store import GraphStore
from autoforge.af_watch_toggle import (
    ENABLED_OVERRIDE_FILE,
    EnabledOverrideGate,
    merge_overrides,
    read_overrides,
)

AUTO_ID = "study_ac_smart_v7"

_AUTO_RAW = {
    "ir_version": "0.2.1",
    "id": AUTO_ID,
    "name": "书房空调",
    "version": 1,
    "mode": "single",
    "enabled": True,
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
    raw = dict(_AUTO_RAW)
    raw["tags"] = ["qa"]
    s.save(Graph([load_automation(raw)]), AUTO_ID, tags=["qa"])
    return s


def _event() -> SimpleNamespace:
    return SimpleNamespace(entity_id="switch.study_ac", state="on", payload={})


def _scheduler(store_root: Path, gate: bool = True) -> tuple[Scheduler, list]:
    """最小 Scheduler：只量「派发闸」，不跑真的执行器。"""
    fired: list = []
    auto = SimpleNamespace(
        id=AUTO_ID,
        enabled=True,
        entry_nodes=lambda: [SimpleNamespace(trigger=object(), for_=None)],
    )
    sched = Scheduler(
        graph=[auto],
        instances=SimpleNamespace(),
        executor=SimpleNamespace(),
        states=SimpleNamespace(snapshot=lambda ids: {i: "on" for i in ids}),
        disable_gate=(EnabledOverrideGate(store_root) if gate else None),
    )
    sched._satisfied = lambda *a, **k: True  # type: ignore[method-assign]

    def _try_fire(*a, **k):
        fired.append(1)
        return SimpleNamespace(id="inst")

    sched._try_fire = _try_fire  # type: ignore[method-assign]
    return sched, fired


# ── X1 即时：下一次事件就不派发 ─────────────────────────────────
def test_x1_disable_takes_effect_on_the_very_next_event(store):
    sched, fired = _scheduler(Path(store.root))
    # 控制腿：停用前照常派发（⛔ 别让「恒不派发」冒充修复）
    assert sched.handle_event(_event()) == [SimpleNamespace(id="inst")] or fired == [1]
    assert len(fired) == 1, "控制腿：停用前必须能派发"

    # store 侧停用（服务层会调这条）
    merge_overrides(store.root, {AUTO_ID: False})

    # **下一次**事件：不派发（不是「N 秒内」、不是「重启后」）
    sched.handle_event(_event())
    assert len(fired) == 1, "停用后下一次事件仍派发＝安全阀没关（现场 19 分钟事故）"


# ── X2 两个入口都写旁车 ─────────────────────────────────────────
def test_x2_both_entry_points_write_the_override(store):
    # 入口一：标签面
    out = af_service.enable_by_tag(store, "qa", False, allow_bulk=False)
    assert out["ok"] is True, out
    assert read_overrides(store.root).get(AUTO_ID) is False, "enable_by_tag 没写启停旁车"
    assert out["enabled_override"]["ok"] is True
    assert out["watch_reconcile"]["action"] == "hot_flipped"

    # 入口二：单归档面
    af_service.set_automation_enabled(store, AUTO_ID, True)
    assert read_overrides(store.root).get(AUTO_ID) is True, "set_automation_enabled 没写启停旁车"
    af_service.set_automation_enabled(store, AUTO_ID, False)
    assert read_overrides(store.root).get(AUTO_ID) is False
    assert ENABLED_OVERRIDE_FILE in str(Path(store.root) / ENABLED_OVERRIDE_FILE)


# ── X3 免码不扩散，但停用照样写旁车 ─────────────────────────────
def test_x3_disable_is_code_free_but_still_writes_override(store):
    off = af_service.enable_by_tag(store, "qa", False, allow_bulk=False)
    assert off["ok"] is True, "停用被码闸挡住＝故障时无法止损"
    assert read_overrides(store.root).get(AUTO_ID) is False, "免码不等于不写安全阀"

    # 启用面：服务层直接抛 403（MCP/REST 面把它映成 ADM_ERR_AUTH_REQUIRED）——
    # ⛔ 别为了「让用例过得去」给它配码，那会把这张哨兵致盲。
    with pytest.raises(af_service.ServiceError) as exc:
        af_service.enable_by_tag(store, "qa", True, allow_bulk=False)
    assert exc.value.status == 403 and "授权码" in str(exc.value)


# ── X4 停用只翻一位：不重装 IR ─────────────────────────────────
def test_x4_disable_never_redeploys_ir(store, monkeypatch):
    deploys = {"start": 0, "stop": 0}
    monkeypatch.setattr(af_service, "_start_watch_impl",
                        lambda **kw: deploys.__setitem__("start", deploys["start"] + 1) or {})
    monkeypatch.setattr(af_service, "stop_watch",
                        lambda **kw: deploys.__setitem__("stop", deploys["stop"] + 1) or {})
    monkeypatch.setattr(af_service, "list_watches", lambda root=None: {"watches": [{"alive": True}]})

    before = {p.name for p in Path(store.root).glob("watch-ir-*")}
    af_service.enable_by_tag(store, "qa", False, allow_bulk=False)
    after = {p.name for p in Path(store.root).glob("watch-ir-*")}

    assert deploys == {"start": 0, "stop": 0}, (
        f"停用⛔ 不许停/起 watch（打断在跑实例、`ir_deployment_sha` 变），实得 {deploys}"
    )
    assert after == before, f"停用⛔ 不许新建部署目录（＝重装 IR），多出 {after - before}"


# ── X5 内存图与 store 的 enabled 位一致 ────────────────────────
def test_x5_memory_graph_matches_store_after_disable(store):
    sched, _ = _scheduler(Path(store.root))
    auto = sched.graph[0]
    assert auto.enabled is True

    af_service.set_automation_enabled(store, AUTO_ID, False)
    sched.handle_event(_event())

    assert auto.enabled is False, "停用后 watch 内存图仍 enabled=True＝现场那个形状"
    # store 侧真值（归档最新版本里的 enabled）
    latest = max((r for r in store.history() if r.get("name") == AUTO_ID),
                 key=lambda r: int(r.get("version") or 0))
    graph = store.load(latest["name"], int(latest["version"]))
    assert [a.enabled for a in graph.automations] == [False], "store 与内存图必须一致"


# ── X6 坏旁车⛔ 不许把已停用的自动化重新武装（读侧 fail-soft 方向）──
def test_x6_corrupt_sidecar_keeps_last_known_overrides(tmp_path):
    """AF-PM 2026-11-13 02:20 代码复核的 blocker：`_refresh` 把「保留上一份」做成了「清空」。

    `read_overrides` 是 fail-soft 的（坏 JSON ⇒ `{}`），而 `_refresh` 把它**直接
    赋给** `_map` ⇒ 旁车被外部写者搞坏（用户手工编辑／别的工具／磁盘层截断——
    `atomic_write_text` 只管本模块自己的写，挡不住这些）时，`apply()` 走
    `if not self._map: return 0`，**一位不翻**，内存图里那一位回到 IR 的 `True`
    ⇒ 停用的自动化重新触发，直到有人想起重启 watcher。

    安全阀的失效方向必须是「关着」，不是「偷偷开开」——所以这里量的是**保留**：
    坏文件之后 `apply()` 仍要把新建出来的内存图翻成 `False`。

    ⚠️ 判据⛔ 不能用「复用同一个 graph 对象、断言 `flipped == 0`」：那个形状在
    **有 bug 的版本上也是绿的**（`_map` 清空 ⇒ 早退 ⇒ 一位不翻 ⇒ 对象上那一位
    仍是上一轮的 `False`）。必须每条腿都拿**刚从 IR 重建**的对象（`enabled=True`）。
    """
    root = tmp_path / "store"
    root.mkdir()
    merge_overrides(root, {AUTO_ID: False})
    path = root / ENABLED_OVERRIDE_FILE

    def _auto() -> SimpleNamespace:
        # 每条腿都是「刚从 IR 重建」的新对象：现场 watch 每次事件都这么干
        return SimpleNamespace(id=AUTO_ID, enabled=True)

    gate = EnabledOverrideGate(root)
    first = _auto()
    assert gate.apply([first]) == 1 and first.enabled is False, "控制腿：停用位必须翻得动"
    assert gate.last_error == ""

    # 外部写者把旁车截断（半个 JSON）。⛔ 别用 `atomic_write_text` 造——那是本模块
    # 自己的写，这条腿要的是**外部**能造出来的形状。
    path.write_text('{"version": 1, "enabled": {"study', encoding="utf-8")

    after_corrupt = _auto()
    flipped = gate.apply([after_corrupt])
    assert after_corrupt.enabled is False, (
        "旁车损坏 ⇒ 已停用的自动化被重新武装（安全阀朝失效方向倒，⛔ 不许）"
    )
    assert flipped == 1, "上一份已知 override 必须仍然生效——flipped 应为 1，不是早退的 0"
    assert gate.overrides == {AUTO_ID: False}, "保留的必须是**上一份**，⛔ 不是空表"
    assert gate.last_error, "读失败必须留痕，否则现场只看到「停用自己恢复了」"

    # 修好之后必须自己跟上（⛔ 别为了保住旧值把 stamp 冻死，那会让修复也不生效）
    merge_overrides(root, {AUTO_ID: True})
    # ⚠️ 这一腿的 graph 对象要**反过来**给 `enabled=False`：若沿用 `enabled=True`，
    # 「override 也是 True」⇒ 无需翻位 ⇒ flipped 本来就是 0，量不出「跟上了没有」。
    recovered = SimpleNamespace(id=AUTO_ID, enabled=False)
    assert gate.apply([recovered]) == 1 and recovered.enabled is True, "旁车修好后必须跟上"
    assert gate.last_error == "", "读成功要把失败痕迹清掉"

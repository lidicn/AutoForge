"""真机路径的时间轴：`live_run` 必须锚在家庭墙钟的"现在"，而不是 `build_runtime` 的仿真锚点。

`build_runtime(graph)` 的默认钟是 `VirtualTimeSource(2026-09-14 08:00 UTC)`，是给仿真用的。
`live_run` 此前就是这么调的——同一次**对真实设备**的下发，`context.trigger_time`、`audit[].at`
与 canary 证据 `at` 全落在那个锚点上，`at:19:30` 这类 time 触发也按锚点判定；而 CLI 的
live/dry-live 分支早已显式取 `SystemTimeSource()`（`af_cli._make_runtime`）。两条真机路径
对同一次下发给出两套时间轴，正是第七轮审计那一族（同一 IR 判相反结论）。

判据取 `context.trigger_time`，不取 `audit[].at`：一次**顺利**的真机下发一条审计都不会产生
（`AuditLog` 只记漂移/失败/总线类事件），拿 `audit` 当时间轴会写成"`all(...)` 在空表上恒真"
的空转判据——本仓 fight 的假绿形状。`trigger_time` 由 `self.clock.now()` 写入
（`af_instance.py` v1.7.1 口径），成功路径上必然存在。

改法保留"可推进"这一点：锚点换成真实"现在"的虚拟钟，`_replay_live` 的 `advance_s` 前跳照旧能用
（第三条用例钉它；若把默认钟换成 `SystemTimeSource`，`runtime.advance` 会当场 `TypeError`），
需要确定性的调用方显式传 `clock=`（第二条用例钉它）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autoforge import af_service as svc
from autoforge.af_adapters import CallResult
from autoforge.af_time import VirtualTimeSource

LIVE_IR: dict = {
    "automations": [
        {
            "ir_version": "0.2.1",
            "id": "a_clock",
            "name": "a_clock",
            "version": 1,
            "mode": "restart",
            "nodes": [
                {
                    "id": "t1",
                    "kind": "on",
                    "trigger": {"type": "state", "entity_id": "binary_sensor.hall", "to": "on"},
                },
                {
                    "id": "d1",
                    "kind": "do",
                    "adapter": "ha",
                    "action": "light.turn_off",
                    "params": {"entity_id": "light.study"},
                    "on_error": {"default": "pass"},
                    "expect": {"entity_id": "light.study", "state": "off"},
                },
            ],
            "edges": [{"from": "t1", "to": "d1", "kind": "then"}],
        }
    ]
}

HALL_ON = [{"entity_id": "binary_sensor.hall", "state": "on"}]


class FakeTransport:
    def __init__(self):
        self.states = {"light.study": ("on", {})}
        self.dispatched: list[tuple[str, dict]] = []

    def __call__(self, action, params):
        return self.call(action, params)

    def call(self, action, params):
        self.dispatched.append((action, dict(params)))
        return CallResult.ok({"action": action, "result": []})

    def all_states(self):
        return dict(self.states)

    def get_state(self, entity_id):
        found = self.states.get(entity_id)
        return found[0] if found else None


@pytest.fixture()
def live(monkeypatch):
    monkeypatch.setenv("AUTOFORGE_LIVE_ENABLED", "1")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "srv-side-token")
    monkeypatch.delenv("AUTOFORGE_UNDO", raising=False)
    monkeypatch.setattr(svc, "LIVE_TRANSPORT_FACTORY", lambda ha_url, token: FakeTransport())


def _trigger_times(out: dict) -> list[datetime]:
    """取实例上下文里的触发时刻；空表直接判红，避免下游断言在 `all()` 上空转。"""
    times = [datetime.fromisoformat(i["context"]["trigger_time"]) for i in out["instances"]]
    assert times, "真机跑完一个实例都没有 ⇒ 时间轴无从判定，这条用例等于没跑"
    return times


def test_live_run_anchors_the_real_wall_clock(tmp_path, live):
    """一条真机下发的时间轴不许落在 2026-09-14 08:00 的仿真锚点上。"""
    out = svc.live_run(
        LIVE_IR, ["light.study"], HALL_ON, confirm=True, store=svc.GraphStore(str(tmp_path))
    )

    now = datetime.now(timezone.utc)
    assert all(abs(t - now) < timedelta(minutes=5) for t in _trigger_times(out)), (
        "真机路径的触发时刻落在仿真锚点上：时间窗判断与证据时间都会按 2026-09-14 08:00 计算"
    )


def test_explicit_clock_is_honoured(tmp_path, live):
    """确定性入口：显式传钟就按给定锚点，不受真实时间影响。"""
    anchor = datetime(2027, 3, 4, 5, 6, 7, tzinfo=timezone.utc)

    out = svc.live_run(
        LIVE_IR,
        ["light.study"],
        HALL_ON,
        confirm=True,
        store=svc.GraphStore(str(tmp_path)),
        clock=VirtualTimeSource(anchor),
    )

    assert all(t == anchor for t in _trigger_times(out)), (
        "显式 clock 被忽略 ⇒ 复现一次真机下发永远拿不到确定性时间轴"
    )


def test_advance_s_still_moves_the_live_clock(tmp_path, live):
    """锚在真实"现在"不等于放弃回放：`advance_s` 仍要把钟推进一小时。"""
    events = [{"advance_s": 3600}, {"entity_id": "binary_sensor.hall", "state": "on"}]

    out = svc.live_run(
        LIVE_IR, ["light.study"], events, confirm=True, store=svc.GraphStore(str(tmp_path))
    )

    delta = max(_trigger_times(out)) - datetime.now(timezone.utc)
    assert timedelta(seconds=3500) <= delta <= timedelta(seconds=3700), (
        f"advance_s 没推进真机路径的钟（偏移 {delta}）"
    )

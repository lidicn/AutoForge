"""三处"只作诊断、全仓没人读"的日志容器：封顶腿必须真在，且**纯写不读也要被回收**。

起因（稳定性审计 `docs/audit/审计报告-稳定性与功能性缺陷.md` BUG-01 与其 §六 P1）：BUG-01 删掉的是
`NodeExecutor.node_visits`——每次进节点 append 一条、全文件从来没人读、也没人裁。同一形状在本仓
还有三处（`check_bounded_caches.py` 的扫描器 + 判据 E 实测）：

- `af_adapters/ha.py::HAAdapter.intents`（`dry_run` 每下发一条 append 一条）
- `af_adapters/http.py::HTTPAdapter.intents`（同上）
- `af_scheduler.py::Scheduler.rejections`（每次拒绝 append 一条）

三处的适配器/调度器都由 `af_runtime.build_runtime` / `af_cli` 在进程启动时装一次，`forge serve`
常驻模式下活到进程结束 ⇒ 增长是真的。判据 E 只当场抓到 `rejections`（两处 `intents` 被
`af_apply.py:271` 的同名局部变量掩护掉），三处按同一形状一并收口。

这一族不能只测"有上限"：本仓口径是**纯写不读也必须被回收**（`af_bounded_caches.py` 的约定本体，
第六轮审计 §三）。所以每条腿都先证"harness 真会写、且没到上限时不提前丢"，再证"超过上限只丢最旧的"。
另外钉一条"空的那一眼还是 `== []`"：换成 `deque(maxlen=N)` 会让 `deque([]) == []` 变成 False，
把三处现有断言（`test_v0_6_tags.py` 两处、`test_af_adapters.py` 一处）整片撞翻——那不是本批要的行为变化。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoforge.af_adapters.ha import HAAdapter
from autoforge.af_adapters.http import HTTPAdapter
from autoforge.af_ir import Graph, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_scheduler import REJECTIONS_MAX
from autoforge.af_state import InMemoryStateProvider

DEMO = {
    "ir_version": "0.2.1", "id": "demo", "name": "示例", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass"},
    ],
    "edges": [{"from": "a1", "to": "p1", "kind": "then"}],
}

# 一条比 200 小得多的封顶值：让"超过就丢最旧的"这一半能在几十次写内测完，而不是把测试变成压测。
CAP = 4


# ── ① HAAdapter.intents ──────────────────────────────────────────────


def test_ha_adapter_starts_empty_and_stays_a_plain_list():
    adapter = HAAdapter(dry_run=True)
    assert adapter.intents == []
    assert isinstance(adapter.intents, list), "换成 deque 会让上面那句 == [] 永久为假"


def test_ha_adapter_intent_ring_evicts_oldest_without_reads(monkeypatch):
    from autoforge.af_adapters import ha as ha_mod

    monkeypatch.setattr(ha_mod, "INTENTS_MAX", CAP)
    adapter = HAAdapter(dry_run=True)
    for i in range(CAP):
        assert adapter.call(f"svc.e{i}", {"entity_id": "light.a"}).ok
    # 没到上限之前不许丢（只测"有上限"的话，把裁剪改成无条件清空也能绿）
    assert [a for a, _ in adapter.intents] == [f"svc.e{i}" for i in range(CAP)]

    for i in range(CAP, CAP * 3):  # 全程不读 intents，只有写入
        adapter.call(f"svc.e{i}", {"entity_id": "light.a"})
    assert len(adapter.intents) == CAP
    assert [a for a, _ in adapter.intents] == [f"svc.e{i}" for i in range(CAP * 3 - CAP, CAP * 3)]


def test_ha_adapter_declares_a_real_cap():
    from autoforge.af_adapters import ha as ha_mod

    assert ha_mod.INTENTS_MAX > 0


# ── ② HTTPAdapter.intents ────────────────────────────────────────────


def test_http_adapter_intent_ring_evicts_oldest_without_reads(monkeypatch):
    from autoforge.af_adapters import http as http_mod

    monkeypatch.setattr(http_mod, "INTENTS_MAX", CAP)
    adapter = HTTPAdapter(allowed_hosts=("example.com",), dry_run=True)
    assert adapter.intents == []
    for i in range(CAP * 2):
        adapter.call("post", {"url": f"http://example.com/p{i}"})
    assert len(adapter.intents) == CAP
    assert [p["url"] for _, p in adapter.intents] == [
        f"http://example.com/p{i}" for i in range(CAP * 2 - CAP, CAP * 2)
    ]
    assert http_mod.INTENTS_MAX > 0


# ── ③ Scheduler.rejections ───────────────────────────────────────────


def _runtime(tmp_path):
    return build_runtime(
        Graph([load_automation(DEMO)]),
        states=InMemoryStateProvider(states={"binary_sensor.m": "off"}),
        persist_dir=str(tmp_path),
    )


def test_scheduler_rejection_ring_evicts_oldest_without_reads(tmp_path, monkeypatch):
    from autoforge import af_scheduler

    monkeypatch.setattr(af_scheduler, "REJECTIONS_MAX", CAP)
    runtime = _runtime(tmp_path)
    scheduler = runtime.scheduler
    auto = SimpleNamespace(id="demo")
    assert scheduler.rejections == []

    for i in range(CAP):
        scheduler._reject(auto, f"r{i}")  # 走的就是生产侧那个写入口
    assert scheduler.rejections == [f"r{i}:demo" for i in range(CAP)]

    for i in range(CAP, CAP * 3):
        scheduler._reject(auto, f"r{i}")
    assert len(scheduler.rejections) == CAP
    assert scheduler.rejections == [f"r{i}:demo" for i in range(CAP * 3 - CAP, CAP * 3)]
    # 拒绝同时必须还在落审计：封顶裁的是内存镜像，不是审计账本
    assert len(runtime.audit.events) >= CAP * 3


def test_scheduler_rejection_ring_default_is_declared_and_positive():
    assert REJECTIONS_MAX > 0


#: (文件, append 点期望数, 封顶守卫期望数, 守卫写法)
RING_WIRING = [
    ("af_adapters/ha.py", "self.intents.append(", "len(self.intents) >= INTENTS_MAX", 1, 1),
    ("af_adapters/http.py", "self.intents.append(", "len(self.intents) >= INTENTS_MAX", 1, 1),
    ("af_scheduler.py", "self.rejections.append(", "len(self.rejections) >= REJECTIONS_MAX", 1, 1),
]


@pytest.mark.parametrize("path,append_token,guard_token,want_appends,want_guards", RING_WIRING)
def test_the_cap_is_wired_at_every_write_site(path, append_token, guard_token,
                                              want_appends, want_guards):
    """封顶不许只写在初始化那一行：容器每个 append 点都得先过一道 `len(...) >= 上限`。

    `Scheduler.rejections` 的两个生产写入口都收在 `_record_rejection` 里，所以它的直接
    append 点只有 1 个——那个 helper 必须正好被调用 2 次，否则有一条写路径绕过了封顶。
    扫描器只看初始化那行，接线漏没漏它看不出来，所以这条按真实文本计数。
    """
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[2] / "src" / "autoforge" / path).read_text(
        encoding="utf-8"
    )
    assert text.count(append_token) == want_appends, f"{path} 的 {append_token!r} 写入口数变了"
    assert text.count(guard_token) == want_guards, f"{path} 的封顶守卫 {guard_token!r} 没接到写入口上"
    if path == "af_scheduler.py":
        assert text.count("self._record_rejection(") == 2, "拒绝的两条写路径有一条不再走封顶"

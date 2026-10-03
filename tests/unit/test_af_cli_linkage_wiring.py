"""第 1/2 步 + 裁定 ④A 的**接线**判据：CLI 起桥时递给桥的四样东西。

`af_mqtt_bridge` 侧每条各自有测试（`start_from_env` 的 fail-closed、durable sink 的落盘、
`caps.version` 的默认值），但 `af_cli._start_linkage_bridge` 这根线此前一条判据都没有：
把 `proposal_sink=` 摘掉、把 `version=` 传成空串、或把未开启时的 `return None` 改掉，
全量 pytest 照样绿——而这三条恰好对应"洞察重启即清零""retained 空版本号""假桥"三个已知坑。
"""

from autoforge import af_cli, af_mcp, af_mqtt_bridge
from autoforge.af_insight_queue import PersistentInsightSink


class _FakeBridge:
    """只用来占位：本文件判的是"传了什么给桥"，不是桥本身。"""


def _capture_start(monkeypatch, fake):
    captured: dict = {}

    def fake_start_from_env(**kwargs):
        captured.update(kwargs)
        return fake

    attached: list = []
    monkeypatch.setattr(af_mqtt_bridge, "env_enabled", lambda: True)
    monkeypatch.setattr(af_mqtt_bridge, "start_from_env", fake_start_from_env)
    monkeypatch.setattr(af_mqtt_bridge, "attach", attached.append)
    return captured, attached


def test_no_bridge_and_no_connection_attempt_when_env_is_off(monkeypatch):
    calls: list = []
    monkeypatch.setattr(af_mqtt_bridge, "env_enabled", lambda: False)
    monkeypatch.setattr(af_mqtt_bridge, "start_from_env", lambda **kw: calls.append(kw))

    assert af_cli._start_linkage_bridge(store_root="unused") is None
    assert calls == [], "未开启却去连 broker = 默认试连，与『默认关』的口径相反"


def test_bridge_gets_the_durable_sink_plan_version_and_tool_names(tmp_path, monkeypatch):
    fake = _FakeBridge()
    captured, attached = _capture_start(monkeypatch, fake)

    result = af_cli._start_linkage_bridge(store_root=tmp_path)

    assert result is fake
    assert attached == [fake], "起桥后没 attach ⇒ Runtime 终态永远不流向 broker"
    assert captured["version"] == af_mqtt_bridge.PRESENCE_CAPS_VERSION
    assert captured["version"], "caps.version 是 retained 载荷，空串要等对端查账才看得见"
    assert captured["tools"] == [tool[0] for tool in af_mcp.TOOLS]
    assert isinstance(captured["proposal_sink"], PersistentInsightSink), (
        "④A 要的是只落盘的独立队列；进程内 ProposalManager 重启即清零"
    )


def test_the_wired_sink_actually_lands_on_disk_under_the_store_root(tmp_path, monkeypatch):
    fake = _FakeBridge()
    captured, _ = _capture_start(monkeypatch, fake)
    af_cli._start_linkage_bridge(store_root=tmp_path)

    captured["proposal_sink"].submit(
        hypothesis_id="h-1", natural_language="夜里Study 的灯在人离开后关掉", conf=0.4
    )

    pending = sorted((tmp_path / "insight_proposals" / "pending").glob("*.json"))
    assert len(pending) == 1
    assert "h-1" in pending[0].read_text(encoding="utf-8")
    assert not hasattr(captured["proposal_sink"], "approve"), "落盘队列不许带部署把手"

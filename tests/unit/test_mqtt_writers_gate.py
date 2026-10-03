"""出向 MQTT 写者门禁必须"能变红"（铁律 #8），且红的必须是**下一个写者**而不是已知那一个。

起因（§二之二十二）：本仓有条"逐字段对契约"的测试一直绿，而它测的是 `publish_fired()/publish_failed()`
的**直接调用**路径；生产唯一发事件的路径是 `observe_terminal()`，它多发一个契约表 §1.2 没列的 `node_id`。
两条路各测一头 ⇒ 真实载荷和契约行不一样，而**没有一条测试红过**。本门钉的就是这个形状：
出向消息只能有一个写者模块、事件只能由那一条路径产生、载荷必须经 `_envelope()` 组装。

三条判据各自单独可红（A 桥外写 MQTT / B 桥外或观察者外的生产者 / C 手搓 dict），
射程外不判（内部总线 `bus.publish(...)` 不是 MQTT），豁免要带理由且单独计数。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mqtt_writers.py"

BRIDGE_HEAD = '''
FIRED_TOPIC = "af/automation/fired"
FAILED_TOPIC = "af/automation/failed"


class Bridge:
    def _envelope(self, *, automation_id, instance_id, extra=None):
        return {"trace_id": "x", "automation_id": automation_id}

    def _publish(self, topic, payload):
        return _mqtt.publish(self.client, topic, payload, retain=False)

    def publish_fired(self, *, automation_id, instance_id, **extra):
        return self._publish(FIRED_TOPIC, self._envelope(automation_id=automation_id,
                                                         instance_id=instance_id, extra=extra))

    def publish_failed(self, *, automation_id, instance_id, error="", **extra):
        payload = self._envelope(automation_id=automation_id, instance_id=instance_id, extra=extra)
        payload["error"] = error
        return self._publish(FAILED_TOPIC, payload)

    def observe_terminal(self, instance, state):
        if state == "done":
            return self.publish_fired(automation_id="a", instance_id="i")
        return self.publish_failed(automation_id="a", instance_id="i", error="boom")
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_mqtt_writers", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan(tmp_path: pathlib.Path, **files: str) -> tuple[list[str], dict]:
    for name, code in files.items():
        (tmp_path / name).write_text(code, encoding="utf-8")
    return _module().check(tmp_path)


def _bridge(tmp_path: pathlib.Path, extra: str) -> str:
    """把 `extra` 拼进桥文件（保持合法缩进：都挂在模块级）。"""
    return BRIDGE_HEAD + extra


# ── 本职：下一个写者要红 ─────────────────────────────────────────────

def test_known_good_shape_is_green(tmp_path):
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD})[0] == []


def test_publisher_outside_the_bridge_is_red(tmp_path):
    """A 判据：别人自己拿 client 发 MQTT = 绕过桥的 QoS/`ts` 口径/失败留痕。"""
    findings = _scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD,
        "af_live.py": 'def poke(client):\n    _mqtt.publish(client, "af/automation/fired", {})\n',
    })[0]
    assert len(findings) == 1
    assert "af_live.py" in findings[0] and "_mqtt.publish" in findings[0]


def test_presence_advertise_outside_the_bridge_is_red(tmp_path):
    findings = _scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD,
        "af_cli.py": 'def hello(client, name):\n    _presence.advertise(client, name)\n',
    })[0]
    assert len(findings) == 1 and "af_cli.py" in findings[0]


def test_advertise_inside_the_bridge_is_not_red(tmp_path):
    """同一条判据不许把桥自己的 presence 写法算成违例（射程=桥外）。"""
    code = _bridge(tmp_path, '\n\ndef startup(bridge):\n    bridge.advertise()\n')
    (tmp_path / "af_mqtt_bridge.py").write_text(
        code.replace("    def observe_terminal", "    def advertise(self, caps=None):\n        _presence.advertise(self.client, 'adm/x/status')\n\n    def observe_terminal"),
        encoding="utf-8",
    )
    assert _scan(tmp_path)[0] == []


# ── 射程边界：别把内部总线当 MQTT ────────────────────────────────────

def test_internal_bus_publish_is_out_of_scope(tmp_path):
    """`af_bus`/`af_runtime` 的 `publish()` 是进程内事件总线，不是出向 MQTT。

    按方法名一刀切会让本门天天红在无关代码上，最后被人当成噪音跳过。
    """
    assert _scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD,
        "af_bus.py": 'class Bus:\n    def emit(self, ev):\n        return self.publish(ev)\n',
    })[0] == []


# ── B 判据：事件生产者只许那一条路径 ────────────────────────────────

def test_publisher_called_from_another_bridge_function_is_red(tmp_path):
    """本批那件事的静态形状：多一个生产者 = 多一条"测试测不到、对端却在收"的载荷。"""
    code = _bridge(tmp_path, "\n\ndef resend(bridge):\n    return bridge.publish_fired(automation_id='a', instance_id='i')\n")
    findings = _scan(tmp_path, **{"af_mqtt_bridge.py": code})[0]
    assert len(findings) == 1
    assert "publish_fired" in findings[0] and "resend" in findings[0]


def test_publisher_called_from_another_module_is_red(tmp_path):
    findings = _scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD,
        "af_service.py": 'def nudge(bridge):\n    return bridge.publish_failed(automation_id="a", instance_id="i", error="x")\n',
    })[0]
    assert len(findings) == 1 and "af_service.py" in findings[0]


def test_publisher_inside_nested_helper_of_observer_is_red(tmp_path):
    """最内层归属：把发布塞进 `observe_terminal` 里的闭包，同样算第二条路径。

    写死"外层函数名"会放行这种形状——所以判据问的是"这段调用发生在谁体内"。
    """
    code = _bridge(tmp_path, "\n") + (
        "\n\nclass Wrapper:\n"
        "    def observe_terminal(self, inst, state):\n"
        "        def emit():\n"
        "            return self.publish_fired(automation_id='a', instance_id='i')\n"
        "        return emit()\n"
    )
    findings = _scan(tmp_path, **{"af_mqtt_bridge.py": code})[0]
    assert len(findings) == 1 and "`emit()`" in findings[0]


# ── C 判据：载荷必经 _envelope() ────────────────────────────────────

def test_handmade_dict_payload_is_red(tmp_path):
    code = _bridge(tmp_path, "\n\nclass Raw:\n"
                             "    def fire(self):\n"
                             '        return self._publish(FIRED_TOPIC, {"ts": "2026-01-01T00:00:00+08:00"})\n')
    findings = _scan(tmp_path, **{"af_mqtt_bridge.py": code})[0]
    assert len(findings) == 1 and "_envelope" in findings[0]


def test_payload_from_another_source_is_red(tmp_path):
    code = _bridge(tmp_path, "\n\nclass Raw:\n"
                             '    def fire(self):\n'
                             '        payload = json.loads(blob)\n'
                             '        return self._publish(FIRED_TOPIC, payload)\n')
    assert len(_scan(tmp_path, **{"af_mqtt_bridge.py": code})[0]) == 1


def test_keyword_call_form_is_also_judged(tmp_path):
    """只认位置参数会留一条静默放行：`_publish(topic=…, payload=…)` 必须同样进射程。"""
    code = _bridge(tmp_path, "\n\nclass Raw:\n"
                             '    def fire(self):\n'
                             '        return self._publish(topic=FIRED_TOPIC, payload={"ts": "x"})\n')
    findings = _scan(tmp_path, **{"af_mqtt_bridge.py": code})[0]
    assert len(findings) == 1

    ok = _bridge(tmp_path, "\n\nclass Good:\n"
                           '    def fire(self, aid, iid):\n'
                           '        return self._publish(topic=FIRED_TOPIC, payload=self._envelope(automation_id=aid, instance_id=iid))\n')
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": ok})[0] == []


def test_envelope_augmented_then_published_is_green(tmp_path):
    """`publish_failed` 的真实形状：先由 `_envelope()` 赋值，再补 `error` 字段——合法。"""
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD})[0] == []


# ── 豁免：要带理由，且单独计数（铁律 #5）──────────────────────────

def test_exemption_needs_a_reason_and_is_counted(tmp_path):
    bad = 'def poke(client):\n    _mqtt.publish(client, "af/automation/fired", {})  # mqtt-writers: exempt()\n'
    assert len(_scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD, "af_live.py": bad})[0]) == 1

    good = 'def poke(client):\n    _mqtt.publish(client, "af/automation/fired", {})  # mqtt-writers: exempt(裁定 20261004 §二 的自检通道)\n'
    findings, stats = _scan(tmp_path, **{
        "af_mqtt_bridge.py": BRIDGE_HEAD, "af_live.py": good})
    assert findings == [] and stats["exempted"] == 1


# ── 锚点：读不到就 exit 2，不许静默全绿 ────────────────────────────

def _main(tmp_path: pathlib.Path, anchor_text: str) -> int:
    g = _module()
    anchor = tmp_path / "af_mqtt_bridge.py"
    anchor.write_text(anchor_text, encoding="utf-8")
    g.ANCHOR_FILE = anchor
    return g.main(["check_mqtt_writers.py", str(tmp_path)])


def test_missing_topic_constant_is_exit2(tmp_path):
    assert _main(tmp_path, BRIDGE_HEAD.replace('FIRED_TOPIC = "af/automation/fired"',
                                               'FIRE_TOPIC = "af/automation/fired"')) == 2


def test_renamed_envelope_is_exit2(tmp_path):
    assert _main(tmp_path, BRIDGE_HEAD.replace("def _envelope", "def _wrap")) == 2


def test_missing_observer_is_exit2(tmp_path):
    assert _main(tmp_path, BRIDGE_HEAD.replace("def observe_terminal", "def on_terminal")) == 2


# ── 真实 src：绿，且绿色行的数字是实测不是形容词 ───────────────────

def test_real_src_is_clean_with_measured_counts():
    g = _module()
    findings, stats = g.check(ROOT / "src")
    assert findings == []
    assert stats["writers"] >= 2 and stats["writer_files"] == 1
    assert stats["producers"] >= 2 and stats["producer_files"] == 1
    assert stats["payload_sites"] == stats["payload_ok"] >= 2
    assert stats["exempted"] == 0


def test_green_line_prints_the_counts_not_adjectives(tmp_path, capsys):
    """绿色行必须报数：写"只在/全部"而实际没数过，等于把未验证算成已验证（铁律 #5）。"""
    (tmp_path / "af_mqtt_bridge.py").write_text(BRIDGE_HEAD, encoding="utf-8")
    g = _module()
    g.ANCHOR_FILE = tmp_path / "af_mqtt_bridge.py"
    assert g.main(["check_mqtt_writers.py", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    _, stats = g.check(tmp_path)
    assert f"{stats['writers']} 处" in out and f"{stats['payload_ok']} 处" in out
    assert "只在" not in out and "全部" not in out

"""入向订阅门禁必须"能变红"（铁律 #8），红的是**下一个订阅入口**而不是已知那两处。

背景：契约表 §1.3 护栏与计划 第 1 步 ④ 写死了"AF 不订阅 `butler/inbox/*`（收件箱是 DB 的）"。
今天这条靠 `FORBIDDEN_SUBSCRIPTIONS` + `subscribe_topic()`/`handle_message()` 两处运行时判定 +
行为测试钉住。行为测试的结构缺口与 §二之二十二 出向那批一模一样：**它只测自己认识的入口**，
新加一个不查禁订族的 `self.client.subscribe("butler/inbox/#")` 一条测试都不会红。
本门钉的是判据的形状：调用在哪个文件、在哪个最内层函数、主题实参是什么、那个函数体里有没有守卫。

射程靠两条信号的**并集**（接收者是 `client`/`self.client`，或调用带 `qos=`）：只用接收者名会漏掉
`hub.subscribe(topic, qos=…)`，只用 `qos=` 会漏掉不传 QoS 的写法。进程内总线 `bus.subscribe(handler)`
不在射程——它既没有主题字符串也没有 QoS，契约面完全不同。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mqtt_subscriptions.py"

BRIDGE_HEAD = '''
INSIGHTS_TOPIC = "ma/insights"
FORBIDDEN_SUBSCRIPTIONS = ("butler/inbox/#",)


class Bridge:
    def start(self):
        self.client.on_message = self.handle_message
        self.client.subscribe(INSIGHTS_TOPIC, qos=QOS)

    def subscribe_topic(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS or topic.startswith("butler/inbox/"):
            return False
        self.client.subscribe(topic, qos=QOS)
        return True

    def handle_message(self, topic):
        if topic != INSIGHTS_TOPIC:
            return {"handled": False}
        return {"handled": True}
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_mqtt_subscriptions", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan(tmp_path: pathlib.Path, **files: str) -> tuple[list[str], dict]:
    for name, code in files.items():
        (tmp_path / name).write_text(code, encoding="utf-8")
    return _module().check(tmp_path)


# ── 本职：下一个订阅入口要红 ─────────────────────────────────────────

def test_known_good_shape_is_green(tmp_path):
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD})[0] == []


def test_known_good_shape_is_actually_counted(tmp_path):
    """绿色行得有数：两个站点若压根没进射程，"干净"就毫无意义。"""
    findings, stats = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD})
    assert findings == []
    assert (stats["sites"], stats["insights"], stats["guarded"], stats["exempted"]) == (2, 1, 1, 0)


def test_subscribe_outside_the_bridge_is_red(tmp_path):
    findings, stats = _scan(
        tmp_path,
        **{"af_mqtt_bridge.py": BRIDGE_HEAD,
           "af_extra.py": "class Hook:\n    def open(self):\n"
                          "        self.client.subscribe(INSIGHTS_TOPIC, qos=QOS)\n"})
    assert len(findings) == 1
    assert "af_extra.py:3" in findings[0]
    assert "桥外" in findings[0]
    assert stats["sites"] == 3


def test_qos_keyword_pulls_a_foreign_receiver_into_scope(tmp_path):
    """只认 `self.client` 会漏掉 `hub.subscribe(topic, qos=…)`——接收者名不是判据的全部。"""
    findings, _ = _scan(
        tmp_path,
        **{"af_mqtt_bridge.py": BRIDGE_HEAD,
           "af_extra.py": "def listen(hub, topic):\n    hub.subscribe(topic, qos=QOS)\n"})
    assert len(findings) == 1
    assert "af_extra.py:2" in findings[0]


def test_in_process_bus_subscribe_is_out_of_scope(tmp_path):
    """`bus.subscribe(handler)` 收的是回调不是主题，塞进射程只会把门变成噪音。"""
    findings, stats = _scan(
        tmp_path,
        **{"af_mqtt_bridge.py": BRIDGE_HEAD,
           "af_bus_like.py": "class Bus:\n"
                             "    def subscribe(self, key, handler):\n"
                             "        self._subs[key] = handler\n\n"
                             "def wire(bus, observer):\n"
                             "    bus.subscribe(observer)\n"
                             "    subscribe(observer)\n"})
    assert findings == []
    assert stats["sites"] == 2      # 只有桥内那两处


# ── 判据 B：动态主题必须当场过守卫 ───────────────────────────────────

def test_dynamic_topic_without_guard_is_red(tmp_path):
    findings, _ = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        self.client.subscribe(topic, qos=QOS)
"""})
    assert len(findings) == 1
    assert "`open_channel()`" in findings[0]
    assert "禁订族" in findings[0]


def test_dynamic_topic_with_guard_in_the_same_function_is_green(tmp_path):
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS:
            return False
        self.client.subscribe(topic, qos=QOS)
        return True
"""})[0] == []


def test_guard_must_live_in_the_innermost_function(tmp_path):
    """守卫在外层、订阅在闭包里 ⇒ 照样红：判的是"这个调用点当场被不被判"。"""
    findings, _ = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS:
            return False

        def do_it():
            self.client.subscribe(topic, qos=QOS)
        return do_it()
"""})
    assert len(findings) == 1
    assert "`do_it()`" in findings[0]


def test_keyword_form_topic_argument_is_recognized(tmp_path):
    """只认位置参数的话，`subscribe(topic=…)` 是一条静默放行的写法。"""
    red = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        self.client.subscribe(topic=topic, qos=QOS)
"""})[0]
    assert len(red) == 1 and "`open_channel()`" in red[0]
    green = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS:
            return False
        self.client.subscribe(topic=topic, qos=QOS)
        return True
"""})[0]
    assert green == []


def test_module_level_subscribe_is_red(tmp_path):
    findings, _ = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
client.subscribe("ma/alerts", qos=QOS)
"""})
    assert len(findings) == 1
    assert "模块顶层" in findings[0]


# ── 判据 C：收件箱族写死就红，带守卫也不给过 ─────────────────────────

def test_hardcoded_inbox_topic_is_red_even_behind_a_guard(tmp_path):
    findings, _ = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def relay_inbox(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS:
            return False
        self.client.subscribe("butler/inbox/queue", qos=QOS)
        return True
"""})
    assert len(findings) == 1
    assert "收件箱" in findings[0]
    assert "'butler/inbox/queue'" in findings[0]


def test_other_literal_topic_behind_a_guard_is_not_rule_C(tmp_path):
    """C 只认收件箱前缀：别的字面量主题走 B（有守卫即放行），门不该顺手扩权。"""
    assert _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def watch_presence(self, topic):
        if topic in FORBIDDEN_SUBSCRIPTIONS:
            return False
        self.client.subscribe("ma/presence", qos=QOS)
        return True
"""})[0] == []


# ── 豁免：要理由，且单独计入读数 ─────────────────────────────────────

def test_exemption_without_reason_stays_red(tmp_path):
    findings, stats = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        self.client.subscribe(topic, qos=QOS)  # mqtt-subscriptions: exempt()
"""})
    assert len(findings) == 1
    assert stats["exempted"] == 0


def test_exemption_with_reason_turns_green_and_is_counted(tmp_path):
    findings, stats = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD + """
class Bridge2:
    def open_channel(self, topic):
        # mqtt-subscriptions: exempt(只订对端自证的广播主题)
        self.client.subscribe(topic, qos=QOS)
"""})
    assert findings == []
    assert stats["exempted"] == 1


# ── 锚点：读不到就 exit 2，不许静默全绿 ──────────────────────────────

def _main_with_anchor(tmp_path: pathlib.Path, code: str) -> int:
    mod = _module()
    (tmp_path / "af_mqtt_bridge.py").write_text(code, encoding="utf-8")
    mod.ANCHOR_FILE = tmp_path / "af_mqtt_bridge.py"
    return mod.main(["check_mqtt_subscriptions.py", str(tmp_path)])


def test_missing_anchor_file_exits_2(tmp_path):
    mod = _module()
    mod.ANCHOR_FILE = tmp_path / "nope.py"
    assert mod.main(["x", str(tmp_path)]) == 2


def test_renamed_insights_topic_exits_2(tmp_path):
    assert _main_with_anchor(tmp_path, BRIDGE_HEAD.replace("INSIGHTS_TOPIC", "ASK_TOPIC")) == 2


def test_missing_forbidden_constant_exits_2(tmp_path):
    assert _main_with_anchor(
        tmp_path,
        BRIDGE_HEAD.replace('FORBIDDEN_SUBSCRIPTIONS = ("butler/inbox/#",)', "_ = 1")) == 2


def test_renamed_subscribe_entry_exits_2(tmp_path):
    assert _main_with_anchor(
        tmp_path, BRIDGE_HEAD.replace("def subscribe_topic(", "def attach_topic(")) == 2


# ── 真实 src 与绿色行读数 ────────────────────────────────────────────

def test_real_src_is_clean_and_counted():
    findings, stats = _module().check(ROOT / "src")
    assert findings == []
    assert stats["site_files"] == 1 and stats["sites"] == 2
    assert stats["insights"] == 1 and stats["guarded"] == 1
    assert stats["insights"] + stats["guarded"] == stats["sites"]
    assert stats["exempted"] == 0


def test_green_line_prints_measured_numbers_not_adjectives(tmp_path, capsys):
    mod = _module()
    (tmp_path / "af_mqtt_bridge.py").write_text(BRIDGE_HEAD, encoding="utf-8")
    mod.ANCHOR_FILE = tmp_path / "af_mqtt_bridge.py"
    assert mod.main(["x", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    _, stats = _scan(tmp_path, **{"af_mqtt_bridge.py": BRIDGE_HEAD})
    assert f"{stats['sites']} 处" in out and f"{stats['insights']} 处" in out
    for adjective in ("全部", "只在", "都经"):
        assert adjective not in out

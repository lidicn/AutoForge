"""窗后验收脚本必须"能变红"，且**缺项绝不被读成绿**（铁律 #5：EXEMPT ≠ VERIFIED）。

这个脚本是停机窗当天的验收工具，它自己的判据若错，代价是"整步被记成完成而实际没跑"。
所以本文件钉的是三件事：
1. ③ 的四条契约键、`ts` 的家庭墙钟口径、以及"事件类永不 retained"三条**各自单独可红**；
2. ④ 用 `msg.retain` 判 retained——一条现发的 `online` 不算；
3. 退出码映射：有 FAIL ⇒ 1，无 FAIL 但有 UNAVAILABLE ⇒ 2（且正文那行不是"通过"），全 PASS ⇒ 0。

凭据不经本脚本的手：连接与账密都走机制层 `homesdk.mqtt`，所以这里另有一条结构性断言——
脚本源码里不出现任何读环境凭据的写法，也就没有把口令打印进日志的路径。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_adm_window.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_adm_window", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(**over) -> argparse.Namespace:
    base = dict(base="http://127.0.0.1:8787", service="autoforge", wait=5,
                max_skew=900, http_timeout=5, container_up=False)
    base.update(over)
    return argparse.Namespace(**base)


def _msg(payload, *, retain=False):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return SimpleNamespace(payload=body, retain=retain, topic="x")


def _now_ts() -> str:
    """家庭墙钟口径：带 +08:00 偏移的当下时刻。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _good_fired(**over):
    data = {"trace_id": "t-1", "ts": over.pop("ts", _now_ts()),
            "automation_id": "a-1", "ref": "inst-1"}
    data.update(over)
    return data


def _patch_collect(monkeypatch, mod, value):
    monkeypatch.setattr(mod, "_collect", lambda topic, wait: value)
    return mod


# ── ③ fired：契约键 / 墙钟 / 不 retained，三条各自可红 ────────────────

def test_fired_pass_carries_measured_readings(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(_good_fired()))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.PASS
    assert "automation_id" in detail and "retain=False" in detail
    assert "差" in detail            # 偏差秒数是实测数字，不是形容词


def test_fired_missing_contract_key_is_fail(monkeypatch):
    mod = _module()
    data = _good_fired()
    data.pop("ref")
    _patch_collect(monkeypatch, mod, _msg(data))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.FAIL
    assert "ref" in detail and "实际键" in detail


def test_fired_extra_node_id_does_not_go_red(monkeypatch):
    """多余的 `node_id` 仍等 DCD 裁定（第 12 件第三问），本脚本不代为判红，但要把键集打出来。"""
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(_good_fired(node_id="n-9")))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.PASS
    assert "node_id" in detail


def test_fired_retained_is_fail(monkeypatch):
    """契约 §1.2：事件类永不 retained。retained 的 fired 会让新订阅者收到几天前的"有人回家"。"""
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(_good_fired(), retain=True))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.FAIL
    assert "retained" in detail


def test_fired_sim_anchor_ts_is_fail(monkeypatch):
    mod = _module()
    anchor = "2026-09-14T08:00:00+08:00"      # 仿真锚点当墙钟发出去的那族回归
    _patch_collect(monkeypatch, mod, _msg(_good_fired(ts=anchor)))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.FAIL
    assert "超过" in detail


def test_fired_naive_ts_is_fail(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(_good_fired(ts="2026-10-03T15:00:00")))
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.FAIL
    assert "带偏移" in detail


def test_fired_non_json_is_fail(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(b"not-json"))
    assert mod.item_fired(_args())[0] == mod.FAIL


def test_fired_silence_after_connect_is_fail_not_na(monkeypatch):
    """已连上 broker 却一条都没抓到 ⇒ 窗后没有真实事件流量，是 FAIL（不许退成"没跑"）。"""
    mod = _module()
    _patch_collect(monkeypatch, mod, None)
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.FAIL
    assert "没抓到" in detail


def test_fired_missing_env_is_unavailable(monkeypatch):
    mod = _module()

    def boom(topic, wait):
        raise mod.MqttEnvMissing("机制层缺配置：MQTT_HOST")

    monkeypatch.setattr(mod, "_collect", boom)
    verdict, detail = mod.item_fired(_args())
    assert verdict == mod.NA
    assert "MQTT_HOST" in detail


def test_fired_connection_error_is_fail(monkeypatch):
    """地址配好了却连不上 = 窗后 broker/服务没起，必须判红而不是"无从判定"。"""
    mod = _module()

    def boom(topic, wait):
        raise ConnectionRefusedError("111 refused")

    monkeypatch.setattr(mod, "_collect", boom)
    assert mod.item_fired(_args())[0] == mod.FAIL


# ── ④ status：retained 用标志判，不用"读到一条 online"判 ──────────────

def test_status_pass_requires_retain_flag(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(b"online", retain=True))
    verdict, detail = mod.item_status_retained(_args())
    assert verdict == mod.PASS
    assert "retained=True" in detail


def test_status_live_sent_online_is_not_retained(monkeypatch):
    """现发的一条 `online` 和 retained 快照长得一样——所以判据只能问 retain 标志。"""
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(b"online", retain=False))
    verdict, detail = mod.item_status_retained(_args())
    assert verdict == mod.FAIL
    assert "retained" in detail


def test_status_offline_value_is_fail(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, _msg(b"offline", retain=True))
    assert mod.item_status_retained(_args())[0] == mod.FAIL


def test_status_silence_is_fail(monkeypatch):
    mod = _module()
    _patch_collect(monkeypatch, mod, None)
    assert mod.item_status_retained(_args())[0] == mod.FAIL


# ── ② health：同一条"连不上"由 ① 的实测决定是红还是无从判定 ──────────

def test_health_unreachable_without_container_proof_is_unavailable(monkeypatch):
    mod = _module()

    def boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.item_health(_args(container_up=False))[0] == mod.NA


def test_health_unreachable_with_container_up_is_fail(monkeypatch):
    """容器确认在跑却连不上 = AF 起了但服务面没起，窗当天这一条必须拦得住。"""
    mod = _module()

    def boom(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert mod.item_health(_args(container_up=True))[0] == mod.FAIL


def test_health_reports_which_path_answered(monkeypatch):
    mod = _module()
    body = json.dumps({"ok": True, "readonly": True}).encode("utf-8")
    calls = []

    class Resp:
        status = 200

        def read(self, _n):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=None):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake)
    verdict, detail = mod.item_health(_args())
    assert verdict == mod.PASS
    assert "/api/health" in detail
    assert calls[0].endswith("/api/health")     # 真名先试，不等 404 再兜底


# ── ① compose ────────────────────────────────────────────────────────

def test_compose_without_docker_is_unavailable(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout: (-1, "", "ENOENT:docker"))
    assert mod.item_compose(_args())[0] == mod.NA


def test_compose_container_present_but_not_up_is_fail(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout: (0, "autoforge Restarting (1)", ""))
    assert mod.item_compose(_args())[0] == mod.FAIL


def test_compose_running_line_is_pass(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout: (0, "autoforge Up 3 minutes", ""))
    verdict, detail = mod.item_compose(_args())
    assert verdict == mod.PASS
    assert "Up 3 minutes" in detail             # 打印实测状态行，不写形容词


# ── 退出码映射：部分绿绝不印成通过 ───────────────────────────────────

def _stub_items(monkeypatch, mod, verdicts):
    for name, verdict in zip(("item_compose", "item_health", "item_fired",
                              "item_status_retained"), verdicts):
        monkeypatch.setattr(mod, name, lambda args, v=verdict: (v, "stub"))


def test_main_all_fail_free_pass_exits_zero(monkeypatch, capsys):
    mod = _module()
    _stub_items(monkeypatch, mod, [mod.PASS] * 4)
    assert mod.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert "全 PASS" in out
    assert "读数：PASS 4 / FAIL 0 / UNAVAILABLE 0" in out


def test_main_any_fail_exits_one(monkeypatch, capsys):
    mod = _module()
    _stub_items(monkeypatch, mod, [mod.PASS, mod.PASS, mod.FAIL, mod.PASS])
    assert mod.main(["verify"]) == 1
    assert "未通过" in capsys.readouterr().out


def test_main_unavailable_without_fail_exits_two_and_is_not_green(monkeypatch, capsys):
    mod = _module()
    _stub_items(monkeypatch, mod, [mod.PASS, mod.PASS, mod.NA, mod.NA])
    assert mod.main(["verify"]) == 2
    out = capsys.readouterr().out
    assert "EXEMPT ≠ VERIFIED" in out
    assert "该步可记账" not in out              # 两绿两没跑，绝不印成"可以记账"


def test_main_fail_outranks_unavailable(monkeypatch):
    """同时有 FAIL 与 UNAVAILABLE ⇒ 取 1：真实违例不能被"环境缺项"稀释成 2。"""
    mod = _module()
    _stub_items(monkeypatch, mod, [mod.NA, mod.FAIL, mod.NA, mod.PASS])
    assert mod.main(["verify"]) == 1


# ── 结构性：凭据不经本脚本 ───────────────────────────────────────────

def test_script_never_reads_credentials():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("os.environ", "getenv", "password", "PASSWD"):
        assert forbidden not in source, f"脚本里出现了 {forbidden}：凭据应只由机制层读取"


def test_uses_the_mechanism_layer_for_broker_config():
    """连接参数只能问 `homesdk.mqtt`，否则就是第二真源（键名/匿名回退各抄一套）。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "from homesdk import mqtt as hm" in source
    assert "hm.broker_settings()" in source and "hm.get_client(" in source


def test__collect_maps_missing_env_to_unavailable(monkeypatch):
    """机制层的 `MissingEnv` 在脚本里必须转成 UNAVAILABLE 语义，而不是冒成 traceback。"""
    mod = _module()
    from homesdk.config import MissingEnv
    monkeypatch.setattr("homesdk.mqtt.broker_settings",
                        lambda *a, **k: (_ for _ in ()).throw(MissingEnv("no host")))
    try:
        mod._collect("af/automation/fired", 1)
    except mod.MqttEnvMissing as exc:
        assert "MQTT_HOST" in str(exc) or "MissingEnv" in str(exc)
    else:
        raise AssertionError("缺 MQTT_HOST 时应抛 MqttEnvMissing，而不是继续往下连")

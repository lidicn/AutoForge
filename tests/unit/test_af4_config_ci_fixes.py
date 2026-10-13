"""第四期审计 7 条缺陷的修复测试（AF44-R / AF49-R / AF62 / AF63 / AF70 / AF71 / AF71b）。

每条对应一个缺陷，均含正向（修复后行为）+ 反向（修过头的回归对照）：

- AF44-R：`gh_ci_status._get` 跨域重定向必须摘 Authorization，同域重定向必须保留。
- AF49-R：`load_device_guard` 读失败 → 全拒注册表（fail-closed）；文件不存在仍 None。
- AF62：`ConfGrading.restore()` 逐模块降级——一个脏模块不拖垮其余四个挂载。
- AF63：HA `/api/states` 拉取失败留痕（warning + last_states_error），不再与"真空"同形。
- AF70：导出 bundle 显式登记 missing_confs；导入据此报错，不再报假"全成"。
- AF71：`list_graphs` / `graphs_by_tag` 的 get_tags 失败降级为空 tags，不炸掉列表。
- AF71b：`load_confidence` 容忍档位词 'high'/'medium'/'low'；报错带快照路径。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_adapters.ha import HAStateProvider, HATransport
from autoforge.af_runtime_ext import ConfGrading
from autoforge.af_scanner import DeviceGuardRegistry
from autoforge.af_service import list_graphs, graphs_by_tag
from autoforge.af_state import UnknownEntity
from autoforge.af_store import GraphStore, load_confidence

# scripts/ 不在包内，按文件路径导入（AF44-R）
import importlib.util
from pathlib import Path as _Path

_gh_ci_path = _Path(__file__).resolve().parents[2] / "scripts" / "gh_ci_status.py"
spec = importlib.util.spec_from_file_location("gh_ci_status", _gh_ci_path)
gh_ci_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gh_ci_status)


# ─────────────────────────────────────────────────────────────────────────────
# AF44-R：跨域摘 Authorization，同域保留
# ─────────────────────────────────────────────────────────────────────────────
class TestAF44CrossDomainStripAuth:
    def _handler_request(self, newurl: str):
        handler = gh_ci_status._StripAuthOnCrossRedirect()
        req = gh_ci_status.urllib.request.Request(
            "https://api.github.com/repos/x/y",
            headers={"Authorization": "token SUPERSECRET", "Accept": "application/vnd.github+json"},
        )
        return handler.redirect_request(req, None, 302, "Found", {}, newurl)

    def test_cross_domain_strips_authorization(self):
        """302 跳到外域（如日志签名地址）⇒ 新请求不带 Authorization。"""
        new = self._handler_request("https://logs.githubusercontent.com/abc123")
        assert new.has_header("Authorization") is False

    def test_same_domain_keeps_authorization(self):
        """同域 301/302（仓库重命名/转移的合法场景）⇒ 保留 Authorization，功能不回归。"""
        new = self._handler_request("https://api.github.com/repos/other/repo")
        assert new.has_header("Authorization") is True

    def test_get_uses_safe_opener(self):
        """`_get` 走的 opener 装了跨域摘头 handler（不再是裸 urlopen）。"""
        opener = gh_ci_status._safe_redirect_opener
        assert any(isinstance(h, gh_ci_status._StripAuthOnCrossRedirect) for h in opener.handlers)


# ─────────────────────────────────────────────────────────────────────────────
# AF49-R：load_device_guard 读失败 fail-closed
# ─────────────────────────────────────────────────────────────────────────────
class TestAF49DeviceGuardFailClosed:
    def test_deny_all_registry_blocks_everything(self):
        reg = DeviceGuardRegistry.deny_all_registry("device_acl.json 读取失败")
        assert reg.deny_all is True
        # 一切实体命中 Tier-0（与"rules 为空=放行"逐字可区分）
        assert reg.match_tier("light.x") == 0
        assert reg.match_tier("switch.anything") == 0
        assert reg.match_perm("light.x") == "-"

    def test_normal_registry_not_deny_all(self):
        """反向：普通注册表 deny_all=False，未命中仍放行（防拦太宽）。"""
        reg = DeviceGuardRegistry.from_acl({"light.x": "rw"})
        assert reg.deny_all is False
        assert reg.match_tier("light.unknown") is None

    def test_corrupt_acl_returns_deny_all_not_none(self, tmp_path):
        """坏 device_acl.json ⇒ 返回全拒注册表（不再是 None=fail-open）。"""
        store = GraphStore(str(tmp_path))
        (tmp_path / "device_acl.json").write_text("{ not json", encoding="utf-8")
        guard = gh_load_device_guard(store)
        assert guard is not None
        assert guard.deny_all is True

    def test_missing_file_still_none(self, tmp_path):
        """反向：文件不存在（未启用保护）仍 None，向后兼容。"""
        store = GraphStore(str(tmp_path))
        assert gh_load_device_guard(store) is None


def gh_load_device_guard(store):
    from autoforge import af_service as svc
    return svc.load_device_guard(store)


# ─────────────────────────────────────────────────────────────────────────────
# AF62：ConfGrading.restore() 逐模块降级
# ─────────────────────────────────────────────────────────────────────────────
class _FakeLog:
    def __init__(self):
        self.loaded = "UNTOUCHED"

    def load(self, data):
        self.loaded = data


class _FakeShadow:
    def __init__(self):
        self.log = _FakeLog()


class _FakeRecorder:
    def __init__(self):
        self.loaded = "UNTOUCHED"

    def load(self, data):
        self.loaded = data


class _FakeCanary:
    def __init__(self, fail: bool = False):
        self.loaded = "UNTOUCHED"
        self._fail = fail

    def load(self, data):
        if self._fail:
            raise TypeError("canary 收到脏结构（应是 dict，实际是 list）")
        self.loaded = data


class _FakeProposals:
    def __init__(self):
        self.loaded = "UNTOUCHED"

    def load(self, data):
        self.loaded = data


class TestAF62PerModuleDegrade:
    def _grading(self, tmp_path, canary_fail=False):
        return ConfGrading(
            recorder=_FakeRecorder(),
            exporter=None,
            shadow=_FakeShadow(),
            intervention=None,
            canary=_FakeCanary(fail=canary_fail),
            proposals=_FakeProposals(),
            persist_dir=str(tmp_path),
        )

    def test_one_dirty_module_does_not_kill_others(self, tmp_path):
        """feedback.json 字段脏（是 list，.get 炸）+ canary.load 炸 ⇒
        shadow/proposals 照常恢复，两个坏模块记进 restore_corrupt。"""
        # feedback.json 合法 JSON 但形状错（list）⇒ .get 抛 AttributeError
        (tmp_path / "feedback.json").write_text("[1, 2, 3]", encoding="utf-8")
        # canary_state.json 合法，但 load 主动炸
        (tmp_path / "canary_state.json").write_text("{}", encoding="utf-8")

        g = self._grading(tmp_path, canary_fail=True)
        g.restore()  # 修复前：这里直接穿出 install()，五个模块全不挂载

        # 没坏的模块照常恢复
        assert g.shadow.log.loaded == []          # shadow_log.json 不存在 → 空
        assert g.proposals.loaded == {}           # proposals 空 dict
        # 坏的两个被记账
        assert "feedback.json" in g.restore_corrupt
        assert "canary_state.json" in g.restore_corrupt

    def test_all_clean_leaves_ledger_empty(self, tmp_path):
        """反向：全部文件正常 ⇒ restore_corrupt 空，四个模块都恢复。"""
        (tmp_path / "shadow_log.json").write_text("[]", encoding="utf-8")
        (tmp_path / "feedback.json").write_text('{"events": []}', encoding="utf-8")
        (tmp_path / "canary_state.json").write_text("{}", encoding="utf-8")
        (tmp_path / "proposals.json").write_text("{}", encoding="utf-8")
        g = self._grading(tmp_path)
        g.restore()
        assert g.restore_corrupt == []
        assert g.shadow.log.loaded == []
        assert g.recorder.loaded == []
        assert g.canary.loaded == {}
        assert g.proposals.loaded == {}


# ─────────────────────────────────────────────────────────────────────────────
# AF63：HA 拉取失败留痕
# ─────────────────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _BoomOpener:
    def __init__(self, exc):
        self._exc = exc

    def __call__(self, req, timeout=10):
        raise self._exc


class TestAF63HAPullFailure:
    def test_pull_failure_records_error_not_silent_empty(self):
        transport = HATransport(
            base_url="http://ha.local:8123", token="t",
            opener=_BoomOpener(OSError("conn refused")),
        )
        out = transport.all_states()
        assert out == {}                                  # 返回仍是空 dict（契约不变）
        assert transport.last_states_error is not None    # 但原因被显式留痕（出参型）
        assert "conn refused" in transport.last_states_error

    def test_pull_success_clears_error(self):
        transport = HATransport(
            base_url="http://ha.local:8123", token="t",
            opener=lambda req, timeout=10: _FakeResp(
                [{"entity_id": "light.x", "state": "on", "attributes": {}}]
            ),
        )
        out = transport.all_states()
        assert "light.x" in out
        assert transport.last_states_error is None

    def test_snapshot_attaches_pull_failure_reason(self):
        """HA 连不上时 snapshot 仍 fail-closed 抛 UnknownEntity，但 __cause__ 带上
        'HA 连不上'——不再与'实体真漂移'同形。"""
        transport = HATransport(
            base_url="http://ha.local:8123", token="t",
            opener=_BoomOpener(OSError("conn refused")),
        )
        sp = HAStateProvider(transport=transport)
        with pytest.raises(UnknownEntity) as ei:
            sp.snapshot(["light.x"])
        assert ei.value.__cause__ is not None
        assert "HA 状态拉取失败" in str(ei.value.__cause__)

    def test_snapshot_normal_entity(self):
        transport = HATransport(
            base_url="http://ha.local:8123", token="t",
            opener=lambda req, timeout=10: _FakeResp(
                [{"entity_id": "light.x", "state": "on", "attributes": {"brightness": 80}}]
            ),
        )
        sp = HAStateProvider(transport=transport)
        snap = sp.snapshot(["light.x"])
        assert snap.values["light.x"] == "on"


# ─────────────────────────────────────────────────────────────────────────────
# AF70：导出显式 missing_confs，导入报错
# ─────────────────────────────────────────────────────────────────────────────
def _graph_dict():
    return {
        "ir_version": "0.2.1", "id": "g", "name": "g", "version": 1,
        "mode": "single",
        "nodes": [
            {"id": "o", "kind": "on",
             "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "c", "kind": "pass"},
        ],
        "edges": [{"from": "o", "to": "c", "kind": "then"}],
    }


class TestAF70BackupConfIntegrity:
    def _seed(self, root):
        store = GraphStore(str(root))
        store.save_version_raw("a1", _graph_dict(), 1, "2026-01-01T00:00:00Z", "")
        store.save_version_raw("a2", _graph_dict(), 1, "2026-01-01T00:00:00Z", "")
        store.save_conf_raw("a1", {"name": "a1", "conf": {"values": {"auto1": 0.8}, "samples": {}}})
        store.save_conf_raw("a2", {"name": "a2", "conf": {"values": {"auto2": 0.7}, "samples": {}}})
        return store

    def test_unreadable_conf_recorded_in_missing_confs(self, tmp_path):
        store = self._seed(tmp_path)
        # 把 a2 的 conf.json 写坏（存在但读不出）
        for p in store.root.glob("*.conf.json"):
            if "a2" in p.name:
                p.write_text("{ broken", encoding="utf-8")
        bundle = store.export_bundle()
        assert "a2" in bundle["missing_confs"]     # 不再静默 continue
        assert "a1" not in bundle["missing_confs"]

    def test_import_reports_missing_conf_not_fake_success(self, tmp_path):
        src = self._seed(tmp_path / "src")
        for p in src.root.glob("*.conf.json"):
            if "a2" in p.name:
                p.write_text("{ broken", encoding="utf-8")
        bundle = src.export_bundle()

        dst = GraphStore(str(tmp_path / "dst"))
        rep = dst.import_bundle(bundle)
        # 三条归档导入了，但报告必须说出 a2 的 conf 没跟着来（不再报假"全成"）
        assert "a2" in [e["name"] for e in rep["errors"]]
        assert rep["ok"] is False

    def test_no_missing_confs_when_all_readable(self, tmp_path):
        """反向：conf 都正常 ⇒ missing_confs 空，导入 ok。"""
        src = self._seed(tmp_path / "src")
        bundle = src.export_bundle()
        assert bundle["missing_confs"] == []
        dst = GraphStore(str(tmp_path / "dst"))
        rep = dst.import_bundle(bundle)
        assert rep["errors"] == []
        assert rep["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# AF71：get_tags 失败降级为空 tags
# ─────────────────────────────────────────────────────────────────────────────
class TestAF71TagsDegrade:
    def test_list_graphs_survives_tag_failure(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.save_version_raw("g1", _graph_dict(), 1, "2026-01-01T00:00:00Z", "")
        store.get_tags = lambda name: (_ for _ in ()).throw(RuntimeError("tags.json 炸了"))
        res = list_graphs(store)
        assert res["ok"] is True
        assert len(res["items"]) == 1
        assert res["items"][0]["tags"] == []   # 降级为空，不炸掉整个列表

    def test_graphs_by_tag_survives_tag_failure(self, tmp_path):
        store = GraphStore(str(tmp_path))
        store.save_version_raw("g1", _graph_dict(), 1, "2026-01-01T00:00:00Z", "")
        store.get_tags = lambda name: (_ for _ in ()).throw(RuntimeError("tags.json 炸了"))
        res = graphs_by_tag(store, "home")
        assert res["ok"] is True

    def test_normal_tags_still_returned(self, tmp_path):
        """反向：tags 正常时照常返回，不是无脑清空。"""
        store = GraphStore(str(tmp_path))
        store.save_version_raw("g1", _graph_dict(), 1, "2026-01-01T00:00:00Z", "")
        store.set_tags("g1", ["home"])
        res = list_graphs(store)
        assert res["items"][0]["tags"] == ["home"]


# ─────────────────────────────────────────────────────────────────────────────
# AF71b：load_confidence 容忍档位词 + 报错带路径
# ─────────────────────────────────────────────────────────────────────────────
class TestAF71bConfidenceCoerce:
    def test_band_strings_coerced(self):
        store = load_confidence({"values": {"a": "high", "b": "medium", "c": "low", "d": 0.5}})
        assert store.values["a"] == pytest.approx(0.85)
        assert store.values["b"] == pytest.approx(0.60)
        assert store.values["c"] == pytest.approx(0.30)
        assert store.values["d"] == pytest.approx(0.5)

    def test_plain_floats_unchanged(self):
        store = load_confidence({"values": {"a": 0.9, "b": 0.1}})
        assert store.values["a"] == pytest.approx(0.9)

    def test_load_conf_wraps_error_with_path(self, tmp_path):
        """load_conf 遇到真不可解析的值，报错带快照路径（不再是裸
        'could not convert string to float'）。"""
        store = GraphStore(str(tmp_path))
        (tmp_path / "g.conf.json").write_text(
            json.dumps({"name": "g", "conf": {"values": {"auto1": "xyzzy"}, "samples": {}}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="置信度快照读取失败"):
            store.load_conf("g")

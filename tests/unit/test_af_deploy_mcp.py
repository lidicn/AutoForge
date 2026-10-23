"""af_deploy / af_deploy_status MCP 工具（v1.13.3，autoflow 照搬）的判定腿。

两枚工具都挂 admin scope；`af_deploy` 与 WebUI 升级按钮写**同一枚** marker
（`af_api.submit_update_request` 唯一实现——"不写第二份 marker 格式"铁约束）；
`af_deploy_status` 读 marker 存在性 + `.update_result.json` 最近结果。

失败形状（都会静默不报错，必须钉住）：
① 工具表 scope 漏写 admin ⇒ expert 令牌（配对预设）就能发起部署——agent 自部署不设防；
② marker 格式抄了第二份 ⇒ HTTP 面与 MCP 面两处口径漂移（ref 白名单/防重/原子写三闸
  只在一面生效），发布线门判据 D 只盯 af_api 一面、MCP 侧的漂移它看不见；
③ status 读不回 result ⇒ agent 无法判断部署完成与否，只能盲等；
④ requested_by 不记主体 ⇒ 部署审计断链（谁点的升级查不到）。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoforge.af_store import GraphStore
from autoforge.af_mcp import TOOLS, dispatch

_ADMIN = {"subject": "dev-agent", "scopes": ["read", "write", "live", "admin"]}
_EXPERT = {"subject": "paired-agent", "scopes": ["read", "write", "live"]}
_READ = {"subject": "ro", "scopes": ["read"]}


def _scopes() -> dict[str, str | None]:
    return {t[0]: t[4] for t in TOOLS}


def test_deploy_tools_are_registered_with_admin_scope():
    """① 两枚工具必须在 TOOLS 表且 scope=admin——漏写=配对预设（expert）即可发起部署。"""
    scopes = _scopes()
    assert scopes.get("af_deploy") == "admin", scopes.get("af_deploy")
    assert scopes.get("af_deploy_status") == "admin", scopes.get("af_deploy_status")


def test_expert_token_gets_mcp_noaccess_on_deploy(tmp_path):
    """expert（配对签发预设）调 af_deploy 必须被 _guard 拒（缺 admin scope）。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_deploy", {"ref": "master"}, store, _EXPERT)
    assert is_error is True
    body = json.loads(content[0]["text"])
    assert body["ok"] is False
    assert "admin" in body["message"]
    assert "paired-agent" not in json.dumps(body)
    # marker 没写（被拒就不该有副作用）
    assert not (tmp_path / ".update_request.json").exists()


def test_read_token_and_no_auth_get_denied_on_deploy(tmp_path):
    """read 令牌与无身份两档同样拒（admin 门对非 admin 一律 NOACCESS）。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_deploy", {"ref": "master"}, store, _READ)
    assert is_error is True
    content, is_error = dispatch("af_deploy", {"ref": "master"}, store, None)
    assert is_error is True
    assert not (tmp_path / ".update_request.json").exists()


def test_admin_token_deploy_writes_the_shared_marker(tmp_path):
    """② admin 令牌成功写 marker——键名/白名单/防重与 HTTP 面同一份实现。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_deploy", {"ref": "master"}, store, _ADMIN)
    assert is_error is False
    body = json.loads(content[0]["text"])
    assert body["ok"] is True
    marker = tmp_path / ".update_request.json"
    assert marker.exists()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["ref"] == "master"
    assert payload["requested_by"] == "dev-agent"  # ④ 主体归属进 marker
    assert payload["version"]  # 当前版本号非空


def test_deploy_marker_shape_matches_http_contract(tmp_path):
    """marker 键集必须等于 HTTP 面契约（ref/requested_at/requested_by/version）。

    af_api.submit_update_request 是唯一实现，两面对撞同一函数——这条钉的是
    "不写第二份 marker 格式"：如果有人在 af_mcp 里另拼 dict，键集漂移当场红。
    """
    from autoforge.af_api import submit_update_request

    store = GraphStore(tmp_path)
    dispatch("af_deploy", {"ref": "v1.13.3"}, store, _ADMIN)
    payload = json.loads((tmp_path / ".update_request.json").read_text(encoding="utf-8"))
    # 直接调共用函数写一枚，键集必须一致
    other = tmp_path / "other"
    other.mkdir()
    ok, _ = submit_update_request(str(other), "master", "probe", "0.0.0")
    assert ok
    ref_payload = json.loads((other / ".update_request.json").read_text(encoding="utf-8"))
    assert set(payload.keys()) == set(ref_payload.keys()) == {"ref", "requested_at", "requested_by", "version"}


def test_deploy_rejects_illegal_ref_and_inflight_dup(tmp_path):
    """ref 白名单（400 语义）与进行中防重（409 语义）在 MCP 面同样生效。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_deploy", {"ref": "main"}, store, _ADMIN)
    assert is_error is False  # 业务拒绝（ok=False），不是协议错误
    body = json.loads(content[0]["text"])
    assert body["ok"] is False
    assert "vX.Y.Z" in body["error"]
    assert not (tmp_path / ".update_request.json").exists()

    # 进行中防重：已有一枚 marker 再提一枚 ⇒ 拒
    (tmp_path / ".update_request.json").write_text('{"ref": "master"}', encoding="utf-8")
    content, is_error = dispatch("af_deploy", {"ref": "master"}, store, _ADMIN)
    body = json.loads(content[0]["text"])
    assert body["ok"] is False
    assert "进行中" in body["error"]


def test_deploy_status_reads_pending_and_result(tmp_path):
    """③ status 读回 pending/requested/result/current_version 四格。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_deploy_status", {}, store, _ADMIN)
    assert is_error is False
    body = json.loads(content[0]["text"])
    assert body["ok"] is True
    assert body["pending"] is False and body["requested"] is None and body["result"] is None
    assert body["current_version"]  # 版本号非空（对端比对用）

    # 写 marker + result 后再读：两格都回来
    (tmp_path / ".update_request.json").write_text(
        json.dumps({"ref": "master", "requested_by": "dev-agent", "version": "1.13.3"}), encoding="utf-8"
    )
    (tmp_path / ".update_result.json").write_text(
        json.dumps({"ok": True, "from": "aaa", "to": "bbb", "to_tag": "v1.13.3", "changelog": ["x"]}),
        encoding="utf-8",
    )
    content, _ = dispatch("af_deploy_status", {}, store, _ADMIN)
    body = json.loads(content[0]["text"])
    assert body["pending"] is True
    assert body["requested"]["ref"] == "master"
    assert body["result"]["to_tag"] == "v1.13.3"


def test_deploy_status_denied_without_admin(tmp_path):
    """status 也是 admin 门——expert/read/无身份三档全拒。"""
    store = GraphStore(tmp_path)
    for ident in (_EXPERT, _READ, None):
        content, is_error = dispatch("af_deploy_status", {}, store, ident)
        assert is_error is True, ident

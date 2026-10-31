"""审计第七轮（ee14d6d）判据批：AF-701 残缺身份 + AF-402 pattern 执行 + 卫生项。

出处 `E:/NAS/QA/AF_Tester/reports/AF_code_audit_ee14d6d.md` §5：
- AF-701 🔴：subject 为空的合法 write 令牌实测可改他人归档标签/批量启停/删除——
  `_assert_archive_mutator` 的"未携带身份不阻断"豁免被空主体令牌穿过；
- AF-402：`"pattern": "^[0-9]{8}$"` 三处声明、dispatch 零执行——非数字码与合规码
  同一条四因合一文案，声明是装饰；
- 卫生：`af_scanner.py` 的 `__import__("re")` 无必要动态导入（round-019 唯一真新增）；
  README 被上批 sed 翻转成 LF（337 行假 diff）。

修法（对齐 D5 判例"空 owner 归档仅 owner 面可改，fail-closed"同族）：
- MCP `_guard` + HTTP `requires()` 各加一道：**要 scope 就必须有 subject**；
- dispatch 补 `_pattern_violations`：auth_code 的 pattern 拒绝与四因合一逐字同形
  （格式错也是"无效"的一种，⛔ 不外传具体哪一因）；pattern 声明改 `{6,8}`
  兼容 6 位老码（af_auth._rand6 口径）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_ir import Graph, load_automation
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore

AGENT_B = {"subject": "agent-b", "scopes": ["read", "write"]}
#: AF-701 的主角：带 write scopes、subject 为空的"残缺身份"令牌
ANON_WRITE = {"subject": "", "scopes": ["read", "write", "live"]}


def _make_ir(aid: str) -> dict:
    return {"ir_version": "0.3.0", "id": aid, "name": aid, "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                       "action": "light.turn_on", "params": {"entity_id": "light.x"}}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}]}


def _seed(store: GraphStore) -> None:
    store.save(Graph([load_automation(_make_ir("theirs"))]), "theirs", tags=["prod"], owner="agent-b")


def _call(tool: str, args: dict, store: GraphStore, who: dict) -> dict:
    content, _ = dispatch(tool, args, store, who)
    return json.loads(content[0]["text"])


# ── AF-701：残缺身份（空 subject + write scopes）─────────────────

@pytest.mark.parametrize("tool,args", [
    ("af_set_tags", {"name": "theirs", "tags": ["hijacked"]}),
    ("af_delete_graph", {"name": "theirs"}),
    ("af_enable_by_tag", {"tag": "prod", "enabled": False}),
])
def test_anonymous_write_token_cannot_mutate(tmp_path, tool, args):
    """🔴 审计实测复现位：空主体 write 令牌曾可改他人归档——现在三路全拒。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call(tool, args, store, ANON_WRITE)
    assert body["ok"] is False, f"{tool} 被残缺身份穿透（AF-701）：{body}"
    assert "subject" in str(body.get("message") or ""), "拒绝文案要指向病灶（缺 subject）"


def test_anonymous_write_rejection_leaves_disk_untouched(tmp_path):
    """拒绝不是嘴上说说——盘上标签原样。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    _call("af_set_tags", {"name": "theirs", "tags": ["hijacked"]}, store, ANON_WRITE)
    assert store.get_tags("theirs") == ["prod"], "残缺身份改动了盘上标签（AF-701 实害）"


def test_named_owner_still_mutates_their_own(tmp_path):
    """误伤面：归属者本人照常（AF-701 收口不许把合法路径一起关掉）。"""
    store = GraphStore(str(tmp_path))
    _seed(store)
    body = _call("af_set_tags", {"name": "theirs", "tags": ["ok"]}, store, AGENT_B)
    assert body["ok"] is True and store.get_tags("theirs") == ["ok"]


def test_scopeless_tools_unaffected_by_subject_gate(tmp_path):
    """scope=None 的公开工具（af_health）不受 subject 闸影响——它不需要身份。"""
    body = _call("af_health", {}, GraphStore(str(tmp_path)), ANON_WRITE)
    assert body["ok"] is True


def test_http_empty_subject_token_is_refused(tmp_path):
    """HTTP 面同一道闸：AF_ACCESS_TOKENS 里 subject 为空的令牌拿不到 write 域。"""
    from fastapi.testclient import TestClient
    import os
    os.environ["AUTOFORGE_TOKENS"] = json.dumps({
        "tok-empty": {"subject": "", "scopes": ["read", "write", "live"]},
        "tok-ok": {"subject": "owner", "scopes": ["read", "write", "live", "admin"]},
    })
    try:
        from autoforge.af_api import build_app
        client = TestClient(build_app(store_root=str(tmp_path)))
        r = client.post("/api/graphs/tags", json={"name": "x", "tags": ["t"]},
                        headers={"Authorization": "Bearer tok-empty"})
        assert r.status_code == 403, f"HTTP 面残缺身份穿透（AF-701）：{r.status_code} {r.text[:120]}"
        assert "subject" in r.text
        r2 = client.get("/api/graphs", headers={"Authorization": "Bearer tok-ok"})
        assert r2.status_code == 200, "有名令牌被误伤"
    finally:
        os.environ.pop("AUTOFORGE_TOKENS", None)


# ── AF-402：pattern 声明即契约 ──────────────────────────────────

_LIVE_IR = {"ir_version": "0.3.0", "id": "g", "name": "g", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on",
                       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                      {"id": "p", "kind": "pass"}],
            "edges": [{"from": "a", "to": "p", "kind": "then"}]}


def test_non_numeric_code_rejected_at_pattern_layer(tmp_path):
    """字母码在 dispatch 的 pattern 闸被拒——文案与四因合一逐字同形（不泄漏哪一因）。"""
    body = _call("af_live_run", {"ir": _LIVE_IR, "live_allow": ["light.x"], "confirm": True,
                                 "auth_code": "abcdefgh"}, GraphStore(str(tmp_path)),
                 {"subject": "qa", "scopes": ["read", "write", "live"]})
    assert body["ok"] is False and body["code"] == "ADM_ERR_AUTH_REQUIRED"
    msg = str(body.get("message") or "")
    assert "之一" in msg, f"必须保持四因合一形状：{msg}"
    assert "abcdefgh" not in msg, "拒绝文案不许回显码值"


def test_six_digit_legacy_code_passes_pattern(tmp_path):
    """6 位老码仍可 validate/consume（af_auth._rand6 口径）——pattern 不得误伤存量。"""
    body = _call("af_live_run", {"ir": _LIVE_IR, "live_allow": ["light.x"], "confirm": True,
                                 "auth_code": "123456"}, GraphStore(str(tmp_path)),
                 {"subject": "qa", "scopes": ["read", "write", "live"]})
    # 过了 pattern 闸 → 落到下游闸（LIVE_ENABLED 等），绝不再是 pattern/格式拒绝
    msg = str(body.get("message") or "")
    assert "格式" not in msg, f"6 位老码被 pattern 误伤：{msg}"


def test_declared_patterns_are_6_8_compatible():
    """三处 auth_code 的 pattern 声明必须兼容 6 位老码（AF-402 修复不能变误伤）。"""
    for tool in TOOLS:
        props = tool[2].get("properties") or {}
        ac = props.get("auth_code")
        if ac and ac.get("pattern"):
            assert ac["pattern"] == "^[0-9]{6,8}$", (
                f"{tool[0]} 的 auth_code pattern={ac['pattern']!r}——8 位独占会误伤 6 位老码"
            )


# ── 卫生项：无必要动态导入（round-019 唯一真新增站点）────────────

def test_scanner_has_no_dynamic_import():
    """`__import__("re")` 已换回顶层 import——静态正则用模块级导入（规范化门读数归零）。"""
    import autoforge.af_scanner as scanner
    src = open(scanner.__file__, encoding="utf-8").read()
    assert "__import__(" not in src, "af_scanner 又出现动态导入（round-019 唯一真新增回来了）"


# ── README 行尾（AF-502 第三次复发的本批份额）────────────────────

def test_readme_keeps_crlf():
    """README 原生 CRLF（337 行）——上批 sed 把它翻转成 LF 造成 337 行假 diff，已复原。"""
    raw = open("README.md", "rb").read()
    crlf = raw.count(b"\r\n")
    assert crlf == 337 and crlf == raw.count(b"\n"), (
        f"README 行尾又被翻转（CRLF {crlf}）——sed/脚本改 README 后必须核行尾"
    )

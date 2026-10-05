"""裁定 20261004 §一 Q2=B：MCP 面「没配令牌 = 一切 scope 放行」改成默认拒绝。

判据形状与 `read←{read,write}` 那条同型——**两个方向各钉一次**：不给身份必须拒、
显式 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 必须放行。只钉一侧的门，把另一侧改坏时不会红。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from autoforge import af_mcp
from autoforge.af_api import build_app
from autoforge.af_auth import SCOPES
from autoforge.af_mcp import MCP_NOACCESS, allow_no_token, dispatch, serve_mcp
from autoforge.af_store import GraphStore

FLAG = 'AUTOFORGE_MCP_ALLOW_NO_TOKEN'
SCOPED_TOOL = 'af_set_tags'          # write 域：不给身份必须拒
PUBLIC_TOOL = 'af_health'            # scope=None：默认拒绝不该打断公开面
_ALL = {'subject': 'test-all', 'scopes': sorted(SCOPES)}


def _text(content) -> str:
    return content[0]['text']


# ---------- 默认档：不给身份就是拒 ----------

def test_no_identity_refuses_a_scoped_tool(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    content, is_error = dispatch(SCOPED_TOOL, {'name': 'demo', 'tags': ['x']}, GraphStore(str(tmp_path)), None)
    assert is_error is True
    assert '默认拒绝' in _text(content)
    assert FLAG in _text(content)              # 拒绝的话必须说出"怎么显式放行"，否则只剩一句不解释


def test_explicit_prototype_flag_restores_pass_through(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, '1')
    content, is_error = dispatch(SCOPED_TOOL, {'name': 'demo', 'tags': ['x']}, GraphStore(str(tmp_path)), None)
    assert '默认拒绝' not in _text(content)     # 放行档走到业务层（图不存在是另一件事）


def test_public_tools_still_answer_without_identity(tmp_path, monkeypatch):
    """默认拒绝只收需鉴权那一族；公开面（含 health）不被这道改动打断。"""
    monkeypatch.delenv(FLAG, raising=False)
    content, is_error = dispatch(PUBLIC_TOOL, {}, GraphStore(str(tmp_path)), None)
    assert is_error is False, _text(content)
    assert _json(content)['ok'] is True


def test_guard_refuses_even_when_scopes_would_have_matched_before(tmp_path, monkeypatch):
    """`current` 给了身份但缺 scope ⇒ 走的是老那条拒绝分支，消息不能变成"没有身份"。"""
    monkeypatch.delenv(FLAG, raising=False)
    content, is_error = dispatch(SCOPED_TOOL, {'name': 'd', 'tags': ['x']}, GraphStore(str(tmp_path)), MCP_NOACCESS)
    assert is_error is True
    assert '缺少' in _text(content)


# ---------- 开关本身的取值 ----------

@pytest.mark.parametrize('raw_value,expected', [(None, False), ('', False), ('0', False), ('true', False), (' 1 ', True), ('1', True)])
def test_opt_in_reads_only_the_documented_value(monkeypatch, raw_value, expected):
    """放行入口只认 `1`：把 "true" 当开关会让"我配了放行"这句话在部署里静默失效。"""
    if raw_value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, raw_value)
    assert allow_no_token() is expected


# ---------- 启动横幅与 whoami 的 note：不许把默认档印成"全放行" ----------

def test_startup_banner_states_the_default_refusal(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr('sys.stdin', [])
    serve_mcp(root=str(tmp_path))
    err = capsys.readouterr().err
    assert '默认拒绝' in err and 'AUTOFORGE_MCP_ALLOW_NO_TOKEN' in err
    assert '未启用鉴权（全放行' not in err        # 旧横幅那句话现在是不实陈述


def test_startup_banner_marks_the_prototype_as_an_explicit_choice(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(FLAG, '1')
    monkeypatch.setattr('sys.stdin', [])
    serve_mcp(root=str(tmp_path))
    err = capsys.readouterr().err
    assert '原型档，显式选择' in err


def test_whoami_note_matches_the_档_it_is_in(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    content, _ = dispatch('af_whoami', {}, GraphStore(str(tmp_path)), None)
    assert '默认拒绝' in _json(content)['note']
    monkeypatch.setenv(FLAG, '1')
    content, _ = dispatch('af_whoami', {}, GraphStore(str(tmp_path)), None)
    assert '原型全放行' in _json(content)['note']


# ---------- 同一道门的第二张脸：HTTP `/mcp` 也必经同一个 _guard ----------

def test_http_mcp_face_shares_the_same_default(tmp_path, monkeypatch):
    """`AF_ALLOW_NOAUTH=1` 的逃生舱放行 HTTP 层，但不越权放行工具层——两面共用一个 `_guard`，
    否则"HTTP 面拒、MCP 面放"就是这次裁定点名要消掉的那种两面对不上。"""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv('AF_ALLOW_NOAUTH', '1')
    client = TestClient(build_app(str(tmp_path)))
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': SCOPED_TOOL, 'arguments': {'name': 'demo', 'tags': ['x']}}}
    out = client.post('/mcp', json=body).json()
    assert out['result']['isError'] is True
    assert '默认拒绝' in out['result']['content'][0]['text']


def _json(content) -> dict:
    return json.loads(_text(content))

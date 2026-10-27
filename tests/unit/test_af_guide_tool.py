"""af_guide（起草指引工具，体验者 2026-10-27 诉求）：MCP 面能自取语法，不靠人肉传文档。

背景：`skills/autoforge.md` 一直存在，但 MCP 工具面不含语法 ⇒ 体验者靠猜写
`then <动作>` 这类不存在的写法，第一步就卡住（原话「mcp里需要提供一个skill指引」）。
本文件钉四条：全文 / 小节命中 / 未命中回退不抛错 / 文件缺失诚实报错（⛔ 不假绿）。
注册面再加一条：af_guide 公开无鉴权（scope=None）——没配令牌的 agent 第一步也能拿到指引。
"""

from __future__ import annotations

import pytest

from autoforge.af_mcp import TOOLS, _guide_path, _t_guide


def test_guide_full_text():
    r = _t_guide(None, {})
    assert r["ok"] is True
    assert "AF-Spec" in r["content"] and "af_draft" in r["content"], "全文要能盖到两条起草路"
    assert r["source"], "必须报正文来源（agent 可核对是不是它以为的那份）"


def test_guide_section_filter_hits():
    r = _t_guide(None, {"section": "排错"})
    assert r["ok"] is True and r["section"] == "排错"
    assert "SpecError" in r["content"] or "DEVICE_ACL" in r["content"]
    assert len(r["content"]) < len(_t_guide(None, {})["content"]), "命中一节必须比全文短"
    assert r["sections"], "目录要在（未命中时 agent 能照着挑）"


def test_guide_missing_section_falls_back_not_raises():
    """未命中小节回全文 + 目录，⛔ 不抛错——抛错等于把"猜小节名"的负担丢回 agent。"""
    r = _t_guide(None, {"section": "不存在xyz"})
    assert r["ok"] is True and r.get("section") is None and r.get("note")
    assert "AF-Spec" in r["content"] and r["sections"]


def test_guide_missing_file_is_honest(tmp_path, monkeypatch):
    """指引不在位时回 ok:False + 指明路径（部署镜像漏 COPY skills/ 就是这个形状）。"""
    monkeypatch.setenv("AF_GUIDE_PATH", str(tmp_path / "nope" / "autoforge.md"))
    r = _t_guide(None, {})
    assert r["ok"] is False and "autoforge.md" in r["error"]


def test_guide_registered_public_and_consistent():
    """注册面三查：在 TOOLS 里 / scope=None（无鉴权可取）/ inputSchema 声明 section。

    参数声明↔handler 消费的「声明即契约」对账由 scripts/check_mcp_arg_schemas.py 全表覆盖，
    这里钉的是本工具的两条个体属性（可见性与鉴权档）。
    """
    tool = next((t for t in TOOLS if t[0] == "af_guide"), None)
    assert tool, "af_guide 必须真注册进 TOOLS"
    _name, desc, schema, _fn, scope = tool
    assert scope is None, "指引是公开面：未配令牌的 agent 第一步也要能拿到"
    assert "section" in schema["properties"]
    assert "起草" in desc or "必读" in desc, "描述要能让 agent 扫 tools/list 时知道何时调它"


def test_guide_path_prefers_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_GUIDE_PATH", str(tmp_path / "x.md"))
    assert _guide_path() == tmp_path / "x.md"


def test_guide_path_finds_repo_skill_without_env(monkeypatch):
    """不靠 cwd：包内/仓库根两候选推导，开发态必须命中仓里那份真文件。"""
    monkeypatch.delenv("AF_GUIDE_PATH", raising=False)
    assert _guide_path().is_file(), "开发态仓库里没有 skills/autoforge.md 的话，工具在生产必死"

# -*- coding: utf-8 -*-
"""AF-791 判据 · `af_live_run` 描述补租约口径（元宝 MCP-17，P2 文档面，2026-11-14）。

**缺陷形状**：`af_live_run` 在别的入口都说明了"四重闸（服务端开关+令牌、confirm、白名单、
授权码）"，**唯独没说单写者租约**。于是 agent 在 serve 持锁的常态部署上调用它，拿到的
`503 READONLY_DEGRADED:` 会被读成"服务故障"——重试、报障、甚至改配置去"修"它，
而这恰恰是设计口径（同一 store 根上写面只许一个写者，裁定 20261004 §一 1 A）。

**修法（只动描述文本，⛔ 不改任何行为）**：描述里点明三件事——
① 生产 serve 持单写者租约时本工具**必然** 503；② 这是**设计**口径、不是故障；
③ 真机下发请走 **HTTP 面**（`/api/live/run`）；并保留 `READONLY_DEGRADED` 这个机器可读码。

**判据**：描述文本钉（含「设计」与「HTTP 面」字样，含 `READONLY_DEGRADED` 码），
且工具档位仍是 `live`（⛔ 没顺手动别的列）。
"""

from __future__ import annotations

from autoforge import af_mcp


def _desc(name: str) -> str:
    return next(t[1] for t in af_mcp.TOOLS if t[0] == name)


def test_d1_description_states_the_lease_is_by_design():
    desc = _desc("af_live_run")
    assert "单写者租约" in desc, desc[:240]
    assert "设计" in desc, "必须点明是设计口径而非故障——否则 agent 会去重试/报障，而那永远修不好"
    assert "HTTP 面" in desc, "必须指出真机下发该走哪条路"
    assert "READONLY_DEGRADED" in desc, "留机器可读的码，便于 agent 按码分流"


def test_d2_description_keeps_the_existing_four_gates():
    """⛔ 不许把原有四重闸的描述挤掉：补一段不是换一段。"""
    desc = _desc("af_live_run")
    for word in ("四重闸", "confirm", "白名单", "授权码", "需 live 权限"):
        assert word in desc, f"原有描述里的 {word!r} 被挤掉了"


def test_d3_scope_column_still_live():
    scopes = {t[0]: t[4] for t in af_mcp.TOOLS}
    assert scopes["af_live_run"] == "live", "本笔是文档面，⛔ 不许顺手动档位"

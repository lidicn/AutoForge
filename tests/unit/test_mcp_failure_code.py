#!/usr/bin/env python3
"""「联动失败必须带码」的判据腿（裁定 20261006 §二.2；执行记录 §二之一百五十二）。

被收口的形状：`af_mcp` 的 handler 里 `except … as e: return {"ok": False, "error": str(e)}` 这一族——
过去 `af_test_submit`／`af_test_report` 两站回的对端只有一句自由文本，分不出"入参不合法"与"内部没做成"，
而裁定那句是「**凡是联动失败，必须带码——不许再"静默丢弃"或"只记一句不带码的日志"**」。

四条腿各自单独可红：

1. **真仓扫描腿**：现读 `src/autoforge/af_mcp.py`，数出 `except` 句里每一枚 `"ok": False` 的 dict 字面量，
   逐枚要求带 `code` 键。★ 同腿带**射程下限**：扫到的站点数为 0 ⇒ 直接红——⛔ 用"没扫到"冒充"没问题"
   （载体改名、整块搬走、后缀口径变了，都会让"零违规"变成假绿）。
2. **合成树反例腿**：临时目录写一份**修前形状**的源，同一把尺必须扫出那一枚。⛔ 反空集的另一半——
   扫描器只认真仓、换个文件就瞎，那它那条真仓腿永远绿。
3. **异常码腿**：`TestError` 的缺省码＝homesdk 目录里的兜底档（⛔ 新造 `ADM_ERR_*`），入参校验两站给
   `ADM_ERR_PAYLOAD_INVALID`，守卫子类继续带自己那两枚具名码，且消息前缀逐字节不变（既有测试吃它）。
4. **两站返回体腿**：真调 `_t_test_submit`／`_t_test_report`，断言返回体里 `code` 这一键**在场且等于**
   异常上那枚——⛔ 只断"字典里有 code"会放过 `code: None` 那种更阴的形状。

**射程边界**（查重先分清，⛔ 跟既有那把尺重复造）：`tests/unit/test_dcd_20261007_mcp_failure_envelope.py`
钉的是 `dispatch()` 的**外层信封**（`{ok:false, code, message}` 三键、`is_error=True`、唯一写者
`_failure_payload`）；本文件钉的是**工具 handler 返回体**里 `{"ok": False, …, "error": …}` 这一族——
对端在那张脸上读到的是 `error`，过去 `af_test_submit`／`af_test_report` 两站就在这张脸上⛔ 带码。
两张脸⛔ 共用一把尺，也⛔ 互相顶替。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from homesdk.adm.errors import ADM_ERR_INTERNAL, ADM_ERR_PAYLOAD_INVALID

from autoforge import af_mcp, af_test

REPO = Path(__file__).resolve().parents[2]
MCP_SRC = REPO / "src" / "autoforge" / "af_mcp.py"
#: 射程下限＝本批现读真数（⛔ 手抄别处的数，本文件第一条腿就是被它打回来的）。取法：
#: `python -m pytest tests/unit/test_mcp_failure_code.py -q` 的失败消息里 `只扫出 N 枚`，
#: 或就地跑 `ast.walk` 数 `except` 句内的 `ok:False`。现读 4 枚：`af_mcp.py:378`（DraftError）、
#: `:404`／`:415`（TestError，本批补码）、`:430`（TestGuardError）。低于此＝扫描退化或载体搬走。
MIN_SITE_FLOOR = 4


def _ok_false_dicts(tree: ast.AST) -> list[ast.Dict]:
    """`except …` 处理句里所有带 `"ok": False` 键的 dict 字面量（含嵌在句体深处的）。"""
    hits: list[ast.Dict] = []
    for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
        for node in ast.walk(handler):
            if isinstance(node, ast.Dict) and _is_ok_false(node):
                hits.append(node)
    return hits


def _is_ok_false(node: ast.Dict) -> bool:
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "ok":
            return isinstance(value, ast.Constant) and value.value is False
    return False


def _has_key(node: ast.Dict, name: str) -> bool:
    return any(isinstance(key, ast.Constant) and key.value == name for key in node.keys)


def _missing_code_lines(source: str) -> list[int]:
    """修前形状：`ok:False` 却⛔ `code` 的那几行行号。"""
    return [node.lineno for node in _ok_false_dicts(ast.parse(source)) if not _has_key(node, "code")]


def test_real_repo_mcp_failure_sites_all_carry_code() -> None:
    """真仓腿：一枚不漏，且⛔ 用"零命中"冒充"零违规"。"""
    nodes = _ok_false_dicts(ast.parse(MCP_SRC.read_text(encoding="utf-8")))
    assert len(nodes) >= MIN_SITE_FLOOR, (
        f"射程塌：在 `{MCP_SRC.name}` 的 except 句里只扫出 {len(nodes)} 枚 `ok:False` 返回站"
        f"（下限 {MIN_SITE_FLOOR}）——文件改名／整块搬走／扫描退化都会读到这个形状，⛔ 报『干净』。"
    )
    assert _missing_code_lines(MCP_SRC.read_text(encoding="utf-8")) == []


def test_synthetic_pre_fix_shape_is_caught(tmp_path: Path) -> None:
    """反空集腿：把修前那枚形状种进临时文件，同一把尺必须扫出来。"""
    synthetic = (
        "def handler(args):\n"
        "    try:\n"
        '        return {"ok": True}\n'
        "    except ValueError as e:\n"
        '        return {"ok": False, "error": str(e)}\n'
    )
    target = tmp_path / "mcp_like.py"
    target.write_text(synthetic, encoding="utf-8")
    assert _missing_code_lines(target.read_text(encoding="utf-8")) == [5]
    assert len(_ok_false_dicts(ast.parse(synthetic))) == 1, "扫描器连合成树都数不出站点＝尺是瞎的"


def test_test_error_carries_catalogued_codes() -> None:
    assert af_test.TestError("裸一句").code == ADM_ERR_INTERNAL
    assert af_test.TestError("入参不合法", ADM_ERR_PAYLOAD_INVALID).code == ADM_ERR_PAYLOAD_INVALID


def test_guard_error_keeps_its_own_named_code_and_message_prefix() -> None:
    err = af_test.TestGuardError(af_test.GUARD_ERR_FILESYSTEM_ROOT, "删不得")
    assert err.code == af_test.GUARD_ERR_FILESYSTEM_ROOT
    assert str(err) == f"{af_test.GUARD_ERR_FILESYSTEM_ROOT}: 删不得"


@pytest.mark.parametrize(
    "intents,expect_code",
    [([], ADM_ERR_PAYLOAD_INVALID), ({}, ADM_ERR_PAYLOAD_INVALID)],
)
def test_submit_batch_validation_rejects_are_coded(tmp_path: Path, intents, expect_code: str) -> None:
    channel = af_test.TestChannel(test_root=str(tmp_path))
    with pytest.raises(af_test.TestError) as excinfo:
        channel.submit_batch(intents)  # type: ignore[arg-type]
    assert excinfo.value.code == expect_code


def test_report_for_missing_batch_is_coded(tmp_path: Path) -> None:
    channel = af_test.TestChannel(test_root=str(tmp_path))
    with pytest.raises(af_test.TestError) as excinfo:
        channel.get_report("no-such-batch")
    assert excinfo.value.code == ADM_ERR_INTERNAL


def test_mcp_submit_handler_returns_the_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    channel = af_test.TestChannel(test_root=str(tmp_path))
    monkeypatch.setattr(af_test, "get_test_channel", lambda: channel)
    out = af_mcp._t_test_submit(None, {"intents": []})
    assert out["ok"] is False
    assert out["code"] == ADM_ERR_PAYLOAD_INVALID


def test_mcp_report_handler_returns_the_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    channel = af_test.TestChannel(test_root=str(tmp_path))
    monkeypatch.setattr(af_test, "get_test_channel", lambda: channel)
    out = af_mcp._t_test_report(None, {"batch_id": "no-such-batch"})
    assert out["ok"] is False
    assert out["code"] == ADM_ERR_INTERNAL

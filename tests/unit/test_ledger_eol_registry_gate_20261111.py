# -*- coding: utf-8 -*-
"""判据 · 批 2.5：ledger-eol 门「注册面检出」腿 F（卡号 AF-V129-G2.5-20261111）。

**为什么单独一枚文件**：仓规（`f9fe`）——新增判据文件名必须含 `gate`，否则
`pytest tests/unit -k gate` 族扫不到，本地窄面会漏检（本批原来把四条腿写进
`test_sync_ledger_regen_eol_20261031.py`，那个文件名不含 gate ⇒ 全被 deselected，
族计数一动不动——⑪ 上就是"零漂移"的假象）。

钉的四句话：
- **F1 有牙**：在册纯 LF 的文件 blob 变 CRLF ⇒ 腿 F 点名该文件并给出 `crlf=`／`lf_only=` 两侧数字；
  还原后无违规（⛔ 恒红）。
- **F2 不是单文件**：换任何一枚在册文件翻转都必须命中（红线：⛔ 只查 af_mcp 这一个文件）。
- **F3 读不出就塌**：blob 读不出 ⇒ `RuntimeError`（门 exit 2），⛔ 静默当"符合"。
- **F4 真登记册纳管面**：真 `docs/仓库卫生清单.md` §五 的表现在被机器读——
  `docs/信任边界清单.md`＝CRLF、`src/autoforge/af_mcp.py`＝LF 等逐条在册。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO / "scripts" / "check_ledger_regen_eol.py"


def _gate_module():
    spec = importlib.util.spec_from_file_location("ledger_eol_gate_ut", GATE_SCRIPT)
    assert spec is not None and spec.loader is not None, "读不出门脚本——射程塌了"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_f1_lf_file_flipped_to_crlf_is_named():
    """F1：在册 LF ⇒ blob 出现 CR 即红，且读数两侧都给。"""
    mod = _gate_module()
    rows = [("src/autoforge/af_mcp.py", "LF"), ("src/autoforge/af_service.py", "CRLF")]
    blobs = {
        "src/autoforge/af_mcp.py": b"a\r\nb\r\n",          # 翻转形状（在册 LF）
        "src/autoforge/af_service.py": b"a\r\nb\r\n",      # 正常（在册 CRLF）
    }
    violations = mod.check_registry(Path("."), rows, lambda rel: blobs[rel])
    assert len(violations) == 1, violations
    assert "af_mcp.py" in violations[0] and "在册 **LF**" in violations[0]
    assert "crlf=2" in violations[0], violations[0]
    blobs["src/autoforge/af_mcp.py"] = b"a\nb\n"           # 还原 ⇒ 无违规（⛔ 恒红）
    assert mod.check_registry(Path("."), rows, lambda rel: blobs[rel]) == []


def test_f1b_crlf_file_flipped_to_lf_is_named():
    """F1 反向腿：在册 CRLF ⇒ blob 出现裸 LF 即红（两个方向都要有牙）。"""
    mod = _gate_module()
    rows = [("src/autoforge/af_scanner.py", "CRLF")]
    violations = mod.check_registry(Path("."), rows,
                                    lambda rel: b"x\ny\n")
    assert len(violations) == 1 and "lf_only=2" in violations[0], violations


def test_f2_scope_is_the_whole_registry_not_one_file():
    """F2：红线——⛔ 只查 af_mcp；换一枚在册文件翻转也必须命中。"""
    mod = _gate_module()
    rows = [("docs/仓库卫生清单.md", "LF"), ("README.md", "CRLF"),
            ("src/autoforge/af_scanner.py", "CRLF")]
    flipped = {
        "docs/仓库卫生清单.md": b"x\ny\n",
        "README.md": b"x\r\ny\r\n",
        "src/autoforge/af_scanner.py": b"x\ny\n",   # ← 变异目标
    }
    violations = mod.check_registry(Path("."), rows, lambda rel: flipped[rel])
    assert len(violations) == 1 and "af_scanner.py" in violations[0], violations


def test_f3_unreadable_blob_fails_closed():
    """F3：读不出 blob ⇒ 射程塌（RuntimeError），⛔ 静默当"符合"。"""
    mod = _gate_module()

    def boom(_rel):
        raise RuntimeError("git 不可用")

    with pytest.raises(RuntimeError) as ei:
        mod.check_registry(Path("."), [("README.md", "CRLF")], boom)
    assert "读不出" in str(ei.value)


def test_f3b_registry_parse_fails_closed_on_empty_table():
    """F3 另一半：登记册读不出表 ⇒ 射程塌（⛔ 空表当干净）。"""
    mod = _gate_module()
    with pytest.raises(RuntimeError):
        mod.read_registry("# 没有 §五 的文档\n")
    with pytest.raises(RuntimeError):
        mod.read_registry("## 五、行尾表\n\n（表被删了）\n")


def test_f4_real_registry_is_machine_readable():
    """F4：真登记册 §五 的表被机器读（加一行＝自动纳管，⛔ 靠散文点名）。"""
    mod = _gate_module()
    rows = dict(mod.read_registry((REPO / mod.REGISTRY_DOC).read_text(encoding="utf-8")))
    for rel in ("docs/可观测性清单.md", "docs/信任边界清单.md", "README.md",
                "src/autoforge/af_mcp.py", "src/autoforge/af_service.py",
                "src/autoforge/af_scanner.py", "src/autoforge/af_executor.py"):
        assert rel in rows, f"登记册缺 {rel}（散文点名不算取数面）"
    assert rows["docs/信任边界清单.md"] == "CRLF"
    assert rows["src/autoforge/af_mcp.py"] == "LF"

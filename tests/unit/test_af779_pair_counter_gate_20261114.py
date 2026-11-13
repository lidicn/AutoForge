# -*- coding: utf-8 -*-
"""AF-779 判据 · pair 失败计数的**读**失败不许静默清零（2026-11-14，§1.216 裁定）。

**缺陷形状**：`_pair_record_failure` 里读盘失败时 `data = {}`，紧接着整表
`atomic_write_text` 覆盖 ⇒ **一次坏写把全部 link_key 的历史计数清零**，而且盘上一行说明
都没有（只有"写失败"那条留痕，读失败这条完全没有）——限流壳子就这么静默失效。

**修法**：读失败补具名 warning `PAIR_LINK_FAILURE_COUNT_READ_FAILED`，并**逐键抢救**
（`json.loads` 整份读不出时按 `"key": <int>` 逐对捞，读得出多少算多少，只丢真正坏的那格），
回写只覆盖抢救结果 + 本轮那一笔。

**判据**：

- R1 计数文件是坏 JSON ⇒ 留痕恰好 1 条具名码，且**好键保留**（⛔ 不许整表清零）
- R2 坏的那一格（值是字符串等非数字）只丢它自己，其余键照旧
- R3 正常路径⛔ 不落痕（不许把正常读也记成失败）
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app

CODE = "PAIR_LINK_FAILURE_COUNT_READ_FAILED"


def _client(tmp_path, monkeypatch) -> tuple[TestClient, object]:
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({
        "svc-tok": {"subject": "svc", "scopes": ["read", "write"]},
    }))
    root = tmp_path / "store"
    return TestClient(build_app(str(root))), root / ".auth" / "pair_link_failures.json"


def _codes(caplog) -> list[str]:
    return [r.message.split()[0] for r in caplog.records if r.message.startswith(CODE)]


# ── R1 坏 JSON ⇒ 留痕 1 条 ＋ 好键保留 ───────────────────────────────
def test_r1_corrupt_json_logs_once_and_keeps_good_keys(tmp_path, monkeypatch, caplog):
    """判据 a：整份 JSON 都坏了也⛔ 不许静默清零——留痕＋尽量抢救。"""
    caplog.set_level(logging.WARNING)
    client, cnt = _client(tmp_path, monkeypatch)
    cnt.parent.mkdir(parents=True, exist_ok=True)
    # 形状：坏在尾部（写一半断电的典型），但前面的键其实是完整的
    cnt.write_text('{"good_a": 7, "good_b": 3,,,\n', encoding="utf-8")

    r = client.get("/pair?k=bogus_key_abcdefg", headers={"Accept": "application/json"})
    assert r.status_code == 404, "打错 link_key 仍必须是 404（计数不阻断主流程）"

    assert len(_codes(caplog)) == 1, f"读失败必须留 1 条具名码，实得 {_codes(caplog)}"
    assert cnt.is_file()
    data = json.loads(cnt.read_text(encoding="utf-8"))
    assert data.get("good_a") == 7 and data.get("good_b") == 3, (
        "能抢救的好键必须保留（⛔ 修前这里是整表清零）：%r" % (data,)
    )
    assert data.get("bogus_key_abcdefg") == 1, "本轮那一笔照常记上"


# ── R2 只丢坏的那一格 ────────────────────────────────────────────────
def test_r2_only_broken_cell_is_dropped(tmp_path, monkeypatch, caplog):
    """判据 b：值是字符串（非数字）⇒ 只丢那一格，别的键与计数保持。"""
    caplog.set_level(logging.WARNING)
    client, cnt = _client(tmp_path, monkeypatch)
    cnt.parent.mkdir(parents=True, exist_ok=True)
    # 合法 JSON，但有一格不是数字 ⇒ `int(v)` 抛 ⇒ 走抢救路径
    cnt.write_text(json.dumps({"ok_key": 5, "bad_key": "not-a-number"}), encoding="utf-8")

    client.get("/pair?k=another_bogus_key", headers={"Accept": "application/json"})

    data = json.loads(cnt.read_text(encoding="utf-8"))
    assert data.get("ok_key") == 5, "好键必须保留"
    assert "bad_key" not in data, "坏的那一格该被丢掉（⛔ 不许硬塞 0 冒充）"
    assert data.get("another_bogus_key") == 1
    assert len(_codes(caplog)) == 1


# ── R3 正常路径不落痕 ────────────────────────────────────────────────
def test_r3_healthy_file_does_not_log(tmp_path, monkeypatch, caplog):
    """反证腿：文件好着⛔ 不许记"读失败"（否则真异常被噪音淹没）。"""
    caplog.set_level(logging.WARNING)
    client, cnt = _client(tmp_path, monkeypatch)
    cnt.parent.mkdir(parents=True, exist_ok=True)
    cnt.write_text(json.dumps({"healthy_key": 2}), encoding="utf-8")

    client.get("/pair?k=third_bogus_key", headers={"Accept": "application/json"})

    assert _codes(caplog) == [], "正常读⛔ 不许落痕"
    data = json.loads(cnt.read_text(encoding="utf-8"))
    assert data.get("healthy_key") == 2 and data.get("third_bogus_key") == 1

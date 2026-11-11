"""F2（2026-11-11 线上）：`answer_inbox` 陈旧应答每秒重放，日志风暴。

现场：`/data/answer_inbox` 20 个陈旧 json（最新 11-11 11:31），watch ticker 每秒把 20 个全
reject 一遍；`watch.log` 里 `INBOX_REJECT_STALE` 已 26840 条 / 6MB，上一份 `watch.log.1`
32MB 被 8MB 上限轮转。拒绝后文件留在原地 ⇒ 下一 tick 再来一遍，**永不收敛**。

判据（卡 §F2 验收）：
1. 同一陈旧文件连续两次 tick **只产生一条 WARNING**，且文件已移出扫描目录（`.rejected/` 保留现场）；
2. 日志不再随陈旧应答线性增长（重复拒绝降 DEBUG）；
3. 负控：**新鲜且签名正确**的应答照旧被消费（⛔ 隔离不许误伤正路）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoforge import af_ask_bridge as _ask_bridge
from autoforge import af_live

_LOGGER = "autoforge.live"


@pytest.fixture(autouse=True)
def _reset_reject_warned() -> Any:
    """拒绝去重表是模块级状态：每个用例从空表起（⛔ 不让上个用例的 ask_id 影响判定）。"""
    af_live._REJECT_WARNED.clear()
    yield
    af_live._REJECT_WARNED.clear()


def _inbox(tmp_path: Path) -> Path:
    inbox = tmp_path / "answer_inbox"
    inbox.mkdir()
    return inbox


def _write_reply(inbox: Path, name: str, *, ask_id: str, ts: float, sig: str = "sig",
                 answer: dict[str, Any] | None = None) -> Path:
    payload = {
        "ask_id": ask_id,
        "text": "开吗",
        "room": "living_room",
        "answer": answer or {"device": "light.study"},
        "sig": sig,
        "ts": ts,
        "via": "webui",
    }
    path = inbox / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class _Executor:
    """记录调用、返回可消费结果的假 executor（⛔ 不碰真机）。"""

    def __init__(self) -> None:
        self.answered: list[dict[str, Any]] = []

    def answer(self, **kwargs: Any) -> Any:
        self.answered.append(kwargs)
        return SimpleNamespace(instance_id="inst-1")

    def answer_structured(self, **kwargs: Any) -> Any:
        self.answered.append(kwargs)
        return SimpleNamespace(instance_id="inst-1")


def _runtime() -> Any:
    return SimpleNamespace(store=None, executor=_Executor())


def _stale_ts() -> float:
    return time.time() - 3600.0


def test_stale_reply_is_quarantined_and_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """① 同一陈旧文件两次 tick 只一条 WARNING；文件移出扫描目录、现场保留在 .rejected/。"""
    monkeypatch.setenv(_ask_bridge.INBOX_KEY_ENV, "test-key")
    inbox = _inbox(tmp_path)
    stale = _write_reply(inbox, "stale.json", ask_id="ask-stale", ts=_stale_ts())
    runtime = _runtime()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        af_live.read_answer_inbox(runtime, inbox_dir=str(inbox))
        first = [r for r in caplog.records if "INBOX_REJECT_STALE" in r.getMessage()]
        # 第二 tick：文件已不在扫描目录 ⇒ 不得再产生任何拒绝日志（这是"收敛"的判据）
        af_live.read_answer_inbox(runtime, inbox_dir=str(inbox))
        second = [r for r in caplog.records if "INBOX_REJECT_STALE" in r.getMessage()]

    assert len(first) == 1, f"陈旧应答首 tick 应只记一条 WARNING，现 {len(first)} 条"
    assert len(second) == 1, f"第二 tick 又刷了 {len(second) - 1} 条（风暴未收敛）"
    assert not stale.exists(), "陈旧应答必须移出扫描目录（⛔ 留在原地即每 tick 重放）"
    quarantined = inbox / ".rejected" / "stale.json"
    assert quarantined.is_file(), "现场必须保留在 .rejected/ 子目录（⛔ 不删证据）"
    assert runtime.executor.answered == [], "陈旧应答 ⛔ 不得驱动执行链"


def test_same_ask_id_second_rejection_downgrades_to_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """② 同一 ask_id 的第二份拒绝降 DEBUG：WARNING 只一条，两份都隔离（重名加纳秒戳）。"""
    monkeypatch.setenv(_ask_bridge.INBOX_KEY_ENV, "test-key")
    inbox = _inbox(tmp_path)
    _write_reply(inbox, "a.json", ask_id="ask-dup", ts=_stale_ts())
    _write_reply(inbox, "b.json", ask_id="ask-dup", ts=_stale_ts())

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        af_live.read_answer_inbox(_runtime(), inbox_dir=str(inbox))
        warnings = [r for r in caplog.records
                    if r.levelno >= logging.WARNING and "INBOX_REJECT_STALE" in r.getMessage()]
        debugs = [r for r in caplog.records
                  if r.levelno < logging.WARNING and "INBOX_REJECT_STALE" in r.getMessage()]

    assert len(warnings) == 1, f"同一 ask_id 只许一条 WARNING，现 {len(warnings)} 条"
    assert len(debugs) == 1, f"重复拒绝应降 DEBUG，现 {len(debugs)} 条"
    kept = sorted(p.name for p in (inbox / ".rejected").iterdir())
    assert len(kept) == 2, f"两份现场都要保留（重名加纳秒戳），现 {kept}"
    assert not list(inbox.glob("*.json")), "扫描目录必须清空（否则下一 tick 重放）"


def test_corrupt_non_object_json_is_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """非对象 JSON（如 `[]`）同样是确定性拒绝 ⇒ 一次隔离，⛔ 不留在扫描目录。"""
    monkeypatch.setenv(_ask_bridge.INBOX_KEY_ENV, "test-key")
    inbox = _inbox(tmp_path)
    bad = inbox / "array.json"
    bad.write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        af_live.read_answer_inbox(_runtime(), inbox_dir=str(inbox))

    assert not bad.exists()
    assert (inbox / ".rejected" / "array.json").is_file()


def test_fresh_signed_reply_is_still_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """③ 负控（⑧ 探针必须有牙）：新鲜 + 签名正确的应答照旧消费，⛔ 不被隔离误伤。"""
    key = "test-key"
    monkeypatch.setenv(_ask_bridge.INBOX_KEY_ENV, key)
    inbox = _inbox(tmp_path)
    ask_id = "ask-fresh"
    ts = time.time()
    answer = {"device": "light.study"}
    answer_json = json.dumps(answer, sort_keys=True, ensure_ascii=False)
    sig = _ask_bridge.sign_answer(key, ask_id, "开吗", "living_room", answer_json, ts)
    fresh = _write_reply(inbox, "fresh.json", ask_id=ask_id, ts=ts, sig=sig, answer=answer)
    runtime = _runtime()

    af_live.read_answer_inbox(runtime, inbox_dir=str(inbox))

    assert not fresh.exists(), "消费成功后必须清理（原语义）"
    assert not list(inbox.glob("*.json"))
    rejected = inbox / ".rejected"
    assert not rejected.exists() or not list(rejected.iterdir()), "正路 ⛔ 不许进隔离目录"
    assert runtime.executor.answered, "新鲜应答必须驱动执行链（负控要有牙）"

# -*- coding: utf-8 -*-
"""第四期审计·持久化与存储面 10 条缺陷的回归测试（远端复修组 + 原生组）。

贯穿全组的一条族纪律：**区分"合法空态"（文件不存在 / 0 字节 / 纯空白）与"损坏"
（文件存在却读不出 / 解析失败 / 顶层形状不对）**。损坏时 fail-closed——拒写 + 留痕 + 隔离，
绝不允许"读失败 → 当空库 → 整档覆盖把坏字节抹掉且零取证"。

覆盖：
- AF39-R  catalog 损坏后 refresh() 三条路径（full / 增量 / 收窄）一律拒写，坏字节保留
- AF42-R  落盘记录读不出 → 隔离为 .corrupt，下次 save 不再无声抹现场
- AF52    护栏对 0 字节 / 纯空白文件放行（合法空态，不是"已损坏"）
- AF76    空文件放行不得误伤：有内容却读不出仍被拒写
- AF77    损坏检测覆盖"能打开但 json.loads 失败"
- AF61    records() 解析失败留痕 + 隔离，不再静默跳过
- AF69    JsonFireStore._load 顶层不是 dict 时不炸穿 __init__
- AF73    catalog 读侧校验 CATALOG_VERSION，未知版本告警
- AF78    preferences.jsonl 存在却读不出 → 毒化拒写，不当空库覆盖
- AF79    revision.json 读不出 → bump 拒写，不把盘上代数抹回小数字
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from autoforge.af_atomic import refuse_when_shape_unreadable
from autoforge.af_catalog import CATALOG_VERSION, DeviceCatalog
from autoforge.af_config import Config
from autoforge.af_fire_recorder import JsonFireStore
from autoforge.af_persist import PersistStore
from autoforge.af_preference import PreferenceModel, PREFERENCES_FILE

CORRUPT = '{"__corrupt": ['
BAD_LIST = "[1, 2, 3]"


def _make_catalog(root: Path) -> DeviceCatalog:
    return DeviceCatalog(
        root,
        fetch_all=lambda: {"light.b": ("off", {"friendly_name": "B"}, "")},
    )


# ── AF39-R：catalog 损坏后三条刷新路径一律拒写 ─────────────────────────────


@pytest.mark.parametrize("mode", ["full", "incr", "narrow"])
def test_af39r_corrupt_catalog_refreshes_refuse_all_paths(tmp_path, caplog, mode):
    cat = _make_catalog(tmp_path)
    cat.refresh(full=True)  # 先落一份好目录
    cp = tmp_path / ".catalog" / "catalog.json"
    cp.write_text(CORRUPT, encoding="utf-8")

    cat2 = _make_catalog(tmp_path)
    with caplog.at_level(logging.ERROR):
        if mode == "full":
            result = cat2.refresh(full=True)
        elif mode == "incr":
            result = cat2.refresh(full=False)
        else:
            result = cat2.refresh(full=True, area="书房")

    assert result.get("ok") is False, result
    # 决定性读数：坏字节原样保留，没被新目录盖掉
    assert cp.read_text(encoding="utf-8") == CORRUPT


# ── AF42-R + AF61：落盘记录读不出 → 留痕 + 隔离，不静默跳过 ────────────────


def test_af61_records_warn_and_quarantine_corrupt(tmp_path, caplog):
    store = PersistStore(tmp_path / "p")
    store.directory.mkdir(parents=True, exist_ok=True)
    bad = store.directory / "bad.json"
    bad.write_text(CORRUPT, encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        records = store.records()

    assert records == []
    # AF61：必须留痕（不是静默 continue）
    assert any("解析失败" in r.message or "读不出" in r.message for r in caplog.records)
    # AF42-R：坏文件被隔离为 .corrupt，原位不再是 .json——下次 save(同 id) 不会无声抹现场
    assert not bad.exists(), "坏字节必须被移走隔离，而非留在原位等下次原子替换抹掉"
    assert (store.directory / "bad.corrupt").read_text(encoding="utf-8") == CORRUPT


def test_af61_load_quarantines_corrupt(tmp_path):
    store = PersistStore(tmp_path / "p")
    store.directory.mkdir(parents=True, exist_ok=True)
    bad = store.directory / "inst1.json"
    bad.write_text(CORRUPT, encoding="utf-8")

    assert store.load("inst1") is None
    assert not bad.exists()
    assert (store.directory / "inst1.corrupt").exists()


# ── AF52 / AF76 / AF77：护栏三态区分 ──────────────────────────────────────


def test_af52_zero_byte_and_whitespace_are_legal_empty_state(tmp_path):
    """AF52：0 字节 / 纯空白 = 合法空态，必须放行初始化，不得报"已损坏"锁死。"""
    for content in ("", "   \n  \t  ", "{}"):
        p = tmp_path / f"f_{abs(hash(content))}.json"
        p.write_text(content, encoding="utf-8")
        # 不抛 = 放行
        refuse_when_shape_unreadable(p, "测试态")


def test_af76_nonempty_unparseable_is_still_refused(tmp_path):
    """AF76：空文件放行不得误伤——有内容却 json.loads 失败仍要拒写（否则整档覆盖抹现场）。"""
    p = tmp_path / "corrupt.json"
    p.write_text(CORRUPT, encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏"):
        refuse_when_shape_unreadable(p, "测试态")
    # AF77：坏字节原样保留
    assert p.read_text(encoding="utf-8") == CORRUPT


def test_af77_wrong_top_level_shape_refused(tmp_path):
    """AF77/形状：合法 JSON 但顶层是 list，同样拒写。"""
    p = tmp_path / "list.json"
    p.write_text(BAD_LIST, encoding="utf-8")
    with pytest.raises(ValueError, match="形状不是"):
        refuse_when_shape_unreadable(p, "测试态")


# ── AF69：JsonFireStore 顶层不是 dict 不炸穿 ────────────────────────────────


def test_af69_fire_log_list_shape_does_not_crash_init(tmp_path):
    fp = tmp_path / "fire_log.json"
    fp.write_text(BAD_LIST, encoding="utf-8")
    store = JsonFireStore(tmp_path)  # 不抛 AttributeError = 修住
    assert store._records == {}
    # 写侧护栏拒写，避免把这份错形状文件抹掉
    with pytest.raises(ValueError):
        store._save()
    assert fp.read_text(encoding="utf-8") == BAD_LIST


# ── AF73：catalog 读侧校验版本 ─────────────────────────────────────────────


def test_af73_catalog_version_mismatch_warns(tmp_path, caplog):
    cp = tmp_path / ".catalog" / "catalog.json"
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(
        json.dumps({"version": CATALOG_VERSION + 99, "freshness": "", "entities": {}}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        DeviceCatalog(tmp_path, fetch_all=lambda: {})._load()
    assert any("CATALOG_VERSION_MISMATCH" in r.message for r in caplog.records), \
        "未知/更新版本号必须告警，不能被 setdefault 悄悄伪装成当前版本"


# ── AF78：preferences.jsonl 读不出 → 毒化拒写 ───────────────────────────────


def test_af78_preferences_unreadable_blocks_rewrite(tmp_path):
    p = tmp_path / PREFERENCES_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"__corrupt": [', encoding="utf-8")  # 有内容却一行都读不出

    pm = PreferenceModel(tmp_path)
    assert pm._records_unreadable is True
    pm._rewrite_all()
    # 整档压缩被拒写，坏字节原样保留
    assert p.read_text(encoding="utf-8") == '{"__corrupt": ['


# ── AF79：revision.json 读不出 → bump 拒写 ──────────────────────────────────


def test_af79_corrupt_revision_bump_refuses_and_preserves(tmp_path):
    rp = tmp_path / "revision.json"
    rp.write_text(CORRUPT, encoding="utf-8")
    cfg = Config(tmp_path)

    with pytest.raises(ValueError, match="revision.json 已损坏"):
        cfg.bump_revision()
    # 决定性读数：坏字节没被抹成 {"revision": 1}
    assert rp.read_text(encoding="utf-8") == CORRUPT


def test_af79_missing_revision_is_legal_first_state(tmp_path):
    """对照：文件不存在是合法首建，bump 必须照常工作（不能被毒化锁死）。"""
    cfg = Config(tmp_path)
    new_rev = cfg.bump_revision()
    assert new_rev == 1
    assert json.loads((tmp_path / "revision.json").read_text(encoding="utf-8"))["revision"] == 1

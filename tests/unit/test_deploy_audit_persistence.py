"""裁定 20261011 §3 Q13 丙：部署仪式台账（`DEPLOY_AUDIT` 一族）落盘的那几条规矩。

报告说"审计只在内存"这一半**成立**（`AuditLog` 全类原本零文件 I/O），而台账要的恰恰是
"进程死了以后还能对账"——首演码／试演台账是凭据面。本文件钉住这个改动带的四件事：

1. 落点跟着本次部署的 store 根走（不新开一份路径真值，也不写进仓根）；
2. 一次性凭据（首演码）**只留在内存**，进盘的那行换成 `[redacted]`；
3. 落不成必须响（两枚具名码），**不许**把"凭据没落上"读成"没有这次部署"；
4. 台账有上限，超了留最近那段（整份重写走原子写）；
5. 乙并入丙的那半边——"只有本进程可查、只有部署台账落盘"这句告示有**真源常量**
   （`af_audit.AUDIT_SCOPE_NOTE`），不再只散在文档里。把这句挂到每张序列化 `audit` 的脸
   （`Runtime.stats()` 与 `af_service` 三处响应）本批未落：那要往 `af_service.py` 插 4 行，
   连带 `scripts/check_process_model.py` 的四枚现读锚点与约 50 处文档锚点重钉，而该文件正被
   并发登录线在途修改。已作为偏差递追认，见裁定书执行回填。

红绿都由真跑得出：写盘用 `tmp_path`，不碰仓库；两条"读不出根"的档用 `object()`，
和 `tests/test_af_ir_group_apply.py` 里既有那批 `store=object()` 的用法同形。
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoforge import af_apply, af_audit
from autoforge.af_audit import (
    DEPLOY_AUDIT_FILE,
    DEPLOY_AUDIT_KEEP_LINES,
    DEPLOY_AUDIT_MAX_LINES,
    REDACTED,
)

AUDIT_LOGGER = "autoforge.audit"


def _event(**over):
    kwargs = {
        "type": af_audit.PREMIERE_ISSUED,
        "at": datetime.now(timezone.utc),
        "message": "取码用例：前门 → 客厅灯",
        "data": {"store_diff_sha256": "a" * 64},
    }
    kwargs.update(over)
    return af_audit.AuditEvent(**kwargs)


@pytest.fixture
def clean_ledger():
    """把进程级台账挪开再还回去：`DEPLOY_AUDIT` 是模块级单例，测试之间不许互相看见。"""
    saved = list(af_audit.DEPLOY_AUDIT)
    af_audit.DEPLOY_AUDIT.clear()
    yield
    af_audit.DEPLOY_AUDIT.clear()
    for event in saved:
        af_audit.DEPLOY_AUDIT.events.append(event)


# ── 落点 ──────────────────────────────────────────────────────────────────────


def test_sink_follows_the_store_root(tmp_path):
    assert af_audit.deploy_audit_sink(SimpleNamespace(root=tmp_path)) == tmp_path / DEPLOY_AUDIT_FILE
    # str 型根与 Path 型根给出同一个落点（store 侧两种写法都真实存在）
    assert af_audit.deploy_audit_sink(SimpleNamespace(root=str(tmp_path))) == (
        tmp_path / DEPLOY_AUDIT_FILE
    )


def test_sink_reads_missing_or_blank_root_as_none():
    # 取不到根 ⇒ None，由调用方响；这里不许抛，也不许猜一个默认目录
    assert af_audit.deploy_audit_sink(object()) is None
    assert af_audit.deploy_audit_sink(None) is None
    assert af_audit.deploy_audit_sink(SimpleNamespace(root="")) is None
    assert af_audit.deploy_audit_sink(SimpleNamespace(root="   ")) is None
    assert af_audit.deploy_audit_sink(SimpleNamespace(root=12)) is None


def test_file_name_and_cap_are_pinned():
    """钉住字面量：改名＝ NAS 那边挂载点白名单与 `.gitignore` 口径要跟着走，不许悄悄改。"""
    assert DEPLOY_AUDIT_FILE == "deploy_audit.jsonl"
    assert DEPLOY_AUDIT_MAX_LINES > DEPLOY_AUDIT_KEEP_LINES > 0
    # 反空集：豁免名单空掉＝凭据原样进盘。这一格塌了不会有人发现，所以先数它一眼。
    assert af_audit.PERSIST_REDACT_KEYS == frozenset({"code"})


# ── 落盘正身 ──────────────────────────────────────────────────────────────────


def test_record_deploy_appends_one_readable_jsonl_line(tmp_path, clean_ledger):
    sink = tmp_path / DEPLOY_AUDIT_FILE
    af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["type"] == af_audit.PREMIERE_ISSUED
    assert entry["message"] == "取码用例：前门 → 客厅灯"  # ensure_ascii=False：中文不许变成 \uXXXX
    assert "\\u" not in lines[0]
    assert entry["data"]["store_diff_sha256"] == "a" * 64
    assert entry["at"]  # ISO 字符串，供事后排时间线


def test_record_deploy_keeps_the_memory_event_too(tmp_path, clean_ledger):
    af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))
    assert len(af_audit.DEPLOY_AUDIT) == 1
    assert af_audit.DEPLOY_AUDIT.of_type(af_audit.PREMIERE_ISSUED)


def test_premiere_code_stays_in_memory_and_never_reaches_disk(tmp_path, clean_ledger):
    """首演码是一次性凭据：台账证明"签过码"，不证明"码是哪个"。"""
    event = _event(data={"store_diff_sha256": "b" * 64, "code": "482913"})
    af_audit.record_deploy(event, SimpleNamespace(root=tmp_path))

    raw = (tmp_path / DEPLOY_AUDIT_FILE).read_text(encoding="utf-8")
    assert "482913" not in raw
    assert json.loads(raw)["data"]["code"] == REDACTED
    # 内存里那份不许被动过——调用方（`issue_premiere` 的返回值）靠它把码交给运维
    assert event.data["code"] == "482913"


# ── 落不成必须响 ─────────────────────────────────────────────────────────────


def test_missing_store_root_warns_instead_of_failing_silently(tmp_path, caplog, clean_ledger):
    with caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER):
        af_audit.record_deploy(_event(), object())

    assert any("DEPLOY_AUDIT_NOT_PERSISTED" in r.getMessage() for r in caplog.records)
    assert list(tmp_path.iterdir()) == []  # 没根就没往仓里猜路径
    assert len(af_audit.DEPLOY_AUDIT) == 1  # 响归响，这条仍在本进程台账里


def test_write_failure_is_named_and_does_not_escape(tmp_path, caplog, clean_ledger):
    """落点被占成目录 ⇒ open 抛 OSError：必须具名响，且不许把已经成功的部署打崩。"""
    (tmp_path / DEPLOY_AUDIT_FILE).mkdir()
    with caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER):
        af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))  # 不许抛

    assert any("DEPLOY_AUDIT_PERSIST_FAILED" in r.getMessage() for r in caplog.records)
    assert len(af_audit.DEPLOY_AUDIT) == 1


def test_unreadable_ledger_skips_trim_with_the_same_named_code(tmp_path, caplog, clean_ledger):
    """盘上那档读不回来时，裁剪这一腿跳过并响同一枚码——不许静默跳过对上限的核对。"""
    sink = tmp_path / DEPLOY_AUDIT_FILE
    sink.write_bytes(b"\xff\xfe\x00broken")  # 不是合法 utf-8：read_text 必抛
    with caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER):
        af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))

    assert any("DEPLOY_AUDIT_PERSIST_FAILED" in r.getMessage() for r in caplog.records)
    assert len(sink.read_bytes()) > 10  # 追加照样发生（没把已有字节整档抹掉）


# ── 上限 ──────────────────────────────────────────────────────────────────────


def test_ledger_is_trimmed_to_the_newest_block(tmp_path, clean_ledger):
    sink = tmp_path / DEPLOY_AUDIT_FILE
    old = [{"note": f"line{i}"} for i in range(DEPLOY_AUDIT_MAX_LINES + 5)]
    sink.write_text("".join(json.dumps(o) + "\n" for o in old), encoding="utf-8")

    af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))

    lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(lines) == DEPLOY_AUDIT_KEEP_LINES
    assert json.loads(lines[-1])["type"] == af_audit.PREMIERE_ISSUED  # 尾部是刚记的那条
    assert json.loads(lines[0])["note"] == f"line{DEPLOY_AUDIT_MAX_LINES + 5 - DEPLOY_AUDIT_KEEP_LINES + 1}"


def test_under_the_cap_nobody_rewrites_the_file(tmp_path, clean_ledger):
    """没到上限就只追加：整档重写会让并发写者读到半截，能不碰就不碰。"""
    sink = tmp_path / DEPLOY_AUDIT_FILE
    af_audit.record_deploy(_event(), SimpleNamespace(root=tmp_path))
    before = sink.read_bytes()

    af_audit.record_deploy(_event(type=af_audit.PREMIERE_CONSUMED), SimpleNamespace(root=tmp_path))
    after = sink.read_bytes()

    assert after.startswith(before)
    assert len(after.splitlines()) == 2


# ── 接缝：四站点全走新入口 ────────────────────────────────────────────────────


def test_all_four_apply_sites_go_through_record_deploy():
    """直接 `DEPLOY_AUDIT.add(...)` 的那四站必须全部改走 `record_deploy`。

    少改一站不是"少一条凭据"，是"这一条凭据永远不落盘而没人知道"——所以按调用点计数，
    而不是只断言"新入口存在"。
    """
    path = Path(af_apply.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    direct_adds = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "DEPLOY_AUDIT"
    ]
    records = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_deploy"
    ]
    assert direct_adds == []
    assert len(records) == 4


def test_add_still_works_without_a_sink_for_the_other_three_logs(tmp_path, clean_ledger):
    """其余三族（`runtime.audit` 等）维持易失：`add()` 不递 sink 时一个字都不写。"""
    log = af_audit.AuditLog()
    log.add(_event())
    assert len(log) == 1
    assert list(tmp_path.iterdir()) == []


# ── 读者告示（裁定 §3 Q13 的「乙并入丙」那半边）────────────────────────────────


def test_scope_note_says_what_it_has_to():
    """告示必须同时说到两件事：只有本次进程可查 ＋ 只有部署台账落了盘。"""
    note = af_audit.AUDIT_SCOPE_NOTE
    assert "本进程" in note
    assert DEPLOY_AUDIT_FILE in note


def test_scope_note_is_exported():
    """告示得是**导出的真源**，不是模块里的私有字面量。

    挂载那半边（把 `audit_scope` 打进 `Runtime.stats()` 与 `af_service` 三处响应脸）本批未落，
    理由是锚点成本与在途冲突。这一腿钉住"将来挂的时候不必从头造那句话、也不必猜落点"——
    少了它，常量可以被人悄悄降成私有而没有任何读数变红。
    """
    assert "AUDIT_SCOPE_NOTE" in af_audit.__all__
    assert af_audit.AUDIT_SCOPE_NOTE == af_audit.AUDIT_SCOPE_NOTE.strip()

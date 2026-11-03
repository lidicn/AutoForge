"""判据 · `GraphStore.history()` 显式跳过点目录（§1.133 幽灵归档的代码防御层）。

病灶（台账 §1.133）：operator 把手工备份放在 store 根（点目录一层，如
`.qa_r4_backup_20261103/v1.json`）⇒ `history()` 的 `root.glob("*/v*.json")` 命中
⇒ 列表报出幽灵条目（`unavailable`＋导出空壳）。运维侧已把备份移出 store 根，
本判据守的是**代码防御层**：同类动作再发生也不产生幽灵。

腿：
- D1（核心，差分自证）：store 根放 `.dotdir_test/v1.json`（合法归档形状）⇒ `history()`
  结果**不含**它派生的条目。用「同夹具、只切判定」的方式证明牙齿：D1b 直接对**未过滤**
  的 glob 结果做断言，证明夹具确实会被旧实现命中（修前应含＝病灶实证）。
- D2（回归）：正常归档目录、`.deleted/<name>--<ts>/` 两层形状 ⇒ `history()` 行为与修前
  逐字一致（`.deleted` 隔离区本就不该出现在 history 里，且**不靠**新过滤实现——它的
  首层名以点开头，但记录在第二层，`*/v*.json` 形状本来就不命中）。
- D3（源码钉）：`history()` 里确实有「前导点目录 ⇒ continue」的判定，且**只有这一处**
  root 级 glob 承担列表职责（防止后人把过滤挪走/删掉还留绿）。

文件名带 `gate` ⇒ 入 `tests/unit/*gate*.py` 族选。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoforge.af_store import GraphStore


def _record(name: str, version: int = 1, saved_at: str = "2026-11-03T00:00:00+00:00",
            note: str = "", owner: str = "") -> str:
    """最小合法归档记录（history 只读这几个字段，不带 graph 体也够）。"""
    return json.dumps(
        {
            "name": name,
            "version": version,
            "saved_at": saved_at,
            "note": note,
            "owner": owner,
            "graph": {"id": name, "nodes": [], "edges": []},
        },
        ensure_ascii=False,
        indent=2,
    )


@pytest.fixture()
def store(tmp_path: Path) -> GraphStore:
    return GraphStore(root=tmp_path)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── D1 核心：点目录派生条目必须被跳过 ────────────────────────────────
def test_d1_dotdir_entries_excluded_from_history(store: GraphStore) -> None:
    """store 根放 `.dotdir_test/v1.json` ⇒ history() 不含该条目。"""
    _write(store.root / ".dotdir_test" / "v1.json", _record("ghost_from_backup"))

    names = [r["name"] for r in store.history()]

    assert "ghost_from_backup" not in names, (
        f"点目录派生的幽灵条目仍在 history 里：{names!r}（§1.133 未闭）"
    )
    assert names == [], f"除点目录外没有任何归档，history 应为空，实读 {names!r}"


def test_d1_dotdir_alongside_real_archive(store: GraphStore) -> None:
    """点目录与真实归档共存 ⇒ 只跳过点目录，真实归档照旧出（不是一刀切清空）。"""
    _write(store.root / "real_arch" / "v1.json", _record("real_arch", 1))
    _write(store.root / ".qa_backup" / "v1.json", _record("ghost_a"))
    _write(store.root / ".dotdir_test" / "v2.json", _record("ghost_b", 2))

    names = [r["name"] for r in store.history()]

    assert names == ["real_arch"], f"应只剩真实归档，实读 {names!r}"


def test_d1b_fixture_would_have_been_hit_by_old_glob(store: GraphStore) -> None:
    """牙齿（差分自证）：同一夹具对**未过滤**的旧形状 glob ⇒ 确实命中。

    这条不测产品代码，测的是「D1 的夹具真能触发旧病灶」——否则 D1 变绿可能只是
    夹具没打到点上（§8：恒 0 不是负控，是覆盖洞）。
    """
    _write(store.root / ".dotdir_test" / "v1.json", _record("ghost_from_backup"))

    old_shape = sorted(store.root.glob("*/v*.json"))          # 修前的枚举形状，逐字复用
    old_names = [json.loads(p.read_text(encoding="utf-8")).get("name") for p in old_shape]

    assert old_names == ["ghost_from_backup"], (
        f"夹具没能复现旧病灶（旧 glob 未命中点目录）⇒ D1 无牙，实读 {old_names!r}"
    )


# ── D2 回归：正规归档 ＋ .deleted 两层形状逐字不变 ────────────────────
def test_d2_real_archives_unchanged(store: GraphStore) -> None:
    """多版本、多归档 ⇒ 顺序与字段与修前一致（按 path 升序 ⇒ 同目录内 v1<v10<v2 字典序）。"""
    _write(store.root / "arch_a" / "v1.json", _record("arch_a", 1, "2026-11-01T00:00:00+00:00", "n1", "own1"))
    _write(store.root / "arch_a" / "v2.json", _record("arch_a", 2, "2026-11-02T00:00:00+00:00", "n2", "own1"))
    _write(store.root / "arch_b" / "v1.json", _record("arch_b", 1, "2026-11-03T00:00:00+00:00"))

    got = store.history()

    assert got == [
        {"name": "arch_a", "version": 1, "saved_at": "2026-11-01T00:00:00+00:00", "note": "n1", "owner": "own1"},
        {"name": "arch_a", "version": 2, "saved_at": "2026-11-02T00:00:00+00:00", "note": "n2", "owner": "own1"},
        {"name": "arch_b", "version": 1, "saved_at": "2026-11-03T00:00:00+00:00", "note": "", "owner": ""},
    ], f"正规归档 history 逐字漂移：{got!r}"


def test_d2_deleted_two_level_shape_unchanged(store: GraphStore) -> None:
    """`.deleted/<name>--<ts>/v1.json` 两层形状 ⇒ 不出现在 history（与修前一致）。

    修前它就不出现：`*/v*.json` 只匹配**一层**目录，隔离区记录在第二层。
    这条钉住「新过滤不是靠 `.deleted` 命中再排除」——否则语义会变（比如将来把隔离区
    挪成一层就会突然串味）。
    """
    _write(store.root / ".deleted" / "gone--20261103T000000000000" / "v1.json", _record("gone"))
    _write(store.root / "live" / "v1.json", _record("live", 1))

    names = [r["name"] for r in store.history()]

    assert names == ["live"], f"隔离区一层形状混进 history：{names!r}"


def test_d2_quarantine_still_works(store: GraphStore) -> None:
    """端到端：quarantine 后 history 少一条、隔离清单多一条（新过滤未动删除语义）。"""
    _write(store.root / "to_go" / "v1.json", _record("to_go", 1))
    assert [r["name"] for r in store.history()] == ["to_go"]

    store.quarantine("to_go")

    assert store.history() == [], "隔离后 history 应空"
    assert len(store.list_quarantined()) == 1, "隔离区清单应有一条"


# ── D3 源码钉：过滤确实在 history 里，且无用死代码 ─────────────────────
def test_d3_history_source_has_dotdir_guard() -> None:
    """`history()` 源码里必须出现「前导点 ⇒ continue」，防后人删过滤留绿。"""
    import inspect

    src = inspect.getsource(GraphStore.history)

    assert re.search(r"parent\.name\.startswith\(\s*[\"']\.[\"']\s*\)", src), (
        "history() 里没有点目录判定（§1.133 防御被删）"
    )
    assert "continue" in src, "判定没有配套的 continue（过滤未生效）"


def test_d3_only_history_globs_store_root() -> None:
    """根级 `glob("*/v*.json")` 只应出现在 history（列表职责单点，防两处形状漂移）。"""
    import inspect

    src = inspect.getsource(GraphStore)

    hits = [m.start() for m in re.finditer(r"self\.root\.glob\(\s*[\"']\*/v\*\.json[\"']\s*\)", src)]
    assert len(hits) == 1, f"根级归档 glob 出现 {len(hits)} 次，列表职责不止一处"

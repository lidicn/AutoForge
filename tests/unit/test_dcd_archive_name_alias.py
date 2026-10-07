"""审计《安全审计_核实与修复》§四「路径处理」里**成立的那一半**：归档名别名折叠。

HEAD 上实测（不是推断）：
- `GraphStore._dir("living room").name == _dir("living_room").name == "living_room"`；
- `_dir("###") == _dir("@@@") == _dir("") == "graph"`（`strip("_") or "graph"` 的兜底桶）。

穿越那一半**不成立**（保留集是 `isalnum + -_`，`/` 与 `\\` 一律换成 `_`），所以本文件不判它；
判的是"两个不同的合法名字共享同一个版本目录"之后发生的三件事：

1. **写**：第二条归档的新版本落进同一个目录，记录里的 `name` 却是前一个主人的名字 ⇒ 别名隐身，
   双方互相覆盖（`save` / `resave_raw` / `save_version_raw` / `save_conf*` 全部收口）。
2. **删**：`DELETE /api/automations/{name}` 是 `shutil.rmtree(store._dir(name))`，`overwrite` 导入
   是整目录逐条 unlink ⇒ 删 A 连带销毁 B 的**整条**归档，且不可逆。
3. **面**：`name` 来自 URL 路径段/CLI 参数，不在 IR schema 的 `^[a-z][a-z0-9_]*$` 管辖内，
   所以边界上没有东西阻止这两个名字同时出现。

反例族（铁律 #8）：CONTROL（两个不折叠的名字照常各自写各自删）、`_dir` 映射本身**没改**
（改映射＝把存量 `living_room` 目录变成孤儿，属数据可见性变更）、写拒绝后原记录逐字节不变、
`resave_raw` 拒绝后**没有**多出新版本、删拒绝后目录还在、HTTP 两条腿（409 + 归档存活 / 200 正常删）、
空目录不误拒（`_dir_owner` 的 None 分支）、`_delete_archive` 与 `import_bundle(overwrite)` 两条删除入口、
以及"写只看最新一条、删扫每一条"这个**不对称**本身（v1 属于别人但 v2 属于自己时：写放行、删必须红）。

已知**没盖住**的一半在文末两条里钉着（读侧别名与无归档的 conf 同名），不静默、不自签"已审完"。

**2026-10-07 第十八轮把 F12 的默认值改过来了**：归属读不出不再是"None ⇒ 放行"，而是
`ArchiveOwnerUnknown` ⇒ 拒绝。本文件旧版有一条 `test_broken_records_do_not_leak_a_false_conflict`
把 fail-open 当成正确语义钉住——它只否定了"编造一个主人会造成假红"那一半，却同时把"归属未知"
当成了"归属无约束"。第十八轮的 PoC 把链路跑完，证实放行的后果是**另一条归档被真覆盖**
（`save(alias)` 真的产出 v2），不是"守卫放行"而已。`None` 现在只剩一种含义：目录里没有任何版本记录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_ir import load_graph  # noqa: E402
from autoforge.af_store import (  # noqa: E402
    ArchiveNameConflict,
    ArchiveOwnerUnknown,
    GraphStore,
)

ALIAS_A = "living room"  # 折叠后与 ALIAS_B 同目录
ALIAS_B = "living_room"

GRAPH = {
    "ir_version": "0.2.1",
    "id": "demo",
    "name": "demo",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
    ],
    "edges": [{"from": "o", "to": "d", "kind": "then"}],
}


def _graph(extra: str = "light.x"):
    data = json.loads(json.dumps(GRAPH))
    data["nodes"][1]["params"]["entity_id"] = extra
    return load_graph(data)


def _dir_files(store: GraphStore, name: str) -> list[str]:
    d = store._dir(name)
    return sorted(p.name for p in d.glob("v*.json")) if d.is_dir() else []


def _do_entity(rec: dict) -> str:
    """记录里 do 节点的目标 entity_id（按 kind 取，避开 nodes 列表顺序假设）。

    `GraphStore` 把图序列化成 `{"automations":[<auto.raw>]}`，单条 automation 的 `raw`
    里 `nodes` 是列表、`do` 节点带 `params.entity_id`。
    """
    auto = rec["graph"]["automations"][0]
    do = next(n for n in auto["nodes"] if n.get("kind") == "do")
    return do["params"]["entity_id"]


# ── 0. 前提：折叠形状成立，且本批没有改这套映射（改了就等于制造孤儿目录）──────────


def test_dir_mapping_is_many_to_one_on_head(tmp_path):
    store = GraphStore(tmp_path)
    assert store._dir(ALIAS_A) == store._dir(ALIAS_B) == tmp_path / "living_room"
    assert store._dir("###") == store._dir("@@@") == store._dir("") == tmp_path / "graph"


def test_sanitization_shape_is_unchanged_so_existing_dirs_stay_readable(tmp_path):
    """存量归档必须仍按**同一套**映射读得到：本批判的是"撞车后拒绝"，不是"换一套命名"。"""
    store = GraphStore(tmp_path)
    store.save(_graph(), ALIAS_A)
    assert (tmp_path / "living_room" / "v1.json").is_file()
    rec = store.load_record(ALIAS_A)
    assert rec["name"] == ALIAS_A
    assert store.load(ALIAS_A).automations[0].nodes  # 反序列化仍走同一路径，没被新检查挡住


# ── 1. CONTROL：不折叠的名字一律照常（这条必须"什么都不改"也绿）───────────────


def test_control_distinct_names_write_and_delete_independently(tmp_path):
    store = GraphStore(tmp_path)
    assert store.save(_graph("light.a"), "kitchen") == 1
    assert store.save(_graph("light.b"), "bedroom") == 1
    assert store.save(_graph("light.c"), "kitchen") == 2
    assert store.versions("kitchen") == [1, 2]
    assert store.versions("bedroom") == [1]
    assert store.load_record("kitchen")["name"] == "kitchen"
    store._delete_archive("kitchen")
    assert store.versions("kitchen") == []
    assert store.versions("bedroom") == [1], "删一条不许牵连另一条"


def test_empty_dir_is_not_treated_as_a_conflict(tmp_path):
    """目录存在但没有记录（上一批留下的空壳）时，`_dir_owner` 必须是 None 而不是误拒。"""
    store = GraphStore(tmp_path)
    (tmp_path / "living_room").mkdir()
    assert store._dir_owner(ALIAS_B) is None
    assert store.save(_graph(), ALIAS_B) == 1


# ── 2. 写侧：别名撞车必须拒绝，且拒绝得"什么都没发生"────────────────────────


def test_aliased_save_is_refused_and_owner_record_is_byte_identical(tmp_path):
    store = GraphStore(tmp_path)
    store.save(_graph("light.a"), ALIAS_A)
    before = (tmp_path / "living_room" / "v1.json").read_bytes()

    with pytest.raises(ArchiveNameConflict) as exc:
        store.save(_graph("light.b"), ALIAS_B)

    assert ALIAS_A in str(exc.value) and ALIAS_B in str(exc.value)
    assert (tmp_path / "living_room" / "v1.json").read_bytes() == before, "拒绝必须真的没写"
    assert _dir_files(store, ALIAS_B) == ["v1.json"], "不许多出 v2"
    assert _do_entity(store.load_record(ALIAS_A)) == "light.a"


def test_fallback_bucket_names_cannot_both_write(tmp_path):
    """`"###"` / `"@@@"` / `""` 全落进兜底的 `graph`：第二个开始就该被拒。"""
    store = GraphStore(tmp_path)
    store.save(_graph(), "###")
    for alias in ("@@@", "", "  "):
        with pytest.raises(ArchiveNameConflict):
            store.save(_graph(), alias)
    assert _dir_files(store, "@@@") == ["v1.json"]


def test_aliased_resave_raw_refuses_without_bumping_version(tmp_path):
    """启停这类"翻旗子"的写走 `resave_raw`：它在锁内复用已读记录，所以别名检查零额外 IO。"""
    store = GraphStore(tmp_path)
    store.save(_graph(), ALIAS_A)
    before = (tmp_path / "living_room" / "v1.json").read_bytes()
    with pytest.raises(ArchiveNameConflict):
        store.resave_raw(ALIAS_B, lambda g: g.__setitem__("enabled", False))
    assert _dir_files(store, ALIAS_B) == ["v1.json"]
    assert (tmp_path / "living_room" / "v1.json").read_bytes() == before


def test_aliased_save_version_raw_refuses(tmp_path):
    store = GraphStore(tmp_path)
    store.save(_graph(), ALIAS_A)
    with pytest.raises(ArchiveNameConflict):
        store.save_version_raw(ALIAS_B, GRAPH, 5, "2026-10-05T00:00:00+00:00", "灌版本")
    assert _dir_files(store, ALIAS_B) == ["v1.json"]


def test_aliased_conf_writes_refuse_and_write_no_file(tmp_path):
    from autoforge.af_conf import ConfidenceStore

    store = GraphStore(tmp_path)
    store.save(_graph(), ALIAS_A)
    with pytest.raises(ArchiveNameConflict):
        store.save_conf_raw(ALIAS_B, {"seed": 1})
    with pytest.raises(ArchiveNameConflict):
        store.save_conf(ConfidenceStore(), ALIAS_B, "note")
    assert list(tmp_path.glob("*.conf.json")) == []


# ── 3. 删侧：不对称（写只看最新一条，删扫每一条）────────────────────────────


def _forge_record(directory: Path, version: int, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"v{version}.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "saved_at": "2026-10-05T00:00:00+00:00",
                "note": "手搓混入",
                "graph": GRAPH,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_delete_check_is_stricter_than_write_check_by_design(tmp_path):
    """v1 属于 A、v2 属于 B：写检查（只看最新）放行，删检查（扫每一条）必须红。

    这不是漏判——覆盖写只会替换最新那条，而删除会把**整个目录**连 v1 一起销毁。
    """
    store = GraphStore(tmp_path)
    _forge_record(tmp_path / "living_room", 1, ALIAS_A)
    _forge_record(tmp_path / "living_room", 2, ALIAS_B)
    assert store._dir_owner(ALIAS_B) == ALIAS_B
    store._assert_name_owns_dir(ALIAS_B)  # 写侧：最新记录属于自己 ⇒ 放行
    with pytest.raises(ArchiveNameConflict):
        store.assert_deletable(ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json", "v2.json"]


def test_aliased_delete_is_refused_and_dir_survives(tmp_path):
    store = GraphStore(tmp_path)
    store.save(_graph("light.a"), ALIAS_A)
    with pytest.raises(ArchiveNameConflict):
        store._delete_archive(ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json"]


def test_broken_latest_record_refuses_write_rather_than_passing(tmp_path):
    """F12（第十八轮端到端确证）：有记录却读不出归属 ⇒ **不放行**。

    旧形状把这件事写成"坏记录不许误报撞车"，也就是 `_dir_owner` 返回 None ⇒ 守卫放行。
    那个断言钉的是错的一半：它确实避免了"编造一个主人"造成的假红，却同时把"归属未知"
    当成了"归属无约束"。第十八轮的 PoC 跑完整条链，证实放行的后果是**另一条归档被真覆盖**
    （`save("a_b")` 产出 v2），不是"守卫放行"就完事。
    所以这里同时判两面：① 不编造主人（抛的不是 `ArchiveNameConflict`）；② 不放行（写必须被拒、
    目录里不许多出 v2、那条坏记录逐字节不动）。
    """
    store = GraphStore(tmp_path)
    _forge_record(tmp_path / "living_room", 1, ALIAS_A)
    broken = tmp_path / "living_room" / "v1.json"
    broken.write_text("{半截", encoding="utf-8")
    before = broken.read_bytes()

    with pytest.raises(ArchiveOwnerUnknown) as exc:
        store._assert_name_owns_dir(ALIAS_B)
    assert ALIAS_A not in str(exc.value), "归属未知不能伪装成「知道主人是谁」"

    with pytest.raises(ArchiveOwnerUnknown):
        store.save(_graph("light.b"), ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json"], "拒绝必须真的没写：不许多出 v2"
    assert broken.read_bytes() == before


def test_unreadable_record_without_name_is_also_not_unowned(tmp_path):
    """JSON 完好但没有 `name` 字段 ⇒ 同样归属未知。

    `save`/`save_version_raw` 落盘的记录一定带 `name`，所以这条只在手工/半写现场出现。
    旧形状 `owner if isinstance(owner, str) else None` 把它当成"没有主人"放行；本批判它必须红，
    并且红得**不是撞车**（没有第二个名字可比）。同一条也在 `resave_raw` 的锁内复用支上判。
    """
    store = GraphStore(tmp_path)
    d = tmp_path / "living_room"
    d.mkdir(parents=True)
    (d / "v1.json").write_text(
        json.dumps({"version": 1, "graph": GRAPH}), encoding="utf-8"
    )
    with pytest.raises(ArchiveOwnerUnknown):
        store._dir_owner(ALIAS_B)
    with pytest.raises(ArchiveOwnerUnknown):
        store.save(_graph(), ALIAS_B)
    with pytest.raises(ArchiveOwnerUnknown):
        store.resave_raw(ALIAS_B, lambda g: g.__setitem__("enabled", False))
    assert _dir_files(store, ALIAS_B) == ["v1.json"]


def test_delete_refuses_when_no_record_ownership_can_be_read(tmp_path):
    """删侧的 F12 边缘实例：**全部**记录都读不出时，旧形状是逐条 `continue` ⇒ 放行 `rmtree`。

    第十八轮记的是"需全部不可解析才 fail-open"这一格。坏记录旧注释说"由加载路径各自处置"，
    可删除根本不是加载路径——它紧跟着就把整个目录销毁了。读不出归属的那条**可能正是别人的
    归档**，跳过它等于"看不见就当不存在"。
    """
    store = GraphStore(tmp_path)
    d = tmp_path / "living_room"
    _forge_record(d, 1, ALIAS_A)
    (d / "v2.json").write_text("{半截", encoding="utf-8")
    # 逐条处置：可读且属别人 ⇒ 撞车（这一条第十八轮之前就拦住）；坏记录本身也必须拦得住。
    with pytest.raises(ArchiveNameConflict):
        store.assert_deletable(ALIAS_B)
    (d / "v1.json").write_text("{半截", encoding="utf-8")       # 现在两条都读不出
    with pytest.raises(ArchiveOwnerUnknown):
        store.assert_deletable(ALIAS_B)
    with pytest.raises(ArchiveOwnerUnknown):
        store._delete_archive(ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json", "v2.json"], "拒绝删除后两条记录都还在"


def test_delete_check_does_not_softer_on_partial_corruption(tmp_path):
    """坏记录**不削弱**删侧保护：目录里还剩一条可读且属别人 ⇒ 照旧红（第十八轮确证的形状）。"""
    store = GraphStore(tmp_path)
    d = tmp_path / "living_room"
    _forge_record(d, 1, ALIAS_A)
    (d / "v2.json").write_text("{半截", encoding="utf-8")
    with pytest.raises(ArchiveNameConflict):
        store._delete_archive(ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json", "v2.json"]


def test_delete_refuses_record_without_name_field(tmp_path):
    """删侧同一格：JSON 完好但**没有 name 字段** ⇒ 「确认不了它属于谁」不等于「它不属于谁」。

    这条是变异腿逼出来的：把 `assert_deletable` 那个 `if not isinstance(owner, str)` 改成
    `if False` 之后，本文件当时**全绿**——坏 JSON 那条腿盖住了不可解析，却没盖住可解析但无归属。
    """
    store = GraphStore(tmp_path)
    d = tmp_path / "living_room"
    d.mkdir(parents=True)
    (d / "v1.json").write_text(json.dumps({"version": 1, "graph": GRAPH}), encoding="utf-8")
    with pytest.raises(ArchiveOwnerUnknown):
        store.assert_deletable(ALIAS_B)
    with pytest.raises(ArchiveOwnerUnknown):
        store._delete_archive(ALIAS_B)
    assert _dir_files(store, ALIAS_B) == ["v1.json"]


# ── 3.5 CONTROL：归属查得清时，收紧的那一半不许假红 ─────────────────────────

def test_control_no_record_means_no_owner_and_still_passes(tmp_path):
    """None 的新含义只有一个：**目录里没有任何版本记录**。这一支必须照旧放行。

    F12 收的是"有记录却读不出"，不是"没有归属信息"。把两种情况混成一个 None，就等于
    把整条归档目录的空壳、未落盘的新名字全都拒掉——假红一多，这道门会被当噪音绕开。
    """
    store = GraphStore(tmp_path)
    assert store._dir_owner("never_saved") is None            # 目录不存在
    (tmp_path / "living_room").mkdir()
    assert store._dir_owner(ALIAS_B) is None                  # 空壳目录
    store._assert_name_owns_dir(ALIAS_B)
    store.assert_deletable(ALIAS_B)
    assert store.save(_graph(), ALIAS_B) == 1                 # 新名字照常可写


def test_control_healthy_archive_writes_and_deletes(tmp_path):
    store = GraphStore(tmp_path)
    store.save(_graph("light.a"), ALIAS_A)
    store.save(_graph("light.a"), ALIAS_A)
    assert _dir_files(store, ALIAS_A) == ["v1.json", "v2.json"]
    store.assert_deletable(ALIAS_A)
    store._delete_archive(ALIAS_A)
    assert _dir_files(store, ALIAS_A) == []


def test_http_delete_of_unreadable_record_returns_409_not_500(tmp_path, monkeypatch):
    """HTTP 面：归属未知必须落成 409（可修复后重试），不能冒成 500 让调用方以为是自己的格式问题。"""
    store = GraphStore(tmp_path / "store")
    _forge_record(tmp_path / "store" / "living_room", 1, ALIAS_A)
    (tmp_path / "store" / "living_room" / "v1.json").write_text("{半截", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    r = client.delete(f"/api/automations/{ALIAS_B}")
    assert r.status_code == 409, r.text
    assert "读不出归属" in r.json()["error"]
    assert _dir_files(store, ALIAS_B) == ["v1.json"], "409 之后目录必须原样还在"


# ── 4. 导入面：overwrite 是删除，skip 不是 ──────────────────────────────────


def test_import_overwrite_refuses_alias_and_keeps_target_archive(tmp_path):
    src = GraphStore(tmp_path / "src")
    src.save(_graph("light.new"), ALIAS_B)
    bundle = src.export_bundle()

    dst = GraphStore(tmp_path / "dst")
    dst.save(_graph("light.old"), ALIAS_A)
    with pytest.raises(ArchiveNameConflict):
        dst.import_bundle(bundle, "overwrite")
    assert _do_entity(dst.load_record(ALIAS_A)) == "light.old"


def test_import_skip_and_rename_do_not_destroy_anything(tmp_path):
    src = GraphStore(tmp_path / "src")
    src.save(_graph("light.new"), ALIAS_B)
    bundle = src.export_bundle()

    dst = GraphStore(tmp_path / "dst")
    dst.save(_graph("light.old"), ALIAS_A)
    report = dst.import_bundle(bundle, "skip")
    assert ALIAS_B in report["skipped"], "折叠目录里已有版本 ⇒ 跳过而不是覆盖"
    assert _do_entity(dst.load_record(ALIAS_A)) == "light.old"


# ── 5. HTTP 面：409 而不是 500，且归档存活；正常名字不受影响 ─────────────────


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    return TestClient(build_app(store_root=str(tmp_path / "store")))


def test_http_delete_of_aliased_name_returns_409_and_archive_survives(tmp_path, monkeypatch):
    store = GraphStore(tmp_path / "store")
    store.save(_graph("light.a"), ALIAS_A)
    client = _client(tmp_path, monkeypatch)

    r = client.delete(f"/api/automations/{ALIAS_B}")
    assert r.status_code == 409, r.text
    assert "里混有" in r.json()["error"]
    assert _dir_files(store, ALIAS_B) == ["v1.json"], "409 之后对方整条归档必须还在"


def test_http_enable_of_aliased_name_returns_409(tmp_path, monkeypatch):
    """异常处理器挂在 app 上，不是只挂在 delete 端点：启停这条写路径同样落 409。"""
    store = GraphStore(tmp_path / "store")
    store.save(_graph("light.a"), ALIAS_A)
    client = _client(tmp_path, monkeypatch)

    r = client.post(f"/api/automations/{ALIAS_B}/enable")
    assert r.status_code == 409, r.text
    assert store.load_record(ALIAS_A)["graph"].get("enabled") is not True


def test_http_delete_of_owned_name_still_returns_200(tmp_path, monkeypatch):
    """收紧的那一半不许假红：正常删除照旧成功（假红一多，这条门会被当噪音绕开）。"""
    store = GraphStore(tmp_path / "store")
    store.save(_graph(), "kitchen")
    client = _client(tmp_path, monkeypatch)

    r = client.delete("/api/automations/kitchen")
    assert r.status_code == 200, r.text
    assert store.versions("kitchen") == []


def test_http_delete_of_unknown_name_still_returns_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/api/automations/never_saved").status_code == 404


# ── 6. 已知没盖住的一半：钉住而不是宣布"审完了"（铁律 #5）───────────────────


def test_read_side_alias_is_still_a_read_of_the_owners_archive(tmp_path):
    """读侧别名**本批不判**：按别名名字 GET 读到的是主人的归档。

    写侧拒绝 ⇒ 目录里不会再混进第二个主人的记录；但**存量**上仍可以用别人的名字读到那份归档
    （`_dir` 是多对一，读没有归属可核对）。彻底修需要 name→目录 一对一（哈希后缀或边界
    schema 校验），那是数据可见性变更 ⇒ 交 DCD，不在本批自决。
    """
    store = GraphStore(tmp_path)
    store.save(_graph("light.a"), ALIAS_A)
    assert store.load_record(ALIAS_B)["name"] == ALIAS_A


def test_conf_without_any_archive_is_still_keyed_by_the_folded_name(tmp_path):
    """没有归档时 conf 同名仍会互相覆盖（`_dir_owner` 无从判断 ⇒ 放行）。

    这里钉的是"检查的依据是归档记录"这一事实：影响面是孤儿元数据（`names()` 里没有它，
    导出/列表都不含），不是归档本体。彻底修要给 conf 也建归属记录，属新文件格式 ⇒ 不在本批。
    """
    store = GraphStore(tmp_path)
    store.save_conf_raw(ALIAS_A, {"marker": "A"})
    store.save_conf_raw(ALIAS_B, {"marker": "B"})  # 不抛：目录里没有任何归档记录
    payload = json.loads((tmp_path / "living_room.conf.json").read_text(encoding="utf-8"))
    assert payload["marker"] == "B", "已知缺口：第二条覆盖了第一条"

"""测试区删除面（`af_test.clear()` / MCP 工具 `af_test_clear`）的守卫与如实报数判据。

收《AF完整架构与运行时说明》§十八 结构性残余 B.3。缺陷有两格，本批前都是真的（修前探针的逐字读数见
执行记录 §二之八十六）：

1. **无守卫的不可逆删除**：旧实现是 `shutil.rmtree(self.test_root, ignore_errors=True)`，而
   `test_root` 由调用方定，与正式存储根没有任何对账。部署里正式根是 `forge serve --store-root`
   （缺省 `DEFAULT_STORE_ROOT = ".forge"`），所以「test_root 指到 `.forge` 本身或它的父目录」这一档，
   一次 `af_test_clear` 就把整条归档线连同版本历史一起销毁，工具照旧回 `{"ok": true, "cleared": true}`。
2. **`ignore_errors=True` 的假绿**：删除失败被抹平成「清完了」。这一格与仓内「没做成都不许报成 done」
   那一族同形，所以判据不能只问「有没有守卫」，还得钉「报的数是不是盘上的数」。

守卫刻意做得窄：只拦两种「删下去必然出格」的形状（盘根／等于或包住正式根），合法测试区照常放行。
不引入标记文件（正式根里也有 `pending/`，看形状不具鉴别力），也不抄第二份配置——对照量就是这个
dispatch 手里那枚 `store.root`。归属/前缀不明则不放行这一档沿用 `af_store._dir_owner` 与
`assert_deletable` 的 F12 口径。

17 条腿各自单独可红（副本树变异自证逐枚点名）：把 `protected_root` 改成可选、把对照量从 `store.root`
换成字面默认值、让守卫排在删除之后、把 `residual`/`cleared` 写回常量、或让 `ignore_errors` 回来，
都各有至少一条腿红。
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import shutil
from pathlib import Path

import pytest

from autoforge import af_test as af_test_mod
from autoforge.af_ir import load_graph
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore
from autoforge.af_test import (
    DELETE_ERRORS_MAX,
    GUARD_ERR_COVERS_PRODUCTION,
    GUARD_ERR_FILESYSTEM_ROOT,
    assert_test_area_deletable,
)

ROOT = Path(__file__).resolve().parents[2]
AF_TEST_SRC = ROOT / "src" / "autoforge" / "af_test.py"
AF_MCP_SRC = ROOT / "src" / "autoforge" / "af_mcp.py"

#: MCP 面要显式给身份（裁定 20261004 §一 Q2=B：默认拒绝），`af_test_clear` 需要 write
_WRITE = {"subject": "guard-test", "scopes": ["write"]}

_REAL_RMTREE = shutil.rmtree

_DEMO_IR = {
    "ir_version": "0.2.1",
    "id": "front_door",
    "name": "front_door",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "o", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}


def _production_store(tmp_path: Path) -> GraphStore:
    """正式区里有一条真归档的 store（守卫拒判时「没伤到它」必须能被证出来）。"""
    store = GraphStore(root=str(tmp_path / ".forge"))
    store.save(load_graph(_DEMO_IR), "front_door", note="guard fixture", owner="guard-test")
    return store


def _archive_bytes(store: GraphStore) -> bytes:
    path = Path(store.root) / "front_door" / f"v{store.latest('front_door')}.json"
    return path.read_bytes()


def _seed_area(channel: af_test_mod.TestChannel, *, files: int = 2) -> int:
    """往测试区放真条目，返回删除前盘上的条目数（三个子目录本身也计入）。"""
    for i in range(files):
        (channel.reports_dir / f"b{i}.json").write_text("{}", encoding="utf-8")
        (channel.graphs_dir / f"g{i}.txt").write_text("x", encoding="utf-8")
    return sum(1 for _ in channel.test_root.rglob("*"))


def _exc_info(exc: BaseException) -> tuple:
    return (type(exc), exc, exc.__traceback__)


def _patch_rmtree_for(monkeypatch, target: Path, behavior) -> None:
    """只把 `af_test.clear()` 那一次 rmtree 换成 behavior。

    `shutil` 是全局模块对象，`af_test` 里 `shutil.rmtree` 在调用时才查属性，所以注入必须打在属性上；
    非本次目标的调用一律转给真实现，免得把 pytest 清理旧临时目录的那一路也一起废掉。
    """
    wanted = Path(target).resolve()

    def wrapper(path, *args, **kwargs):
        if Path(str(path)).resolve() != wanted:
            return _REAL_RMTREE(path, *args, **kwargs)
        return behavior(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", wrapper)


# ---------------------------------------------------------------- 一、守卫拦下的那一档


def test_target_equal_to_production_root_is_refused_and_archive_untouched(tmp_path: Path) -> None:
    store = _production_store(tmp_path)
    before = _archive_bytes(store)
    channel = af_test_mod.TestChannel(str(store.root))  # 部署里把测试区根写成正式根，就是这一档
    with pytest.raises(af_test_mod.TestGuardError) as exc:
        channel.clear(protected_root=store.root)
    assert exc.value.code == GUARD_ERR_COVERS_PRODUCTION
    assert store.latest("front_door") == 1
    assert _archive_bytes(store) == before
    assert Path(store.root).is_dir()


def test_ancestor_of_production_root_is_refused(tmp_path: Path) -> None:
    store = _production_store(tmp_path)
    before = _archive_bytes(store)
    channel = af_test_mod.TestChannel(str(tmp_path))  # test_root 是正式根的父目录：删下去连带销毁整条归档
    with pytest.raises(af_test_mod.TestGuardError) as exc:
        channel.clear(protected_root=store.root)
    assert exc.value.code == GUARD_ERR_COVERS_PRODUCTION
    assert _archive_bytes(store) == before
    assert (Path(store.root) / "front_door" / "v1.json").exists()


def test_relative_production_root_still_matches_absolute_test_root(tmp_path, monkeypatch) -> None:
    """正式根常写成相对路径（`DEFAULT_STORE_ROOT = ".forge"`）：不 resolve 就是「同一条路径的两种写法」
    看起来互不包含 ⇒ 守卫形同虚设。这一腿专钉两侧 `resolve()`。"""
    _production_store(tmp_path)
    monkeypatch.chdir(tmp_path)
    absolute_test_root = str((tmp_path / ".forge").resolve())
    with pytest.raises(af_test_mod.TestGuardError) as exc:
        assert_test_area_deletable(absolute_test_root, protected_root=".forge")
    assert exc.value.code == GUARD_ERR_COVERS_PRODUCTION


def test_filesystem_root_is_refused(tmp_path: Path) -> None:
    anchor = Path("C:\\") if os.name == "nt" else Path("/")
    with pytest.raises(af_test_mod.TestGuardError) as exc:
        assert_test_area_deletable(anchor, protected_root=tmp_path / ".forge")
    assert exc.value.code == GUARD_ERR_FILESYSTEM_ROOT


def test_guard_runs_before_any_deletion(tmp_path: Path) -> None:
    """顺序判据：守卫必须在 rmtree 之前。反例是「先删再判、判完抛错」——那种实现这条腿红。"""
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(tmp_path / "outer"))  # 合法形状，先铺真条目
    seeded = _seed_area(channel, files=1)
    doomed = af_test_mod.TestChannel(str(tmp_path))  # 越界形状：外层目录包着正式根
    with pytest.raises(af_test_mod.TestGuardError):
        doomed.clear(protected_root=store.root)
    assert channel.test_root.is_dir()
    assert sum(1 for _ in channel.test_root.rglob("*")) == seeded  # 一条没少


# ---------------------------------------------------------------- 二、合法形状照常放行


def test_legitimate_child_is_deleted_for_real_and_recreated(tmp_path: Path) -> None:
    store = _production_store(tmp_path)
    before = _archive_bytes(store)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    seeded = _seed_area(channel, files=3)
    result = channel.clear(protected_root=store.root)
    assert result["ok"] is True and result["cleared"] is True
    assert result["residual"] == 0
    assert result["entries_before"] == seeded
    assert "errors" not in result
    # 三子目录重建，但里面是真空的：residual 在重建之前量，否则 `_ensure_dirs()` 会把三条空目录算成残留
    assert all((channel.test_root / n).is_dir() for n in ("pending", "graphs", "reports"))
    assert sum(1 for _ in channel.test_root.rglob("*")) == 3
    assert _archive_bytes(store) == before


def test_missing_test_root_clears_to_a_green_ok_without_touching_production(tmp_path: Path) -> None:
    """测试区根本不存在（首次调用）⇒ 不该炸，也不该把正式根当成「要删的东西」。"""
    store = _production_store(tmp_path)
    before = _archive_bytes(store)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    _REAL_RMTREE(channel.test_root)
    result = channel.clear(protected_root=store.root)
    assert result["ok"] is True and result["residual"] == 0
    assert _archive_bytes(store) == before


# ---------------------------------------------------------------- 三、报的数＝盘上的数


def test_no_op_deletion_is_not_reported_as_cleared(tmp_path: Path, monkeypatch) -> None:
    """假绿那一格的正面对撞：删除一步没做成（这里让 rmtree 什么都不干、也不报错），
    `cleared`/`ok` 必须跟着盘面走。旧实现这条必红——它回的是常量 True。"""
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    seeded = _seed_area(channel, files=2)
    _patch_rmtree_for(monkeypatch, channel.test_root, lambda path, *a, **k: None)
    result = channel.clear(protected_root=store.root)
    assert result["residual"] == seeded
    assert result["cleared"] is False
    assert result["ok"] is False
    assert sum(1 for _ in channel.test_root.rglob("*")) == seeded


def test_partial_failure_reports_survivors_and_error_count(tmp_path: Path, monkeypatch) -> None:
    """部分失败：真删掉两条目录、留下 `reports/` 里的两条文件，再对它做一次真 `os.rmdir`
    （非空目录 ⇒ 真抛错）。残留和错误都是从盘面上数出来的，不是 rmtree 的返回值。"""
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    _seed_area(channel, files=2)

    def partial(path, onerror=None, **_kw):
        here = Path(str(path))
        for name in ("pending", "graphs"):
            victim = here / name
            if victim.is_dir():
                _REAL_RMTREE(victim)
        leftover = here / "reports"
        try:
            os.rmdir(str(leftover))
        except OSError as exc:
            if onerror is not None:
                onerror(os.rmdir, str(leftover), _exc_info(exc))

    _patch_rmtree_for(monkeypatch, channel.test_root, partial)
    result = channel.clear(protected_root=store.root)
    assert result["cleared"] is False and result["ok"] is False
    assert result["residual"] == 3  # reports/ 目录本身 + 它里面那两条文件
    assert result["errors_total"] == len(result["errors"]) == 1
    assert "reports" in result["errors"][0]
    assert (channel.test_root / "reports" / "b0.json").exists()


def test_error_list_is_bounded_but_the_count_is_honest(tmp_path: Path, monkeypatch) -> None:
    """常驻服务里一次失败删除不能攒出无界清单（BUG-01 同族）：明细封顶，总数照实报。"""
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    _seed_area(channel, files=1)

    def always_fails(path, onerror=None, **_kw):
        if onerror is None:
            return
        for i in range(DELETE_ERRORS_MAX + 5):
            onerror(os.rmdir, f"{path}/x{i}", _exc_info(OSError("injected")))

    _patch_rmtree_for(monkeypatch, channel.test_root, always_fails)
    result = channel.clear(protected_root=store.root)
    assert len(result["errors"]) == DELETE_ERRORS_MAX
    assert result["errors_total"] == DELETE_ERRORS_MAX + 5
    assert result["ok"] is False and result["cleared"] is False


# ---------------------------------------------------------------- 四、形状不回来的静态腿


def _af_test_tree() -> ast.Module:
    return ast.parse(AF_TEST_SRC.read_text(encoding="utf-8"))


def test_ignore_errors_keyword_is_gone_from_af_test() -> None:
    """根因那一格：`ignore_errors=True` 不许回来（回来了第三节那三条腿就白写）。"""
    offenders = []
    for call in (n for n in ast.walk(_af_test_tree()) if isinstance(n, ast.Call)):
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
        if name == "rmtree":
            offenders += [kw.arg for kw in call.keywords if kw.arg == "ignore_errors"]
    assert offenders == [], f"af_test.py 又用回 rmtree(ignore_errors=…)：{offenders}"


def test_guard_compares_against_the_argument_not_a_default_path() -> None:
    """对照量必须是调用方递进来的 `protected_root`，不许拿 `/data/test` 那种字面默认值当判据。"""
    guard = next(
        n for n in _af_test_tree().body
        if isinstance(n, ast.FunctionDef) and n.name == "assert_test_area_deletable"
    )
    assert [a.arg for a in guard.args.args] == ["test_root", "protected_root"]
    names = {n.id for stmt in guard.body[1:] for n in ast.walk(stmt) if isinstance(n, ast.Name)}
    assert "protected_root" in names, "守卫没读 protected_root，等于拿第二份配置当判据"
    strings = {
        c.value
        for stmt in guard.body[1:]
        for c in ast.walk(stmt)
        if isinstance(c, ast.Constant) and isinstance(c.value, str)
    }
    assert not [s for s in strings if "/data" in s], f"守卫里出现了硬编码部署路径：{strings}"


def test_protected_root_is_required_keyword_only() -> None:
    protected = inspect.signature(af_test_mod.TestChannel.clear).parameters["protected_root"]
    assert protected.kind is inspect.Parameter.KEYWORD_ONLY
    assert protected.default is inspect.Parameter.empty


def test_clear_without_protected_root_deletes_nothing(tmp_path: Path) -> None:
    """上一腿的行为半边：少递对照量宁可当场 `TypeError`，也不做那次不可逆删除。"""
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    seeded = _seed_area(channel, files=1)
    with pytest.raises(TypeError):
        channel.clear()
    assert sum(1 for _ in channel.test_root.rglob("*")) == seeded
    assert store.latest("front_door") == 1
    _archive_bytes(store)


# ---------------------------------------------------------------- 五、MCP 那半边脸


def _tool_schema(name: str) -> dict:
    return next(t[2] for t in TOOLS if t[0] == name)


def test_mcp_gives_the_agent_no_way_to_name_a_delete_target(tmp_path: Path, monkeypatch) -> None:
    """删除目标由服务端定：schema 零参数，且 `test_root=` 这类未声明键在 dispatch 就被拒、一条没删。"""
    assert _tool_schema("af_test_clear")["properties"] == {}
    store = _production_store(tmp_path)
    channel = af_test_mod.TestChannel(str(Path(store.root) / "test"))
    seeded = _seed_area(channel, files=1)
    monkeypatch.setattr(af_test_mod, "get_test_channel", lambda *a, **k: channel)
    content, is_error = dispatch(
        "af_test_clear", {"test_root": str(Path(store.root) / "test")}, store, current=_WRITE
    )
    assert is_error is True
    assert "参数未声明" in content[0]["text"]
    assert sum(1 for _ in channel.test_root.rglob("*")) == seeded


def test_mcp_guard_rejection_becomes_an_honest_envelope(tmp_path: Path, monkeypatch) -> None:
    """服务端把测试区配歪（正好落在正式根上）时：工具不许抛给对端、也不许塌成「清完了」，
    要如实回 `ok=False` + 具名 code，且正式归档字节不变。"""
    store = _production_store(tmp_path)
    before = _archive_bytes(store)
    channel = af_test_mod.TestChannel(str(store.root))
    _seed_area(channel, files=1)
    monkeypatch.setattr(af_test_mod, "get_test_channel", lambda *a, **k: channel)
    content, is_error = dispatch("af_test_clear", {}, store, current=_WRITE)
    payload = json.loads(content[0]["text"])
    assert is_error is False
    assert payload["ok"] is False
    assert payload["cleared"] is False
    assert payload["code"] == GUARD_ERR_COVERS_PRODUCTION
    assert _archive_bytes(store) == before
    assert (Path(store.root) / "front_door" / "v1.json").exists()


def test_mcp_clear_call_site_passes_store_root() -> None:
    """静态钉住调用点 `clear(protected_root=store.root)`：换成 `"/data/test"`、`Path.cwd()`
    或干脆不递，这条都红。行为腿只证明「这一次跑对了」，这条证明写法没漂。"""
    tree = ast.parse(AF_MCP_SRC.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_t_test_clear"
    )
    calls = [
        c for c in ast.walk(fn)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "clear"
    ]
    assert len(calls) == 1
    kwargs = {k.arg: k.value for k in calls[0].keywords}
    assert set(kwargs) == {"protected_root"}
    value = kwargs["protected_root"]
    assert isinstance(value, ast.Attribute) and value.attr == "root"
    assert isinstance(value.value, ast.Name) and value.value.id == "store"

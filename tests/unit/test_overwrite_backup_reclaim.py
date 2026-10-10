"""§十八 B.14 判据：overwrite 让位备份的回收必须可数、可见、可归因。

缺陷形状只有一个——**静默**。`af_store.py` 的备份生命周期原先带 5 枚 `shutil.rmtree(...,
ignore_errors=True)`，把"整份旧归档还躺在盘上（而读侧看不见）"抹平成和"已回收"一模一样：
`_drop_stash` 返回 `None`、调用点不检查、0 条日志。

两条纪律写在这里，因为它们是本批真踩过的：

1. **失败注入一律打在 `rmtree` 遍历内部**（`os.unlink` / `os.rmdir`），不替换 `shutil.rmtree`
   本身。`ignore_errors=True` 吞的是遍历各站的错误，替换整函数让它一进门就抛的那种它不吞——
   那样注入出来的"红"证明不了任何事（等于拿一条假绿的反证当证据）。真实现场是 NAS/Windows 上
   文件被别的进程占着，失败点就在 unlink/rmdir。
2. **日志读数面先自证**：`af_store` 的 logger 名是 `autoforge.store`（不是 `autoforge.af_store`），
   挂错名字的 handler 会让"0 条"恒真。`store_logs` 这个 fixture 在 setup 里先发一枚哨兵、抓不到
   就当场失败，所以任何一条断言里的"0 条/1 条"都是被验证过的读数面量出来的。
3. **注入本身要自证落过**：`break_delete` 返回命中清单，每条腿 `assert hits`。predicate 一站没中
   就等于这条腿什么都没测——CI（POSIX）上按整条路径认目标的三条腿正是这样静默读绿的。
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
from pathlib import Path

import pytest

from autoforge import af_store as store_mod
from autoforge.af_ir import load_graph
from autoforge.af_store import (
    BACKUP_DIR_SUFFIX,
    GraphStore,
    _purge_tree,
)

REPO = Path(__file__).resolve().parents[2]
STORE_PY = REPO / "src" / "autoforge" / "af_store.py"
CLI_PY = REPO / "src" / "autoforge" / "af_cli.py"
MCP_PY = REPO / "src" / "autoforge" / "af_mcp.py"


# ─────────────────────────────────────────────────────────────────────
# 读数面 / 注入面
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def store_logs():
    """抓 `autoforge.store` 的日志；setup 里先证明这个 handler 真抓得到，再清空。"""
    records: list[tuple[int, str]] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append((record.levelno, record.getMessage()))

    root = logging.getLogger()
    handler = _Cap()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    sentinel = "SENTINEL-B14-读数面自证"
    store_mod.logger.warning(sentinel)
    assert any(sentinel in message for _, message in records), (
        "日志读数面自证失败：handler 抓不到 af_store 的日志，"
        "本文件所有『0 条/1 条』断言都会恒真"
    )
    records.clear()
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


@pytest.fixture
def break_delete():
    """把删除失败注入到遍历内部（真实现场形状），teardown 一定还原。

    `shutil.rmtree` 在 POSIX 走 fd 版遍历，传给 `os.unlink` 的是**裸文件名**（`v9.json`）而不是
    整条路径——按路径认目标的 predicate 会一站都不中，注入静默失效、腿照样绿（CI 实测就是这一形）。
    这里把 `_use_fd_functions` 按回 False（Windows 本来就 False），两侧同形；命中清单由腿自己核对。
    """
    originals = (os.unlink, os.rmdir)
    fd_original = shutil._use_fd_functions
    shutil._use_fd_functions = False

    def _apply(predicate) -> list[str]:
        hits: list[str] = []

        def _wrap(real):
            def inner(path, *args, **kwargs):
                if predicate(str(path)):
                    hits.append(str(path))
                    raise OSError(13, "injected delete failure", str(path))
                return real(path, *args, **kwargs)

            return inner

        os.unlink, os.rmdir = _wrap(originals[0]), _wrap(originals[1])
        return hits

    yield _apply
    os.unlink, os.rmdir = originals
    shutil._use_fd_functions = fd_original


def _warns(records: list[tuple[int, str]]) -> list[str]:
    return [message for level, message in records if level >= logging.WARNING]


def test_injection_predicate_is_handed_full_paths(tmp_path, break_delete):
    """predicate 收到的是整条路径，不是裸文件名——这条腿钉住跨平台同形的那一格。

    fd 版遍历（POSIX 默认）传的是 `entry.name`，按路径认目标的 predicate 会一站不中；
    fixture 把 `_use_fd_functions` 按回 False，两侧都必须被这条腿验一次，而不是靠人记得。
    """
    root = tmp_path / "store"
    (root / "deep").mkdir(parents=True)
    (root / "deep" / "x.json").write_text("{}", encoding="utf-8")

    hits = break_delete(lambda path: path.endswith("x.json"))
    _purge_tree(root)

    # 命中项必须带得上目录前缀：fd 版遍历只交 `entry.name`，那样 predicate 认的是裸文件名
    assert hits and all(str(root) in hit for hit in hits), hits


# ─────────────────────────────────────────────────────────────────────
# 存储 / bundle 造形
# ─────────────────────────────────────────────────────────────────────


def _raw(name: str, target: str) -> dict:
    return {
        "ir_version": "0.2.1",
        "id": name,
        "name": name,
        "version": 1,
        "mode": "single",
        "nodes": [
            {
                "id": "o",
                "kind": "on",
                "trigger": {"type": "state", "entity_id": "binary_sensor.front_door", "to": "on"},
            },
            {"id": "d", "kind": "do", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": target}},
            {"id": "p", "kind": "pass"},
        ],
        "edges": [{"from": "o", "to": "d", "kind": "then"}, {"from": "d", "to": "p", "kind": "then"}],
    }


def _build(root: Path, *, name: str = "demo", times: int = 1) -> GraphStore:
    store = GraphStore(root)
    for i in range(times):
        store.save(load_graph(_raw(name, f"light.entrance{i}")), name, note=f"n{i}")
    return store


def _bundle(tmp_path: Path, *, name: str = "demo", times: int = 2) -> dict:
    return _build(tmp_path / "src", name=name, times=times).export_bundle()


def _backup_dir(root: Path, name: str = "demo") -> Path:
    return root / f"{name}{BACKUP_DIR_SUFFIX}"


def _seed_stale_backup(root: Path, *, name: str = "demo") -> Path:
    """造一份"上一轮没回收"的备份：形状与 `_stash_archive` 产出的一致（versions/ 深一层）。"""
    backup = _backup_dir(root, name)
    (backup / "versions").mkdir(parents=True, exist_ok=True)
    (backup / "versions" / "v9.json").write_text("{}", encoding="utf-8")
    return backup


# ─────────────────────────────────────────────────────────────────────
# 1. 正常档：什么都不改时，备份被回收、报告干净、零日志（"不许顺手报残留"那一半）
# ─────────────────────────────────────────────────────────────────────


def test_clean_overwrite_reclaims_backup_and_reports_nothing(tmp_path, store_logs):
    root = tmp_path / "store"
    store = _build(root, times=1)
    report = store.import_bundle(_bundle(tmp_path), "overwrite")

    assert report["imported"] == ["demo"]
    assert report["errors"] == []
    assert report["residual_backups"] == []
    assert store.versions("demo") == [1, 2]
    assert not _backup_dir(root).exists()
    assert _warns(store_logs) == []


def test_purge_tree_reports_done_only_when_target_is_gone(tmp_path):
    target = tmp_path / "gone"
    (target / "deep").mkdir(parents=True)
    (target / "deep" / "v1.json").write_text("{}", encoding="utf-8")

    reclaimed, residual, errors = _purge_tree(target)

    assert (reclaimed, residual, errors) == (True, 0, [])
    assert _purge_tree(tmp_path / "never-existed") == (True, 0, [])


# ─────────────────────────────────────────────────────────────────────
# 2. 收尾那枚（`_drop_stash`）删失败：导入照旧做成，但残留必须被数出来、报出来
# ─────────────────────────────────────────────────────────────────────


def test_unreclaimed_backup_is_counted_reported_and_warned(tmp_path, store_logs, break_delete):
    root = tmp_path / "store"
    store = _build(root, times=1)
    bundle = _bundle(tmp_path)

    hits = break_delete(lambda path: BACKUP_DIR_SUFFIX in path)
    report = store.import_bundle(bundle, "overwrite")
    assert hits, "删除注入一站没落——这一腿什么都没测到"

    backup = _backup_dir(root)
    on_disk = sum(1 for p in backup.rglob("*") if p.is_file())
    assert backup.is_dir(), "注入没生效：备份被真删了，这一格什么都没证"

    entries = report["residual_backups"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "demo"
    assert Path(entry["path"]) == backup
    assert entry["residual_files"] == on_disk, "报的数必须是盘上现数的数"
    assert entry["errors_total"] == len(entry["errors"]) >= 1

    # 导入这件事确实做成了：这一格不冒充"条目失败"，所以既进报告也留日志，而 ok/imported 不动
    assert report["imported"] == ["demo"]
    assert report["errors"] == []
    assert report["ok"] is True
    assert store.versions("demo") == [1, 2]

    warns = _warns(store_logs)
    assert len(warns) == 1
    assert str(backup) in warns[0]
    assert f"{on_disk} 个文件" in warns[0]


def test_residual_files_counted_from_disk_not_from_error_stations(tmp_path, break_delete):
    """两个数不是一回事：遍历可以只失败一半，`residual` 不许写成 `len(errors)`。"""
    root = tmp_path / "backup"
    (root / "deep").mkdir(parents=True)
    (root / "deep" / "a.json").write_text("{}", encoding="utf-8")
    (root / "deep" / "b.json").write_text("{}", encoding="utf-8")

    hits = break_delete(lambda path: path.endswith("a.json"))
    reclaimed, residual, errors = _purge_tree(root)
    assert hits, "删除注入一站没落——这一腿什么都没测到"

    assert reclaimed is False
    assert residual == 1, "盘上只剩 a.json，残留数就该是 1"
    assert any("a.json" in station for station in errors)
    # 遍历还会撞第二、第三站（非空子目录、非空本目录）；站点数按现场形状走，不钉死
    assert len(errors) >= 2
    assert residual != len(errors), "残留个数不许写成 len(errors)——两个数不是一回事"


# ─────────────────────────────────────────────────────────────────────
# 3. 让位前那次预清理（全模块唯一的回收触发点）：清不掉就具名拒判，不许抛伪装成撞车
# ─────────────────────────────────────────────────────────────────────


def test_stale_backup_blocks_stash_with_the_real_reason(tmp_path, break_delete):
    root = tmp_path / "store"
    store = _build(root, times=2)
    live_before = store.versions("demo")
    backup = _seed_stale_backup(root)

    hits = break_delete(lambda path: BACKUP_DIR_SUFFIX in path)
    with pytest.raises(store_mod.BackupNotReclaimed) as excinfo:
        store.import_bundle(_bundle(tmp_path), "overwrite")
    assert hits, "删除注入一站没落——这一腿什么都没测到"

    message = str(excinfo.value)
    assert BACKUP_DIR_SUFFIX in message
    assert "清不掉" in message
    assert "尚未被改动" in message
    # 真因可归因：不再是一柄与备份毫不相干的 WinError 183
    assert not isinstance(excinfo.value, FileExistsError)
    # fail-closed 的对照面：正式区与那份可捞回的备份都没被动过
    assert store.versions("demo") == live_before
    assert (root / "demo").is_dir()
    assert (backup / "versions" / "v9.json").is_file()


def test_next_same_name_overwrite_is_the_reclamation_trigger(tmp_path):
    """不注入时，同名下次 overwrite 前的预清理会真的把残留回收掉——这一档必须钉住，
    否则"下一次还会再试"就成了散文里的空头承诺。"""
    root = tmp_path / "store"
    store = _build(root, times=1)
    backup = _seed_stale_backup(root)

    # 残留读不出（`history()` 的 glob 比备份目录浅一层）——这正是"盘上有、面上无"成立的前提
    assert store.names() == ["demo"]
    assert [p.name for p in root.glob("*/v*.json")] == ["v1.json"]

    report = store.import_bundle(_bundle(tmp_path), "overwrite")

    assert report["imported"] == ["demo"]
    assert report["residual_backups"] == []
    assert not backup.exists()


# ─────────────────────────────────────────────────────────────────────
# 4. 回滚两条腿：回滚成功但壳没回收 ⇒ WARNING；壳挡着回滚 ⇒ ERROR 且备份整份可捞
# ─────────────────────────────────────────────────────────────────────


def test_rollback_with_unreclaimed_shell_leaves_one_warning(tmp_path, store_logs, break_delete):
    root = tmp_path / "store"
    store = _build(root, times=1)
    bundle = _bundle(tmp_path, times=2)

    original = store.save_version_raw
    attempts: list[int] = []

    def boom(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise RuntimeError("injected write failure on the 2nd version")
        return original(*args, **kwargs)

    store.save_version_raw = boom  # type: ignore[method-assign]
    hits = break_delete(lambda path: BACKUP_DIR_SUFFIX in path)

    with pytest.raises(RuntimeError, match="injected write failure"):
        store.import_bundle(bundle, "overwrite")
    assert hits, "删除注入一站没落——这一腿什么都没测到"

    # 旧归档回到正式区（这一档本来就没坏），原错没被吞
    assert store.versions("demo") == [1]
    assert store.names() == ["demo"]

    warns = _warns(store_logs)
    assert len(warns) == 1
    assert "备份目录没能回收" in warns[0]
    assert str(_backup_dir(root)) in warns[0]


def test_rollback_blocked_keeps_backup_whole_and_logs_error(tmp_path, store_logs, break_delete):
    root = tmp_path / "store"
    root.mkdir(parents=True)
    half_written = root / "demo"
    half_written.write_text("half-written", encoding="utf-8")  # 正式位置被删不掉的实体占着
    backup = _seed_stale_backup(root)

    hits = break_delete(lambda path: Path(path).name == "demo")
    GraphStore(root)._unstash_archive("demo", backup)
    assert hits, "删除注入一站没落——这一腿什么都没测到"

    errors = [message for level, message in store_logs if level >= logging.ERROR]
    assert len(errors) == 1
    assert "回滚受阻" in errors[0]
    assert str(backup) in errors[0]
    # 没做成就不许搬：备份整份留在原地可捞，正式位置也没被写坏
    assert (backup / "versions" / "v9.json").is_file()
    assert half_written.is_file()


# ─────────────────────────────────────────────────────────────────────
# 5. 静态腿：防复发的三格（删除口径、后缀单一真源、报告键同源）
# ─────────────────────────────────────────────────────────────────────


def _rmtree_call_lines(tree: ast.AST) -> set[int]:
    return {
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rmtree"
    }


def _ignore_errors_lines(tree: ast.AST) -> set[int]:
    return {
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rmtree"
        and any(keyword.arg == "ignore_errors" for keyword in node.keywords)
    }


def test_store_has_no_ignore_errors_and_one_delete_entry_point():
    """`ignore_errors=True` 在本模块归零，且真删除只走 `_purge_tree` 一个入口。

    按 AST 判而不是按名字 grep：本文件的 docstring 里就写着那个词，grep 会自己踩自己。
    """
    tree = ast.parse(STORE_PY.read_text(encoding="utf-8"))
    assert _ignore_errors_lines(tree) == set()

    purge = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_purge_tree"),
        None,
    )
    assert purge is not None, "_purge_tree 没了——删除入口的钉子必须跟着实现一起改"
    assert _rmtree_call_lines(purge) == _rmtree_call_lines(tree)


def test_backup_suffix_has_a_single_literal_source():
    tree = ast.parse(STORE_PY.read_text(encoding="utf-8"))
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == BACKUP_DIR_SUFFIX
    ]
    assert len(hits) == 1, f"备份后缀的字面量只许出现在常量定义那一行，现读 {hits}"


def test_cli_and_mcp_import_faces_read_the_report_keys():
    """报告键的两张调用脸同源：CLI 只能读真实存在的键，MCP 必须整份透传。

    新键若哪张脸没接住，"如实带出"就只存在于 docstring 里。
    """
    store_tree = ast.parse(STORE_PY.read_text(encoding="utf-8"))
    import_fn = next(
        (node for node in ast.walk(store_tree)
         if isinstance(node, ast.FunctionDef) and node.name == "import_bundle"),
        None,
    )
    assert import_fn is not None
    report_keys: set[str] = set()
    for node in ast.walk(import_fn):
        # 报告初始化在现读里是带注解的赋值（`report: dict[str, Any] = {…}`），只扫 Assign 会恒空
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == "report" for t in targets) \
                and isinstance(node.value, ast.Dict):
            report_keys |= {
                key.value for key in node.value.keys if isinstance(key, ast.Constant)
            }
    assert report_keys, "报告初始化的键一个都没扫到——这条腿本身失效了"
    assert "residual_backups" in report_keys

    cli_tree = ast.parse(CLI_PY.read_text(encoding="utf-8"))
    import_cmd = next(
        (node for node in ast.walk(cli_tree)
         if isinstance(node, ast.FunctionDef)
         and any(
             isinstance(inner, ast.Call)
             and isinstance(inner.func, ast.Attribute)
             and inner.func.attr == "import_bundle"
             for inner in ast.walk(node)
         )),
        None,
    )
    assert import_cmd is not None, "af_cli 里调用 import_bundle 的那个命令找不到了"
    cli_keys: set[str] = set()
    for node in ast.walk(import_cmd):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == "report" and isinstance(node.slice, ast.Constant):
            cli_keys.add(str(node.slice.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "report"
            and node.func.attr in ("get", "pop")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            cli_keys.add(str(node.args[0].value))
    assert "residual_backups" in cli_keys, "CLI 面上读不到残留那一格——回收失败又只剩日志"
    assert cli_keys <= report_keys, f"CLI 读了报告里没有的键：{sorted(cli_keys - report_keys)}"

    mcp_tree = ast.parse(MCP_PY.read_text(encoding="utf-8"))
    t_import = next(
        (node for node in ast.walk(mcp_tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_t_import"),
        None,
    )
    assert t_import is not None
    # 透传的判据是"return 直接把调用结果交出去"，不是"函数体里没有下标"——`args["bundle"]` 读的是入参
    returns = [node for node in ast.walk(t_import) if isinstance(node, ast.Return)]
    assert len(returns) == 1, "_t_import 的返回档数变了，透传读数得重盘"
    assert isinstance(returns[0].value, ast.Call), (
        "_t_import 不再把服务层报告整份交出去（返回的是别的形状），"
        "新加的 residual_backups 就会被丢在这一格"
    )

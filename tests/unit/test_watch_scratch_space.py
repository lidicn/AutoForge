"""watch 磁盘卫生判据（20261012 审计席确证那一族，执行记录 §二之一百二十六）。

判的两件事：
1. `start_watch` 每次新建的 `watch-ir-*` 部署目录**必须有人扫**——建新的之前扫旧的，`Popen`
   抛错那一档就地收回本次那份；
2. `watch.log` **必须有大小闸**——起子进程之前过闸，轮转失败不拦启动。

A 族钉形状（真仓 AST，代码面），B 族钉行为（合成树），C 族是反空集腿：把修前形状种回去，
必须被同一枚 A 判定扫红。D 族钉常量与射程（清扫器只认自己写出的那个前缀）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import time
from pathlib import Path

import pytest

from autoforge import af_service as svc

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src/autoforge" / "af_service.py"
PREFIX = "watch-ir-"
IR_NAME = "deployed_ir.json"


def _src_text() -> str:
    return SRC.read_text(encoding="utf-8")


def _func(node: ast.AST, name: str) -> ast.FunctionDef:
    for found in ast.walk(node):
        if isinstance(found, ast.FunctionDef) and found.name == name:
            return found
    raise AssertionError(f"`{name}` 读不出——它在射程外还是被改名了？")


def _call_names(node: ast.AST) -> list[str]:
    out: list[str] = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            out.append(ast.unparse(call.func))
    return out


# ── A 族：真仓形状 ────────────────────────────────────────────────


def test_a1_start_watch_reaps_before_creating():
    tree = ast.parse(_src_text())
    start = _func(tree, "start_watch")
    assert "_reap_stale_watch_scratch" in _call_names(start), (
        "start_watch 不再先扫旧目录 ⇒ `watch-ir-*` 又回到只增不减那一档"
    )


def test_a2_mkdtemp_carries_the_sweepable_prefix():
    tree = ast.parse(_src_text())
    start = _func(tree, "start_watch")
    mkdtemp = [
        c
        for c in ast.walk(start)
        if isinstance(c, ast.Call) and ast.unparse(c.func).endswith("mkdtemp")
    ]
    assert mkdtemp, "start_watch 里读不出 mkdtemp——部署 IR 的落点换了地方，清扫器射程就对不上"
    for call in mkdtemp:
        keywords = {kw.arg for kw in call.keywords}
        assert "prefix" in keywords, "裸 mkdtemp 落回默认 `tmpXXXXXXXX` 形状，清扫器扫不到"
        values = {kw.arg: ast.unparse(kw.value) for kw in call.keywords}
        assert values["prefix"] == "WATCH_IR_PREFIX", (
            f"prefix 写成了 `{values['prefix']}`，而清扫器按 `WATCH_IR_PREFIX` 射程——两份说法会各扫各的"
        )


def test_a3_log_gate_runs_before_the_log_is_opened():
    tree = ast.parse(_src_text())
    start = _func(tree, "start_watch")
    #: ⚠️ 只取 `ast.Call` 节点。先前写成「凡子树里含这枚调用的节点都算」，外层 `FunctionDef`
    #: 自己就含全部调用、行号永远是 `def` 那一行 ⇒ 这一档怎么挪都绿（变异 M3 实测撞过）。
    gate = [
        c.lineno
        for c in ast.walk(start)
        if isinstance(c, ast.Call) and ast.unparse(c.func) == "_gate_watch_log"
    ]
    opens = [
        c.lineno
        for c in ast.walk(start)
        if isinstance(c, ast.Call)
        and ast.unparse(c.func).endswith(".open")
        and "watch.log" in ast.unparse(c.func)
    ]
    assert gate and opens, f"轮转闸调用 {gate}／日志 open {opens}——缺一样这一格就没接上"
    assert min(gate) < min(opens), "先开日志再轮转＝轮转永远赶不上本次追加"


def test_a4_popen_failure_path_drops_its_own_scratch():
    tree = ast.parse(_src_text())
    start = _func(tree, "start_watch")
    handlers = [h for h in ast.walk(start) if isinstance(h, ast.ExceptHandler)]
    assert any("_drop_watch_scratch" in _call_names(h) for h in handlers), (
        "子进程确定没起来那一档不收回本次目录，就得等下一次启动才扫——失败路径正是最容易连撞的"
    )


# ── D 族：常量与射程 ─────────────────────────────────────────────


def test_d1_constants_are_the_single_source():
    assert svc.WATCH_IR_PREFIX == PREFIX
    assert svc.WATCH_IR_FILENAME == IR_NAME
    assert svc.WATCH_LOG_MAX_BYTES >= 1024 * 1024, "上限低于 1 MiB 会让每次启动都轮转"
    assert svc.WATCH_SCRATCH_MIN_AGE_S > 0.0, "年龄下限归零会把并发那次启动的窗口扫进去"


def test_d2_reaper_scope_is_the_prefix_it_writes():
    tree = ast.parse(_src_text())
    reaper = _func(tree, "_reap_stale_watch_scratch")
    globs = [
        c for c in ast.walk(reaper) if isinstance(c, ast.Call) and ast.unparse(c.func).endswith(".glob")
    ]
    assert len(globs) == 1, "清扫器里 glob 站点不是恰好一枚——射程靠这一枚钉，别把它拆成两处说法"
    assert globs[0].args, "glob 没有位置参数＝射程读不出"
    pattern = globs[0].args[0]
    if isinstance(pattern, ast.Name):
        #: 允许一跳间接（先 `_pat = f"{WATCH_IR_PREFIX}*"` 再 glob(_pat)）——钉的是射程来自
        #: 那个常量，不是钉它必须写在同一行（同义改写不该红）。
        for assign in ast.walk(reaper):
            if isinstance(assign, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == pattern.id for t in assign.targets
            ):
                pattern = assign.value
                break
    unparsed = ast.unparse(pattern)
    assert "WATCH_IR_PREFIX" in unparsed, f"glob 射程取的是 `{unparsed}`，不再跟着本层前缀走"


# ── B 族：行为（合成树） ─────────────────────────────────────────


def _scratch(root: Path, name: str, *, extra: str | None = None, mtime: float | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / IR_NAME).write_text('{"id": "x"}', encoding="utf-8")
    if extra:
        (d / extra).write_text("foreign", encoding="utf-8")
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    return d


def test_b1_reaper_deletes_only_its_own_old_scratch(tmp_path):
    stale = _scratch(tmp_path, PREFIX + "aaaa1111", mtime=time.time() - 3600)
    foreign_shape = _scratch(tmp_path, PREFIX + "bbbb2222", extra="note.txt", mtime=time.time() - 3600)
    other_face = tmp_path / "tmpZZZZZZ"
    other_face.mkdir()
    (other_face / "unrelated.txt").write_text("x", encoding="utf-8")

    deleted = svc._reap_stale_watch_scratch(tmp_path, None)

    assert deleted == 1
    assert not stale.exists(), "旧那一份没扫掉 ⇒ 累积照原样留着"
    assert foreign_shape.exists(), "目录里混了别的东西＝归属未知，按 F12 口径不许动"
    assert other_face.exists(), "不是本层前缀的目录被删了——清扫器越界"


def test_b2_reaper_keeps_the_live_sidecar_scratch(tmp_path):
    live = _scratch(tmp_path, PREFIX + "live0001", mtime=time.time() - 3600)
    live_ir = live / IR_NAME
    deleted = svc._reap_stale_watch_scratch(tmp_path, str(live_ir))
    assert deleted == 0
    assert live_ir.exists(), "sidecar 指着的那份是在役 watcher 的身份锚点，不是垃圾"


def test_b3_reaper_skips_dirs_inside_the_launch_window(tmp_path):
    fresh = _scratch(tmp_path, PREFIX + "young001", mtime=time.time())
    deleted = svc._reap_stale_watch_scratch(tmp_path, None)
    assert deleted == 0
    assert fresh.exists(), "并发的另一次启动可能刚建目录还没写出 sidecar"


def test_b4_reaper_survives_an_unreadable_root(tmp_path, monkeypatch):
    def boom(self, *_a, **_kw):  # noqa: ANN001 - 冒充 Path.glob 抛 OSError
        raise OSError("模拟枚举失败")

    monkeypatch.setattr(Path, "glob", boom)
    assert svc._reap_stale_watch_scratch(tmp_path, None) == 0, "清扫器抛穿＝磁盘卫生把 watch 启动打死"


def test_b5_drop_scratch_removes_the_pair(tmp_path):
    d = _scratch(tmp_path, PREFIX + "drop0001")
    assert svc._drop_watch_scratch(d) is True
    assert not d.exists()


def test_b6_drop_scratch_never_raises(tmp_path, monkeypatch):
    d = _scratch(tmp_path, PREFIX + "drop0002")

    def boom(self, *_a, **_kw):  # noqa: ANN001
        raise OSError("模拟 rmdir 失败")

    monkeypatch.setattr(Path, "rmdir", boom)
    assert svc._drop_watch_scratch(d) is False, "删不掉要如实回 False，但绝不抛穿"


def test_b7_log_gate_rotates_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "WATCH_LOG_MAX_BYTES", 16)
    log = tmp_path / "watch.log"
    log.write_bytes(b"0" * 64)
    svc._gate_watch_log(tmp_path)
    assert not log.exists(), "超限却没轮转"
    assert (tmp_path / "watch.log.1").read_bytes() == b"0" * 64


def test_b8_log_gate_leaves_a_small_log_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "WATCH_LOG_MAX_BYTES", 1024 * 1024)
    log = tmp_path / "watch.log"
    log.write_bytes(b"0" * 10)
    svc._gate_watch_log(tmp_path)
    assert log.read_bytes() == b"0" * 10
    assert not (tmp_path / "watch.log.1").exists()


def test_b9_log_gate_tolerates_a_failed_rotation(tmp_path, monkeypatch):
    #: 旧档位置成一个非空目录 ⇒ `unlink()` 必抛（Windows/Linux 同形状），
    #: 这一腿同时是「轮转失败不许拦启动」的反例来源。
    monkeypatch.setattr(svc, "WATCH_LOG_MAX_BYTES", 16)
    log = tmp_path / "watch.log"
    log.write_bytes(b"0" * 64)
    blocked = tmp_path / "watch.log.1"
    blocked.mkdir()
    (blocked / "keep.txt").write_text("x", encoding="utf-8")
    svc._gate_watch_log(tmp_path)  # 不抛即合格
    assert log.read_bytes() == b"0" * 64, "轮转失败却把原日志挪走／清掉了——那才是丢数据"
    assert blocked.is_dir()


def test_b10_missing_log_is_not_an_error(tmp_path):
    svc._gate_watch_log(tmp_path)  # 文件不在 ⇒ 静默返回


def test_b11_start_watch_sweeps_old_and_drops_its_own_on_popen_failure(tmp_path, monkeypatch):
    stale = _scratch(tmp_path, PREFIX + "old00001", mtime=time.time() - 3600)

    def raiser(*_a, **_kw):
        raise OSError("模拟 Popen 起不来")

    monkeypatch.setattr(subprocess, "Popen", raiser)
    result = svc.start_watch({"id": "g1", "nodes": [], "edges": []}, store_root=str(tmp_path))

    assert result["ok"] is False
    assert not stale.exists(), "旧那一份没被扫"
    survivors = sorted(p.name for p in tmp_path.glob(f"{PREFIX}*"))
    assert survivors == [], f"本次失败那一份留下了：{survivors}"


# ── C 族：反空集（修前形状必须被扫红） ───────────────────────────


PRE_FIX = '''
import tempfile
from pathlib import Path
WATCH_IR_FILENAME = "deployed_ir.json"


def _gate_watch_log(root):
    return None


def start_watch(ir, store_root=None):
    root = Path(store_root)
    tmp = Path(tempfile.mkdtemp(dir=str(root))) / WATCH_IR_FILENAME
    try:
        with (root / "watch.log").open("ab") as logf:
            proc = subprocess.Popen(["forge", "watch", str(tmp)], stdout=logf)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "pid": proc.pid}
'''


def test_c1_the_pre_fix_shape_fails_the_same_predicates():
    tree = ast.parse(PRE_FIX)
    start = _func(tree, "start_watch")
    names = _call_names(start)
    assert "_reap_stale_watch_scratch" not in names, "修前形状本该读不出清扫器调用"
    assert "_gate_watch_log" not in names
    mkdtemp = [
        c
        for c in ast.walk(start)
        if isinstance(c, ast.Call) and ast.unparse(c.func).endswith("mkdtemp")
    ]
    assert all("prefix" not in {kw.arg for kw in c.keywords} for c in mkdtemp)
    assert not any(
        isinstance(h, ast.ExceptHandler) and "_drop_watch_scratch" in _call_names(h)
        for h in ast.walk(start)
    )


def test_c2_the_real_writer_is_wired_not_just_defined():
    #: 助手定义了却没接线＝没修。三枚助手都必须在 `start_watch` 里被叫到。
    tree = ast.parse(_src_text())
    for helper in ("_reap_stale_watch_scratch", "_drop_watch_scratch", "_gate_watch_log"):
        _func(tree, helper)
        assert helper in _call_names(_func(tree, "start_watch")), f"{helper} 只在定义里，没接上落盘点"

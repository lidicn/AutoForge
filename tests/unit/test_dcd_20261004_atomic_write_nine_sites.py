"""裁定 20261004 §二 那 9 站收口的值语义判据。

起因：`8689b39` 立门时把 9 个"固定名 tmp + 不 fsync"的落盘站点冻进基线，裁定 §二 钉了收口顺序
（`af_premiere` 两站 → `af_version` → `af_scene` → `af_fire_recorder` → `af_predict`/`af_pretrigger`/
`af_shadow`/`af_flock`）。本批把 9 站统一收到 `af_atomic.atomic_write_text` 上并把基线清空。

静态那一半（"有没有走助手"）由 `scripts/check_atomic_write_sites.py` 判；**本文件判的是静态门判不出的
另一半**：助手在"写到一半失败"时到底留了什么、tmp 名是不是每次都不同、fsync 是不是真的在 replace 之前
发生、以及这 9 个调用点各自落盘后能不能读回来。少一条，收口就只是把形状换了个名字。
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from autoforge.af_atomic import atomic_write_text
from autoforge.af_fire_recorder import JsonFireStore
from autoforge.af_flock import FileLock
from autoforge.af_premiere import PremiereStore, TrialStore
from autoforge.af_predict import Predictor
from autoforge.af_pretrigger import TriggerHistory
from autoforge.af_scene import Scene, SceneManager
from autoforge.af_shadow import ShadowLogStore, ShadowRecord
from autoforge.af_store import atomic_write_text as store_named_helper
from autoforge.af_version import VersionManager

SRC = Path(__file__).resolve().parents[2] / "src" / "autoforge"


# ── 助手本身：失败留什么、名字唯一性、fsync 顺序 ──────────────────────

def test_replace_failure_keeps_the_original_and_leaves_no_residue(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_text(target, '{"v": 1}')

    def boom(src, dst, *a, **kw):
        raise OSError("replace 被拦")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, '{"v": 2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
    assert not list(tmp_path.glob("*.tmp")), list(tmp_path.iterdir())


def test_each_write_gets_a_different_tmp_name(tmp_path, monkeypatch):
    """固定名的害处是"第二个写者互相截断"；随机名这条判据钉的是每次调用拿到不同的 tmp。"""
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst, *a, **kw):
        seen.append(str(src))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", spy)
    target = tmp_path / "state.json"
    atomic_write_text(target, "a")
    atomic_write_text(target, "b")
    assert len(seen) == 2 and seen[0] != seen[1], seen
    assert all(s.endswith(".tmp") for s in seen), seen
    assert not list(tmp_path.glob("*.tmp")), list(tmp_path.iterdir())


def test_fsync_happens_before_replace(tmp_path, monkeypatch):
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        os, "replace", lambda s, d, *a, **kw: (order.append("replace"), real_replace(s, d, *a, **kw))[1]
    )
    atomic_write_text(tmp_path / "x.json", "{}")
    assert order.index("fsync") < order.index("replace"), order


def test_helper_is_defined_exactly_once_in_src():
    """唯一真源：`def atomic_write_text` 在 src 里只许有一份，且住在 `af_atomic.py`。

    这条防的是"收口之后又长回第二份实现"——那正是本批要消掉的东西，也是刚被扫掉的那族形状。
    """
    homes = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "atomic_write_text"
            for n in ast.walk(tree)
        ):
            homes.append(path.name)
    assert homes == ["af_atomic.py"], homes
    assert store_named_helper is atomic_write_text, "af_store 里那个名字必须是同一个对象，不是第二份实现"


def test_af_atomic_depends_on_nothing_but_stdlib():
    """它存在的理由就是"谁都能 import 而不成环"——一旦引入包内依赖，这个理由当场失效。"""
    tree = ast.parse((SRC / "af_atomic.py").read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
            assert node.level == 0, "相对 import 会成环"
    # 真源取解释器自己的标准库名单：上一版把允许名单手抄成 4 个名字，af_atomic 加一枚 `import json`
    # 就把这条腿判红——手抄名单在"标准库"这个口径上必然过期。
    assert all(
        not m.startswith("autoforge") and m.split(".")[0] in sys.stdlib_module_names for m in mods
    ), mods


# ── 九个调用点：落盘后可读回、目录里没有 tmp 残留 ─────────────────────

def _assert_no_tmp(dir_path: Path) -> None:
    leftovers = [p.name for p in dir_path.glob("*.tmp")] + [p.name for p in dir_path.glob("*.json.tmp")]
    assert not leftovers, leftovers


def test_premiere_and_trial_store_roundtrip_without_residue(tmp_path):
    p1 = tmp_path / "premiere.json"
    store = PremiereStore(path=str(p1))
    code = store.issue("sha-door-1")
    assert json.loads(p1.read_text(encoding="utf-8"))["version"] == 1
    assert PremiereStore(path=str(p1)).peek(code) is not None, "落盘后没读回同一条码"
    _assert_no_tmp(tmp_path)

    p2 = tmp_path / "trial.json"
    trial = TrialStore(path=str(p2))
    trial.save()
    assert json.loads(p2.read_text(encoding="utf-8"))["version"] == 1
    assert TrialStore(path=str(p2)).load() == 0
    _assert_no_tmp(tmp_path)


def test_version_manager_save_roundtrip_without_residue(tmp_path):
    mgr = VersionManager(root=tmp_path)
    mgr._save("auto_a")
    files = list(mgr.versions_dir.glob("*.json"))
    assert files, list(mgr.versions_dir.iterdir())
    assert json.loads(files[0].read_text(encoding="utf-8"))["automation_id"] == "auto_a"
    _assert_no_tmp(mgr.versions_dir)


def test_scene_manager_save_state_roundtrip_without_residue(tmp_path):
    class _Exec:
        def set_automation_enabled(self, automation_id, enabled):
            return True

    mgr = SceneManager([Scene("s1", "回家", ["auto_a"])], _Exec(), str(tmp_path))
    assert mgr._save_state() is True
    assert mgr.last_persist_error is None
    body = json.loads((tmp_path / "scenes.json").read_text(encoding="utf-8"))
    assert body["scenes"]["s1"]["name"] == "回家"
    assert SceneManager([Scene("s1", "回家", ["auto_a"])], _Exec(), str(tmp_path)).last_persist_error is None
    _assert_no_tmp(tmp_path)


def test_fire_recorder_roundtrip_without_residue(tmp_path):
    store = JsonFireStore(tmp_path)
    store._records["rule-1"] = {"2026-10-05": {"count": 3}}
    store._save()
    assert JsonFireStore(tmp_path)._records["rule-1"]["2026-10-05"]["count"] == 3
    _assert_no_tmp(tmp_path)


def test_predictor_save_roundtrip_without_residue(tmp_path):
    class _Hist:
        def events_for(self, automation_id):
            return []

    predictor = Predictor(_Hist(), object(), tmp_path / "pred")
    predictor._save()
    path = Path(predictor.predictions_path)
    assert path.read_text(encoding="utf-8").endswith("\n"), "尾部换行是收口时特意保住的形状"
    assert json.loads(path.read_text(encoding="utf-8"))["automations"] == {}
    assert Predictor(_Hist(), object(), tmp_path / "pred").predictions_path == str(path)
    _assert_no_tmp(path.parent)


def test_pretrigger_history_roundtrip_without_residue(tmp_path):
    hist = TriggerHistory(persist_dir=str(tmp_path))
    hist.record("auto_a", at=1_759_500_000.0, state={"light.living": "on"})
    path = Path(hist.path)
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert "auto_a" in json.loads(path.read_text(encoding="utf-8"))["events"]
    assert TriggerHistory(persist_dir=str(tmp_path)).events_for("auto_a"), "落盘后没读回事件"
    _assert_no_tmp(tmp_path)


def test_shadow_log_store_roundtrip_without_residue(tmp_path):
    store = ShadowLogStore()
    store.append(
        ShadowRecord(
            record_id="r1",
            automation_id="auto_a",
            instance_id="i1",
            node_id="n1",
            action="light.turn_on",
            params={"entity_id": "light.living"},
            expected_state={"light.living": "on"},
            created_at=1_759_500_000.0,
            compare_after=5.0,
        )
    )
    path = tmp_path / "shadow_log.json"
    store.save(str(path))
    assert json.loads(path.read_text(encoding="utf-8")) == store.dump()
    restored = ShadowLogStore()
    assert restored.load_file(str(path)) == 1
    assert restored.get("r1").automation_id == "auto_a"
    _assert_no_tmp(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="Windows 上 chmod 不进 ACL，位模式无从判定（ADM B-14 只在 POSIX 侧可验）")
def test_lock_sidecar_stays_owner_only_without_explicit_chmod(tmp_path):
    """`_stamp` 原来靠一句显式 `tmp.chmod(0o600)` 限权；改走助手后这个性质由 mkstemp 的默认位保证。

    这条判据就是那句 chmod 的替身——真删掉它而性质还在才算收口，只改注释不算。
    """
    lock = FileLock(tmp_path / "instance.lock")
    lock.acquire()
    try:
        assert lock.holder()["owner"], lock.holder()
        mode = lock._info_path.stat().st_mode
        assert mode & 0o077 == 0, oct(mode)
    finally:
        lock.release()
    _assert_no_tmp(tmp_path)

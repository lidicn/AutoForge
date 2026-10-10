#!/usr/bin/env python3
"""`scripts/check_process_model.py` 的判据腿（第六轮审计 ARCH-06 批次，执行记录 §二之百）。

三条纪律，逐条对应下面真实存在的腿：

1. **真仓绿**：现读的 4 枚生命周期站点、33 枚同步原语、9 枚模块级共享名与 `docs/进程模型清单.md` 逐行对撞，
   并把几枚**读数本身**钉死（计数行、`af_live` 占 3 枚名且该模块锁数为 0）——"门绿"不等于"门在看"，
   读数钉住才防得住扫描器静默退化；
2. **每条判据单独可红**：合成树上 B／C／D／E 各注入一枚，红的那条必须带自己的字母前缀；
3. **反空集与"注入未生效"自证**：全树读不出生命周期站点要走 `exit 2`（不许把"扫不到"当"没问题"）；
   每一次变异都断言锚点真的命中了原文，否则整轮绿是假的（§二之九十九 的 MX2 就是栽在这一格）。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check_process_model.py"
DOC_REL = "docs/进程模型清单.md"
SPEC = importlib.util.spec_from_file_location("check_process_model", GATE)
cpm = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(cpm)


def run_gate(root: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)] + ([str(root)] if root else [])
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def load_trees(root: Path) -> dict:
    trees, errs = cpm.parse_all(cpm.py_files(root))
    assert not errs, errs
    return trees


AF_CLI = '''"""合成 serve／watch 两条收尾形状。"""
import signal
import uvicorn


def serve(store_root):
    bridge = _start_bridge()
    try:
        uvicorn.run(bridge.app, host="0.0.0.0")
    finally:
        if bridge is not None:
            bridge.stop()


def watch(stop, ticker, coord):
    def _on_sigterm(signum, frame):
        stop.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        run_watch(stop)
    finally:
        stop.set()
        ticker.join(timeout=2.0)
        coord.release()
'''

AF_SERVICE = '''"""合成一条 watch 生命周期线。"""
import os
import subprocess


def stop_watch(pid):
    os.kill(pid, 15)


def start_watch(cmd, logf):
    os.kill(cmd, 15)
    return subprocess.Popen(cmd, stdout=logf, start_new_session=True)


_LOCK = __import__("threading").Lock()
_HELD = set()


def owner():
    global _OWNER
    return _OWNER


_OWNER = None
'''

AF_LIVE = '''import threading

_TICK = None
_GUARD = threading.Lock()


def start_ticker():
    global _TICK
    with _GUARD:
        _TICK = object()
'''

REGISTER = "## 三、生命周期站点登记（默认拒绝，新站点不登记即红）"
CEILING = "## 四、每类上限（只减不增）"


def good_row(path: str, lineno: int, kind: str) -> str:
    return (
        f"- `{path}:{lineno}` · kind={kind} · 拉起：合成站点只为判据服务 · 收：`stop_watch` 发 SIGTERM · "
        "收不到会怎样：孤儿 watcher 会继续持有协调锁并按档位下发动作 · "
        "依据：docs/进程模型清单.md · 认领：AF：合成树里的现读锚点"
    )


def valid_ceilings(sites) -> str:
    out = []
    for kind in cpm.KINDS:
        out.append(f"- {kind} = {sum(1 for s in sites if s['kind'] == kind)}")
    return "\n".join(out)


def build_doc(root: Path, register_rows: list[str], ceiling_text: str) -> None:
    body = "\n".join(register_rows) if register_rows else "（本树没有生命周期站点）"
    text = (
        "# 合成进程模型清单\n\n"
        "## 一、口径\n\n合成树只为本门的判据腿服务。\n\n"
        "## 二、现读自动段\n\n"
        f"{cpm.AUTO_BEGIN}\n{cpm.AUTO_END}\n\n"
        f"{REGISTER}\n\n{body}\n\n"
        f"{CEILING}\n\n{ceiling_text}\n\n"
        "## 五、复测对撞\n\n略。\n"
    )
    doc_p = root / DOC_REL
    doc_p.parent.mkdir(parents=True, exist_ok=True)
    doc_p.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "src" / "autoforge"
    pkg.mkdir(parents=True)
    (pkg / "af_cli.py").write_text(AF_CLI, encoding="utf-8", newline="\n")
    (pkg / "af_service.py").write_text(AF_SERVICE, encoding="utf-8", newline="\n")
    (pkg / "af_live.py").write_text(AF_LIVE, encoding="utf-8", newline="\n")
    trees, errs = cpm.parse_all(cpm.py_files(tmp_path))
    assert not errs
    sites = cpm.scan_lifecycle(trees, tmp_path)
    rows = [good_row(s["path"], s["lineno"], s["kind"]) for s in sites]
    build_doc(tmp_path, rows, valid_ceilings(sites))
    assert cpm.write_doc(tmp_path) == 0
    return tmp_path


def mutate(root: Path, rel: str, old: str, new: str) -> None:
    """锚点必须命中——不命中就抛，绝不允许"变异没生效"混成绿（§二之九十九 MX2 的教训）。"""
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"变异锚点没命中：{rel} 里找不到 {old[:40]!r}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
    assert (root / rel).read_text(encoding="utf-8") != text


def edit_doc_section(root: Path, heading: str, new_body: str) -> None:
    p = root / DOC_REL
    text = p.read_text(encoding="utf-8")
    assert heading in text
    i = text.index(heading) + len(heading)
    m = re.search(r"\n#{2,3} ", text[i:])
    end = i + (m.start() if m else len(text) - i)
    p.write_text(text[:i] + f"\n\n{new_body}\n" + text[end:], encoding="utf-8", newline="\n")


def register_rows(root: Path) -> list[str]:
    text = (root / DOC_REL).read_text(encoding="utf-8")
    return [l for l in text.splitlines() if l.startswith("- `")]


def append_code(root: Path, rel: str, text: str) -> None:
    """在文件**末尾**追加：不移动既有站点行号，红只该来自新站点那一格。"""
    p = root / rel
    before = p.read_text(encoding="utf-8")
    p.write_text(before.rstrip("\n") + "\n\n" + text, encoding="utf-8", newline="\n")
    assert (root / rel).read_text(encoding="utf-8").startswith(before.rstrip("\n"))


def findings_text(root: Path) -> str:
    r = run_gate(root)
    return (r.stdout or "") + (r.stderr or "")


# ── 真仓读数面 ──

def test_real_repo_gate_green():
    r = run_gate()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "进程模型门禁干净" in r.stdout


def test_real_repo_lifecycle_counts_pinned():
    trees = load_trees(REPO)
    sites = cpm.scan_lifecycle(trees, REPO)
    by = {k: sum(1 for s in sites if s["kind"] == k) for k in cpm.KINDS}
    assert by["subprocess.Popen"] == 1
    assert by["os.kill"] == 2
    assert by["signal.signal"] == 1
    assert by["atexit.register"] == 0
    assert by["os.fork"] == 0 and by["multiprocessing.Process"] == 0


def test_real_repo_counts_line_in_doc():
    doc = (REPO / DOC_REL).read_text(encoding="utf-8")
    assert "atexit.register=0" in doc and "signal.signal=1" in doc
    assert "os.kill=2" in doc and "subprocess.Popen=1" in doc


def test_real_repo_primitive_counts_pinned():
    trees = load_trees(REPO)
    prims = cpm.scan_primitives(trees, REPO)
    inproc = [r for r in prims if r["prim"] in cpm.INPROC_PRIMS]
    cross = [r for r in prims if r["prim"] in cpm.CROSSPROC_PRIMS]
    assert (len(inproc), len({r["path"] for r in inproc})) == (17, 10)
    assert (len(cross), len({r["path"] for r in cross})) == (16, 6)
    assert sum(1 for r in cross if r["path"].endswith("af_service.py")) == 3
    doc = (REPO / DOC_REL).read_text(encoding="utf-8")
    assert "进程内 17 站点／10 个文件 · 跨进程 16 站点／6 个文件" in doc


def test_real_repo_shared_names_tick_trio():
    trees = load_trees(REPO)
    shared = {(r["name"], r["path"]): r for r in cpm.scan_shared_names(trees, REPO)}
    live = [r for r in shared.values() if r["path"].endswith("af_live.py")]
    assert {r["name"] for r in live} == {"_tick_exit_reason", "_tick_supervisor", "_ticker_thread"}
    assert all(r["locks"] == 0 for r in live)          # 报告那句"漏网点"的机器读数
    assert all(r["with"] is False for r in shared.values())  # 全 9 枚所在函数都没有 with
    assert len(shared) == 9


def test_real_doc_register_covers_every_site():
    doc = (REPO / DOC_REL).read_text(encoding="utf-8")
    reg, errs = cpm.parse_register(doc)
    assert not errs, errs
    sites = cpm.scan_lifecycle(load_trees(REPO), REPO)
    keys = {f"{s['path']}:{s['lineno']}": s for s in sites}
    assert set(reg) == set(keys)
    for key, ent in reg.items():
        assert ent["kind"] == keys[key]["kind"]
        for f in cpm.REQUIRED_FIELDS:
            assert ent.get(f), f"{key} 缺 {f}"
        assert (REPO / re.search(r"[\w\-./\u4e00-\u9fff]+\.md", ent["依据"]).group(0)).is_file()


def test_real_doc_ceilings_equal_fresh():
    doc = (REPO / DOC_REL).read_text(encoding="utf-8")
    ceil, errs = cpm.parse_ceilings(doc)
    assert not errs and ceil
    sites = cpm.scan_lifecycle(load_trees(REPO), REPO)
    for kind in cpm.KINDS:
        assert ceil[kind] == sum(1 for s in sites if s["kind"] == kind), kind


def test_real_serve_and_watch_shapes_present():
    trees = load_trees(REPO)
    assert cpm.serve_teardown_shape(trees, REPO) == []
    assert cpm.watch_sigterm_shape(trees, REPO) == []


def test_real_doc_states_range_exclusion():
    doc = (REPO / DOC_REL).read_text(encoding="utf-8")
    assert "scripts/" in doc and "不在射程内" in doc       # §一 明写排除，而不是静默
    assert "门判的是对账，不是缺陷" in doc


# ── 合成树：每条判据单独可红 ──

def test_fake_repo_green(fake_repo: Path):
    assert run_gate(fake_repo).returncode == 0, findings_text(fake_repo)


def test_write_is_idempotent(fake_repo: Path):
    p = fake_repo / DOC_REL
    before = p.read_bytes()
    assert cpm.write_doc(fake_repo) == 0
    assert p.read_bytes() == before


def test_b_unregistered_new_popen_red(fake_repo: Path):
    append_code(fake_repo, "src/autoforge/af_service.py",
                "def extra(cmd):\n    return subprocess.Popen(cmd, stdout=None)\n")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "B：生命周期站点" in out
    assert "subprocess.Popen" in out


def test_b_expired_register_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `"))
    mutate(fake_repo, DOC_REL, row, row + "\n- `src/autoforge/af_service.py:9999` · kind=os.kill · "
           "拉起：已不存在 · 收：不存在 · 收不到会怎样：这条登记是过期现场要删掉 · "
           "依据：docs/进程模型清单.md · 认领：AF")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "过期登记" in out


def test_b_kind_mismatch_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `") and "kind=subprocess.Popen" in l)
    mutate(fake_repo, DOC_REL, row, row.replace("kind=subprocess.Popen", "kind=os.kill"))
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "登记写 kind" in out


def test_b_missing_field_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `"))
    stripped = re.sub(r"收不到会怎样：[^·]*· ?", "", row, count=1)
    mutate(fake_repo, DOC_REL, row, stripped)
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "缺字段" in out


def test_b_unknown_ownership_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `"))
    mutate(fake_repo, DOC_REL, row, row.replace("认领：AF", "认领：谁也不知道"))
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "不属于" in out


def test_b_missing_anchor_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `"))
    mutate(fake_repo, DOC_REL, row, row.replace("依据：docs/进程模型清单.md", "依据：docs/根本没这份文件.md"))
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "指得到一份记录" in out


def test_b_placeholder_reason_red(fake_repo: Path):
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    row = next(l for l in doc.splitlines() if l.startswith("- `"))
    mutate(fake_repo, DOC_REL, row,
           re.sub(r"收不到会怎样：[^·]*", "收不到会怎样：待补", row, count=1))
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "占位词" in out


def test_c_above_ceiling_red(fake_repo: Path):
    """棘轮语义：上限低于现读即红——先降上限，新增那一侧就过不了。"""
    doc = (fake_repo / DOC_REL).read_text(encoding="utf-8")
    assert "- os.kill = 2" in doc
    mutate(fake_repo, DOC_REL, "- os.kill = 2", "- os.kill = 1")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "C：`os.kill` 现读 2 枚，§四 登记上限 1" in out


def test_a_auto_section_drift_red(fake_repo: Path):
    p = fake_repo / DOC_REL
    text = p.read_text(encoding="utf-8")
    new = text.replace("| `_OWNER` |", "| `_OWNERRENAMED` |", 1)
    assert new != text
    p.write_text(new, encoding="utf-8", newline="\n")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "A：" in out and "自动段与现读不一致" in out


def test_decrease_trips_A_not_C(fake_repo: Path):
    """计数下降由 A 逼人重生成并下调上限（§四 那句设计的自证）：C 不该为此判红。"""
    mutate(fake_repo, "src/autoforge/af_service.py", "    os.kill(cmd, 15)", "    pass  # 收不进这条腿")
    assert (fake_repo / "src" / "autoforge" / "af_service.py").read_text(encoding="utf-8").count(
        "os.kill(") == 1
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "A：" in out and "自动段与现读不一致" in out
    assert "C：`os.kill`" not in out          # 上限 2 ≥ 现读 1：只管新增那一侧
    assert run_gate(fake_repo).returncode == 1
    assert cpm.write_doc(fake_repo) == 0      # 重生成后 A 自己会消气
    out2 = findings_text(fake_repo)
    assert "A：" not in out2 and "过期登记" in out2


def test_d_bridge_stop_removed_red(fake_repo: Path):
    mutate(fake_repo, "src/autoforge/af_cli.py",
           "        if bridge is not None:\n            bridge.stop()",
           "        print('served')")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "D：" in out and "finally" in out


def test_e_sigterm_handler_removed_red(fake_repo: Path):
    mutate(fake_repo, "src/autoforge/af_cli.py", "    signal.signal(signal.SIGTERM, _on_sigterm)\n", "")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "E：" in out and "signal.signal(SIGTERM" in out


def test_e_finally_missing_release_red(fake_repo: Path):
    mutate(fake_repo, "src/autoforge/af_cli.py", "        coord.release()\n", "")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert "E：" in out and "coord.release" in out


# ── 射程塌了必须 exit 2 ──

def test_exit2_missing_src_dir(fake_repo: Path):
    import shutil
    shutil.rmtree(fake_repo / "src" / "autoforge")
    r = run_gate(fake_repo)
    assert r.returncode == 2 and "射程" in (r.stdout + r.stderr)


def test_exit2_unparseable_file(fake_repo: Path):
    (fake_repo / "src" / "autoforge" / "broken.py").write_text("def (:\n", encoding="utf-8")
    assert run_gate(fake_repo).returncode == 2


def test_exit2_missing_doc(fake_repo: Path):
    (fake_repo / DOC_REL).unlink()
    assert run_gate(fake_repo).returncode == 2


def test_exit2_no_auto_markers(fake_repo: Path):
    mutate(fake_repo, DOC_REL, cpm.AUTO_BEGIN, "（标记被人改了）")
    assert run_gate(fake_repo).returncode == 2


def test_exit2_missing_register_section(fake_repo: Path):
    p = fake_repo / DOC_REL
    p.write_text(p.read_text(encoding="utf-8").replace(REGISTER, "## 三、登记（改名了）"),
                 encoding="utf-8", newline="\n")
    assert run_gate(fake_repo).returncode == 2


def test_exit2_missing_ceiling_section(fake_repo: Path):
    p = fake_repo / DOC_REL
    p.write_text(p.read_text(encoding="utf-8").replace(CEILING, "## 四、上限（改名了）"),
                 encoding="utf-8", newline="\n")
    assert run_gate(fake_repo).returncode == 2


def test_exit2_zero_sites_is_not_clean(fake_repo: Path):
    """反空集腿：全树扫不出任何生命周期站点＝扫描器坏了，绝不许回 RC=0。"""
    (fake_repo / "src" / "autoforge" / "af_service.py").write_text(
        "X = 1\n", encoding="utf-8", newline="\n")
    (fake_repo / "src" / "autoforge" / "af_cli.py").write_text("Y = 2\n", encoding="utf-8", newline="\n")
    r = run_gate(fake_repo)
    assert r.returncode == 2
    assert "扫描器坏了" in (r.stdout + r.stderr)


def test_register_section_turned_to_prose_still_red(fake_repo: Path):
    """登记整段被人改成散文：不许退化成"没有登记行＝没有站点"的假绿，每枚站点都得单独点名。"""
    edit_doc_section(fake_repo, REGISTER, "全部登记改成散文，一行站点键都没有了。")
    out = findings_text(fake_repo)
    assert run_gate(fake_repo).returncode == 1
    assert out.count("B：生命周期站点") == 4      # 合成树现读正是 4 枚，一枚都没放过


def test_register_rows_all_malformed_is_range_collapse(fake_repo: Path):
    """登记行还在、形状却读不出（键名换了）：射程塌，走 `exit 2` 而不是逐条判红。"""
    rows = register_rows(fake_repo)
    edit_doc_section(fake_repo, REGISTER, "\n".join(
        r.replace("· kind=", "· 类型=", 1) for r in rows))
    assert run_gate(fake_repo).returncode == 2


def test_mutation_helper_refuses_missing_anchor(fake_repo: Path):
    with pytest.raises(AssertionError):
        mutate(fake_repo, "src/autoforge/af_cli.py", "这段文字不在文件里", "x")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

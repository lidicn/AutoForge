#!/usr/bin/env python3
"""`scripts/check_release_line.py` 的判据腿（DCD 20261013 §三「分支一致门」，执行记录 §二之一百四十二）。

三条纪律，逐条对应下面真实存在的腿：

1. **真仓绿**：现读入库面的 5 枚站点、发布线登记值、上限、`body.branch` 取值域那一格**读数本身**钉死——
   「门绿」不等于「门在看」，读数钉住才防得住扫描器静默退化（§二之九十九 的 MX2 那一族）；
2. **每条判据单独可红**：合成树上 A／B／C／D／E 各注入一枚，红的那条必须带自己的字母前缀。注入后先 `--write`
   把自动段重生成，再跑判据——否则 A 会连坐，红到的字母就不是注入的那一条；
3. **反空集与「注入未生效」自证**：载体整个消失、清单那对标记被改写、§二 那一格改名，全部走 `exit 2`
   （不许把「扫不到」当「没问题」）；每一次变异都断言锚点真的命中原文。

合成树必须 `git init` ＋ `git add`：本门判**入库面**，读不出索引就 `exit 2`（`require_index` 那条腿测的就是这个，
连 `--write` 也一样拒）。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check_release_line.py"
DOC_REL = "docs/发布线清单.md"
SPEC = importlib.util.spec_from_file_location("check_release_line", GATE)
crl = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(crl)

API_REL = "src/autoforge/af_api.py"
HOST_REL = "scripts/host_update.sh"
DEPLOY_REL = "scripts/verify_ui_nas_deploy.py"
CI_REL = ".github/workflows/ci.yml"
SITE_PATHS = (API_REL, HOST_REL, DEPLOY_REL, CI_REL)

API_TXT = '''"""合成自更新端点：只保留判据 D 要的三枚形状与写入侧缺省。"""
import json
import os


def build(app, atomic_write_text, _APP_VERSION, _UPDATE_MARKER):
    @app.post("/api/system/update")
    def api_system_update(body) -> dict:
        confirm_version = (body.confirm_version or "").strip()
        if confirm_version != _APP_VERSION:
            return {"ok": False, "error": "版本号不匹配"}, 400
        if os.path.isfile(_UPDATE_MARKER):
            return {"ok": False, "error": "已有更新请求进行中"}, 409
        branch = (body.branch or "main").strip()
        marker = {"ref": branch, "version": _APP_VERSION}
        atomic_write_text(_UPDATE_MARKER, json.dumps(marker, ensure_ascii=False))
        return {"ok": True}
'''

HOST_TXT = '''#!/bin/sh
# 合成宿主 sidecar：读 marker 的 ref，读不出就吃两层兜底，然后走变量传参。
MARKER=/data/.update_request.json
REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','main'))" 2>/dev/null || echo "main")
cd /deploy/autoforge || exit 1
if ! git fetch --force origin "$REF"; then
    exit 1
fi
git reset --hard "origin/$REF"
'''

DEPLOY_TXT = '''"""合成部署指令生成器：它打印的每一条 owner 都会照着敲。"""
NAS_USER_DIR = "/home/nas/autoforge"


def propose_commands(target: str) -> str:
    return "\\n".join(
        [
            "# 以下动作本脚本不执行（要 owner 点头）：",
            f"ssh {target} 'cd {NAS_USER_DIR} && git fetch && git merge --ff-only origin/main'",
        ]
    )
'''

CI_TXT = """name: CI

on:
  push:
    branches: [main, master, dev]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
"""

FILES = {API_REL: API_TXT, HOST_REL: HOST_TXT, DEPLOY_REL: DEPLOY_TXT, CI_REL: CI_TXT}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd), capture_output=True, check=True,
    )


def run_gate(root: Path | None = None, write: bool = False) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(GATE)] + ([str(root)] if root else []) + (["--write"] if write else [])
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def make_tree(tmp_path: Path) -> Path:
    """最小副本树：四枚载体＋真仓那份清单，自动段随后由 `--write` 按这棵树的入库面重生成。"""
    root = tmp_path / "tree"
    for rel, text in FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
    (root / DOC_REL).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / DOC_REL, root / DOC_REL)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    rc = run_gate(root, write=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    _git(root, "add", "-A")
    return root


def write_file(root: Path, rel: str, text: str) -> None:
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")


def patch(root: Path, rel: str, old: str, new: str) -> None:
    """注入变异并**断言命中**：锚点没打到就 FAIL，绝不把「注入未生效」读成门全绿。"""
    p = root / rel
    text = p.read_text(encoding="utf-8")
    hits = text.count(old)
    assert hits == 1, f"锚点没命中（现读 {hits} 处）：{rel} 里 `{old}`"
    write_file(root, rel, text.replace(old, new))


def resync_and_check(root: Path):
    """重生成自动段（把判据 A 摘出去）后再跑判据，返回 CompletedProcess。"""
    rc = run_gate(root, write=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    _git(root, "add", "-A")
    return run_gate(root)


def findings_of(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if re.match(r"^\s+(· |\[)", ln)]


def by_letter(out: str, letter: str) -> list[str]:
    return [f[2:] for f in findings_of(out) if f.startswith(f"· {letter} ")]


# ───────────────────────── 1. 真仓读数腿（入库面） ─────────────────────────


def test_real_repo_gate_green():
    """真仓跑本门必须绿：CI 上索引即 HEAD，所以这条腿两边同源（改取入库面的目的，#118）。"""
    rc = run_gate()
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert "发布线门禁干净" in rc.stdout


def _real_snapshot(fn):
    snaps: dict = {}
    try:
        for rel_dir, _exts in crl.SCANNED:
            snaps[rel_dir] = crl.Face.open(crl.REPO).snapshot(rel_dir)
        return fn(snaps)
    finally:
        for snap in snaps.values():
            snap.close()


def test_real_repo_readings():
    """钉读数本身：5 枚站点／角色齐全／上限 5——扫描器静默退化会先把这条撞红。"""
    def read(snaps):
        return crl.scan_sites(snaps)
    sites = _real_snapshot(read)
    assert len(sites) == 5, [s.line() for s in sites]
    assert {s.path for s in sites} == set(SITE_PATHS), [s.line() for s in sites]
    assert {s.role for s in sites} == {
        crl.ROLE_WRITE, crl.ROLE_READ, crl.ROLE_FALLBACK, crl.ROLE_DEPLOY, crl.ROLE_CI
    }
    assert crl.SITE_CAP == 5


def test_real_repo_every_carrier_equals_registered_line():
    """五枚载体逐枚对上 §二 那一格的值（本门的核心一句，读数在这里再钉一遍）。"""
    def read(snaps):
        text = (crl.REPO / crl.DOC_REL).read_text(encoding="utf-8").splitlines()
        cal = crl._caliber(text)
        return cal[crl.KEY_RELEASE_LINE], crl.scan_sites(snaps)
    release, sites = _real_snapshot(read)
    assert release == "main"
    for s in sites:
        if s.role == crl.ROLE_CI:
            assert release in s.literal.split(","), s.line()
        else:
            assert s.literal == release, s.line()


def test_real_repo_deploy_command_follows_publish_line():
    """2026-10-13 之前那枚「部署指令」载体写的是 `origin/master`：与自更新链不同源，同一台 NAS 会被两条路径喂两套代码。"""
    def read(snaps):
        return crl._text_of(snaps, DEPLOY_REL), crl._text_of(snaps, HOST_REL)
    deploy_txt, host_txt = _real_snapshot(read)
    assert "git merge --ff-only origin/main" in deploy_txt
    assert "origin/master" not in deploy_txt
    assert 'git fetch --force origin "$REF"' in host_txt


def test_real_repo_comments_are_not_carriers():
    """代码行才算载体：本门自己的注释里写着 `body.branch or "main"` 那种例子，不能被扫成站点（散文踩哨兵的孪生形态）。"""
    def read(snaps):
        return crl.scan_sites(snaps)
    paths = {s.path for s in _real_snapshot(read)}
    assert str(GATE.relative_to(REPO)) not in paths


# ───────────────────────── 2. 合成树：每条判据单独可红 ─────────────────────────


def test_synth_green_baseline(tmp_path):
    assert run_gate(make_tree(tmp_path)).returncode == 0


def test_synth_A_manual_auto_edit(tmp_path):
    """A：手改自动段一行字面、不重生成 ⇒ 只有 A 红（清单成了会过期的副本就是本门要消灭的形状）。"""
    root = make_tree(tmp_path)
    doc = root / DOC_REL
    text = doc.read_text(encoding="utf-8")
    target = next(ln for ln in text.splitlines() if "角色=写入侧缺省" in ln)
    doc.write_text(text.replace(target, target.replace("字面=main", "字面=master")), encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    rc = run_gate(root)
    assert rc.returncode == 1, rc.stdout
    assert by_letter(rc.stdout, "A"), rc.stdout
    assert not [f for f in findings_of(rc.stdout) if f.startswith(("· B", "· C", "· D", "· E"))], rc.stdout


def test_synth_A_total_line_drift(tmp_path):
    """A：汇总行被人手改（上限数字也是逐行对撞的一部分）。"""
    root = make_tree(tmp_path)
    patch(root, DOC_REL, "- 站点合计 = 5 · 上限 = 5", "- 站点合计 = 5 · 上限 = 9")
    rc = run_gate(root)
    assert rc.returncode == 1 and by_letter(rc.stdout, "A"), rc.stdout


def test_synth_B_unregistered_carrier(tmp_path):
    """B：新载体文件（`scripts/host_rollback.sh`）自带读取侧缺省却不登记 ⇒ 先显式认领。"""
    root = make_tree(tmp_path)
    write_file(root, "scripts/host_rollback.sh", 'REF=$(python3 -c "x.get(\'ref\',\'main\')" || echo "main")\n')
    rc = run_gate(root, write=True)          # 重生成自动段，把 A 摘出去
    assert rc.returncode == 0, rc.stdout
    _git(root, "add", "-A")
    rc = run_gate(root)
    assert rc.returncode == 1, rc.stdout
    fs = by_letter(rc.stdout, "B")
    assert any("未登记" in f for f in fs), rc.stdout
    assert any("host_rollback.sh" in f for f in fs), rc.stdout


def test_synth_B_stale_registration(tmp_path):
    """B：删掉一枚载体却不删登记行 ⇒ 过期登记红（登记说它存在，现读数不出这一站）。"""
    root = make_tree(tmp_path)
    (root / DEPLOY_REL).unlink()
    _git(root, "add", "-A")
    rc = resync_and_check(root)
    assert rc.returncode == 1, rc.stdout
    assert any("过期登记" in f for f in by_letter(rc.stdout, "B")), rc.stdout


def test_synth_B_placeholder_claim(tmp_path):
    """B：认领格写成占位词＝与不登记同判。"""
    root = make_tree(tmp_path)
    doc = root / DOC_REL
    text = doc.read_text(encoding="utf-8")
    row = next(ln for ln in text.splitlines() if ln.startswith(f"| {crl.ROLE_CI} "))
    cells = row.split("|")
    cells[-2] = " 待补 "
    doc.write_text(text.replace(row, "|".join(cells)), encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    rc = run_gate(root)
    assert rc.returncode == 1, rc.stdout
    assert any("占位" in f for f in by_letter(rc.stdout, "B")), rc.stdout


def test_synth_C_carrier_off_publish_line(tmp_path):
    """C（核心一句）：写入侧缺省改成别的线名 ⇒ 只有 C 红，修法提示指向 §二 那一格。"""
    root = make_tree(tmp_path)
    patch(root, API_REL, 'body.branch or "main"', 'body.branch or "master"')
    rc = resync_and_check(root)
    assert rc.returncode == 1, rc.stdout
    assert any("写入侧缺省" in f and "master" in f for f in by_letter(rc.stdout, "C")), rc.stdout
    assert not [f for f in findings_of(rc.stdout) if f.startswith(("· A", "· B", "· D", "· E"))], rc.stdout


def test_synth_C_fallback_only_diverges(tmp_path):
    """C：只改第二层兜底 `|| echo` 而不动读取侧缺省——正是「改上面漏下面」的形状。"""
    root = make_tree(tmp_path)
    patch(root, HOST_REL, '2>/dev/null || echo "main"', '2>/dev/null || echo "dev"')
    rc = resync_and_check(root)
    assert rc.returncode == 1 and any("兜底字面" in f for f in by_letter(rc.stdout, "C")), rc.stdout


def test_synth_C_ci_drops_publish_line(tmp_path):
    """C：CI 触发名单里没有发布线 ⇒ 推到发布线上的提交根本不跑 CI。"""
    root = make_tree(tmp_path)
    patch(root, CI_REL, "branches: [main, master, dev]", "branches: [master, dev]")
    rc = resync_and_check(root)
    assert rc.returncode == 1 and any("触发名单" in f for f in by_letter(rc.stdout, "C")), rc.stdout


def test_synth_C_ci_extra_names_still_green(tmp_path):
    """C 的反例（防过度收紧）：名单里多几条别的线名不该红——发布线在名单里就成立（重生成自动段后判 C）。"""
    root = make_tree(tmp_path)
    patch(root, CI_REL, "branches: [main, master, dev]", "branches: [main, dev, feat]")
    rc = resync_and_check(root)
    assert rc.returncode == 0, rc.stdout


def test_synth_C_domain_registration(tmp_path):
    """C：偷偷给 `body.branch` 加取值域却不改登记格 ⇒ 红（反向撤校验同样红）。"""
    root = make_tree(tmp_path)
    patch(root, API_REL, '        marker = {',
          '        if branch not in _ALLOWED_BRANCHES:\n            raise ValueError(branch)\n        marker = {')
    rc = resync_and_check(root)
    assert rc.returncode == 1 and any("取值域" in f for f in by_letter(rc.stdout, "C")), rc.stdout


def test_synth_D_hardcoded_ref(tmp_path):
    """D：把 `$REF` 退回硬编码 ⇒ 变量传参锚点红（这一枚不新增站点，所以只有 D 说话）。"""
    root = make_tree(tmp_path)
    patch(root, HOST_REL, 'git fetch --force origin "$REF"', "git fetch --force origin main")
    rc = run_gate(root)
    assert rc.returncode == 1, rc.stdout
    assert any('origin "$REF"' in f for f in by_letter(rc.stdout, "D")), rc.stdout


def test_synth_D_marker_key_diverges(tmp_path):
    """D：写入侧键名从 `ref` 漂走 ⇒ 读取侧永远吃缺省、用户选的 ref 被静默丢弃。"""
    root = make_tree(tmp_path)
    patch(root, API_REL, 'marker = {"ref": branch,', 'marker = {"target_ref": branch,')
    rc = run_gate(root)
    assert rc.returncode == 1 and any('"ref": branch' in f for f in by_letter(rc.stdout, "D")), rc.stdout


def test_synth_D_endpoint_shape_anchor(tmp_path):
    """D：删掉版本二次确认比对（自更新的铁律形状）⇒ 锚点红，⛔ 不靠 C 兜住。"""
    root = make_tree(tmp_path)
    patch(root, API_REL, "if confirm_version != _APP_VERSION:", "if confirm_version and False:")
    rc = run_gate(root)
    assert rc.returncode == 1 and any("confirm_version" in f for f in by_letter(rc.stdout, "D")), rc.stdout


def test_synth_E_site_cap(tmp_path):
    """E：同一枚已登记载体上**多一行**分支字面 ⇒ A/B/C/D 全闭合，只剩上限拦新增。

    （站点按 `路径:行号 · 角色 · 字面` 去重，所以第二枚必须落在**另一行**——同一行的第二枚会被读成一枚。）
    """
    root = make_tree(tmp_path)
    old_line = """f"ssh {target} 'cd {NAS_USER_DIR} && git fetch && git merge --ff-only origin/main'","""
    extra = """f"ssh {target} 'cd {NAS_USER_DIR} && git reset --hard origin/main'","""
    patch(root, DEPLOY_REL, old_line, old_line + "\n            " + extra)
    rc = resync_and_check(root)
    assert rc.returncode == 1, rc.stdout
    assert any("上限" in f for f in by_letter(rc.stdout, "E")), rc.stdout
    assert not [f for f in findings_of(rc.stdout) if f.startswith(("· A", "· B", "· C", "· D"))], rc.stdout


# ───────────────────────── 3. 射程塌：一律 exit 2 ─────────────────────────


def test_scope_collapses_without_git(tmp_path):
    """读不出 git 索引 ⇒ 一律 exit 2，`--write` 也拒（不许把磁盘面烤进清单）。"""
    root = tmp_path / "plain"
    for rel, text in FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
    (root / DOC_REL).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / DOC_REL, root / DOC_REL)
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout + rc.stderr
    assert "入库面" in rc.stdout
    assert run_gate(root, write=True).returncode == 2, "拒绝用磁盘面改写清单"


def test_write_refuses_disk_face_inside_no_index(tmp_path):
    """`--write` 的射程前提单独一条：它比判据更该谨慎——自动段一旦按错面生成，就是给清单烤进一份假读数。"""
    root = tmp_path / "plain2"
    (root / Path(DOC_REL).parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / DOC_REL, root / DOC_REL)
    rc = run_gate(root, write=True)
    assert rc.returncode == 2, rc.stdout


def test_scope_collapses_when_doc_missing(tmp_path):
    root = make_tree(tmp_path)
    (root / DOC_REL).unlink()
    _git(root, "add", "-A")
    assert run_gate(root).returncode == 2


def test_scope_collapses_when_auto_markers_gone(tmp_path):
    root = make_tree(tmp_path)
    text = (root / DOC_REL).read_text(encoding="utf-8")
    write_file(
        root, DOC_REL,
        text.replace(crl.AUTO_BEGIN, "<!-- 手改的标记 -->").replace(crl.AUTO_END, "<!-- 手改的尾 -->"),
    )
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout


def test_scope_collapses_when_role_table_emptied(tmp_path):
    """§三 那节在而表除表头外一行没有 ⇒ 空集给的干净必须塌，⛔ 不许读成「没有问题」。"""
    root = make_tree(tmp_path)
    text = (root / DOC_REL).read_text(encoding="utf-8").splitlines()
    roles = (crl.ROLE_WRITE, crl.ROLE_READ, crl.ROLE_FALLBACK, crl.ROLE_DEPLOY, crl.ROLE_CI)
    keep = [ln for ln in text if not any(ln.startswith(f"| {r} ") for r in roles)]
    write_file(root, DOC_REL, "\n".join(keep) + "\n")
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout


def test_scope_collapses_when_carrier_files_gone(tmp_path):
    """载体文件整个消失：`scripts/` 在索引里读出 0 份 ⇒ 走到 `af_face` 的射程下限（⛔ 不是「没有违规」）。"""
    root = make_tree(tmp_path)
    for rel in SITE_PATHS:
        (root / rel).unlink()
    _git(root, "add", "-A")
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout + rc.stderr
    assert "射程塌" in rc.stdout, rc.stdout
    assert run_gate(root, write=True).returncode == 2, "拒绝用空集改写自动段"


def test_scope_collapses_when_zero_sites_but_files_present(tmp_path):
    """反空集第二形：四枚载体**还在**、字面全被拿掉 ⇒ 命中出口文案里的形状⑥（载体在、站点为 0）。

    这一枚不能被上一条代替：上一条红在取数面下限，这一条红在扫描面下限；`--write` 两条都必须拒，
    否则空集会被烤进清单，之后 every-leg 都对着「清单说没有站点」点头。
    """
    root = make_tree(tmp_path)
    for rel, text in FILES.items():
        neutral = "\n".join(
            ln for ln in text.splitlines()
            if "origin/" not in ln and 'or "main"' not in ln and "'ref','main'" not in ln
            and "branches: [" not in ln
        )
        write_file(root, rel, neutral + "\n")
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout + rc.stderr
    assert "射程断" in rc.stdout, rc.stdout
    assert run_gate(root, write=True).returncode == 2, "拒绝用空集改写自动段"


def test_scope_collapses_when_caliber_key_renamed(tmp_path):
    root = make_tree(tmp_path)
    patch(root, DOC_REL, f"- {crl.KEY_RELEASE_LINE} = main", "- 发布线（远端啊） = main")
    rc = run_gate(root)
    assert rc.returncode == 2, rc.stdout

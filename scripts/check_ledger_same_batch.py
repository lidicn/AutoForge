#!/usr/bin/env python3
"""台账同批门禁：改行号的提交必须与它对应的清单**同一笔**入库，否则判红。

依据：`decisions/20261014-AF台账机械前置与MA本地装闸与回执落点根因-裁定.md` §一 Q1（路线图 rev 14 ①），
判据逐字：「凡改动会移动 `file:line` 的提交，与它对应的清单（台账／索引／名册）必须在同一笔提交里重生成；
⛔ 分两笔 ⇒ CI 判红（红因写「清单与源码不同批」）」。

同族第三次复发的实物：`20261012` 那三格 → #135 那族 → 2026-10-14 的 71 处＋22 处，出处都是
「改行号的提交进了库，清单停在旧值」。裁定原话：**复发三次 ⇒ 已不是疏忽，是缺一道机械前置。**

⚠️ 本门⛔ 重跑那五枚清单生成器，也⛔ 判「清单内容与源码对不对得上」——那是各门自己的 A/B 腿。
本门只管**提交边界**这一件事：一笔提交动没动会让锚点漂的东西，动了就必须同批带着清单一起走。
（判例「门管形状、判据管语义」的分工；两份读数各归各处，⛔ 互相冒充。）

「会移动 `file:line`」的机械判据（三条，任一成立即算会漂）：
  ① 该清单在册锚点所在的**同一份文件**，本笔提交的 diff 里有 hunk 的 `旧行数 ≠ 新行数`，
     且那一 hunk 的起点 ≤ 该文件在册的最大锚点行号 ⇒ 后面的锚点行号必然整体平移；
  ② 本笔提交**删除或改名**了该清单在册锚点所在的那份文件 ⇒ 过期登记必然发生；
  ③ 逐枚清单另有一条与其登记形状对齐的窄判据（如仓库卫生清单的「根目录新增入库 `.py`」）。
三条都⛔ 吃「内容改了但行数没变」的提交——那一类锚点没漂，硬要人同批改清单只会造出一枚假红
（判例「闸必须先保证它有绿的可能」）。

⚠️ 一条必须写明的分工：**新增**一份文件带来的清单变化⛔ 由本门挡，由各清单门自己的 A／B 腿在 CI 上挡
（今天那 71 处正是那条腿咬的）。本门刻意⛔ 把「现读根下有任何新增」算成会漂——那一判据会让每一笔加文件的
提交都红，而加一份⛔ 带敏感站点的普通文件根本不该改清单；把闸写成永远红，等于把它判红的能力交给
「大家都习惯忽略它」。（判例「测不出收益的闸与永远不红的闸等价」的同族。）

四条判据腿（各自单独可红／可塌）：
  A「同批腿」：会漂而清单⛔ 在同一笔里 ⇒ 红，红因逐字写「清单与源码不同批」。
  B「分两笔腿」：本笔只动清单、而**上一笔**恰好让同一枚清单漂过 ⇒ 红（这就是裁定点名要堵的那个形状：
    两笔一起推上去时，CI 只看得到笔尖，A 腿在笔尖上是绿的）。
  C「默认拒绝在册腿」：HEAD 里任何带 `AUTO-BEGIN … --write 生成` 标记的清单，本门名册里没有 ⇒ 红。
    新开一份清单而不登记，本门就永远看不见它——新增台账必须显式认领。
  D「射程塌（`exit 2`）」：⛔ 仓库／⛔ 一枚提交都没有／diff-tree 或 diff 取不出／名册里的清单或生成门
    不在 HEAD／某清单现读锚点数 0 且没有「无」标记行。每一种都不许用「没有发现」冒充「没有问题」。

取数口径＝**HEAD 树**（`git ls-tree HEAD`／`git show HEAD:<path>`），与裁定 §一 Q2 给清单生成器定的那面同源：
判提交这件事，本来就只能拿提交面当依据；工作树脏不脏与本门口径无关。

退出码就是结论：0 干净 / 1 有未获批违规 / 2 射程塌。纯标准库，只读。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: 名册：清单 ↔ 生成门 ↔ 现读根 ↔ 锚点行正则。新增清单必须在这里显式认领（C 腿罩着盘上那一半）。
#: `anchor_re` 的命名组固定两枚：`path`／`lineno`；解析不出任何锚点且没有 `none_re` 命中 ⇒ D 腿射程塌。
ROSTER: tuple[dict, ...] = (
    {
        "doc": "docs/可观测性清单.md",
        "gate": "scripts/check_observability.py",
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"^- 无$|^\*\*无\*\*|^无站点"),
    },
    {
        "doc": "docs/进程模型清单.md",
        "gate": "scripts/check_process_model.py",
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"^- 无$|^\*\*无\*\*|^无站点"),
    },
    {
        "doc": "docs/信任边界清单.md",
        "gate": "scripts/check_trust_boundary.py",
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.py):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"^- 无$|^\*\*无\*\*|^无站点"),
    },
    {
        "doc": "docs/发布线清单.md",
        "gate": "scripts/check_release_line.py",
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.(py|sh|yml|toml)):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"^- 无$|^\*\*无\*\*|^无站点"),
    },
    {
        # 仓库卫生清单的登记面是**整份文件**（根目录 `.py` 名单），一行号锚点都没有。
        # 它靠 ② 那一条（现读根下新增/删除文件）罩住；锚点数 0 是它的真实形状，故 `anchors_optional`。
        "doc": "docs/仓库卫生清单.md",
        "gate": "scripts/check_oneshot_layout.py",
        "anchor_re": re.compile(r"`(?P<path>[\w./\-]+\.(py|sh|conf)):(?P<lineno>\d+)`"),
        "none_re": re.compile(r"无"),
        "anchors_optional": True,
        "toplevel_py": True,   # 它的登记面是「仓库根的文件名单」，没有行号锚点，会漂的形状只有一种：根目录新增入库 `.py`
    },
)

#: 自动段标记的真形状（C 腿拿它找未认领的新清单）；与各清单文件里那行逐字对得上。
AUTO_MARKER = re.compile(r"AUTO-BEGIN[^\n]*?scripts/(?P<gate>check_\w+\.py)\s+--write")

RED_CAUSE = "清单与源码不同批"


class ScopeDown(Exception):
    """射程塌（`exit 2`）：口径读不出＝本门无从判定，报「干净」就是假绿。"""


def _git(repo: Path, args: list[str]) -> str:
    try:
        p = subprocess.run(args, cwd=str(repo), capture_output=True)
    except OSError as exc:
        raise ScopeDown(f"`{args[0]}` 无法执行：{type(exc).__name__}: {exc}") from exc
    if p.returncode != 0:
        detail = (p.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ScopeDown(f"`{' '.join(args[:3])}…` 退出码 {p.returncode}：" + (detail[0] if detail else "无 stderr"))
    return p.stdout.decode("utf-8", "replace")


def head_sha(repo: Path) -> str:
    return _git(repo, ["git", "rev-parse", "HEAD"]).strip()


def head_paths(repo: Path) -> list[str]:
    raw = _git(repo, ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"])
    return [n for n in raw.split("\0") if n]


def parent_of(repo: Path, rev: str) -> str | None:
    """父提交；根提交（无父）返回 None——B 腿对第一笔提交无从判，明写而不假判。"""
    try:
        return _git(repo, ["git", "rev-parse", f"{rev}^"]).strip()
    except ScopeDown:
        return None


def _merge_bases(repo: Path, rev: str) -> list[str]:
    return _git(repo, ["git", "rev-parse", f"{rev}^1", f"{rev}^2"]).split()[:1]


def changed_files(repo: Path, rev: str) -> tuple[dict[str, str], list[str]]:
    """`git diff-tree -r --name-status <rev>^ <rev>` → `({路径: 状态字母}, [失败说明])`。

    合并提交按**第一父**取差：CI 上被推的那笔若是一次 merge，第一父就是主线上被并入的那一侧，
    拿它判「本笔带没带清单」与逐笔提交同口径。⛔ 「merge 判不了就跳过」——跳过会是一枚假绿。
    """
    parent = parent_of(repo, rev)
    if parent is None:
        return {}, [f"`{rev[:7]}` 是根提交（没有父），本笔无从对撞"]
    first = parent
    try:
        _git(repo, ["git", "rev-parse", f"{rev}^2"])
        first = _merge_bases(repo, rev)[0]
    except ScopeDown:
        pass
    out = _git(repo, ["git", "diff-tree", "-r", "--name-status", "-z", first, rev])
    status: dict[str, str] = {}
    recs = [r for r in out.split("\0") if r]
    i = 0
    while i < len(recs):
        field = recs[i]
        if len(field) == 1 and field in "ACMRTUXBD":
            state, path = field, recs[i + 1]
            i += 2
        else:
            parts = field.split("\t")
            state, path = parts[0][0], parts[-1]
            i += 1
        status[path] = state
    return status, []


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def shifting_hunks(repo: Path, rev: str, path: str, max_anchor: int) -> list[str]:
    """该文件本笔 diff 里「起点 ≤ 最大在册锚点、且行数发生变化」的 hunk；空列表＝锚点不会漂。"""
    parent = parent_of(repo, rev)
    if parent is None:
        return []
    out = _git(repo, ["git", "diff", "-U0", "--no-color", parent, rev, "--", path])
    bad: list[str] = []
    for line in out.splitlines():
        m = HUNK_RE.match(line)
        if not m:
            continue
        old_start = int(m.group(1))
        old_len = int(m.group(2) if m.group(2) is not None else 1)
        new_len = int(m.group(4) if m.group(4) is not None else 1)
        if old_len != new_len and old_start <= max_anchor:
            bad.append(f"`@@ -{old_start},{old_len} +{m.group(3)},{new_len} @@`（行数变化在锚点 ≤{max_anchor} 之前）")
    return bad


def anchors_in_doc(repo: Path, doc_rel: str, anchor_re: re.Pattern[str]) -> dict[str, list[int]]:
    """清单正文里在册的 `path:lineno` 锚点，按文件归组（取 HEAD 树那一份，⛔ 取工作树）。"""
    text = _git(repo, ["git", "show", f"HEAD:{doc_rel}"])
    out: dict[str, list[int]] = {}
    for m in anchor_re.finditer(text):
        out.setdefault(m.group("path"), []).append(int(m.group("lineno")))
    return out


def _dropped(status: dict[str, str], anchors: dict[str, list[int]]) -> list[str]:
    """在册锚点所在文件里，本笔被删除或改名的那几份（过期登记的直接来源）。"""
    return sorted(p for p in anchors if status.get(p) in ("D", "R"))


def _toplevel_new_py(status: dict[str, str]) -> list[str]:
    """本笔新增到**仓库根**的入库 `.py`（仓库卫生清单的登记形状）。"""
    return sorted(p for p, s in status.items() if s == "A" and p.endswith(".py") and "/" not in p)


def evaluate(repo: Path, rev: str) -> tuple[list[str], list[str], dict]:
    """返回 `(红, 射程塌, 读数)`。"""
    red: list[str] = []
    errs: list[str] = []
    sha = head_sha(repo)
    if rev != "HEAD" and rev != sha:
        pass  # --rev 允许指到历史某一笔，判据同形
    tree = head_paths(repo)
    changed, note = changed_files(repo, rev)
    parent = parent_of(repo, rev)
    if parent is None:
        errs.append(f"`{sha[:7]}` 读不出父提交（根提交）：同批性对撞没有对照面，本门无从判定")
        return red, errs, {}
    docs_changed = {c for c in changed if c.endswith(".md") and c.startswith("docs/")}

    per_ledger: dict[str, dict] = {}
    for entry in ROSTER:
        doc = entry["doc"]
        if doc not in tree:
            errs.append(f"名册里的清单 `{doc}` 不在 HEAD 树里——名册与库对不上，本门没有口径")
            continue
        if entry["gate"] not in tree:
            errs.append(f"名册里的生成门 `{entry['gate']}` 不在 HEAD 树里——同一枚锚断了另一半")
            continue
        try:
            anchors = anchors_in_doc(repo, doc, entry["anchor_re"])
        except ScopeDown as exc:
            errs.append(f"读不出 `{doc}` 的 HEAD 面：{exc}")
            continue
        if not anchors and not entry.get("anchors_optional"):
            if not entry["none_re"].search(_git(repo, ["git", "show", f"HEAD:{doc}"])):
                errs.append(
                    f"`{doc}` 现读锚点 0 枚，且没有「无」标记行——锚点行形状变了或被清空；"
                    "本门此刻数到的「没有要同批的东西」是空集给的假干净"
                )
                continue
        shifted: list[str] = []
        for path, lnos in anchors.items():
            if path not in changed:
                continue
            if changed[path] in ("D", "R"):
                shifted.append(f"`{path}` 被{changed[path] == 'D' and '删除' or '改名'}（在册 {len(lnos)} 枚锚点全部悬空）")
                continue
            bad = shifting_hunks(repo, rev, path, max(lnos))
            if bad:
                shifted.append(f"`{path}`（在册 {len(lnos)} 枚锚点，最大行号 {max(lnos)}）：{bad[0]}")
        narrow: list[str] = []
        if entry.get("toplevel_py"):
            new_py = _toplevel_new_py(changed)
            if new_py:
                narrow.append(f"根目录新增入库 `.py` {len(new_py)} 份（{new_py[0]} 等）")
        will_shift = bool(shifted or narrow)
        same_batch = doc in changed
        per_ledger[doc] = {
            "anchors": sum(len(v) for v in anchors.values()),
            "anchor_files": len(anchors),
            "shifted": shifted,
            "narrow": narrow,
            "same_batch": same_batch,
        }
        if will_shift and not same_batch:
            red.append(
                f"A：{RED_CAUSE}——`{rev[:7]}` 会让 `{doc}` 的登记漂"
                + (f"（{shifted[0]}）" if shifted else "")
                + (f"；{narrow[0]}" if narrow and not shifted else "")
                + f"，而这一笔⛔ 带上它。修法：`python {entry['gate']} --write` 与源码改动同一笔提交"
            )

    # B 腿：本笔只动清单面，上一笔却让同一枚清单漂过 ⇒ 那就是裁定点名要堵的「分两笔」。
    if changed and not (changed.keys() - docs_changed):
        prev_changed, _ = changed_files(repo, parent)
        for entry in ROSTER:
            doc = entry["doc"]
            if doc not in tree or doc not in changed or doc in prev_changed:
                continue
            try:
                anchors = anchors_in_doc(repo, doc, entry["anchor_re"])
            except ScopeDown:
                continue
            drift: list[str] = []
            for path, lnos in anchors.items():
                if path not in prev_changed:
                    continue
                if prev_changed[path] in ("D", "R") or shifting_hunks(repo, parent, path, max(lnos)):
                    drift.append(f"`{path}`")
            prev_narrow = bool(entry.get("toplevel_py") and _toplevel_new_py(prev_changed))
            if drift or prev_narrow:
                red.append(
                    f"B：{RED_CAUSE}（分两笔）——源码在 `{parent[:7]}` 让 `{doc}` 的登记漂了"
                    + (f"（{drift[0]} 等 {len(drift)} 份）" if drift else "（根目录新增入库 `.py`）")
                    + f"，清单却在下一笔 `{rev[:7]}` 才补。裁定 §一 Q1 要堵的就是这个形状："
                    "两笔一起推上去时 CI 只看得到笔尖"
                )

    # C 腿：HEAD 里带自动段标记的清单必须全部在册。
    for path in tree:
        if not (path.startswith("docs/") and path.endswith(".md")):
            continue
        if path in {e["doc"] for e in ROSTER}:
            continue
        try:
            text = _git(repo, ["git", "show", f"HEAD:{path}"])
        except ScopeDown:
            continue
        m = AUTO_MARKER.search(text)
        if m:
            red.append(
                f"C：`{path}` 带自动段标记（生成门 `{m.group('gate')}`）却不在本门名册里——"
                f"新开台账必须同时在 `ROSTER` 认领（锚点行正则＋现读根），否则它会永远逃过同批判据"
            )

    readout = {
        "sha": sha,
        "rev": rev,
        "parent": parent,
        "changed": len(changed),
        "docs_changed": len(docs_changed),
        "tree": len(tree),
        "ledgers": per_ledger,
    }
    return red, errs, readout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rev", default="HEAD", help="判定哪一笔提交（默认 HEAD）")
    ap.add_argument("--repo", default=str(REPO), help="取数根（默认本仓）")
    ns = ap.parse_args(argv)
    repo = Path(ns.repo).resolve()
    if not (repo / ".git").exists():
        print("✗ 射程塌：取数根不是 git 仓库——同批性判的是提交边界，没有仓库就没有口径")
        return 2
    try:
        rev = _git(repo, ["git", "rev-parse", "--verify", ns.rev]).strip()
    except ScopeDown as exc:
        print(f"✗ 射程塌：读不出提交 `{ns.rev}`：{exc}")
        return 2
    try:
        red, errs, info = evaluate(repo, rev)
    except ScopeDown as exc:
        print(f"✗ 射程塌：{exc}")
        return 2
    if errs:
        print(f"✗ 台账同批门禁射程塌（{len(errs)} 项）：")
        for e in errs:
            print(f"  · {e}")
        print("  修法：名册与清单／生成门必须同时在库；锚点行形状变了要连本门的正则一起改。⛔ 用「没有发现」冒充「没有问题」。")
        return 2
    if red:
        print(f"✗ 台账同批门禁发现 {len(red)} 处：")
        for r in red:
            print(f"  · {r}")
        print(f"  修法：{RED_CAUSE} 的红只有一条路——把清单重生成与源码改动放进同一笔提交（`--write` 现吃 HEAD 树，"
              "工作树脏不脏与它无关，裁定 20261014 §一 Q2）。")
        return 1
    ledgers = info.get("ledgers", {})
    anchor_total = sum(v["anchors"] for v in ledgers.values())
    print(
        f"读数：判定 `{info['rev'][:7]}`（父 `{info['parent'][:7]}`）／本笔改动 {info['changed']} 份"
        f"（其中清单面 {info['docs_changed']} 份）／在册清单 {len(ledgers)} 份／锚点现读 {anchor_total} 枚"
    )
    print(f"✓ 改动会移动在册 `file:line` 锚点的提交，都与它对应的清单同一笔入库；HEAD 带自动段标记的清单全部在册（{len(ledgers)} 份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

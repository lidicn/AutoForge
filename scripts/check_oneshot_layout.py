#!/usr/bin/env python3
"""仓库卫生门禁：根目录一次性脚本**机械拒绝** ＋ `scripts/oneshot/` 约定在册 ＋ 存量只减不增。

依据：第六轮架构审计 §ARCH-08（「已入库的 `analyze_failures.py`／`clear_pending.py`／`export_ir.py`／`fix_ir.py`
与 `KICKOFF.md`、`pyproject.toml` 平级 ⇒ 缺的是"一次性脚本该放哪"的约定」）与同批对账文档登记的残余
（「`scripts/oneshot/` 约定 ＋ 对根目录 `_*` 的机械拒绝」要配套存量名单）。

**为什么判提交面（HEAD）而不是判磁盘**：`.gitignore:91` 的 `_*.py` 已经把盘面上的临时件挡在库外（那一格审计自己
记成"成立·已有防线"）。真漏的是提交面——`git add -f` 穿得过 ignore，而"根目录躺着一份已入库的一次性脚本"
ignore 根本管不着。同口径先例：`scripts/check_pkg_markers.py`（§二之四十四 实测：判磁盘检不出
"盘上有、索引里没有"那一半）。所以 git 读不出 HEAD 树时本门 `exit 2`，⛔ 不用"盘上没有"冒充"库里没有"。

五条判据（各自单独可红）：
  A「自动段逐行对撞」：`docs/仓库卫生清单.md` 的 AUTO 段必须等于现读的提交面（HEAD 树）（根目录 `.py` 名单／
    单下划线脚本名单／`scripts/oneshot/` 名单／登记数与上限）。往约定目录加一份脚本而不重生成登记，
    清单就变成会过期的副本——那正是本仓 fight 的那一族。
  B「默认拒绝」：① 根目录新增入库 `.py` 而登记面上没有它 ⇒ 红；② 提交面任何**单**下划线前缀的
    `.py`／`.sh`／`.conf` ⇒ 红（那枚名字本被 ignore 挡着，能进来只可能是 `git add -f`）。
    双下划线的包标记（`__init__.py`）刻意排除在外，那是另一族，由判据 D 钉。
  C「棘轮只减不增」：存量登记条目数 > `LEGACY_CAP` ⇒ 红（⛔ 这张表不是豁免通道，加一行不会让 B 变绿）；
    登记了却已不在库 ⇒ 过期登记红；身份／理由／处置任一为空或占位词 ⇒ 红（没认领＝与不登记同判）。
  D「ignore 防线两枚锚点」：`.gitignore` 必须同时有 `_*.py` 与 `!**/__init__.py`。前者失守＝临时件能入库；
    后者失守＝回到 §二之四十四 那格（`_` ＋ `*` 连带吃掉 `__init__.py` ⇒ grimp 不递归 ⇒ CI 门比本机门弱）。
  E「约定面与迁移记录自证」：`scripts/oneshot/README.md` 必须真在库；迁移记录每一行的**新路径在库**且
    **原根目录名已不在库**——登记说搬走却没搬、或复制而非移动，两种都红。

退出码就是结论：0 干净 / 1 有未获批违规 / 2 射程塌（锚点读不出＝本门无从判定，报"干净"就是假绿）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC_REL = "docs/仓库卫生清单.md"
GITIGNORE_REL = ".gitignore"
ONESHOT_DIR = "scripts/oneshot"
ONESHOT_README = f"{ONESHOT_DIR}/README.md"
SCRIPT_EXTS = (".py", ".sh", ".conf")
LEGACY_CAP = 0
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_oneshot_layout.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"
SECTION_LEGACY = "## 二、存量登记"
SECTION_MIGRATION = "## 四、迁移记录"
GITIGNORE_ANCHORS = ("_*.py", "!**/__init__.py")
UNDERSCORE_NAME = re.compile(r"^_[^_].*(" + "|".join(re.escape(e) for e in SCRIPT_EXTS) + r")$")
PLACEHOLDERS = {"", "-", "—", "–", "?", "待补", "TODO", "TBD", "n/a", "N/A"}
NONE_ROW = "无"


def tracked_paths(repo: Path) -> list[str] | None:
    """HEAD 树里的相对路径（裁定 20261014 §一 Q2：清单生成器吃**提交面**，⛔ 吃索引、⛔ 吃工作树）；
    git 不可用／这里不是仓库／仓里一枚提交都没有 ⇒ None（由调用方判射程塌）。"""
    try:
        out = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.split("\0") if p]


def _cells(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [c.strip() for c in body.split("|")]


def _table_rows(text: list[str], heading: str) -> list[list[str]] | None:
    """那张节里的表格体（去掉表头行与 `|---|` 分隔行）；节不存在返回 None，节在却没有表返回 []。"""
    start = None
    for i, line in enumerate(text):
        if line.startswith(heading):
            start = i
            break
    if start is None:
        return None
    rows: list[list[str]] = []
    for line in text[start + 1:]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _cells(stripped)
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    return rows[1:]


def _unquote(cell: str) -> str:
    return cell.strip().strip("`").strip()


def _blank(cell: str) -> bool:
    return _unquote(cell) in PLACEHOLDERS


def parse_legacy(text: list[str]) -> tuple[list[dict[str, str]], list[str], list[str], bool]:
    """存量登记表 ⇒ `(条目, 判据 C 的红, 射程塌, 是否显式写了「无」)`。

    分档口径：**节没了**才是射程塌（对照物消失，本门无从判定）；**行填得不对**是违规（红），
    把占位词读成 exit 2 会让"没认领"这一族藏在"门自己读不出"里，正好躲开它该逼的修法。
    """
    rows = _table_rows(text, SECTION_LEGACY)
    if rows is None:
        return [], [], [f"清单里读不出 `{SECTION_LEGACY}` 那一节：登记面整个挪走或改名，判据 B／C 失去对照物"], False
    if not rows:
        return [], [], [f"`{SECTION_LEGACY}` 节在，但表里数不出任何一行（表头之外）：本门没有对照物，"
                        "此刻报『干净』是空集给的干净"], False
    entries: list[dict[str, str]] = []
    explicit_none = False
    reds: list[str] = []
    for cells in rows:
        name = _unquote(cells[0]) if cells else ""
        if not name:
            continue
        if name == NONE_ROW:
            explicit_none = True
            continue
        if len(cells) < 4:
            reds.append(f"C：存量登记 `{name}` 只有 {len(cells)} 格（要 4 格：文件／身份／为什么还在根／处置）"
                        "——格数不齐＝这一行没填完，不算认领")
            continue
        blanks = [label for label, cell in
                  zip(("身份", "为什么还在根", "处置"), cells[1:4]) if _blank(cell)]
        if blanks:
            reds.append(f"C：存量登记 `{name}` 的 {'／'.join(blanks)} 是空的或占位词——没写理由＝没认领，"
                        "与不登记同判红")
        entries.append({"file": name, "row": " | ".join(cells)})
    return entries, reds, [], explicit_none


def parse_migration(text: list[str]) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """迁移记录 ⇒ `(条目, 判据 E 的红, 射程塌)`。同样：节没了退 2，行填错了判红。"""
    rows = _table_rows(text, SECTION_MIGRATION)
    if rows is None:
        return [], [], [f"清单里读不出 `{SECTION_MIGRATION}` 那一节：判据 E 失去对照物"]
    if not rows:
        return [], [], [f"`{SECTION_MIGRATION}` 节在却数不出一行：本门的"
                        "『登记说搬走＝真搬走』判据没有射程，报『干净』是空集给的干净"]
    entries: list[dict[str, str]] = []
    reds: list[str] = []
    for cells in rows:
        if len(cells) >= 1 and _unquote(cells[0]) == NONE_ROW:
            continue
        if len(cells) < 3:
            reds.append(f"E：迁移记录那一行只有 {len(cells)} 格（要 3 格：原根目录名／现入库路径／为什么搬）")
            continue
        if _blank(cells[0]) or _blank(cells[1]):
            reds.append(f"E：迁移记录 `{cells[0]} → {cells[1]}` 的名字或路径是空的／占位词——"
                        "指不到实物的一行不构成记录")
            continue
        if _blank(cells[2]):
            reds.append(f"E：迁移记录 `{_unquote(cells[0])}` 没写「为什么搬」")
        entries.append({"old": _unquote(cells[0]), "new": _unquote(cells[1])})
    return entries, reds, []


def read_state(repo: Path, tracked: list[str]) -> dict[str, object]:
    root_py = sorted(p for p in tracked if "/" not in p and p.endswith(".py"))
    underscore = sorted(p for p in tracked if UNDERSCORE_NAME.match(p.rsplit("/", 1)[-1]))
    oneshot = sorted(p for p in tracked if p.startswith(ONESHOT_DIR + "/"))
    return {"root_py": root_py, "underscore": underscore, "oneshot": oneshot,
            "py_total": sum(1 for p in tracked if p.endswith(".py"))}


def render_auto(state: dict[str, object], n_entries: int, cap: int) -> list[str]:
    def names(items: list[str]) -> str:
        return "／".join(f"`{p}`" for p in items) if items else "无"
    return [
        f"- 根目录入库 `.py`（{len(state['root_py'])} 个）：{names(state['root_py'])}",
        f"- 入库面单下划线前缀脚本（`.py`／`.sh`／`.conf`，{len(state['underscore'])} 个）：{names(state['underscore'])}",
        f"- `{ONESHOT_DIR}/` 入库文件（{len(state['oneshot'])} 个）：{names(state['oneshot'])}",
        f"- 存量登记条目 {n_entries} 条／`LEGACY_CAP` 上限 {cap} 条",
    ]


def _auto_block(text: list[str]) -> tuple[list[str] | None, str | None]:
    try:
        begin = text.index(AUTO_BEGIN)
        end = text.index(AUTO_END)
    except ValueError:
        return None, "清单里的 AUTO 标记读不出（`AUTO-BEGIN`／`AUTO-END` 两行被改写或删掉）：本门与清单失去对撞面"
    if end <= begin:
        return None, "清单的 AUTO 段标记顺序颠倒（`AUTO-END` 在 `AUTO-BEGIN` 之前）：那一块不是本门生成的段"
    # 只数非空行：一对标记之间留一个空行，不构成"有对撞对象"（否则判据 A 会拿空行当第一行去比，
    # 把"自动段被清空"这一档射程塌读成一条普通漂移）。
    return [ln for ln in text[begin + 1:end] if ln.strip()], None


def check(repo: Path, tracked: list[str] | None = None) -> tuple[list[str], list[str], dict[str, object]]:
    """返回 `(红, 射程塌, 读数)`。`tracked` 为 None 时自己去读 HEAD 树（判据的口径是提交面）。"""
    red: list[str] = []
    errs: list[str] = []

    if tracked is None:
        tracked = tracked_paths(repo)
        if tracked is None:
            errs.append(f"在 {repo} 读不出 git 索引（HEAD 树）（git 不可用／这里不是仓库／仓里一枚提交都没有）：本门判的是**入库面（提交面）**，"
                        "没有提交就没有口径——不许改用磁盘读数冒充结论")
            return red, errs, {}

    doc = repo / DOC_REL
    if not doc.is_file():
        errs.append(f"{DOC_REL} 不在盘上：登记面没了，本门五条判据全部失去对照物")
        return red, errs, {}
    text = doc.read_text(encoding="utf-8").splitlines()

    gi = repo / GITIGNORE_REL
    if not gi.is_file():
        errs.append(f"{GITIGNORE_REL} 不在盘上：判据 D 的两枚锚点无从核对（防线本身读不到＝射程塌）")
        return red, errs, {}

    state = read_state(repo, tracked)
    if state["py_total"] == 0:
        errs.append(f"HEAD 树读得出却一个入库 `.py` 都没有（盘上却不可能为 0）：路径口径断了，"
                    "本门此刻数到的「根目录 0 个」是空提交面给的假干净")
        return red, errs, {}

    entries, legacy_reds, legacy_errs, explicit_none = parse_legacy(text)
    migration, mig_reds, mig_errs = parse_migration(text)
    errs.extend(legacy_errs)
    errs.extend(mig_errs)

    auto, err = _auto_block(text)
    if err:
        errs.append(err)
        return red, errs, state
    red.extend(legacy_reds)
    red.extend(mig_reds)

    expected = render_auto(state, len(entries), LEGACY_CAP)
    if not auto:
        errs.append(f"AUTO 段一行都没有（空段）：判据 A 没有对撞对象，报『干净』是空集给的干净")
        return red, errs, state

    # ── 判据 A：自动段逐行对撞 ────────────────────────────────────
    for i in range(max(len(auto), len(expected))):
        got = auto[i] if i < len(auto) else "（该行缺失）"
        want = expected[i] if i < len(expected) else "（多出一行，本门只生成 {n} 行）".replace("{n}", str(len(expected)))
        if got != want:
            red.append(f"A：{DOC_REL} 的自动段第 {i + 1} 行与现读不一致\n    清单写的：{got}\n    现读的：{want}"
                       "　⇒ 修法：`python scripts/check_oneshot_layout.py --write`（先确认差异是真搬家，不是清单被人手改）")

    # ── 判据 B：默认拒绝 ─────────────────────────────────────────
    registered = {e["file"] for e in entries}
    for name in state["root_py"]:
        if name not in registered:
            red.append(
                f"B：`{name}` 在仓库根入库，而 `{SECTION_LEGACY}` 的登记面里没有它"
                f"（当前入库根目录 `.py` {len(state['root_py'])} 个／登记 {len(entries)} 条／上限 {LEGACY_CAP}）"
                f" ⇒ 一次性脚本请放 `{ONESHOT_DIR}/`，要留在根目录得先申请裁定把 `LEGACY_CAP` 抬上去")
    for path in state["underscore"]:
        red.append(
            f"B：提交面（HEAD 树）有单下划线前缀的脚本 `{path}`——`.gitignore` 的 `_*.py`／`_*.sh`／`_*.conf` 本来挡着它，"
            "现在它已进了提交面，只可能是 `git add -f` 穿过 ignore 又提交掉的。要么改名，要么把它从库里撤走"
            "（双下划线包标记不在此列，那一族由判据 D 看）")

    # ── 判据 C：棘轮只减不增 ─────────────────────────────────────
    if len(entries) > LEGACY_CAP:
        red.append(f"C：存量登记 {len(entries)} 条 > 上限 {LEGACY_CAP} 条。这张表⛔不是豁免通道——"
                   f"上限 0 意味着根目录不该有入库脚本，补一行只是把红从判据 B 换到这一格"
                   f"（整条链仍然绿不了）；真要抬上限得改代码里的 `LEGACY_CAP` 常量并先申请裁定")
    for e in entries:
        if e["file"] not in state["root_py"]:
            red.append(f"C：登记 `{e['file']}` 已不在根目录提交面（过期登记）——存量棘轮只减不增，"
                       "文件没了就把那一行删掉，挂着会让下一个人以为根目录还有它")
    if entries and explicit_none:
        red.append(f"C：`{SECTION_LEGACY}` 同时写了显式「{NONE_ROW}」和 {len(entries)} 条真实条目：两种口径不能并存")

    # ── 判据 D：ignore 防线两枚锚点 ──────────────────────────────
    gi_lines = {ln.strip() for ln in gi.read_text(encoding="utf-8").splitlines()}
    for anchor in GITIGNORE_ANCHORS:
        if anchor not in gi_lines:
            why = ("一次性临时件默认不入库这条防线没了" if anchor == "_*.py"
                   else "包标记例外没了：`_*.py` 会连带吃掉 `__init__.py`（`_`＋`*`=`__init__`＋`.py`），"
                        "后果是 grimp 不递归进无包标记的目录 ⇒ CI 上的架构门禁比本机少分析模块，"
                        "即 §二之四十四 实测过的「CI 门比本机门弱」")
            red.append(f"D：{GITIGNORE_REL} 里没有整行 `{anchor}`——{why}")

    # ── 判据 E：约定面与迁移记录自证 ────────────────────────────
    if ONESHOT_README not in tracked:
        red.append(f"E：约定目录的说明文件 `{ONESHOT_README}` 不在库里：目录不存在或没入库 ⇒ "
                   "「一次性脚本放哪」又变回一句口头约定，本门的落点没有实物")
    for m in migration:
        if m["new"] not in tracked:
            red.append(f"E：迁移记录说 `{m['old']}` 已搬到 `{m['new']}`，可 HEAD 树里没有后者——登记比事实乐观")
        if m["old"] in state["root_py"]:
            red.append(f"E：迁移记录说 `{m['old']}` 已搬走，根目录提交面却还有它——复制而非移动，"
                       "同一份残骸现在有两处")

    info = dict(state)
    info.update({"legacy_entries": len(entries), "cap": LEGACY_CAP,
                 "migration_rows": len(migration), "tracked_total": len(tracked),
                 "auto_lines": len(auto)})
    return red, errs, info


def write_doc(repo: Path, tracked: list[str] | None = None) -> int:
    doc = repo / DOC_REL
    if not doc.is_file():
        print(f"射程自证：{DOC_REL} 不在盘上——没有登记面可写，先建档再 --write")
        return 2
    if tracked is None:
        tracked = tracked_paths(repo)
        if tracked is None:
            print(f"射程自证：读不出 HEAD 树，自动段该写什么无从判定（本门口径＝提交面）")
            return 2
    text = doc.read_text(encoding="utf-8").splitlines()
    entries, _reds, _errs, _none = parse_legacy(text)
    lines = render_auto(read_state(repo, tracked), len(entries), LEGACY_CAP)
    auto, err = _auto_block(text)
    if err:
        print(f"射程自证：{err}")
        return 2
    head = text[:text.index(AUTO_BEGIN) + 1]
    tail = text[text.index(AUTO_END):]
    new = "\n".join(head + lines + tail) + "\n"
    if "\r" in doc.read_text(encoding="utf-8"):
        print("射程自证：清单文档里有 CR——先把行尾归一成 LF 再写，否则整份重排会盖掉对撞面")
        return 2
    doc.write_bytes(new.encode("utf-8"))
    print(f"已重生成 {DOC_REL} 自动段（{len(lines)} 行）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", nargs="?", default=str(REPO), help="仓根（默认脚本所在仓的上一级）")
    ap.add_argument("--write", action="store_true", help="用现读提交面（HEAD 树）重生成清单的自动段")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.write:
        return write_doc(repo)
    red, errs, info = check(repo)
    if errs:
        for e in errs:
            print(f"射程自证：{e}")
        return 2
    print(f"读数：入库面（取数面＝提交面 HEAD 树，`git ls-tree HEAD`）{info['tracked_total']} 个文件／根目录 `.py` {len(info['root_py'])} 个／"
          f"单下划线脚本 {len(info['underscore'])} 枚／`{ONESHOT_DIR}/` {len(info['oneshot'])} 个／"
          f"存量登记 {info['legacy_entries']} 条（上限 {info['cap']}）／迁移记录 {info['migration_rows']} 行")
    if red:
        for r in red:
            print(f"红：{r}")
        return 1
    print(f"✓ 仓库根没有一次性脚本，一次性件在 `{ONESHOT_DIR}/`，登记面与提交面逐行对上")
    return 0


if __name__ == "__main__":
    sys.exit(main())

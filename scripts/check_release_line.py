#!/usr/bin/env python3
"""发布线一致门禁：自更新链与部署指令链用的**分支字面**必须同源，且逐枚在册。

依据：DCD 裁定 `E:\\NAS\\关键决策部\\decisions\\20261013-DB历史凭据泄露与部署树复原与AF-MA口径-裁定.md` §三
最后一行——「要不要立一枚"分支一致门"：**立**。因为**当前没有任何门守着它**——将来有人把脚本改掉字面，
**没有门会红**（这正是"永远绿的闸"的孪生形态）。落码归 AF 席位（`scripts/` 在它职权内）」。

为什么判**提交面（HEAD 树）**（git 索引）而不是磁盘：本门要防的是「进了库、被推上发布线之后，NAS 那棵树会被喂哪一条线」，
不是「手边草稿」。同一口径的先例是 `scripts/af_face.py`＋`scripts/check_process_model.py`。由此得到一条
**必须写明的代价**：在途未 `git add` 的改动不进判据（工作树此刻可能是另一套字面，见 `docs/发布线清单.md` §五），
未跟踪的同名载体既不算交付也不算违规——那三格都在结论行与清单里如实标注，⛔ 不伪装成"一致"。
所以本门**拒绝磁盘降级**：读不出 HEAD 树就是 `exit 2`，⛔ 不用"读不出"冒充"没有违规"。

五条判据（各自单独可红）：
  A「自动段逐行对撞」：`docs/发布线清单.md` 的 AUTO 段必须等于现读提交面（HEAD 树）的站点集合（路径／行号／角色／字面
    任一漂移即红）。载体改名、把 `REF=` 挪个位置、清单被手改，三种都能被这条咬住。
  B「默认拒绝登记」：① 现读站点（角色 × 载体文件）在 §三 角色登记表里没有对应行 ⇒ 红——新增一枚分支字面载体
    必须先显式认领；② 登记表里有行、现读却数不出该站 ⇒ 过期登记红；③ 「这一格失守会怎样」或「认领」是空／占位词 ⇒ 红。
  C「发布线一致（本门的核心一句）」：每一枚载体的字面必须逐字节等于 §二「发布线（远端）」那一格；
    唯一例外是 `CI触发线`——它是一张名单，判据是「发布线 ∈ 名单」，⛔ 不要求名单只有一个名字。
    附带同一格的 `body.branch 取值域` 登记值与现读对撞：偷偷加校验、偷偷放宽，两个方向都红。
  D「三枚形状锚点」：① 宿主脚本必须经**变量**取 ref（`origin "$REF"` ＋ `origin/$REF"`），把字面退回硬编码就红；
    ② 自更新端点写标记前必须有版本二次确认比对、脏/在途请求 409 拒执、`atomic_write_text` 三枚形状；
    ③ **键名同源**：写入侧 `"ref": branch` 与读取侧 `.get('ref'` 必须是同一个键——键名一分家，
      用户选的 ref 会被静默丢弃、读取侧永远吃缺省，那是本门能造出的最坏假绿。
  E「站点上限只减不增」：站点数 > `SITE_CAP` ⇒ 红。要新增载体先改这张登记表并申请裁定抬上限。

退出码就是结论：0 干净 / 1 有未获批违规 / 2 射程塌（锚点读不出＝本门无从判定，报"干净"就是假绿）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from af_face import HEAD, Face, FaceError, Snapshot  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOC_REL = "docs/发布线清单.md"
AUTO_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_release_line.py --write 生成，勿手改 -->"
AUTO_END = "<!-- AUTO-END -->"
SECTION_ROLES = "## 三、角色登记"
KEY_RELEASE_LINE = "发布线（远端）"
KEY_LOCAL_LINE = "本地开发分支"
KEY_DOMAIN = "body.branch 取值域"
#: 射程：三条链的载体所在目录＋文件后缀。名单与内容同取出库面（见 docstring）。
SCANNED = (
    ("scripts", (".sh", ".py")),
    ("src/autoforge", (".py",)),
    (".github/workflows", (".yml",)),
)
#: 现读 8 枚站点（清单 §三 八行角色）。2026-10-17 DCD-20261017-001 §2.2④ 批准 5 → 8：
#: 新增的 2 枚是 host_check_update.sh（部署指令）与 host_update_nas.sh（兜底/读取侧），是既成事实登记而非放宽标准。
#: ⛔ 此后 8 为硬上限，第 9 枚必须删一枚换一枚，不许再抬。
SITE_CAP = 8
HOST_SH = "scripts/host_update.sh"
API_PY = "src/autoforge/af_api.py"
#: 未跟踪的同名载体只报观察行：提交面（HEAD 树）读不出它们，按裁甲「未进任何提交 ⇒ 不算交付」。
UNTRACKED_HINTS = ("scripts/host_check_update.sh", "scripts/host_update_nas.sh")
PLACEHOLDERS = {"", "-", "—", "–", "?", "待补", "TODO", "TBD", "n/a", "N/A", "无"}

ROLE_WRITE = "写入侧缺省"
ROLE_READ = "读取侧缺省"
ROLE_FALLBACK = "兜底字面"
ROLE_DEPLOY = "部署指令"
ROLE_CI = "CI触发线"

#: `body.branch or "main"` —— 容器侧写进 `.update_request.json` 的缺省 ref。
RE_WRITE = re.compile(r'body\.branch\s+or\s+"([A-Za-z0-9._/-]+)"')
#: `.get('ref','main')` —— 宿主 sidecar 读 marker 的缺省；单双引号两形都吃（bash 里嵌的是单引号）。
RE_READ = re.compile(r"""\.get\(\s*["']ref["']\s*,\s*["']([A-Za-z0-9._/-]+)["']""")
#: `REF=$(… || echo "main")` —— 第二层兜底；只认 `REF=` 那一行，免得把 `|| echo "unknown"` 那族误收进来。
RE_REF_ASSIGN = re.compile(r"^\s*REF=")
RE_ECHO = re.compile(r'\|\|\s*echo\s+"([A-Za-z0-9._/-]+)"')
#: `origin/main` 这类**硬编码线名**：`origin "$REF"`／`origin/$REF"` 吃不到（`$` 不在字符类里），
#: 所以字面一旦被退回硬编码，这里就会多出一枚未登记站点——判据 B/E 因此有牙。
RE_DEPLOY = re.compile(r'\borigin/([A-Za-z0-9._/-]+)')
RE_BRANCHES = re.compile(r"^\s*branches:\s*\[([^\]]*)\]")
#: 取值域锚点：出现任一形状即判「有校验」，与清单 §二 那一格对撞。
#: 2026-10-23 二次扩形（v1.13.3）：白名单正则提取为模块常量（`_UPDATE_REF_RE = re.compile(r"master|v…")`
#: + 调用点 `_UPDATE_REF_RE.fullmatch(branch)`）——HTTP 端点与 MCP `af_deploy` 共用同一份校验
#: 时字面只在一处，锚点两形都认：内联 `re.fullmatch(r"master|v…"` 或常量定义 `_…_RE = re.compile(r"master|v…`。
RE_DOMAIN = re.compile(r"(?:branch\s+not\s+in|_ALLOWED_BRANCH|VALID_BRANCHES|fullmatch\(r\"master\|v|_UPDATE_REF_RE = re\.compile\(r\"master\|v)")
#: D 锚点（remote 名可参数化，2026-10-17 DCD-20261017-001 §2.3）：脚本按部署事实把 GitHub remote 名叫 `github`
#: （NAS 部署树），本地仓叫 `origin`。⛔ 门不把 remote 名写死成某一个——写死会把"脚本按事实写"误判成"读不出锚点"。
#: 只要求：ref 经变量 `$REF` 传参、fetch/rev-parse/checkout 成对、remote 名是合法 git remote 名（origin / github / nas 均可）。
#: ⚠️ 本文件自身注释里⛔出现 `origin/<名字>` 形状——那会被 RE_DEPLOY 抓成一枚假"部署指令"站点。
RE_ANCHOR_FETCH = re.compile(r'git fetch --force [A-Za-z0-9._-]+ "\$REF"')
#: 2026-10-23 换锚（autoflow 方案 C 照搬）：`reset --hard` 被三护栏禁用（"升级按钮当格式化键"事故），
#: checkout 改走 rev-parse 解析的 pinned SHA——锚点随之换成「rev-parse <remote>/$REF + checkout -f 变量」
#: 加一枚**反锚**：脚本里出现 `reset --hard` 本身即红。
RE_ANCHOR_REVPARSE = re.compile(r'git rev-parse "[A-Za-z0-9._-]+/\$REF"')
RE_ANCHOR_CHECKOUT = re.compile(r'git checkout -f "\$[A-Za-z_][A-Za-z0-9_]*"')
ANCHOR_FORBIDDEN_RESET = "reset --hard"
#: 2026-10-23 二次换锚（v1.13.3 af_deploy 照搬）：marker 原子写提取为模块级
#: `submit_update_request`（HTTP 端点与 MCP `af_deploy` 同一实现，不写第二份格式），
#: 锚点随之从 `atomic_write_text(_UPDATE_MARKER` 换成「提取函数的定义签名 + 函数内
#: `atomic_write_text(marker,` + 端点对它的调用」三枚——重构撞坏任一即红。
ANCHORS_API = (
    "def submit_update_request(",
    "atomic_write_text(marker,",
    "ok, message = submit_update_request(",
)
#: 「已有更新进行中」409 形状锚：2026-10-23 起 API 面从 `}, 409` 元组改为
#: `JSONResponse(status_code=409, ...)`（返回类型标注 dict ⇒ 元组是 500 隐患），
#: 两形态都认，删掉即红（⛔ 不靠 C 兜住）。
RE_ANCHOR_409 = re.compile(r'(?:status_code=409|\}, 409)')
ANCHORS_KEY_WRITE = re.compile(r'"ref":\s*branch')

SITE_RE = re.compile(
    r"^- 站点 `(?P<path>[^`]+):(?P<lineno>\d+)` · 角色=(?P<role>[^ ]+) · 字面=(?P<literal>.*\S)$"
)
TOTAL_RE = re.compile(r"^- 站点合计 = (?P<n>\d+) · 上限 = (?P<cap>\d+)$")


def _caliber_line(key: str) -> re.Pattern[str]:
    return re.compile(rf"^- {re.escape(key)} = (?P<val>.*\S)$")


class Site:
    """一枚分支字面载体。⛔ 不用 `@dataclass`：单测用 `importlib` 直载本文件、不注册进 `sys.modules`，
    3.13 的 `_process_class` 会去 `sys.modules[cls.__module__]` 取命名空间而抛 `AttributeError`（本批实测）。"""

    __slots__ = ("path", "lineno", "role", "literal")

    def __init__(self, path: str, lineno: int, role: str, literal: str) -> None:
        self.path = path
        self.lineno = lineno
        self.role = role
        self.literal = literal

    @property
    def key(self) -> tuple[str, int, str, str]:
        return (self.path, self.lineno, self.role, self.literal)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Site) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def line(self) -> str:
        return f"- 站点 `{self.path}:{self.lineno}` · 角色={self.role} · 字面={self.literal}"


def _snaps(root: Path) -> dict[str, Snapshot]:
    """三枚提交面（HEAD 树）子树快照；调用方负责 `close()`。"""
    out: dict[str, Snapshot] = {}
    for rel_dir, _exts in SCANNED:
        out[rel_dir] = Face.open(root, HEAD).snapshot(rel_dir)
    return out


def scan_sites(snaps: dict[str, Snapshot]) -> list[Site]:
    """现读提交面（HEAD 树），按角色提取每一枚分支字面站点。"""
    sites: list[Site] = []
    for rel_dir, exts in SCANNED:
        snap = snaps[rel_dir]
        for p in snap.files(rel_dir, exts):
            rel = snap.rel(p)
            try:
                text = snap.text(p)
            except FaceError:
                continue
            for i, raw in enumerate(text.splitlines(), 1):
                # 只吃代码行：注释与文档里的示例字面不是载体（本门自己被清单式例子撞过一次——
                # 那是「名字哨兵罩散文」的孪生形态；载体只有走进命令面的字面才会喂到 NAS）。
                if raw.lstrip().startswith("#"):
                    continue
                found: list[tuple[str, str]] = []
                if rel.endswith(".yml"):
                    m = RE_BRANCHES.match(raw)
                    if m:
                        names = sorted(x.strip() for x in m.group(1).split(",") if x.strip())
                        found.append((ROLE_CI, ",".join(names)))
                else:
                    if rel.endswith(".py"):
                        for m in RE_WRITE.finditer(raw):
                            found.append((ROLE_WRITE, m.group(1)))
                    for m in RE_READ.finditer(raw):
                        found.append((ROLE_READ, m.group(1)))
                    if RE_REF_ASSIGN.match(raw):
                        for m in RE_ECHO.finditer(raw):
                            found.append((ROLE_FALLBACK, m.group(1)))
                    for m in RE_DEPLOY.finditer(raw):
                        found.append((ROLE_DEPLOY, m.group(1)))
                for role, literal in found:
                    sites.append(Site(rel, i, role, literal))
    uniq = sorted(set(sites), key=lambda s: (s.path, s.lineno, s.role, s.literal))
    return uniq


def render(snaps: dict[str, Snapshot]) -> list[str]:
    sites = scan_sites(snaps)
    rows = [s.line() for s in sites]
    rows.append(f"- 站点合计 = {len(sites)} · 上限 = {SITE_CAP}")
    return rows


def _caliber(text: list[str]) -> dict[str, str]:
    """§二 那三行 `- 键 = 值`；读不到键不算塌（由 `caliber_ok` 统一判形）。"""
    out: dict[str, str] = {}
    for line in text:
        for key in (KEY_RELEASE_LINE, KEY_LOCAL_LINE, KEY_DOMAIN):
            m = _caliber_line(key).match(line)
            if m:
                out[key] = m.group("val").strip()
    return out


def _role_rows(text: list[str]) -> list[list[str]] | None:
    """§三 那张表体（去表头与分隔行）；节不存在返回 None，节在却没有行返回 []。"""
    start = None
    for i, line in enumerate(text):
        if line.startswith(SECTION_ROLES):
            start = i
            break
    if start is None:
        return None
    rows: list[list[str]] = []
    for line in text[start + 1:]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("角色", "") or set(cells[1]) <= {"-", ":", " "}:
            continue
        rows.append(cells)
    return rows


def _registered(rows: list[list[str]]) -> set[tuple[str, str]]:
    """(角色, 载体文件) 集合；一行的载体列可以有多枚反引号路径。"""
    out: set[tuple[str, str]] = set()
    for cells in rows:
        role = cells[0].strip()
        for path in re.findall(r"`([^`]+)`", cells[1]):
            out.add((role, path.strip()))
    return out


def doc_state(root: Path) -> tuple[list[str], str] | str:
    """返回（文档行, 已闭合的 AUTO 段）或一句射程塌的原因。"""
    doc_p = root / DOC_REL
    if not doc_p.is_file():
        return f"`{DOC_REL}` 不在盘上——本门的在册面整块消失，读成「没有违规」就是假绿"
    text = doc_p.read_text(encoding="utf-8", errors="replace").splitlines()
    if AUTO_BEGIN not in text or AUTO_END not in text:
        return (
            f"`{DOC_REL}` 里那对自动段标记读不出（被改写或清空）——判据 A 失去对照物：{AUTO_BEGIN[:24]}…"
        )
    i, j = text.index(AUTO_BEGIN), text.index(AUTO_END)
    if j < i:
        return f"`{DOC_REL}` 的自动段标记顺序反了（`AUTO-END` 在 `AUTO-BEGIN` 之前）"
    return text, "\n".join(text[i + 1:j]).strip("\n")


def caliber_ok(cal: dict[str, str]) -> str | None:
    for key in (KEY_RELEASE_LINE, KEY_DOMAIN):
        v = cal.get(key, "")
        if v in PLACEHOLDERS and key != KEY_DOMAIN:
            return f"§二「{key}」那一格是空／占位（现读 `{v or '空'}`）——没有真源，一致判据就无从判起"
    if KEY_RELEASE_LINE not in cal:
        return f"§二读不出「{KEY_RELEASE_LINE}」那一行（键名改了或整行挪走）"
    if KEY_DOMAIN not in cal:
        return f"§二读不出「{KEY_DOMAIN}」那一行——取值域这一格失去对照物"
    if cal[KEY_DOMAIN] not in {"有", "无", "master | v*tag"}:
        return f"§二「{KEY_DOMAIN}」现读 `{cal[KEY_DOMAIN]}`，只许写「有」「无」或「master | v*tag」"
    return None


def check(root: Path) -> tuple[list[str], dict[str, object]]:
    findings: list[str] = []
    snaps: dict[str, Snapshot] = {}
    try:
        for rel_dir, _exts in SCANNED:
            snaps[rel_dir] = Face.open(root, HEAD).snapshot(rel_dir)
        face_label = snaps[SCANNED[0][0]].label
    except FaceError as e:
        return [f"[射程] 提交面（HEAD 树）读不出：{e}"], {"label": "读不出"}
    try:
        state = doc_state(root)
        if isinstance(state, str):
            return [f"[射程] {state}"], {"label": face_label}
        text, auto_body = state
        cal = _caliber(text)
        bad = caliber_ok(cal)
        if bad:
            return [f"[射程] {bad}"], {"label": face_label}
        rows = _role_rows(text)
        if rows is None:
            return [f"[射程] §三「角色登记」整节读不出（改名或挪走）——判据 B 失去在册面"], {"label": face_label}
        if not rows:
            return [
                "[射程] §三那张表除表头外一行都没有——本门已盯不住任何载体，"
                "「干净」是空集给的干净",
            ], {"label": face_label}

        sites = scan_sites(snaps)
        release = cal[KEY_RELEASE_LINE]
        registered = _registered(rows)
        tracked = _tracked(root)
        info: dict[str, object] = {
            "sites": len(sites),
            "cap": SITE_CAP,
            "release": release,
            "rows": len(rows),
            "label": face_label,
            "domain": _domain_detected(snaps),
            "untracked": [rel for rel in UNTRACKED_HINTS if rel not in tracked],
        }
        if not sites:
            return [
                "[射程] 提交面（HEAD 树）读不出任何一枚分支字面站点——四条链的载体同时消失不是「没有违规」，"
                "是射程断了（载体改名／整块搬走／后缀口径变了）"
            ], info

        # 判据 A：自动段逐行对撞
        auto_lines = [ln for ln in auto_body.splitlines() if ln.startswith("- 站点 `")]
        fresh = render(snaps)
        fresh_lines = [ln for ln in fresh if ln.startswith("- 站点 `")]
        if auto_lines != fresh_lines:
            only_doc = [ln for ln in auto_lines if ln not in fresh_lines]
            only_face = [ln for ln in fresh_lines if ln not in auto_lines]
            n = max(len(only_doc), len(only_face))
            for k in range(n):
                findings.append(
                    f"A 自动段与现读对不上：清单 `{(only_doc[k] if k < len(only_doc) else '（清单缺这一行）')}` "
                    f"｜现读 `{(only_face[k] if k < len(only_face) else '（现读没有这一行）')}`"
                )
        doc_total = [ln for ln in auto_body.splitlines() if ln.startswith("- 站点合计")]
        want_total = f"- 站点合计 = {len(sites)} · 上限 = {SITE_CAP}"
        if doc_total != [want_total]:
            findings.append(
                f"A 汇总行对不上：清单现读 `{doc_total[0] if doc_total else '（没有汇总行）'}` ｜应为 `{want_total}`"
            )

        # 判据 B：默认拒绝登记／过期登记／占位
        for s in sites:
            if (s.role, s.path) not in registered:
                findings.append(
                    f"B `{s.path}:{s.lineno}` 是一枚未登记的「{s.role}」载体（字面 `{s.literal}`）"
                    f"——先在清单 §三 补一行认领它，再谈它该不该存在"
                )
        seen_pairs = {(s.role, s.path) for s in sites}
        for role, path in sorted(registered):
            if (role, path) not in seen_pairs:
                findings.append(f"B §三登记了「{role}」× `{path}`，现读提交面（HEAD 树）却数不出这一站——过期登记要删")
        for cells in rows:
            for idx, col in ((2, "这一格失守会怎样"), (3, "认领")):
                if len(cells) <= idx or cells[idx] in PLACEHOLDERS:
                    findings.append(
                        f"B 「{cells[0]}」那一行的「{col}」是空／占位（`{cells[idx] if len(cells) > idx else ''}`）"
                        "——没写清＝与不登记同判"
                    )

        # 判据 C：发布线一致（CI触发线按成员判）
        for s in sites:
            if s.role == ROLE_CI:
                if release not in s.literal.split(","):
                    findings.append(
                        f"C `{s.path}:{s.lineno}` 的触发名单 `{s.literal}` 里没有发布线 `{release}`"
                        "——推到发布线上的提交根本不跑 CI"
                    )
            elif s.literal != release:
                findings.append(
                    f"C `{s.path}:{s.lineno}` 的「{s.role}」字面是 `{s.literal}`，"
                    f"而发布线登记值（§二）是 `{release}`——两边不同源，NAS 会被两条路径喂两套代码"
                )
        detected_domain = _domain_detected(snaps)
        if detected_domain != cal[KEY_DOMAIN]:
            findings.append(
                f"C `body.branch` 取值域：清单登记「{cal[KEY_DOMAIN]}」而现读是「{detected_domain}」"
                "——加校验或撤校验都要连同那一格一起改，不许只改代码"
            )

        # 判据 D：三枚形状锚点
        host = _text_of(snaps, HOST_SH)
        api = _text_of(snaps, API_PY)
        if not RE_ANCHOR_FETCH.search(host):
            findings.append(
                f"D `{HOST_SH}` 读不出锚点 `git fetch --force <remote> \"$REF\"`——ref 必须是变量传参、"
                "remote 名可参数化（origin / github / nas 均可），退回硬编码线名就断链"
            )
        if not RE_ANCHOR_REVPARSE.search(host):
            findings.append(
                f"D `{HOST_SH}` 读不出锚点 `git rev-parse \"<remote>/$REF\"`——checkout 目标必须是"
                "解析出的 pinned SHA（v1.13.0 三护栏：禁 reset --hard，checkout -f 到定锚提交）"
            )
        if not RE_ANCHOR_CHECKOUT.search(host):
            findings.append(
                f"D `{HOST_SH}` 读不出锚点 `git checkout -f \"$<SHA变量>\"`——同上，checkout 目标必须是变量 ref"
            )
        if ANCHOR_FORBIDDEN_RESET in host:
            findings.append(
                f"D `{HOST_SH}` 出现 `reset --hard`——2026-10-23 三护栏禁用（autoflow「升级按钮当格式化键」"
                "事故铁律：永不 reset --hard / git clean -f）；checkout -f 到 pinned SHA 同效且不动分支指针"
            )
        for anchor in ANCHORS_API:
            if anchor not in api:
                findings.append(f"D `{API_PY}` 读不出锚点 `{anchor}`——自更新端点的三枚形状被重构撞坏了一枚")
        if not RE_ANCHOR_409.search(api):
            findings.append(f"D `{API_PY}` 读不出 409 形状锚（`status_code=409` 或 `}}, 409`）——进行中请求的冲突返回形状被挪走")
        if not ANCHORS_KEY_WRITE.search(api):
            findings.append(f"D `{API_PY}` 写标记处读不出 `" + '"ref": branch' + "`——写入侧键名漂移")
        if not RE_READ.search(host):
            findings.append(f"D `{HOST_SH}` 读不出 `.get('ref'`——读取侧键名漂移，"
                            "用户选的 ref 会被静默丢弃、永远吃缺省")

        # 判据 E：站点上限
        if len(sites) > SITE_CAP:
            findings.append(
                f"E 现读 {len(sites)} 枚站点 > 上限 {SITE_CAP}——新增载体要先在 §三 登记并申请抬上限"
            )
        return findings, info
    finally:
        for snap in snaps.values():
            snap.close()


def _tracked(root: Path) -> set[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=str(root), capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {n for n in out.decode("utf-8", "replace").split("\0") if n}


def _text_of(snaps: dict[str, Snapshot], rel: str) -> str:
    for snap in snaps.values():
        p = snap.base / rel
        if p.is_file():
            try:
                return snap.text(p)
            except FaceError:
                return ""
    return ""


def _domain_detected(snaps: dict[str, Snapshot]) -> str:
    """af_api 里读不读得出 branch 取值域校验。

    读出 fullmatch(r"master|v…) 内联形状**或**模块常量 `_…_RE = re.compile(r"master|v…")`
    形状 ⇒「master | v*tag」（v1.13.0 双侧校验形态；v1.13.3 提取常量供 HTTP/MCP 共用）；
    读出其他白名单/黑名单形状 ⇒「有」；读不出 ⇒「无」。
    """
    api = _text_of(snaps, API_PY)
    if re.search(r'fullmatch\(r"master\|v', api) or re.search(r'_UPDATE_REF_RE = re\.compile\(r"master\|v', api):
        return "master | v*tag"
    return "有" if RE_DOMAIN.search(api) else "无"


def write_doc(root: Path) -> int:
    snaps: dict[str, Snapshot] = {}
    try:
        for rel_dir, _exts in SCANNED:
            snaps[rel_dir] = Face.open(root, HEAD).snapshot(rel_dir)
        sites = scan_sites(snaps)
        if not sites:
            print("✗ 现读 0 枚站点，拒绝用空集改写自动段（那会把射程塌伪装成干净）")
            return 2
        body = "\n".join(render(snaps))
        doc_p = root / DOC_REL
        if not doc_p.is_file():
            print(f"✗ `{DOC_REL}` 不在盘上——`--write` 只重写既有标记之间，不代造清单")
            return 2
        doc = doc_p.read_text(encoding="utf-8", errors="replace")
        if AUTO_BEGIN not in doc or AUTO_END not in doc:
            print("✗ 自动段那对标记读不出（被改写或清空），拒绝改写")
            return 2
        head, _, rest = doc.partition(AUTO_BEGIN)
        _, _, tail = rest.partition(AUTO_END)
        with open(doc_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{head}{AUTO_BEGIN}\n{body}\n{AUTO_END}{tail}")
        print(f"✓ 已重新生成 `{DOC_REL}` 自动段（站点 {len(sites)} 枚；手工段未动）")
        return 0
    except FaceError as e:
        print(f"✗ 提交面（HEAD 树）读不出，拒绝改写：{e}")
        return 2
    finally:
        for snap in snaps.values():
            snap.close()


def require_index(root: Path) -> str | None:
    """本门拒绝磁盘降级：读不出 HEAD 树就没有口径（理由见 docstring 第三段）。"""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=str(root), capture_output=True, check=True
        ).stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.CalledProcessError):
        return "git 不可用或这里不是工作树"
    if Path(top).resolve() != Path(root).resolve():
        return f"`git rev-parse --show-toplevel` 读出的根（`{top}`）不等于取数根——索引面不是这棵树"
    return None


def main(argv: list[str]) -> int:
    root = REPO
    rest = [a for a in argv[1:] if not a.startswith("--")]
    if rest:
        root = Path(rest[0]).resolve()
    if "--write" in argv:
        bad = require_index(root)
        if bad:
            print(f"✗ 发布线门禁拒绝改写自动段：{bad}——自动段是按**提交面（HEAD 树）**生成的，"
                  "用磁盘面改写会把在途字面烤进清单（#118 那族两边不同源就是这么来的）")
            return 2
        return write_doc(root)
    bad = require_index(root)
    if bad:
        print(f"✗ 发布线门禁读不出射程：{bad}——本门判**提交面（HEAD 树）**，⛔ 不许改用磁盘读数冒充结论")
        print(
            "  修法：在本仓工作树里跑（`python scripts/check_release_line.py`）；"
            "副本树要先 `git init` ＋ `git add` 才有索引面可读。"
        )
        return 2
    findings, info = check(root)
    if findings and all(f.startswith("[射程]") for f in findings):
        print(f"✗ 发布线门禁读不出射程：{findings[0][len('[射程] '):]}")
        print(
            "  七种形状：① `docs/发布线清单.md` 不在盘上；② 读不出 HEAD 树或取数根不等于工作树根——本门判**提交面（HEAD 树）**，"
            "⛔ 不许改用磁盘读数冒充结论；③ 自动段那对标记被改写或被清空；④ §二「发布线（远端）」或「取值域」那一格"
            "改名／留空；⑤ §三 那张表除表头外一行都没有；⑥ 四条链的载体在提交面（HEAD 树）同时读不出；⑦ 快照导出缺文件。"
            "七种都不许报「干净」。"
        )
        return 2
    if findings:
        print(f"✗ 发布线门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}" if f.startswith("[") else f"  · {f}")
        print(
            "  修法：A 跑 `python scripts/check_release_line.py --write` 重生成自动段（先确认那处漂移是真的）；"
            "B 在 §三 补一行认领新载体，或删掉过期那一行；C 把载体字面改成 §二「发布线（远端）」那一格的值——"
            "**要换发布线请改那一格并申请裁定**，⛔ 不要逐枚改字面（那正是本门要拦的形状）；"
            "D 是把已验证过的三枚形状钉住的锚点，改坏它请连同本门一起说明；"
            "E 站点数只减不增，抬上限先申请。"
        )
        return 1
    untracked = info.get("untracked") or []
    print(
        f"✓ 发布线门禁干净（现读 {info['sites']} 枚站点全部等于发布线 `{info['release']}`；"
        f"§三 {info['rows']} 行角色登记与站点互相闭合；上限 {info['cap']}；"
        f"`body.branch` 取值域现读与登记同为「{info['domain']}」；"
        + (
            f"⚠️ 观察：{len(untracked)} 枚同名件未进任何提交 ⇒ 提交面（HEAD 树）射程外（不算交付也不算违规）；"
            if untracked else ""
        )
        + f"{info['label']}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

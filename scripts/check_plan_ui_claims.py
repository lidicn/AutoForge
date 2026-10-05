#!/usr/bin/env python3
"""计划表口径门：`docs/plan/开发计划_WebUI全功能接入.md` 里声明"UI 已接 ✅"的每一条路由，
必须被 UI↔路由契约门的反向读数认领为"有调用点"。

缘起（§六 那条登记的原文是"其余 ✅ 行未经逐行复测"）：上一批按 `--list-uncalled` 的实测读数，
把会话族那三行的 `✅` 改成 `后端 ✅ ／ UI ✗` 的双段写法，并在登记里写明"若下一批要拿这份计划表
当『已完成』的依据，先对一遍 `--list-uncalled` 的名单"。那句话是一次性的**人工动作**：文档改一次、
门跑一次，两边没有任何机械接缝。而这份表漂移的方向恰好是**文档比代码乐观**（面板被拆、路由改了名，
✅ 还留在原处），本仓对这一族的定性一直是"该红的不红"。本门把那次人工对表变成每批都做的判据，
读数口径完全复用 `check_ui_api_paths.py`——**本门不建第二份路由名单**，与"名单手抄"那一族同规。

两条判据（两边的集合都从兄弟门现取）：
- **判据 A（虚报已接）**：某行状态栏以 `✅` 领头，而它完整写出的 `VERB /api/…` 落在兄弟门的
  "未被任何调用点认领"名单里 ⇒ 红。文档说面板接了，门在三棵第一方 UI 树里读不出认领。
- **判据 B（引用了盘上落不到的契约）**：文档里完整写出的任意一条 `VERB /api/…`（不论状态）
  在服务端路由表里读不到 ⇒ 红。拦的是"计划表抄了改名前的路由"——与 §5.3 第 1 件特别标注的
  `/api/health` vs `/health` 同族：路径口径写错时，文档与代码会各说各话而无人判红。

射程边界（写死在这里，别让下一个人重新猜一遍）：
- 只认**状态栏以 `✅` 领头**的声明。`后端 ✅ ／ **UI ✗**` 领头不是 ✅ ⇒ 不算声明（上一批正是把
  那个形状改成双段写法的，本门反过来把它判红就等于否掉那次更正）；`✅（expect 编辑入口⚠️）`
  领头是 ✅ ⇒ 算声明，括号里是附注。
- 只数**首列完整写出** `VERB /api/…` 的行。`·/cancel`、`·/disable`、`·/export` 这类省略前缀的简写
  拼不回唯一路由（`/api/experience` + `/export` 要的是"接在尾巴后"，`/api/graphs/enable` + `/disable`
  要的是"换掉末段"，两种合并规则互相冲突），所以不在射程内——它们由服务端路由表自己管辖。
- **反向那一向（`🔲`／`⚠️` 却读得出调用点）只登记、不判红**。兄弟门的"调用点"包含
  `ui/src/api/client.ts` 那一层服务包装（`request<…>('GET', '/metrics')` 这种），它证明的是
  "有这个函数"，不是"面板用了它"——计划表第 22 行"metrics / experience / telemetry 全无 UI"标的
  正是这个差别。把这一向也判红，等于把服务层与交付面重新压成一个口径，比现状更差。
- 判据 A 能判的是"✅ 领头 + 门读不出认领"，**判不了"这个面板是不是真的把功能做完了"**——
  那需要人读页面。它挡的是过期标记，不是所有谎报。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN_DOC = REPO / "docs" / "plan" / "开发计划_WebUI全功能接入.md"
SIBLING = REPO / "scripts" / "check_ui_api_paths.py"

#: 首列里"动词 + 完整 `/api/…`"才算一条声明；`·/cancel` 这类简写读不出动词+路径的成对形状，天然落榜。
PAIR_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/api/[^\s`|、]*)")

#: 反空洞的最小计数：这份表实测 41 行 / 36 条 ✅ 声明（§二之五十二 的读数）。掉到这一格以下
#: 说明本门数的已经不是这张表（表被改名、列被换顺序、分隔符变了），"干净"就成了空集给的干净。
MIN_ROWS = 20
MIN_CLAIMS = 20


def norm(path: str) -> str:
    """把两侧的路径收到同一个可比口径：去 query/anchor、路径参数名归一、去尾斜杠。

    计划表写 `{name}`/`{id}`，服务端声明写 `{session_id}`/`{entity_id}`——名字不同、形状相同，
    不归一就会把"同一件事"读成"文档引用了不存在的路由"（假红）。
    """
    p = re.sub(r"\{[^}]*\}", "{}", path.split("?")[0].split("#")[0])
    p = p.rstrip("/")
    return p or "/"


def parse_doc(text: str) -> list[dict]:
    """数出文档首列里完整写出的每条路由声明（含状态栏、含是否 ✅ 领头）。"""
    rows: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        status = cells[-1]
        for verb, path in PAIR_RE.findall(cells[0]):
            rows.append({
                "verb": verb.upper(),
                "path": norm(path),
                "raw": path,
                "status": status,
                "claimed": status.startswith("✅"),
                "line": lineno,
            })
    return rows


def load_sibling():
    spec = importlib.util.spec_from_file_location("check_ui_api_paths_for_plan", SIBLING)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"兄弟门读不出：{SIBLING}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def gate_reading() -> dict:
    """现取兄弟门的三个读数：服务端路由表、反向未认领名单、以及"它自己射程够不够"的计数。

    本门不复算认领逻辑（那是兄弟门的 `_claimed`，尾巴认领那套口径只有一份）。
    """
    mod = load_sibling()
    trees = mod._registry_trees()
    sites: list[dict] = []
    unparsed: list[str] = []
    for t in trees:
        if not t["root"].is_dir():
            raise RuntimeError(f"UI 树目录不存在：{t['root']}")
        s, u = mod.collect_ui_sites(t["root"], t["name"])
        sites.extend(s)
        unparsed.extend(u)
    routes, _excluded, mount_errs, _boundary = mod.collect_routes(REPO / "src")
    if mount_errs:
        raise RuntimeError(f"挂载表读不出：{mount_errs}")
    key = lambda r: (r["method"], norm(r["path"]))  # noqa: E731  两侧同口径的唯一处
    return {
        "server": {key(r) for r in routes},
        "uncalled": {key(r) for r in mod._unused_routes(sites, routes)},
        "sites": len(sites),
        "routes": len(routes),
        "unparsed": len(unparsed),
        "trees": len(trees),
    }


def run(text: str | None, reading: dict, *, min_rows: int = MIN_ROWS,
        min_claims: int = MIN_CLAIMS) -> tuple[int, list[str]]:
    """返回 (rc, 逐行输出)。rc：0 干净 / 1 两条判据命中 / 2 射程塌了或计数不像这张表。"""
    if text is None:
        return 2, ["计划表读不出：docs/plan/开发计划_WebUI全功能接入.md 不在盘上——本门没有口径来源"]
    rows = parse_doc(text)
    claims = [r for r in rows if r["claimed"]]
    if not rows:
        return 2, ["文档里数不出任何一条完整写出的 `VERB /api/…`（表格列序或写法变了？）："
                   "本门此刻没有射程，报『没有发现』没有依据"]
    if not claims:
        return 2, [f"文档行 {len(rows)} 条，却一条 `✅` 领头的声明都没有：口径不是这张表，"
                   "或者 ✅ 全被改成了别的写法——两种情况下本门的『干净』都是空集给的干净"]
    if len(rows) < min_rows or len(claims) < min_claims:
        return 2, [f"反空洞计数不过：文档行 {len(rows)}（下限 {min_rows}）/ ✅ 声明 {len(claims)}"
                   f"（下限 {min_claims}）——数出来的已经不是这份表，判据无从谈起"]
    if reading["routes"] == 0 or reading["sites"] == 0:
        return 2, [f"兄弟门自己的射程是 0（UI 调用点 {reading['sites']} 处、服务端路由 "
                   f"{reading['routes']} 条）：它的反向未认领名单此刻没有依据，本门不拿它当依据"]
    if reading["unparsed"]:
        return 2, [f"兄弟门有 {reading['unparsed']} 个调用点解析不出 ⇒ 认领集合不完整，"
                   "此时判『✅ 却没人调』会误伤真接了的面板；先修静态可读性（或就地 exempt），再来对表"]

    findings: list[str] = []
    for r in rows:
        pair = (r["verb"], r["path"])
        if pair not in reading["server"]:
            findings.append(f"文档 L{r['line']}: `{r['verb']} {r['raw']}` 在服务端路由表里读不出"
                            "（判据 B：计划表引用了一条盘上落不到的契约——改名/删路由/口径写错都走这一条）")
        if r["claimed"] and pair in reading["uncalled"]:
            findings.append(f"文档 L{r['line']}: `{r['verb']} {r['raw']}` 状态栏以 ✅ 领头，"
                            "却在 UI↔路由门的反向读数里无人认领（判据 A：文档标已接、三棵第一方 UI 树读不出调用点）")

    if findings:
        head = [f"✗ 计划表口径门发现 {len(findings)} 处不一致（文档行 {len(rows)} 条、"
                f"✅ 声明 {len(claims)} 条、服务端路由 {reading['routes']} 条、"
                f"反向未认领 {len(reading['uncalled'])} 条）："]
        return 1, head + [f"  {f}" for f in findings] + [
            "  修法：按实测读数改**文档**——面板真没接就写 `后端 ✅ ／ UI ✗` 的双段（上一批会话族三行就是这么改的），"
            "路由改名就改文档那一行的路径；**不要**为了让本门变绿去补一个假调用点，也不要往兄弟门的名单里加豁免。"]

    stale = [r for r in rows if not r["claimed"] and (r["verb"], r["path"]) in reading["server"]
             and (r["verb"], r["path"]) not in reading["uncalled"]]
    out = [f"✓ 计划表口径门干净（文档行 {len(rows)} 条、✅ 领头声明 {len(claims)} 条，"
           f"逐条在 UI↔路由门的认领读数里落到了调用点；服务端路由 {reading['routes']} 条、"
           f"反向未认领 {len(reading['uncalled'])} 条、跨 {reading['trees']} 棵树）"]
    if stale:
        names = "、".join(f"{r['verb']} {r['raw']}" for r in stale)
        out.append(f"— 登记（不判红，见模块 docstring 第三条边界）：另有 {len(stale)} 行标 🔲／⚠️ 却在"
                   f"服务层读得出调用点（{names}）——`client.ts` 有函数不等于面板接了，本门不据此改文档")
    return 0, out


LEG_ROWS = [
    "| `GET /api/alpha` | 甲 | 面板 A | ✅ |\n",
    "| `POST /api/beta` | 乙 | 面板 B | ✅（附注⚠️） |\n",
    "| `GET /api/sessions` | 会话列表 | 列表页 | 后端 ✅ ／ **UI ✗** |\n",
    "| `GET /api/gamma` | 共现 | 共现图谱 | 🔲 |\n",
]
LEG_TEXT = "".join(LEG_ROWS)
LEG_SERVER = {("GET", "/api/alpha"), ("POST", "/api/beta"), ("GET", "/api/sessions"),
              ("GET", "/api/gamma")}
LEG_READING = {"server": LEG_SERVER, "uncalled": {("GET", "/api/sessions")},
               "sites": 3, "routes": 4, "unparsed": 0, "trees": 3}


def self_test() -> int:
    """六档：一条**未变异**的对照腿（真盘读数必须真绿）+ 两档注入必红 + 三档射程必红
    （§二之二十六 的教训：变异腿全红不等于检测器活着，对照腿才把"它只是什么都判红"这一档否掉）。"""
    try:
        control = gate_reading()
    except Exception as exc:  # noqa: BLE001  对照腿自己读不出 ⇒ 本腿没有依据，直接报红
        print(f"✗ [self-test] 对照腿读不出兄弟门的锚点：{exc}")
        return 1
    legs = [
        ("对照：真盘读数应当干净", run,
         (PLAN_DOC.read_text(encoding="utf-8"), control,
          {"min_rows": MIN_ROWS, "min_claims": MIN_CLAIMS}), 0, "干净"),
        ("判据 A：✅ 领头却无人认领", run, (LEG_TEXT, {**LEG_READING,
         "uncalled": LEG_READING["uncalled"] | {("GET", "/api/alpha")}},
         {"min_rows": 4, "min_claims": 2}), 1, "判据 A"),
        ("判据 B：文档路由在服务端表里读不出", run, (LEG_TEXT, {**LEG_READING,
         "server": LEG_SERVER - {("POST", "/api/beta")}}, {"min_rows": 4, "min_claims": 2}),
         1, "判据 B"),
        ("已改双段写法的行不算声明（不许把上次更正判成谎报）", run,
         (LEG_ROWS[2], LEG_READING, {"min_rows": 1, "min_claims": 1}), 2, "一条 `✅` 领头"),
        ("认领集合不完整 ⇒ 不拿它判红", run, (LEG_TEXT, {**LEG_READING, "unparsed": 2},
         {"min_rows": 4, "min_claims": 2}), 2, "解析不出"),
        ("计数不像这张表 ⇒ 射程红", run, (LEG_TEXT, LEG_READING, {"min_rows": 5, "min_claims": 2}),
         2, "反空洞"),
    ]
    bad = 0
    for title, fn, (a, b, kw), want_rc, want_needle in legs:
        rc, out = fn(a, b, **kw)
        text = "\n".join(out)
        ok = rc == want_rc and want_needle in text
        print(f"{'✓' if ok else '✗'} [self-test] {title}：rc={rc}（期望 {want_rc}）")
        if not ok:
            print(f"    期望命中 {want_needle!r}，实际输出：{text[:200]}")
            bad += 1
    if bad:
        print(f"✗ 计划表口径门的 self-test 有 {bad} 档不过：检测器自己失效了，"
              "此时它对真盘的『干净』读数不可信")
        return 1
    print("✓ self-test 六档全过（含一条未变异的对照腿）")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    listing = "--list-claims" in argv
    rest = [a for a in argv[1:] if not a.startswith("--")]
    doc = Path(rest[0]).resolve() if rest else PLAN_DOC
    if listing:
        rows = parse_doc(doc.read_text(encoding="utf-8")) if doc.is_file() else []
        for r in rows:
            if r["claimed"]:
                print(f"{r['verb']:<6} {r['path']:<44} L{r['line']}  {r['status'][:36]}")
        print(f"— ✅ 领头声明 {sum(1 for r in rows if r['claimed'])} 条 / 文档行 {len(rows)} 条")
        return 0
    text = doc.read_text(encoding="utf-8") if doc.is_file() else None
    try:
        reading = gate_reading()
    except Exception as exc:  # noqa: BLE001  锚点读不出就是读不出，抛异常会被当成"没跑"
        print(f"计划表口径门读不出兄弟门的锚点：{exc}")
        return 2
    rc, out = run(text, reading)
    for line in out:
        print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))

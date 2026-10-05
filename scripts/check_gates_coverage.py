"""门禁装配覆盖门：`scripts/check_*.py` 必须真被某条链跑到，且远端有的本机也得有。

缘起（§二之四十六）：裁定 20261005 要求的那条 `check_ir_runtime_keys.py` 当时只写在
`.github/workflows/ci.yml` 的 `quality-gates` 作业里（`bash gates.sh` 的**下一步**，前置完全相同），
`gates.sh` 里没有 ⇒ 本机 `bash gates.sh` 绿、推上去 CI 红。这类不对称不会让任何东西变红，
只会让"该红的不红"，与包标记门（`check_pkg_markers.py`，为一次 `.gitignore` 事故立的复发门）同族。

五条判据（全部静态可判）：
- **漏跑**：盘上某个 `check_*.py` 既不在 `gates.sh`、也不在任一工作流里 ⇒ 本门**射程里有它**，
  没有任何一条链跑它 ⇒ 判红。（"写了没接"比"没写"更坏：它看起来是一道门。）
- **远端有、本机没有**：工作流里引用的 `check_*.py` 不在 `gates.sh`，且不在 `CI_ONLY_EXEMPT` ⇒ 判红。
  豁免表要求逐条写理由，理由为空也判红。
- **豁免过期**：`CI_ONLY_EXEMPT` 里登记了盘上不存在、或工作流里根本没引用的脚本 ⇒ 判红。
  豁免名单只减不增；把某条门接进 `gates.sh` 之后必须当场删掉那一格。
- **豁免理由不合格**（§六 那条"静态只能判有没有写字"的收口）：理由必须同时给两个**核对得住**的锚点——
  ① 反引号点名的一个作业（写 job id 或它的 `name:` 都行，两者都从工作流 YAML 现取，不建第二份名单），
  且**那个作业的本体真的引用了这个脚本**（"跑在别的作业里"这种话、以及拉一个不相干的真作业当掩护，都过不了）；
  ② 反引号点名的一个**盘上真实存在**的路径，用来说明前置差落在哪里。
  两个锚点各自可红：只给话不给锚点 ⇒ 红；给了锚点但锚点是编的 ⇒ 红。
- **射程塌了不许报干净**：`gates.sh` 或 `.github/workflows/` 读不到、或 `gates.sh` 里一个
  `check_*.py` 都没数到、或工作流里数不出**任何一个 job** ⇒ `exit 2`（"没有发现"不等于"没有问题"）。

判据⑤能判的是"引用的东西真不真"，**判不了"这句话是不是那条前置差的正确解释"**——那需要人读。
所以它挡住的是空话与假锚点，剩下的语义对不对仍归 §二 的记录与复核。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATES_SH = REPO / "gates.sh"
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPTS = REPO / "scripts"
#: 判据⑤ 的"前置差锚点"按这里为根做存在性核对（测试把这根指向沙箱树）。
PATH_ROOT = REPO

CHECK_RE = re.compile(r"(check_[a-z0-9_]+\.py)")
#: `gates.sh` 的调用行一律写成 `"$REPO/scripts/check_x.py"`；只认这个形状，
#: 注释与结论文案里提到的名字不算"这条链跑过它"。
GATE_CALL_RE = re.compile(r'"\$REPO/scripts/(check_[a-z0-9_]+\.py)"')
#: 工作流里 job id 的形状：`jobs:` 之下、两格缩进的 `foo:`（其后紧跟的 `name:` 是显示名）。
JOB_ID_RE = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
JOB_NAME_RE = re.compile(r"^    name:\s*(\S.*?)\s*$")
#: 理由里用反引号点名的锚点（作业名/路径都走这一条通道，避免"散文里碰巧出现一个词"算数）。
BACKTICK_RE = re.compile(r"`([^`]+)`")
#: "路径形状"的词：`名字.扩展名`，扩展名限常见几种。这样 `af_x.SOMETHING`（模块属性）、
#: `pip install -e ".[dev]"`（命令引用）都不算路径，不会被存在性核对误伤。
PATH_ANCHOR_RE = re.compile(r"^[\w./_-]+\.(?:py|sh|toml|ya?ml|txt|json|md)$")

#: 允许「只在某条独立作业里跑、不进 `gates.sh`」的例外，键为脚本名。
#: 每格必须有理由，且理由要说明**前置为什么不同**——"跑在别的作业里"本身不是理由，
#: 同一个作业里 `bash gates.sh` 的下一步不是理由（那正是本门要拦的形状）。
#: 判据⑤ 会当场核对这里的两个锚点：作业是否真引用了这个脚本、路径是否真在盘上。
CI_ONLY_EXEMPT: dict[str, str] = {
    # 前置差是真的：`check_imports.py:23` 顶层 `import grimp`，而 grimp 只声明在
    # `pyproject.toml` 的 dev extras（`:40`），本机没有装包通道 ⇒ 这条链接不进 `gates.sh`。
    "check_imports.py": (
        "跑在 `architecture`（显示名 `layering-gates`）作业：判据顶层 `import grimp`，"
        "该依赖只声明在 `pyproject.toml` 的 dev extras 里、由 `.github/workflows/ci.yml` 的 "
        "`pip install -e \".[dev]\"` 装上，本机没有装包通道 ⇒ `gates.sh` 这条链接不了它（§二之十一）。"
        "前置差＝作业有装包步骤、本机没有，不是『同作业里的下一步』"
    ),
}


def _refs(text: str) -> set[str]:
    return set(CHECK_RE.findall(text))


def _refs_no_comment(text: str) -> set[str]:
    """只从**真会执行**的行取引用，注释行整行跳过。

    这一步是本门的要害：工作流里一行 `# 见 check_x.py` 会让"CI 在跑这道门"变成一句空话——
    按整份文本取引用，注释里提到的脚本会被算成"远端覆盖"，于是 ① 那条门其实没接也照样绿、
    ② 只在注释里出现的脚本被算进 `unknown` 而把整门变成 `exit 2`。本仓 `ci.yml` 的写法是
    注释独占一行（`run:` 的命令不带行内注释），按行首判断即可覆盖这个形状。
    """
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return _refs("\n".join(kept))


class Unreadable(RuntimeError):
    """射程塌了：读不到 `gates.sh` / 工作流，或数出来的东西不像样。

    这一族一律 `exit 2` 而不是判红也不是报干净——别的门用 2 是"锚点形状变了"，本门用它还包括
    "整条链一条门都没数到"，那种情况下任何『干净』结论都没有依据。
    """


def collect() -> tuple[set[str], set[str], set[str]]:
    """返回（盘上在册的 `check_*.py`，`gates.sh` 引用的，工作流引用的）。"""
    if not GATES_SH.is_file():
        raise Unreadable(f"读不到 {GATES_SH}")
    if not WORKFLOWS.is_dir():
        raise Unreadable(f"读不到 {WORKFLOWS}")
    on_disk = {p.name for p in SCRIPTS.glob("check_*.py")}
    gates_refs = set(GATE_CALL_RE.findall(GATES_SH.read_text(encoding="utf-8")))
    ci_refs: set[str] = set()
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        ci_refs |= _refs_no_comment(wf.read_text(encoding="utf-8"))
    if not on_disk:
        raise Unreadable("scripts/ 下一个 check_*.py 都没数到（改名/挪走会让本门静默全绿）")
    if not gates_refs:
        raise Unreadable(
            "gates.sh 里一条 `\"$REPO/scripts/check_*.py\"` 调用都没数到（装配口径变了，此刻无从判定谁没被跑到）"
        )
    unknown = (gates_refs | ci_refs) - on_disk
    if unknown:
        # 引用了一个盘上不存在的脚本：那条链上这一步其实什么都没判。
        raise Unreadable(
            "被引用却不在 scripts/ 下：" + "、".join(sorted(unknown)) + "（那一行是死步骤，报『干净』没有依据）"
        )
    return on_disk, gates_refs, ci_refs


def collect_jobs() -> dict[str, tuple[str, set[str]]]:
    """工作流里每个 job：`id →（显示名, 它本体真引用的 check_*.py 集合）`。

    判据⑤ 的两个锚点都从这里现取：作业名单与"哪个作业真的跑着哪个脚本"都只来自 YAML 本体，
    本门**不建第二份作业名单**（那是 §二之十九 那族"名单手抄"的形状）。步骤级的 `- name:` 缩进是 6 格
    且带破折号，与 job 显示名的 `^    name:` 形状不同，不会混进来。
    """
    jobs: dict[str, tuple[str, set[str]]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        in_jobs = False
        cur: str | None = None
        for line in wf.read_text(encoding="utf-8").splitlines():
            if line.rstrip() == "jobs:":
                in_jobs, cur = True, None
                continue
            if not in_jobs:
                continue
            if line.strip() and not line.startswith("  "):
                in_jobs = False  # `jobs:` 段结束（回到顶层键）
                continue
            m = JOB_ID_RE.match(line)
            if m:
                cur = m.group(1)
                jobs.setdefault(cur, (cur, set()))
                continue
            nm = JOB_NAME_RE.match(line)
            if nm and cur:
                _old, refs = jobs[cur]
                jobs[cur] = (nm.group(1), refs)
                continue
            if cur and not line.lstrip().startswith("#"):
                _name, refs = jobs[cur]
                jobs[cur] = (_name, refs | _refs(line))
    return jobs


def _reason_problems(name: str, reason: str, jobs: dict[str, tuple[str, set[str]]]) -> list[str]:
    """判据⑤：理由里的锚点各自单独可红。

    反引号里的词按形状分三类：作业名（与 YAML 现取的名字比）、路径形状（`x.y` 且 `y` 在常见扩展名里）、
    其余算散文。路径形状的词**必须在盘上**——这一条今天就有真猎物：旧理由写的
    `.gates-imports-baseline.txt` 盘上从来没有过（`check_imports.py` 不读任何 baseline 文件）。
    为什么不判"所有反引号词都得存在"：那会把 `af_bounded_caches.BOUNDED_CACHES` 这类模块名、
    `pip install -e ".[dev]"` 这类命令引用一律判红，等于逼写理由的人少写信息（run 91 那条
    "名字哨兵罩住散文"的同族教训：约束要落在**能核对的形状**上，不是落在"少说话"上）。
    """
    cited = set(BACKTICK_RE.findall(reason))
    runners = {jid for jid, (jname, refs) in jobs.items()
               if name in refs and (jid in cited or jname in cited)}
    shaped = {t for t in cited if PATH_ANCHOR_RE.match(t)}
    missing = sorted(t for t in shaped if not (PATH_ROOT / t).is_file())
    anchors = sorted(t for t in shaped if CHECK_RE.fullmatch(t) is None and (PATH_ROOT / t).is_file())
    problems: list[str] = []
    if not runners:
        possible = "、".join(sorted(f"{jid}（{jname}）" for jid, (jname, refs) in jobs.items() if name in refs))
        problems.append(
            f"豁免理由是空话：{name} 的理由没点名一个**真的在引用它**的作业"
            f"（可写的作业：{possible or '没有——那这格该整个删掉'}）"
        )
    if not anchors:
        problems.append(
            f"豁免理由缺前置差锚点：{name} 的理由没给出一个盘上真实存在的路径"
            "（反引号包住，用来指明前置差落在哪个文件——作业名之外还得有这一条）"
        )
    for t in missing:
        problems.append(f"豁免理由里的锚点是编的：{name} 的理由引用了 `{t}`，盘上没有这个路径")
    return problems


def check(
    on_disk: set[str],
    gates_refs: set[str],
    ci_refs: set[str],
    jobs: dict[str, tuple[str, set[str]]],
    exempt: dict[str, str] | None = None,
) -> list[str]:
    problems: list[str] = []
    exempt = CI_ONLY_EXEMPT if exempt is None else exempt
    covered = gates_refs | ci_refs
    for name in sorted(on_disk - covered):
        problems.append(f"漏跑：{name} 在盘上，但 `gates.sh` 与任何工作流都不跑它（写了没接＝看起来像一道门）")
    for name in sorted(ci_refs - gates_refs - set(exempt)):
        problems.append(f"远端有、本机没有：{name} 被工作流引用，却不在 `gates.sh` 也不在豁免表")
    for name in sorted(set(exempt) - ci_refs):
        problems.append(f"豁免过期：{name} 登记在 `CI_ONLY_EXEMPT`，但没有任何工作流引用它")
    for name, reason in exempt.items():
        if not reason.strip():
            problems.append(f"豁免没理由：{name} 的 `CI_ONLY_EXEMPT` 理由为空")
            continue
        problems.extend(_reason_problems(name, reason, jobs))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="负控：注入五个假形状，断言五类判红都真能触发后退出 0",
    )
    args = ap.parse_args()

    try:
        on_disk, gates_refs, ci_refs = collect()
    except Unreadable as exc:
        print(f"[gates-coverage] 读不出（exit 2）：{exc}")
        return 2
    jobs = collect_jobs()
    if not jobs:
        print("[gates-coverage] 读不出（exit 2）：工作流里数不出任何一个 job（判据⑤ 此刻没有射程）")
        return 2

    if args.self_test:
        # 五类判红各注入一次，证明检测器本体真能抓到（反空洞自证）。
        legs = {
            "漏跑": check(on_disk | {"check_unwired.py"}, gates_refs, ci_refs, jobs),
            "远端有、本机没有": check(on_disk, gates_refs, ci_refs | {"check_ci_only.py"}, jobs),
            "豁免过期": check(on_disk, gates_refs, ci_refs - set(CI_ONLY_EXEMPT), jobs),
            # 一句真实存在过的空话：它**曾经**让本门照抄成"有理由"。
            "豁免理由不合格": check(on_disk, gates_refs, ci_refs, jobs,
                                   {"check_imports.py": "跑在别的作业里"}),
            # 本仓 `CI_ONLY_EXEMPT` 那格的**历史原文**（含一个盘上不存在的 baseline 文件名），
            # 这一档注进去就是要证明：编出来的锚点今天会被判红。
            "豁免锚点是编的": check(on_disk, gates_refs, ci_refs, jobs, {
                "check_imports.py": (
                    "跑在 `layering-gates` 独立作业：判据是 grimp 的包图 + `.gates-imports-baseline.txt`，"
                    "前置与失败口径都和 `quality-gates` 不同一条链（§二之十一），不并进 `gates.sh`"
                )}),
        }
        for k, v in legs.items():
            if not v:
                print(f"[self-test] FAIL：{k} 这一档没检出任何东西，检测器失效")
                return 1
        print(f"[self-test] OK：五档注入全部被检出（{sum(len(v) for v in legs.values())} 条问题）")
        return 0

    problems = check(on_disk, gates_refs, ci_refs, jobs)
    if problems:
        print("门禁装配覆盖门未通过：")
        for p in problems:
            print(f"  - {p}")
        print(
            f"（在册 {len(on_disk)} 个 / `gates.sh` 跑 {len(on_disk & gates_refs)} 个 / "
            f"工作流跑 {len(on_disk & ci_refs)} 个 / 豁免 {len(CI_ONLY_EXEMPT)} 格 / 作业 {len(jobs)} 条）"
        )
        return 1
    print(
        f"门禁装配覆盖门干净（盘上 `check_*.py` {len(on_disk)} 个，"
        f"`gates.sh` 覆盖 {len(on_disk & gates_refs)} 个，工作流覆盖 {len(on_disk & ci_refs)} 个，"
        f"独立作业豁免 {len(CI_ONLY_EXEMPT)} 格且两个锚点都核对得住——作业真引用了该脚本、路径真在盘上）"
    )
    return 0



if __name__ == "__main__":
    sys.exit(main())

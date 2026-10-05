"""门禁装配覆盖门：`scripts/check_*.py` 必须真被某条链跑到，且远端有的本机也得有。

缘起（§二之四十六）：裁定 20261005 要求的那条 `check_ir_runtime_keys.py` 当时只写在
`.github/workflows/ci.yml` 的 `quality-gates` 作业里（`bash gates.sh` 的**下一步**，前置完全相同），
`gates.sh` 里没有 ⇒ 本机 `bash gates.sh` 绿、推上去 CI 红。这类不对称不会让任何东西变红，
只会让"该红的不红"，与包标记门（`check_pkg_markers.py`，为一次 `.gitignore` 事故立的复发门）同族。

四条判据（全部静态可判）：
- **漏跑**：盘上某个 `check_*.py` 既不在 `gates.sh`、也不在任一工作流里 ⇒ 本门**射程里有它**，
  没有任何一条链跑它 ⇒ 判红。（"写了没接"比"没写"更坏：它看起来是一道门。）
- **远端有、本机没有**：工作流里引用的 `check_*.py` 不在 `gates.sh`，且不在 `CI_ONLY_EXEMPT` ⇒ 判红。
  豁免表要求逐条写理由，理由为空也判红。
- **豁免过期**：`CI_ONLY_EXEMPT` 里登记了盘上不存在、或工作流里根本没引用的脚本 ⇒ 判红。
  豁免名单只减不增；把某条门接进 `gates.sh` 之后必须当场删掉那一格。
- **射程塌了不许报干净**：`gates.sh` 或 `.github/workflows/` 读不到、或 `gates.sh` 里一个
  `check_*.py` 都没数到 ⇒ `exit 2`（"没有发现"不等于"没有问题"）。
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

CHECK_RE = re.compile(r"(check_[a-z0-9_]+\.py)")
#: `gates.sh` 的调用行一律写成 `"$REPO/scripts/check_x.py"`；只认这个形状，
#: 注释与结论文案里提到的名字不算"这条链跑过它"。
GATE_CALL_RE = re.compile(r'"\$REPO/scripts/(check_[a-z0-9_]+\.py)"')

#: 允许「只在某条独立作业里跑、不进 `gates.sh`」的例外，键为脚本名。
#: 每格必须有理由，且理由要说明**前置为什么不同**——"跑在别的作业里"本身不是理由，
#: 同一个作业里 `bash gates.sh` 的下一步不是理由（那正是本门要拦的形状）。
CI_ONLY_EXEMPT: dict[str, str] = {
    "check_imports.py": (
        "跑在 `layering-gates` 独立作业：判据是 grimp 的包图 + `.gates-imports-baseline.txt`，"
        "前置与失败口径都和 `quality-gates` 不同一条链（§二之十一），不并进 `gates.sh`"
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


def check(on_disk: set[str], gates_refs: set[str], ci_refs: set[str]) -> list[str]:
    problems: list[str] = []
    covered = gates_refs | ci_refs
    for name in sorted(on_disk - covered):
        problems.append(f"漏跑：{name} 在盘上，但 `gates.sh` 与任何工作流都不跑它（写了没接＝看起来像一道门）")
    for name in sorted(ci_refs - gates_refs - set(CI_ONLY_EXEMPT)):
        problems.append(f"远端有、本机没有：{name} 被工作流引用，却不在 `gates.sh` 也不在豁免表")
    for name in sorted(set(CI_ONLY_EXEMPT) - ci_refs):
        problems.append(f"豁免过期：{name} 登记在 `CI_ONLY_EXEMPT`，但没有任何工作流引用它")
    for name, reason in CI_ONLY_EXEMPT.items():
        if not reason.strip():
            problems.append(f"豁免没理由：{name} 的 `CI_ONLY_EXEMPT` 理由为空")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="负控：注入三个假形状，断言三类判红都真能触发后退出 0",
    )
    args = ap.parse_args()

    try:
        on_disk, gates_refs, ci_refs = collect()
    except Unreadable as exc:
        print(f"[gates-coverage] 读不出（exit 2）：{exc}")
        return 2

    if args.self_test:
        # 三条判红各注入一次，证明检测器本体真能抓到（反空洞自证）。
        legs = {
            "漏跑": check(on_disk | {"check_unwired.py"}, gates_refs, ci_refs),
            "远端有、本机没有": check(on_disk, gates_refs, ci_refs | {"check_ci_only.py"}),
            "豁免过期": check(on_disk, gates_refs, ci_refs - set(CI_ONLY_EXEMPT)),
        }
        for k, v in legs.items():
            if not v:
                print(f"[self-test] FAIL：{k} 这一档没检出任何东西，检测器失效")
                return 1
        print(f"[self-test] OK：三档注入全部被检出（{sum(len(v) for v in legs.values())} 条问题）")
        return 0

    problems = check(on_disk, gates_refs, ci_refs)
    if problems:
        print("门禁装配覆盖门未通过：")
        for p in problems:
            print(f"  - {p}")
        print(
            f"（在册 {len(on_disk)} 个 / `gates.sh` 跑 {len(on_disk & gates_refs)} 个 / "
            f"工作流跑 {len(on_disk & ci_refs)} 个 / 豁免 {len(CI_ONLY_EXEMPT)} 格）"
        )
        return 1
    print(
        f"门禁装配覆盖门干净（盘上 `check_*.py` {len(on_disk)} 个，"
        f"`gates.sh` 覆盖 {len(on_disk & gates_refs)} 个，工作流覆盖 {len(on_disk & ci_refs)} 个，"
        f"独立作业豁免 {len(CI_ONLY_EXEMPT)} 格且理由齐全）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

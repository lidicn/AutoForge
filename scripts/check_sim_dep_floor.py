#!/usr/bin/env python3
"""包声明里的依赖必须**带版本约束**，且裁定钉下来的那几枚下界逐字节对撞得回真源。

起因（第六轮审计 ARCH-03 第二半 ＋ 裁定 `20261011-AF第六轮与第二期审计攒批十三问-裁定.md` §3 Q4.3
「仿真依赖钉版本：做」）：`pytest-homeassistant-custom-component` 在 `pyproject.toml` 里被抄了**两遍**
（`sim` 与 `dev` 两枚 extra 各一次），两处原本都没有任何版本约束。后果不是理论上的：同一条
`pip install -e ".[dev]"` 在 CI（钉 3.11）与镜像 base（3.14）两面上解析出的**不是同一个版本**——
PyPI 元数据现读该包 578 枚 wheel，`>=3.11` 面最新只到 `0.13.109`，`>=3.14` 面是 `0.13.371`，相隔 262 个
发布（读数存证 `docs/ADM联动执行记录-AF.md:8666`）。而无下界时某次重建还可能一路回溯到更老的不兼容版本。

本门判形状，不替主人选版本：

- **A 每一枚依赖都带约束**：`[project].dependencies` 与**每一枚** `[project.optional-dependencies]`
  里的每一条都必须写成 `名字<specifier>`，裸名（`"foo"`）当场红。这一条没有豁免档——本批现读全部依赖
  都已带约束（读数印在结论里），所以"红"只可能来自未来新加的那一枚裸名；真要留裸名就得先删掉这条规则，
  那是一次显式越权而不是静默漂移。
- **B 在册下界对撞**：`SIM_DEP_FLOORS` 里登记的每一枚包，在它出现的**每一枚 extra** 里的下界必须与
  登记值逐字符相同。这一条咬的正是"同包两枚手抄"：改 `sim` 忘 `dev`、或把某一面偷偷降到 `>=0.13.0`，
  单看 extra 都自洽、合起来是两套环境。登记的包从 extra 里整个消失 ⇒ 同样判红（在册项不许静默蒸发）。
- **C 下界必须有可核对的依据**：每条登记的依据非空、含「裁定」字样、且反引号里至少点一个**盘上真实存在**
  的锚点（写成 `路径:行号` 时那一行也得真在）。登记等于把数字抄进第二个地方，依据是那数字没变成第三次手抄的唯一保证。

⛔ 本门**不判该钉成多少**，也**不加上界**：上界会替交付面选环境，那属 §3 Q4.1 明写"对齐后再谈"的第二半。
射程前提（读不到就 exit 2）：`pyproject.toml` 不在盘上／解析不了／没有 `[project.optional-dependencies]`／
登记的 extra 名字在文件里读不出来——每一种都会让"没有发现"冒充"没有问题"。

纯标准库：`tomllib`（与本仓 `requires-python` 同口径）＋ 逐字符解析依赖串，与 `check_mqtt_runtime_dep.py`
同一形状。
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
BACKTICK_RE = re.compile(r"`([^`]+)`")
# 登记：包名 -> (钉死的最小下界, 必须各出现一次的 extra, 依据)
SIM_DEP_FLOORS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "pytest-homeassistant-custom-component": (
        ">=0.13.109",
        ("sim", "dev"),
        "裁定 `20261011-AF第六轮与第二期审计攒批十三问-裁定.md` §3 Q4.3 裁「仿真依赖钉版本：做」。"
        "取 `0.13.109` 不是随手抄的：它是 `requires-python>=3.11` 那一档最新可取到的那一版，"
        "所以这条下界不抬高任何一面的解析结果，只禁止重建时回溯到更老的版本。PyPI 元数据读数存证 "
        "`docs/ADM联动执行记录-AF.md:8666`（578 枚 wheel 的八档 `requires-python` 对撞）。",
    ),
}


def _split_requirement(text: str) -> tuple[str, str]:
    """把一条依赖串拆成（包名, 版本约束串）。约束串为空即"裸名"。"""
    m = NAME_RE.match(text.strip())
    if not m:
        return "", text.strip()
    name = m.group(0)
    rest = text.strip()[len(name):].strip()
    # 环境标记（`; python_version < "3.12"`）不参与版本约束判定，先切掉。
    rest = rest.partition(";")[0].strip()
    return name, rest


def _floor(spec: str) -> str | None:
    """从约束串里取 `>=` 那一枚的下界原文（含 `>=`），没有 `>=` 子句则返回 None。"""
    for clause in spec.split(","):
        clause = clause.strip()
        if clause.startswith(">="):
            return clause
    return None


def _anchor_exists(root: Path, token: str) -> bool:
    if " " in token or "\n" in token:
        return False
    path_part, _, line_part = token.partition(":")
    if not path_part:
        return False
    candidate = root / path_part.replace("\\", "/")
    if not candidate.is_file():
        return False
    if not line_part:
        return True
    if not line_part.isdigit():
        return False
    return len(candidate.read_text(encoding="utf-8", errors="replace").splitlines()) >= int(line_part)


def _extras(data: dict[str, object]) -> dict[str, list[str]]:
    raw = data["project"].get("optional-dependencies", {})
    return {str(k): [str(x) for x in v] for k, v in dict(raw).items()}


def check(root: Path) -> tuple[list[str], dict[str, object]]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    core = [str(x) for x in data["project"].get("dependencies", [])]
    extras = _extras(data)

    findings: list[str] = []

    # A 每一枚依赖都带约束
    bare = [(where, text) for where, items in [("dependencies", core)] + sorted(extras.items())
            for text in items if not _split_requirement(text)[1]]
    for where, text in bare:
        findings.append(
            f"`pyproject.toml` 的 `{where}` 里有一条裸名依赖 `{text}`（无版本约束）⇒ 每次重建都可能拿到"
            f"另一版，镜像与 CI 解析出的依赖就不是同一套，而两遍都绿"
        )

    # B 在册下界逐面对撞 + C 依据锚点
    readings: dict[str, dict[str, str]] = {}
    for name, (want, required_extras, basis) in SIM_DEP_FLOORS.items():
        per_extra: dict[str, str] = {}
        for extra, items in extras.items():
            for text in items:
                got_name, spec = _split_requirement(text)
                if got_name != name:
                    continue
                floor = _floor(spec)
                per_extra[extra] = floor if floor is not None else f"（无 `>=` 下界：`{spec}`）"
                if floor is None:
                    findings.append(
                        f"`{extra}` 里的 `{name}` 写成 `{text}`，没有 `>=` 下界，而登记要求 `{want}` ⇒ "
                        f"这一面重装时仍能往下走薄到更老的版本"
                    )
                elif floor != want:
                    findings.append(
                        f"`{extra}` 里的 `{name}` 现读下界是 `{floor}`，登记值是 `{want}` ⇒ 同包多枚手抄对不上，"
                        f"两枚 extra 会各自解析出不同的环境"
                    )
        missing = [extra for extra in required_extras if extra not in per_extra]
        if missing:
            findings.append(
                f"在册依赖 `{name}` 从这些 extra 里消失了：{missing}（登记要求它在 {list(required_extras)} "
                f"各出现一次）⇒ 在册项不许静默蒸发，删登记要走显式改动"
            )
        readings[name] = per_extra

        err, hits = None, [t for t in BACKTICK_RE.findall(basis) if _anchor_exists(root, t)]
        if not basis.strip():
            err = "依据为空——登记等于没登记"
        elif "裁定" not in basis:
            err = "依据里没有「裁定」二字（钉死的数字要指得到裁它的文书，否则就是第三次手抄）"
        elif not hits:
            err = "依据里没有一个盘上真实存在的锚点（写成 `路径:行号` 时那一行也要真在）"
        if err:
            findings.append(f"在册依赖 `{name}` 的依据不合格：{err}")

    info = {
        "core": core,
        "extras": {k: len(v) for k, v in sorted(extras.items())},
        "total": len(core) + sum(len(v) for v in extras.values()),
        "floors": {name: (want, readings.get(name, {})) for name, (want, _r, _b) in SIM_DEP_FLOORS.items()},
    }
    return findings, info


def anchor_ok(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return f"读不到 `{pyproject.as_posix()}`（包声明的真源不在盘上）"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 射程判定要的是"读不出"这个事实本身
        return f"`pyproject.toml` 解析失败（{type(exc).__name__}: {exc}）⇒ 读不出依赖清单"
    if "project" not in data:
        return "`pyproject.toml` 里没有 `[project]` 表（依赖清单的归属读不出）"
    if "optional-dependencies" not in data["project"]:
        return "`pyproject.toml` 没有 `[project.optional-dependencies]`——extra 那一族不在了就该同步改本门口径"
    extras = _extras(data)
    for name, (_want, required_extras, _basis) in SIM_DEP_FLOORS.items():
        for extra in required_extras:
            if extra not in extras:
                return (f"登记的 extra `{extra}`（为 `{name}` 而列）在 `[project.optional-dependencies]` 里"
                        f"读不出来——本门要比的那一面不在盘上")
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok(root)
    if err:
        print(f"✗ 依赖下界门读不到锚点：{err}")
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 依赖下界门发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}")
        print("  修法：A 给那条裸名依赖补上版本约束（`名字>=X.Y`）；B 让每一枚 extra 里的在册依赖都等于 "
              "`SIM_DEP_FLOORS` 登记的下界；C 把依据补成「裁定＋一个盘上真在的锚点」。钉成多少不由本门拍板。")
        return 1
    floors = "、".join(
        f"`{name}` 登记 `{want}`，现读 {sorted(per.items())}"
        for name, (want, per) in info["floors"].items()
    )
    print(f"✓ 依赖下界门干净（包声明共 {info['total']} 条依赖——核心 {len(info['core'])} 条、extra 面 "
          f"{info['extras']}——无一条裸名；在册下界逐面同值：{floors}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

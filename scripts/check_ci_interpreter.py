#!/usr/bin/env python3
"""CI 交付链路的解释器口径必须**自洽且漂移显式登记**：手抄钉值一致 / 满足包声明下限 / 与镜像 base
的不一致必须挂着一条理由核对得住的登记。

起因（第六轮审计 ARCH-03，§二之九十七 复测）：`.github/workflows/ci.yml` 的四个 Python 作业把
解释器**手抄了四遍**（`:16`、`:36`、`:57`、`:90`，全是 `"3.11"`），而包声明是
`pyproject.toml:10 requires-python = ">=3.11"`、两份镜像 base 是
`docker/Dockerfile.api:10` / `docker/Dockerfile.test:13` 的 `FROM python:3.14-slim`、README 快速开始
明写「需要 Python 3.14+」。四个口径里有三对关系是**必须成立的**（四枚手抄彼此相等、CI 钉值落在包声明
的允许区间内），一对关系是**当前不成立且归谁对齐不由本门拍板**（CI 3.11 vs 镜像 3.14——它决定 CI 验证
的是哪条环境，属交付/验证口径）。所以本门只做三件事，各自单独可红：

- **A 手抄一致**：`ci.yml` 里所有 `python-version:` 钉值必须彼此相等。改一处忘三处 ⇒ 红。这是本仓
  "名单手抄"那一族（工具名单、IR 版本号）在 CI 装配面上的同款：四枚字面量没有任何东西保证同步。
- **B 满足包声明**：CI 钉值必须被 `pyproject.toml` 的 `requires-python` 允许（现读 `">=3.11"` 允许 3.11）。
  将来把下限抬到 `>=3.12`／`>=3.14`（例如为了和镜像口径对齐）而 CI 的钉值没跟上 ⇒ 红。这一条咬的就是
  ARCH-03 反方向的形状：审计说"缺 CI 解释器与项目自述最低环境一致的校验"，本门把那半边校验补上。
- **C 漂移必须显式登记**：CI 钉值 ≠ 某份镜像 base 时，`INTERPRETER_DRIFT` 里必须有那一份的条目，理由
  非空，且理由里反引号点名的两个锚点核对得住——① 一个**本体带 `python-version:` 钉值**的作业（job id
  或它的 `name:` 显示名，都从 YAML 现取，不建第二份名单；拉一个 Node 作业当掩护过不了），② 一个
  **盘上真实存在**的路径（写成 `path:NN` 时 NN 那行也必须在文件里）。已经对齐了却还挂着条目 ⇒ 红
  （豁免过期）。本门**不判"谁该改成谁"**——那是要裁的事；它判的是"这条差有没有人认领过"。

射程前提（锚点，读不到就 exit 2，不许静默全绿）：`ci.yml` 读不出任何 job、读不出任何
`python-version:` 钉值、`pyproject.toml` 没有 `requires-python`、其写法本门的比较器认不出、或两份
Dockerfile 读不出 `FROM python:` base——每一种都让"没有发现"冒充"没有问题"。

纯标准库：`tomllib`（3.11+，与本仓 `requires-python` 同口径）+ 逐行文本匹配，与
`check_mqtt_runtime_dep.py` 同一形状。
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
NAME_RE = re.compile(r"^    name:\s*(.+?)\s*$")
PIN_RE = re.compile(r"^[ ]+python-version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)[\"']?\s*$")
FROM_RE = re.compile(r"^FROM\s+python:([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)
SPEC_RE = re.compile(r"^\s*(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9.]*)\s*$")
BACKTICK_RE = re.compile(r"`([^`]+)`")
# 只从 `jobs:` 段里数作业：`on:` 那一族的 `  push:`／`  pull_request:` 同样是缩进两格加冒号，
# 整份文件按缩进取会把触发分支数成作业（现仓读数是 6 个作业却被数成 8，那 8 会印在结论里当读数用）。
JOBS_SECTION_RE = re.compile(r"^jobs:\s*$")

IMAGES = {
    "docker/Dockerfile.api": "交付面",
    "docker/Dockerfile.test": "CI 面（测试镜像）",
}

# CI 钉值与镜像 base 的已知差（本批现读：CI 四枚 "3.11" vs 两份 base 3.14）。
# 每条理由必须给两个核对得住的锚点：一个带钉值的作业名 + 一个盘上真在的路径。
INTERPRETER_DRIFT: dict[str, str] = {
    "docker/Dockerfile.api": (
        "`test`（显示名 `pytest`）钉 3.11，交付镜像 base 是 3.14（`docker/Dockerfile.api:10`）。"
        "CI 那条链跑的是内置 FakeHA：`pyproject.toml:78` 的 `addopts = \"-p no:homeassistant\"` 默认不加载 "
        "pytest-homeassistant 插件，`tests/conftest.py` 因此在 CI 上把 vhass 那族用例整批 skip，而镜像面走的是 "
        "`docker/Dockerfile.test:37` 显式 `-p` 加载插件的另一条路。两侧跑的不是同一条口径，"
        "「谁向谁对齐」属交付/验证口径（第六轮审计 ARCH-03 已列入待裁），裁定之前本门不替主人拍板，"
        "只要求这条差**有人认领**；裁完就把这一格改成裁定后的口径或整格删掉。"
    ),
    "docker/Dockerfile.test": (
        "`test`（显示名 `pytest`）钉 3.11，测试镜像 base 是 3.14（`docker/Dockerfile.test:13`）。"
        "同一个 `pip install -e \".[dev]\"` 在两个面上解析出的仿真依赖不是同一个版本：包声明未钉上界，"
        "3.11 侧最新只能取到受 `requires-python` 允许的较老发布，3.14 侧取最新——而真 vhass 只在镜像面被启用"
        "（`docker/Dockerfile.test:37`）。钉版本与「是否在 CI 增设一条真跑 vhass 的作业」同属交付口径、"
        "已在 ARCH-03 待裁清单里；裁之前这一格先挂着，裁完按裁定回填或删格。"
    ),
}


def _vtuple(text: str) -> tuple[int, int, int]:
    parts = [int(x) for x in text.split(".")][:3]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def _satisfied(pin: str, spec: str) -> bool | None:
    """pin 是否落在 requires-python 的允许区间内；认不出的子句返回 None（本门无从判定）。"""
    version = _vtuple(pin)
    for clause in spec.split(","):
        m = SPEC_RE.match(clause)
        if not m:
            return None
        op, raw = m.group(1), m.group(2)
        bound = _vtuple(raw)
        if op == ">=" and not version >= bound:
            return False
        if op == ">" and not version > bound:
            return False
        if op == "<=" and not version <= bound:
            return False
        if op == "<" and not version < bound:
            return False
        if op == "==" and version != bound:
            return False
        if op == "!=" and version == bound:
            return False
        if op == "~=":
            # ~=X.Y 意为 >=X.Y 且主版本不变；~=X.Y.Z 再加一条次版本不变。
            if version < bound:
                return False
            if len(raw.split(".")) >= 2 and version[0] != bound[0]:
                return False
            if len(raw.split(".")) >= 3 and version[:2] != bound[:2]:
                return False
    return True


def _read_jobs(workflow: Path) -> tuple[list[dict[str, object]], list[tuple[int, str]]]:
    """按缩进取 job（id / 显示名 / 它自己的钉值），且不引入 yaml 依赖。

    扫描范围收在 `jobs:` 段内：段外的两格缩进 `key:` 是触发器（`on:` 下的 `push:`／`pull_request:`），
    把它们数成作业会让结论里那个"几个作业"的读数撒谎。
    """
    jobs: list[dict[str, object]] = []
    pins: list[tuple[int, str]] = []
    current: dict[str, object] | None = None
    in_jobs = False
    for lineno, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        if JOBS_SECTION_RE.match(line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        m = JOB_RE.match(line)
        if m:
            current = {"id": m.group(1), "name": m.group(1), "pins": []}
            jobs.append(current)
            continue
        n = NAME_RE.match(line)
        if n and current is not None:
            current["name"] = n.group(1)
            continue
        p = PIN_RE.match(line)
        if p:
            pins.append((lineno, p.group(1)))
            if current is not None:
                cast: list[str] = current["pins"]  # type: ignore[assignment]
                cast.append(p.group(1))
    return jobs, pins


def _path_anchor_ok(root: Path, token: str) -> bool:
    """锚点要么是盘上真在的路径，要么 `路径:行号` 且那一行真存在。"""
    if " " in token or "\n" in token:
        return False
    path_part, _, line_part = token.partition(":")
    if not path_part:
        return False
    candidate = root / path_part.replace("\\", "/")
    if not candidate.exists():
        return False
    if not line_part:
        return True
    if not line_part.isdigit():
        return False
    if candidate.is_dir():
        return False
    return len(candidate.read_text(encoding="utf-8", errors="replace").splitlines()) >= int(line_part)


def _anchors_ok(root: Path, reason: str, jobs: list[dict[str, object]]) -> tuple[str | None, list[str], list[str]]:
    tokens = BACKTICK_RE.findall(reason)
    pinned = {str(job["id"]) for job in jobs if job["pins"]} | {str(job["name"]) for job in jobs if job["pins"]}
    job_hits = [t for t in tokens if t in pinned]
    path_hits = [t for t in tokens if _path_anchor_ok(root, t)]
    if not reason.strip():
        return "理由为空——登记等于没登记", job_hits, path_hits
    if not job_hits:
        return (
            "理由里没有点到一个**本体带 `python-version:` 钉值**的作业（反引号里的作业名要在 ci.yml 现取，"
            "且那个作业自己得有钉值）",
            job_hits,
            path_hits,
        )
    if not path_hits:
        return "理由里没有一个盘上真实存在的路径锚点（写成 `路径:行号` 时那一行也要真在）", job_hits, path_hits
    return None, job_hits, path_hits


def check(root: Path) -> tuple[list[str], dict[str, object]]:
    workflow = root / ".github" / "workflows" / "ci.yml"
    jobs, pins = _read_jobs(workflow)
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    spec = str(data["project"].get("requires-python", ""))

    findings: list[str] = []
    values = sorted({v for _, v in pins})
    pin = values[0] if len(values) == 1 else None

    # A 手抄一致
    if len(values) > 1:
        where = "、".join(f":{lineno} 钉 `{v}`" for lineno, v in pins)
        findings.append(
            f"ci.yml 的 {len(pins)} 枚 `python-version:` 钉值不一致（现读 {values}）：{where}。"
            f"四枚手抄没有任何东西保证同步——改一个作业的解释器，另外三个作业继续跑旧口径，"
            f"而 CI 照绿（每个作业各报各的绿）"
        )

    # B 满足包声明
    if pin is not None:
        ok = _satisfied(pin, spec)
        if ok is None:
            findings.append(
                f"`pyproject.toml` 的 `requires-python = \"{spec}\"` 里有本门比较器认不出的子句"
                f"（只支持 `>= > <= < == != ~=` 加纯数字版本）⇒ 无从判定 CI 钉值 `{pin}` 是否被允许"
            )
        elif ok is False:
            findings.append(
                f"CI 钉值 `{pin}` 不满足包声明 `requires-python = \"{spec}\"` ⇒ CI 装的依赖、算出的版本解析"
                f"都与包声明的环境不是同一口径，而 CI 绿会被读成\"交付环境验过了\""
            )

    # C 漂移显式登记
    bases: dict[str, str] = {}
    anchors: dict[str, str] = {}
    for rel, label in IMAGES.items():
        path = root / rel
        base = None
        for line in path.read_text(encoding="utf-8").splitlines():
            m = FROM_RE.match(line)
            if m:
                base = m.group(1)
                break
        bases[rel] = str(base)
        if pin is None:
            continue
        registered = rel in INTERPRETER_DRIFT
        if base != pin and not registered:
            findings.append(
                f"{label} `{rel}` 的 base 是 `{base}`，与 CI 钉值 `{pin}` 不一致，而 `INTERPRETER_DRIFT` "
                f"里没有这一格 ⇒ 这条差没人认领：下一次对齐口径时被静默改掉，CI 验证的是哪个环境就没人知道了"
            )
        if base == pin and registered:
            findings.append(
                f"`INTERPRETER_DRIFT` 里 `{rel}` 那一格已过期：base `{base}` 与 CI 钉值 `{pin}` 现在一致，"
                f"豁免名单只减不增，当场删掉那一格"
            )
        if base != pin and registered:
            err, job_hits, path_hits = _anchors_ok(root, INTERPRETER_DRIFT[rel], jobs)
            if err:
                findings.append(f"`INTERPRETER_DRIFT` 里 `{rel}` 的理由不合格：{err}")
            else:
                anchors[rel] = f"作业 {job_hits} / 路径 {path_hits}"

    info = {
        "pins": pins,
        "values": values,
        "requires_python": spec,
        "images": bases,
        "anchors": anchors,
        "jobs": len(jobs),
    }
    return findings, info


def anchor_ok(root: Path) -> str | None:
    workflow = root / ".github" / "workflows" / "ci.yml"
    pyproject = root / "pyproject.toml"
    if not workflow.is_file():
        return f"读不到 `{workflow.as_posix()}`（CI 装配面的真源不在盘上）"
    jobs, pins = _read_jobs(workflow)
    if not jobs:
        return f"`{workflow.name}` 里按缩进数不出**任何一个 job**——钉值归属读不出，本门此刻没有射程"
    if not pins:
        return (f"`{workflow.name}` 里数不出任何 `python-version:` 钉值——要么作业不再显式钉解释器"
                f"（改用矩阵或容器镜像就该同步改本门口径），要么本门的锚点已经对不上 YAML")
    if not pyproject.is_file():
        return f"读不到 `{pyproject.as_posix()}`（包声明下限的真源不在盘上）"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 射程判定要的是"读不出"这个事实本身
        return f"`pyproject.toml` 解析失败（{type(exc).__name__}: {exc}）⇒ 读不出 `requires-python`"
    spec = str(data["project"].get("requires-python", ""))
    if not spec:
        return "`pyproject.toml` 的 `[project]` 里没有 `requires-python`（判据 B 的口径来源空了）"
    for rel in IMAGES:
        path = root / rel
        if not path.is_file():
            return f"读不到 {IMAGES[rel]} 的 `{path.as_posix()}`（判据 C 要比的 base 不在盘上）"
        if not any(FROM_RE.match(line) for line in path.read_text(encoding="utf-8").splitlines()):
            return f"`{path.name}` 里读不出 `FROM python:X.Y`——base 换了形态（别的基础镜像、或用变量），本门无从判定"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok(root)
    if err:
        print(f"✗ CI 解释器口径门读不到锚点：{err}")
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ CI 解释器口径门发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}")
        print("  修法：A 把 ci.yml 各作业的 `python-version:` 钉成同一个值；B 让 CI 钉值落在 pyproject "
              "`requires-python` 允许区间内；C 认领那条差——在 `INTERPRETER_DRIFT` 里给那一份镜像补一格，"
              "理由要同时点到一个带钉值的作业和一个盘上真在的路径。对齐成哪一个口径不由本门拍板。")
        return 1
    detail = "、".join(
        f"{rel} base `{info['images'][rel]}`" for rel in sorted(IMAGES)
    )
    print(f"✓ CI 解释器口径门干净（ci.yml {info['jobs']} 个作业、{len(info['pins'])} 枚钉值全为 "
          f"`{info['values'][0]}` 且被 `requires-python = \"{info['requires_python']}\"` 允许；镜像侧 "
          f"{detail}，其中与钉值不一致的 {len(info['anchors'])} 格已逐条核过登记理由的两个锚点）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

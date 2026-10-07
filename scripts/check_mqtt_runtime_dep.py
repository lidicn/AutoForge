#!/usr/bin/env python3
"""联动桥的运行时依赖必须**三面一致**：声明处（pyproject）/ 交付面（Dockerfile.api）/
CI 面（Dockerfile.test + workflows/ci.yml）。

起因（§二之二十六，本批盘出）：`af_mqtt_bridge` 要 paho 才能连 broker，但 paho 在整个依赖链里
**一处声明都没有**——homesdk 把它放在自家 `[mqtt]` extra（"装它是对调用方的要求"），而两个镜像装的
都是**裸 wheel**（`pip install homesdk-0.3.2-py3-none-any.whl`），AF 自己的 `.[api,ha]` / `.[dev]` 也不含它。
开发机却一切正常——因为那份解释器里手动装过 paho。后果两种，且都不是测试能拦的：
- 窗内把 `AUTOFORGE_MQTT=1` 打开 ⇒ `get_client()` 抛 `MqttUnavailable` ⇒ serve **拒绝启动**（fail-loud，
  但发生在停机窗里）；
- 不开 ⇒ 桥永远不上线 ⇒ 计划 第 1/2 步的验收（`adm/autoforge/status=online`、抓到一条 fired）
  在现烤镜像下**原理上不可能达成**，而两千多条测试全绿（它们用 duck-typed client，不碰真库）。

这是 §二之二十二 那族"测试绿在一条生产不走的路径上"的**依赖版**：判据不能靠"记得装"，要能静态判红。
三条规则（各自单独可红）：
- **A 声明在**：paho 在 `pyproject.toml` 里可见——base `dependencies` 或某个 `[project.optional-dependencies]`。
- **B 交付面装到**：`docker/Dockerfile.api` 里 `pip install -e ".[...]"` 的 extras 集合必须包含 A 里那个
  声明 paho 的 extra（paho 在 base 里则自动满足）。
- **C CI 面装到**：`docker/Dockerfile.test` 与 `.github/workflows/ci.yml` 同 B。开发机有、CI 没有 ⇒
  同一条用例在两种机器上给出不同结论（本批自己就踩了一次：一条断言在装了 paho 的本机绿、
  在没装它的机器上会红）。工作流按所有 `-e ".[…]"` 行的**并集**判——`gates` 那一 job 刻意只装
  base（门禁脚本是纯标准库），它不需要 paho，不该因此把整条 CI 面判红。

射程前提（锚点，读不到就 exit 2，不许静默全绿）：`src/autoforge/af_mqtt_bridge.py` 在盘上且确实
`from homesdk import mqtt`——桥哪天被删，本门的射程同时消失，那是要**当场决定**的事，不能让门变绿。

纯标准库：`tomllib`（3.11+，与本仓 `requires-python>=3.11` 同口径）+ 逐行文本匹配。
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAHO = re.compile(r"^paho[-_]mqtt\b", re.IGNORECASE)
_EXTRAS = re.compile(r"pip\s+install[^\n]*?-e\s+\"?\.\[([A-Za-z0-9_,\-\s]+)\]\"?")
_BRIDGE_IMPORT = re.compile(r"^\s*from\s+homesdk\s+import\s+mqtt\b", re.MULTILINE)


def _paths(root: Path) -> tuple[Path, Path, dict[str, Path]]:
    pyproject = root / "pyproject.toml"
    bridge = root / "src" / "autoforge" / "af_mqtt_bridge.py"
    images = {
        "交付面": root / "docker" / "Dockerfile.api",
        "CI 面（测试镜像）": root / "docker" / "Dockerfile.test",
        "CI 面（工作流）": root / ".github" / "workflows" / "ci.yml",
    }
    return pyproject, bridge, images


def _declared(data: dict) -> list[str]:
    """声明 paho 的位置：base 依赖记作 `base`，其余按 extra 名。"""
    where: list[str] = []
    if any(PAHO.match(str(req)) for req in data["project"].get("dependencies", [])):
        where.append("base")
    optional = data["project"].get("optional-dependencies", {}) or {}
    for extra, reqs in optional.items():
        if any(PAHO.match(str(req)) for req in reqs):
            where.append(str(extra))
    return where


def _installed_extras(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _EXTRAS.search(line)
        if m:
            out.extend(tok.strip() for tok in m.group(1).split(",") if tok.strip())
    return sorted(set(out))


def check(root: Path) -> tuple[list[str], dict[str, object]]:
    pyproject, _bridge, images = _paths(root)
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    # 排序后再印/比：TOML 里 extra 的书写顺序会变，读数不该跟着变（否则同一份依赖表在两次提交里
    # 印出两个顺序，对账时像是"声明位置换了"）。
    declared = sorted(_declared(data))
    findings: list[str] = []
    per_image: dict[str, list[str]] = {}

    if not declared:
        findings.append(
            "pyproject.toml：paho-mqtt 既不在 base `dependencies`，也不在任何 `[project.optional-"
            "dependencies]` 里——桥要连 broker 就得有它，而 homesdk 的裸 wheel 不带它"
        )
    for label, path in images.items():
        installed = _installed_extras(path)
        per_image[label] = installed
        if not installed:
            findings.append(f"{label}：`{path.name}` 里找不到 `pip install -e \".[extras]\"` 安装行"
                            f"⇒ 本门无从判定它装了什么（不许当成没装就放行）")
            continue
        if declared and "base" not in declared and not set(declared) & set(installed):
            findings.append(
                f"{label}：`{path.name}` 装的是 extras {installed}，而声明 paho 的 extra 是 {declared} "
                f"⇒ 镜像里没有 paho，`AUTOFORGE_MQTT=1` 时 serve 抛 `MqttUnavailable` 拒绝启动"
                f"（开发机装了 paho，所以只有本机一切正常）"
            )
    return findings, {"declared": declared, "per_image": per_image}


def anchor_ok(root: Path) -> str | None:
    pyproject, bridge, images = _paths(root)
    if not pyproject.is_file():
        return f"读不到 `{pyproject.as_posix()}`（依赖声明的真源不在盘上）"
    if not bridge.is_file():
        return (f"读不到 `{bridge.as_posix()}`——本门判的是它的运行时依赖，桥被删/改名时"
                f"本门必须当场决定去留，不能静默全绿")
    if not _BRIDGE_IMPORT.search(bridge.read_text(encoding="utf-8")):
        return (f"`{bridge.name}` 里没有 `from homesdk import mqtt`——射程前提变了"
                f"（改走别的机制层入口就同步改本门口径）")
    for label, path in images.items():
        if not path.is_file():
            return f"读不到 {label}的 `{path.as_posix()}`"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok(root)
    if err:
        print(f"✗ 联动桥依赖门禁读不到锚点：{err}")
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 联动桥依赖门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  {f}")
        print("  修法：paho-mqtt 声明在 pyproject（base 或某 extra），且交付面与 CI 面的 "
              "`-e \".[…]\"` 都装上那个 extra。只改开发机不算修——本门判的是交付面与 CI 面。")
        return 1
    faces = "，".join(f"{label} 装 extras {extras}" for label, extras in info["per_image"].items())
    print(f"✓ 联动桥依赖门禁干净（paho 声明于 {info['declared']}；{faces}，三个面逐一核过）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

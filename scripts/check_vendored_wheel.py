#!/usr/bin/env python3
"""仓内随附的私有 wheel 要**四面同读一枚**：vendor 目录只许有一枚、所有安装引用面指向同一文件名、
字节等于权威登记值、指路文档在册且不许抄第二份摘要。

起因（第六轮审计 ARCH-04，执行记录 §二之九十八 复测）：`docker/homesdk/homesdk-0.3.2-py3-none-any.whl`
随仓提交（`.gitignore` 为它开了白名单 `!docker/homesdk/*.whl`），报告说"缺校验和清单、缺来源与可再生性
说明、缺装不上怎么报错的统一入口"。复测之后**前两格已被既有裁定接住**（`decisions/20261007-MA五件与AF一件-
裁定.md` §六 Q1 裁 A＝仓内随附＋文件名钉死＋权威 sha；字节真值钉在 `tests/unit/test_mqtt_compose_env.py`
的 `AUTHORITATIVE_WHEEL_SHA256`，库侧 `dist/VERSIONS.txt` 0.3.2 段是登记处）。但复测挖出一条**没人对账的缝**：

- 那条字节判据取的 wheel 路径来自 **`Dockerfile.api` 的 COPY 行**（同一文件 `:31-38`）。于是
  `.github/workflows/ci.yml` 那三条 `pip install …whl`、`docker/Dockerfile.test` 的 COPY 行、compose 的
  注释提及，**都不在这条链上**：把 CI 那一面指到另一枚同名不同字节、或指到目录里另一枚旧 wheel，
  测试算的仍是 Dockerfile.api 那一枚 ⇒ **CI 装的与镜像装/测试验的不是同一枚，而 CI 照绿**。
  这与刚收的 ARCH-03 同族：手抄的引用面各报各的绿。

所以本门判四件事，各自单独可红：

- **A 目录单枚**：`docker/homesdk/` 下只许存在**一枚** `.whl`。多一枚＝"CI 挑旧的那枚、镜像挑新的那枚"
  这种分裂的物理前提。
- **B 引用面同名**：所有安装引用面（`ci.yml` 的 `pip install`、`Dockerfile.api`／`Dockerfile.test` 的
  `COPY` 与 `pip install`）里出现的 wheel 文件名必须**全等于盘上那一枚**。真源从盘上现取，不建第二份名单。
- **C 字节等于权威值**：盘上那枚的实算 sha256 必须等于 `tests/unit/test_mqtt_compose_env.py` 里
  `AUTHORITATIVE_WHEEL_SHA256` 那一行的值。本门**自己不抄那份摘要**——它去读那一行；那一行读不出＝射程断
  （exit 2）。这一腿与 pytest 里那条判据**刻意重复**：门禁链是本机 push 前跑的那一条，不该只靠测试作业。
- **D 指路文档在册且不复制**：`docker/homesdk/README.md` 必须存在、必须指得到两处真源
  （`tests/unit/test_mqtt_compose_env.py` 与 `VERSIONS.txt`）、**里面不许出现 64 位十六进制字面量**
  （钉一份摘要就多一份会过期的副本）、不许出现与盘上不同名的 wheel。

射程前提（读不到就 exit 2，不许"没有发现"冒充"没有问题"）：`docker/homesdk/` 不在盘上、目录里一枚 wheel
都没有、`ci.yml`／`Dockerfile.api` 读不到任何 wheel 引用（引用面形状变了，本门无从对账）、权威摘要行读不出。

纯标准库：`hashlib` ＋ `pathlib` ＋ 逐行文本匹配，与 `check_mqtt_runtime_dep.py`／
`check_ci_interpreter.py` 同一形状。
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

VENDOR_DIR = "docker/homesdk"
README_REL = f"{VENDOR_DIR}/README.md"
CONST_FILE = "tests/unit/test_mqtt_compose_env.py"

#: 引用面名单：这些文件里的 wheel 文件名要逐一对撞盘上真身。
#: `docker-compose.api.yml` 只是注释提及，但注释同样是"换 wheel 的人会不会跟着改"的现场，所以一并扫；
#: 它不在盘上时不参与判定（交付面 compose 由部署机持有，仓内那份是草稿形态）。
INSTALL_FACES = (
    (".github/workflows/ci.yml", "CI 面"),
    ("docker/Dockerfile.api", "交付面"),
    ("docker/Dockerfile.test", "CI 面（测试镜像）"),
    ("docker/docker-compose.api.yml", "部署编排（注释提及）"),
)
#: 这三份是硬射程：读不到任何引用就说明引用面形状变了，而不是"没问题"。
REQUIRED_FACES = (".github/workflows/ci.yml", "docker/Dockerfile.api", "docker/Dockerfile.test")

WHEEL_NAME_RE = re.compile(r"homesdk-[0-9][0-9a-zA-Z.+-]*-py3-none-any\.whl")
SHA_CONST_RE = re.compile(r'^AUTHORITATIVE_WHEEL_SHA256\s*=\s*["\']([0-9a-fA-F]{64})["\']', re.MULTILINE)
HEX64_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")

#: README 必须指得到的两处真源（缺一＝指路断了，下一个人只能靠回忆）。
README_ANCHORS = (CONST_FILE, "VERSIONS.txt")


def _read(root: Path, rel: str) -> str | None:
    p = root / rel
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def check(root: Path) -> tuple[list[str], dict]:
    """返回 (红项列表, 现读信息)。红项非空即 exit 1；射程断由 anchor_ok() 先拦。"""
    findings: list[str] = []
    info: dict = {"wheels": [], "refs": {}, "faces_scanned": [], "sha": None, "authoritative": None}

    vendor = root / VENDOR_DIR
    wheels = sorted(p.name for p in vendor.glob("*.whl")) if vendor.is_dir() else []
    info["wheels"] = wheels

    # A —— 目录单枚
    if len(wheels) > 1:
        findings.append(
            f"A：`{VENDOR_DIR}/` 里有 {len(wheels)} 枚 wheel（{wheels}）——多一枚就是"
            "『CI 装那一枚、镜像装这一枚』的物理前提，而两条链会各自报绿"
        )

    authoritative = None
    const_src = _read(root, CONST_FILE)
    if const_src:
        m = SHA_CONST_RE.search(const_src)
        if m:
            authoritative = m.group(1).lower()
    info["authoritative"] = authoritative

    # B —— 引用面同名（真源＝盘上那一枚的名字；目录空时无从比对，射程已先拦）
    disk = wheels[0] if wheels else None
    for rel, label in INSTALL_FACES:
        text = _read(root, rel)
        if text is None:
            if rel in REQUIRED_FACES:
                findings.append(f"B：引用面 `{rel}`（{label}）不在盘上——本门无法对账它装了哪一枚")
            continue
        names = WHEEL_NAME_RE.findall(text)
        info["faces_scanned"].append(rel)
        info["refs"][rel] = sorted(set(names))
        if disk is not None:
            for name in sorted(set(names) - {disk}):
                findings.append(
                    f"B：`{rel}`（{label}）引用了 `{name}`，而 `{VENDOR_DIR}/` 盘上那枚是 "
                    f"`{disk}`——交付面与 CI 面从此不是同一枚"
                )

    # C —— 字节等于权威值（值从判据文件现读，本门不抄第二份摘要）
    if disk and authoritative:
        digest = hashlib.sha256((vendor / disk).read_bytes()).hexdigest()
        info["sha"] = digest
        if digest != authoritative:
            findings.append(
                f"C：盘上 `{VENDOR_DIR}/{disk}` 的实算 sha256 是 `{digest[:8]}…`，"
                f"而权威登记值是 `{authoritative[:8]}…`——同名不同字节，"
                "「写着 0.3.x」与「跑的真是那一枚」已经脱钩"
            )

    # D —— 指路文档在册且不复制
    readme = _read(root, README_REL)
    if readme is None:
        findings.append(
            f"D：`{README_REL}` 不在盘上——换 wheel 的人读不到来源、构建流程与两处真源在哪，"
            "只能靠回忆（这正是 ARCH-04 剩下的那半格缺陷）"
        )
    else:
        for anchor in README_ANCHORS:
            if anchor not in readme:
                findings.append(f"D：`{README_REL}` 没指到真源 `{anchor}`——指路断了")
        if HEX64_RE.search(readme):
            findings.append(
                f"D：`{README_REL}` 里钉了一份 64 位十六进制摘要——字节真源已有两处"
                f"（`{CONST_FILE}` 的常量与库侧 `dist/VERSIONS.txt`），第三份就是会过期的副本"
            )
        if disk is not None:
            for name in sorted(set(WHEEL_NAME_RE.findall(readme)) - {disk}):
                findings.append(f"D：`{README_REL}` 提到 `{name}`，与盘上那枚 `{disk}` 不同名")

    return findings, info


def anchor_ok(root: Path) -> str | None:
    """射程前提：读不出真身/读不出权威摘要行/引用面形状变了 ⇒ 返回原因（exit 2），None＝射程成立。"""
    vendor = root / VENDOR_DIR
    if not vendor.is_dir():
        return f"`{VENDOR_DIR}/` 不在盘上（wheel 的存放目录本身消失了）"
    wheels = sorted(p.name for p in vendor.glob("*.whl"))
    if not wheels:
        return f"`{VENDOR_DIR}/` 里一枚 `.whl` 都没有——没有真身可对账"

    const_src = _read(root, CONST_FILE)
    if const_src is None:
        return f"权威摘要的所在文件 `{CONST_FILE}` 读不到"
    if not SHA_CONST_RE.search(const_src):
        return f"`{CONST_FILE}` 里读不出 `AUTHORITATIVE_WHEEL_SHA256 = \"<64 hex>\"` 那一行"

    for rel in REQUIRED_FACES:
        text = _read(root, rel)
        if text is None:
            return f"引用面 `{rel}` 不在盘上"
        if not WHEEL_NAME_RE.findall(text):
            return f"引用面 `{rel}` 里读不到任何 `homesdk-*-py3-none-any.whl` 引用（安装面形状变了，本门无从对账）"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else REPO
    blocked = anchor_ok(root)
    if blocked:
        print(f"✗ 随附 wheel 门禁读不出射程：{blocked}")
        print(
            "  修法：射程断不是『没问题』。确认 "
            f"`{VENDOR_DIR}/`、`{CONST_FILE}` 与三份引用面（`ci.yml`／`Dockerfile.api`／`Dockerfile.test`）"
            "都还在原处、原形状；若这些面确实被搬走或改名，本门要跟着改口径，而不是让它沉默通过。"
        )
        return 2
    findings, info = check(root)
    if findings:
        print(f"✗ 随附 wheel 门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  · {f}")
        print(
            "  修法：A 让 `docker/homesdk/` 只留一枚；B 把引用面（CI 三条 `pip install`、两份 Dockerfile 的 "
            "`COPY`＋`pip install`、compose 注释）全改成盘上那枚的文件名；C 换 wheel 要走 DCD——"
            f"`{CONST_FILE}` 里那行注释写明『改这个值只有一条路：新的 DCD 裁定』；"
            f"D 补 `{README_REL}` 的来源／构建／装法，摘要只指路不复制。"
        )
        return 1
    print(
        f"✓ 随附 wheel 门禁干净（`{VENDOR_DIR}/` 单枚 "
        f"`{info['wheels'][0]}`；{len(info['faces_scanned'])} 个引用面全部同名；"
        f"实算 sha256 `{str(info['sha'])[:8]}…` 与 `{CONST_FILE}` 里的权威登记一致；"
        f"`{README_REL}` 在册、两处真源都指得到、未复制摘要）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

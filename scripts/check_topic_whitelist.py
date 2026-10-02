#!/usr/bin/env python3
"""主题白名单门禁：代码里出现的 MQTT topic 必须已在 ADM 主题契约表登记。

依据：`E:/NAS/homesdk/doc/ADM联动主题注册表与消息契约.md` §五
「全部 | 主题白名单断言 | 代码里出现的 topic 必须在本表登记（未登记的判红）」，
以及 §六 变更纪律 1「加主题 = 改本表（先改表、再改码，broker ACL 按表执行）」。

判据刻意做窄：只认**形如 topic 的字符串字面量**（`域/…`，域取 af|ma|butler|adm），
不猜变量的运行时取值。新增域/主题必须先在契约表登记、再来这里加一行——顺序反了
就会在这里留下"我们自己给自己发通行证"的痕迹，评审一眼看得见。

纯标准库：本机禁 pip install，装包的门禁等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

CONTRACT_DOC = "homesdk/doc/ADM联动主题注册表与消息契约.md"

#: 契约表 §1.1 状态类（retained + LWT）——AF 只发 `adm/autoforge/*`，探测对端读同族主题
STATUS_TOPICS = (
    "adm/autoforge/status",
    "adm/autoforge/caps",
    "adm/doubao-butler/status",
    "adm/doubao-butler/caps",
    "adm/memory-agent/status",
    "adm/memory-agent/caps",
)

#: 契约表 §1.2 事件类（永不 retained）
EVENT_TOPICS = (
    "af/automation/fired",
    "af/automation/failed",
    "ma/insights",
    "ma/presence",
    "ma/device-health",
    "ma/alerts",
)

#: 契约表 §1.3 公共收件箱（所有权与校验权在 DB；AF 可投递、**禁止订阅**）
INBOX_TOPICS = (
    "butler/inbox/speak",
    "butler/inbox/notify",
    "butler/inbox/tv",
)

#: 契约表 §1.2 既有 DB 主题（AF 侧只读，用于状态呈现）
LEGACY_TOPICS = (
    "butler/status/state",
    "butler/dialog/event",
)

REGISTERED = frozenset(STATUS_TOPICS + EVENT_TOPICS + INBOX_TOPICS + LEGACY_TOPICS)

#: 允许出现在代码里的**家族前缀 / 订阅过滤器**形态：它们不是可发布的具体主题，
#: 而是围绕已登记主题做的防御性判断（`af_mqtt_bridge.FORBIDDEN_SUBSCRIPTIONS`）。
REGISTERED_FORMS = frozenset({
    "butler/inbox/",     # 前缀判断："凡收件箱族一律不许订阅"
    "butler/inbox/#",    # MQTT 多级通配过滤器
    "butler/inbox/*",    # MQTT 单级通配过滤器
    "adm/",              # 域名前缀判断
    "af/",
    "ma/",
    "butler/",
})

TOPIC_RE = re.compile(r"^(af|ma|butler|adm)/[A-Za-z0-9_#*./-]*$")


def topic_literals(path: Path) -> list[tuple[int, str]]:
    """文件里所有形如 topic 的字符串字面量（含 docstring——注释里写错主题名同样危险）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []   # 由 _parse_failures 单独报，不在这里伪装成 topic
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if TOPIC_RE.match(node.value):
                out.append((node.lineno, node.value))
    return out


def _parse_failures(root: Path) -> list[str]:
    """解析失败的单独走一条路：`<无法解析…>` 不像 topic，不能被 topic 过滤吃掉。"""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, OSError, UnicodeDecodeError) as exc:
            out.append(f"{path.relative_to(root.parent)} 无法解析（{type(exc).__name__}: {exc}）")
    return out


def check(root: Path) -> list[str]:
    findings: list[str] = _parse_failures(root)
    for path in sorted(root.rglob("*.py")):
        for lineno, value in topic_literals(path):
            if value in REGISTERED or value in REGISTERED_FORMS:
                continue
            findings.append(
                f"{path.relative_to(root.parent)}:{lineno} 未登记主题 {value!r}"
                f"（先在 {CONTRACT_DOC} 登记，再加进本脚本）"
            )
    return findings


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "src" / "autoforge"
    if not root.is_dir():
        print(f"主题白名单门禁：目录不存在 {root}")
        return 2
    findings = check(root)
    if findings:
        for line in findings:
            print(f"  ✗ {line}")
        print(f"\n主题白名单门禁报红：{len(findings)} 处未登记（契约表是唯一真源，先改表再改码）")
        return 1
    n = sum(len(topic_literals(p)) for p in sorted(root.rglob("*.py")))
    print(f"✓ 主题白名单门禁干净（{n} 处 topic 字面量全部在契约表内）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

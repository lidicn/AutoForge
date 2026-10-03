#!/usr/bin/env python3
"""入向订阅门禁：AF 只订阅 `ma/insights`，任何别的 MQTT 订阅必须先过禁订族判定。

起因（计划 §第 1 步 ④ + 契约表 §1.3 护栏「收件箱是 DB 的，AF 不替 DB 说话」）：这条约束今天靠
`af_mqtt_bridge.FORBIDDEN_SUBSCRIPTIONS` 常量 + `subscribe_topic()`/`handle_message()` 两处运行时
判定 + 一批行为测试。**测试钉的是已知的这几个入口**：下一个实现直接写 `self.client.subscribe(
"butler/inbox/#")`（或另开一个不查禁订族的订阅口），现有测试一条都不会红——这与 §二之二十二 盘出向
事件时同一个结构性缺口，也正因为同一形状（调用图，不是值语义）才做得成静态门。

三条判据（各自能单独判红）：
- **A 订阅口唯一**：MQTT 层的订阅调用点只允许出现在 `af_mqtt_bridge.py`。桥外订阅 = 绕开禁订族判定、
  绕开 `rejected/forbidden_seen` 留痕，也对端无从分辨"这条消息是谁家代码收的"。
  射程判据取两条信号的**并集**：接收者是 `client`/`self.client`，**或**调用带了 `qos=` 关键字。
  只用接收者名会漏掉 `hub.subscribe(topic, qos=…)`；只用 `qos=` 会漏掉不传 QoS 的写法。
  进程内总线不判：`af_bus`/`af_vhass` 的 `subscribe(handler)` 既没有 topic 字符串也没有 QoS，
  契约面完全不同，硬塞进射程只会把门变成噪音（与 §二之十七 同一口径）。
- **B 主题要么是唯一入向主题，要么当场过守卫**：订阅实参是 `INSIGHTS_TOPIC` ⇒ 放行；否则该调用
  **所在的最内层函数体**里必须出现禁订族判定（`FORBIDDEN_SUBSCRIPTIONS` 或 `startswith("butler/inbox`）。
  动态主题本身是契约允许的（`subscribe_topic()` 就是干这个的），不允许的是**不判就订**。
- **C 硬编码禁订主题一律红**：实参是字面量且落在 `butler/inbox/` 族 ⇒ 即便外层函数带守卫也判红。
  守卫是"给动态入口兜底"，不是"把订收件箱的意图写死在代码里"的通行证。

锚点（读不到就 exit 2，不许静默全绿）：`af_mqtt_bridge.py` 里必须有模块级 `INSIGHTS_TOPIC` 与
`FORBIDDEN_SUBSCRIPTIONS` 两个常量，且类里同时有 `start`/`subscribe_topic`/`handle_message` 三个方法
——本门的判据全长在这些符号上，改名或把禁订族挪走会让门变成"永远干净"（§二之十四 的那课，同一形状）。

现场豁免 `# mqtt-subscriptions: exempt(理由)`，理由不能空，且**单独计入读数**（铁律 #5：绿色行写
"全部经过守卫"而其中一处其实靠豁免过关，等于把未验证的算成已验证）。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANCHOR_FILE = REPO / "src" / "autoforge" / "af_mqtt_bridge.py"
BRIDGE = "af_mqtt_bridge.py"
TOPIC_CONSTANTS = ("INSIGHTS_TOPIC", "FORBIDDEN_SUBSCRIPTIONS")
ANCHOR_METHODS = ("start", "subscribe_topic", "handle_message")
GUARD_TOKENS = ("FORBIDDEN_SUBSCRIPTIONS", 'startswith("butler/inbox')
FORBIDDEN_PREFIX = "butler/inbox/"
_EXEMPT = re.compile(r"mqtt-subscriptions:\s*exempt\(\s*(\S[^)]*)\s*\)")


def _call_parts(node: ast.Call) -> tuple[str, str]:
    """返回 `(对象, 方法名)`；裸函数调用对象为空串。"""
    fn = node.func
    if isinstance(fn, ast.Attribute):
        base = fn.value
        if isinstance(base, ast.Attribute):
            return f"{base.value.id if isinstance(base.value, ast.Name) else ''}.{base.attr}", fn.attr
        if isinstance(base, ast.Name):
            return base.id, fn.attr
        return "", fn.attr
    if isinstance(fn, ast.Name):
        return "", fn.id
    return "", ""


def _kw_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in (call.keywords or []) if kw.arg}


def _is_mqtt_subscribe(call: ast.Call) -> bool:
    obj, name = _call_parts(call)
    if name != "subscribe":
        return False
    return obj in {"client", "self.client"} or "qos" in _kw_names(call)


def _topic_arg(call: ast.Call):
    """第一个实参，位置式与 `topic=` 关键字式都认——只认前者等于留一条静默放行。"""
    if call.args:
        return call.args[0]
    for kw in call.keywords or []:
        if kw.arg == "topic":
            return kw.value
    return None


def _functions_with_calls(tree: ast.Module) -> list[tuple[ast.FunctionDef | None, ast.Call]]:
    """每个 MQTT 订阅点配上**最内层**包围它的函数（顶层调用配 `None`）。"""
    out: list[tuple[ast.FunctionDef | None, ast.Call]] = []

    def visit(node, stack: list[ast.FunctionDef]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*stack, child])
                continue
            if isinstance(child, ast.Call) and _is_mqtt_subscribe(child):
                out.append((stack[-1] if stack else None, child))
            visit(child, stack)

    visit(tree, [])
    return out


def _literal_topic(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_exempt(lines: list[str], lineno: int) -> bool:
    for idx in (lineno, lineno - 1):
        if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
            return True
    return False


def _findings_for(path: Path, rel: str, tree: ast.Module, lines: list[str]):
    hits: list[str] = []
    stats = {"sites": 0, "insights": 0, "guarded": 0, "exempted": 0}
    in_bridge = path.name == BRIDGE

    for func, call in _functions_with_calls(tree):
        stats["sites"] += 1
        line = call.lineno
        topic = _topic_arg(call)
        fname = func.name if func is not None else "<模块顶层>"
        body_src = ast.unparse(func) if func is not None else ""

        if _literal_topic(topic) is not None and _literal_topic(topic).startswith(FORBIDDEN_PREFIX):
            # C：硬编码收件箱主题——带守卫也红，这是契约面的方向问题不是入口问题
            if _is_exempt(lines, line):
                stats["exempted"] += 1
                continue
            hits.append(
                f"{rel}:{line}: `{fname}()` 里把收件箱主题写死成订阅实参"
                f"（{_literal_topic(topic)!r}）——收件箱是 DB 的（契约表 §1.3 护栏 / 计划 第 1 步 ④），"
                f"守卫是给动态入口兜底的，不是把越界意图写进代码的通行证"
            )
            continue

        if not in_bridge:
            if _is_exempt(lines, line):
                stats["exempted"] += 1
                continue
            hits.append(
                f"{rel}:{line}: 桥外 MQTT 订阅 `{ast.unparse(call)}`——禁订族判定与 "
                f"`rejected`/`forbidden_seen` 留痕都在 `{BRIDGE}` 里，绕过它等于 AF 悄悄多了一个耳朵"
            )
            continue

        if isinstance(topic, ast.Name) and topic.id == "INSIGHTS_TOPIC":
            stats["insights"] += 1
            continue
        if any(tok in body_src for tok in GUARD_TOKENS):
            stats["guarded"] += 1
            continue
        if _is_exempt(lines, line):
            stats["exempted"] += 1
            continue
        hits.append(
            f"{rel}:{line}: `{fname}()` 里订阅 `{ast.unparse(topic) if topic is not None else '<无实参>'}` "
            f"却没有禁订族判定（`{GUARD_TOKENS[0]}` / `startswith(\"butler/inbox…\")` 都不在该函数体内）"
            f"——动态主题要判后才能订"
        )
    return hits, stats


def check(root: Path):
    findings: list[str] = []
    totals = {"sites": 0, "site_files": 0, "insights": 0, "guarded": 0, "exempted": 0}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        hits, st = _findings_for(path, rel, tree, source.splitlines())
        findings.extend(hits)
        for k in ("sites", "insights", "guarded", "exempted"):
            totals[k] += st[k]
        if st["sites"]:
            totals["site_files"] += 1
    return findings, totals


def anchor_ok() -> str | None:
    if not ANCHOR_FILE.is_file():
        return f"锚点文件不在盘上：{ANCHOR_FILE.as_posix()}"
    tree = ast.parse(ANCHOR_FILE.read_text(encoding="utf-8"), filename=str(ANCHOR_FILE))
    names = {t.id for st in tree.body if isinstance(st, (ast.Assign, ast.AnnAssign))
             for t in ([st.target] if isinstance(st, ast.AnnAssign) else st.targets)
             if isinstance(t, ast.Name)}
    missing = [n for n in TOPIC_CONSTANTS if n not in names]
    if missing:
        return f"模块级常量缺失：{', '.join(missing)}（入向主题或禁订族改名 ⇒ 本门无从判定）"
    funcs = {f.name for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
             for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for needed in ANCHOR_METHODS:
        if needed not in funcs:
            return f"类里找不到 `{needed}()`（改名/挪走会让本门静默全绿）"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok()
    if err:
        print(f"✗ 入向订阅门禁读不到锚点：{err}")
        return 2
    findings, totals = check(root)
    if findings:
        print(f"✗ 入向订阅门禁发现 {len(findings)} 处（扫描到 MQTT 订阅点 {totals['sites']} 处，"
              f"现场豁免 {totals['exempted']} 处）：")
        for f in findings:
            print(f"  {f}")
        print(f"  修法：订阅只在 `{BRIDGE}` 收口；动态主题要先过 `{GUARD_TOKENS[0]}` 判定；"
              f"收件箱族不订。确实要破例就地写 `# mqtt-subscriptions: exempt(理由)`。")
        return 1
    # 绿色行每个数字都是本轮实测计数，不写"全部/只在"这类没数过的断言（铁律 #5）
    print(f"✓ 入向订阅门禁干净（MQTT 订阅点 {totals['sites']} 处、分布在 {totals['site_files']} 个文件；"
          f"其中主题为 `INSIGHTS_TOPIC` 的 {totals['insights']} 处、动态主题且函数体内有禁订族守卫的 "
          f"{totals['guarded']} 处；现场豁免 {totals['exempted']} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

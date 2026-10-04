#!/usr/bin/env python3
"""出向 MQTT 写者门禁：AF 往 broker 发的每一条消息都只能从**一个模块、一条路径、一个信封**出去。

起因（§二之二十二，本批实测盘出的形状）：AF 有条"逐字段对契约"的测试一直绿，而它测的是
`publish_fired()/publish_failed()` 的**直接调用路径**；生产环境唯一发事件的路径是
`observe_terminal()`，它当时比直接调用多发一个契约表 §1.2 没列的 `node_id`（该键已按裁定
20261004 §一 2 删除）。两条路各测一头 ⇒
**没有任何一条测试红过，而真实载荷早就和契约行不一样了**。这是铁律 #5 的"测到 ≠ 覆盖到"，
本门把它做成静态判据：射程不是"已知的这几个调用点"，而是"**下一个写者**"。

三条判据（各自都能单独判红）：
- **A 写者唯一**：`_mqtt.publish(...)` / `_presence.advertise(...)` 的调用点只允许出现在
  `af_mqtt_bridge.py`。别处自己拿 client 发 = 绕过主题白名单门之外的那半（载荷形态、QoS、
  `ts` 口径、发布失败要留痕）——那半没有任何静态约束。
- **B 事件唯一生产者**：`publish_fired(...)` / `publish_failed(...)` 的调用点必须落在
  `af_mqtt_bridge.py` 的 `observe_terminal()` **函数体内**。多一个生产者 = 多一条"测试测不到、
  对端却在收"的路（本批那件事的形状）。
- **C 载荷必经信封**：桥内 `self._publish(<topic>, payload)` 的 payload 实参必须是
  `self._envelope(...)` 的返回值（直接调用，或本函数内先由它赋值再补字段的变量）。
  手搓 dict = `ts` 的家庭墙钟口径、`ref`=实例 id、trace_id 的生成方式**一次全丢**。

锚点（读不到就 exit 2，不许静默全绿）：`af_mqtt_bridge.py` 里必须有模块级
`FIRED_TOPIC`/`FAILED_TOPIC` 常量，且类里同时有 `_envelope` 与 `observe_terminal` 两个方法——
本门的三条判据全都长在这两个符号上，改名/挪走会让门变成"永远干净"。

现场豁免 `# mqtt-writers: exempt(理由)`，理由不能空，且**单独计入读数**（铁律 #5）。

纯标准库 AST：本机禁 pip install，依赖第三方解析器的门等于没有门禁。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRIDGE = "af_mqtt_bridge.py"
ANCHOR_FILE = REPO / "src" / "autoforge" / BRIDGE
EVENT_PUBLISHERS = ("publish_fired", "publish_failed")
PRODUCER_FUNC = "observe_terminal"
ENVELOPE = "_envelope"
INTERNAL_PUBLISH = "_publish"
MQTT_WRITERS = {("_mqtt", "publish"), ("_presence", "advertise")}
TOPIC_CONSTANTS = ("FIRED_TOPIC", "FAILED_TOPIC")
_EXEMPT = re.compile(r"mqtt-writers:\s*exempt\(\s*(\S[^)]*)\s*\)")


def _call_name(node: ast.Call) -> tuple[str, str]:
    """返回 `(对象, 方法名)`；裸函数调用对象为空串。"""
    fn = node.func
    if isinstance(fn, ast.Attribute):
        base = fn.value
        if isinstance(base, ast.Attribute):
            return f"{base.value.id if isinstance(base.value, ast.Name) else ''}.{base.attr}", fn.attr
        if isinstance(base, ast.Name):
            return base.id, fn.attr
        return "", fn.attr  # self.x.y(...) 之类不在射程
    if isinstance(fn, ast.Name):
        return "", fn.id
    return "", ""


def _calls_with_func(tree: ast.Module) -> list[tuple[str, ast.Call]]:
    """每个 Call 配上**最内层**包围它的函数名（顶层调用用空串）。"""
    out: list[tuple[str, ast.Call]] = []

    def visit(node, stack: list[str]) -> None:
        name = stack[-1] if stack else ""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*stack, child.name])
                continue
            if isinstance(child, ast.Call):
                out.append((name, child))
            visit(child, stack)

    visit(tree, [])
    return out


def _assignments(tree: ast.Module) -> set[tuple[str, str, str]]:
    """`(所在函数, 变量名, 被赋值的调用名)`——用于 C 判据"先 _envelope() 赋值再补字段"。"""
    out: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for st in ast.walk(node):
                if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Call):
                    for t in ([st.target] if isinstance(st, ast.AnnAssign) else st.targets):
                        if isinstance(t, ast.Name):
                            out.add((node.name, t.id, _call_name(st.value)[1]))
    return out


def _is_envelope(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return _call_name(node)[1] == ENVELOPE


def _payload_arg(call: ast.Call):
    """`_publish` 的第二个实参，位置式与 `payload=` 关键字式都认。

    只认位置参数会留一条**静默放行**的写法（`self._publish(topic=…, payload=…)`），
    而判据的红必须只在真违例时出现。
    """
    if len(call.args) >= 2:
        return call.args[1]
    for kw in call.keywords or []:
        if kw.arg == "payload":
            return kw.value
    return None


def _findings_for(
    path: Path, rel: str, tree: ast.Module, lines: list[str]
) -> tuple[list[str], dict[str, int]]:
    hits: list[str] = []
    stats = {"exempted": 0, "writers": 0, "producers": 0, "payload_sites": 0, "payload_ok": 0}
    in_bridge = path.name == BRIDGE
    assigns = _assignments(tree)

    def exempt(line: int) -> bool:
        for idx in (line, line - 1):
            if 1 <= idx <= len(lines) and _EXEMPT.search(lines[idx - 1]):
                return True
        return False

    for func, call in _calls_with_func(tree):
        obj, name = _call_name(call)
        line = call.lineno

        # A：出向写者
        if (obj, name) in MQTT_WRITERS:
            stats["writers"] += 1
            if not in_bridge:
                if exempt(line):
                    stats["exempted"] += 1
                    continue
                hits.append(
                    f"{rel}:{line}: 桥外直接 `{obj}.{name}(…)` 写 MQTT——载荷形态、QoS、`ts` 口径"
                    f"与发布失败留痕全在 `af_mqtt_bridge` 里，绕过它等于发一条没人验过的消息"
                )
            continue

        # B：事件生产者
        if name in EVENT_PUBLISHERS:
            stats["producers"] += 1
            if not in_bridge or func != PRODUCER_FUNC:
                if exempt(line):
                    stats["exempted"] += 1
                    continue
                where = f"`{rel}` 的 `{func}()` 里" if in_bridge else f"桥外文件 `{rel}`"
                hits.append(
                    f"{rel}:{line}: {where}调了 `{name}()`——生产环境发事件的路径只有一条 "
                    f"(`{PRODUCER_FUNC}()`)；多一条就多半会多一条『测试测不到、对端却在收』的载荷"
                )
            continue

        # C：桥内 _publish 的载荷必须来自 _envelope()
        if in_bridge and name == INTERNAL_PUBLISH:
            payload = _payload_arg(call)
            if payload is None:
                continue
            stats["payload_sites"] += 1
            ok = _is_envelope(payload) or (
                isinstance(payload, ast.Name)
                and (func, payload.id, ENVELOPE) in assigns
            )
            if ok:
                stats["payload_ok"] += 1
                continue
            if exempt(line):
                stats["exempted"] += 1
                continue
            hits.append(
                f"{rel}:{line}: `{INTERNAL_PUBLISH}(…, {ast.unparse(payload)})` 的载荷不是 "
                f"`{ENVELOPE}()` 的产物——手搓 dict 会同时丢掉 `ts` 的家庭墙钟口径、"
                f"`ref`=实例 id 与 trace_id 的生成方式"
            )
    return hits, stats


def check(root: Path) -> tuple[list[str], dict[str, int]]:
    findings: list[str] = []
    totals = {"exempted": 0, "writers": 0, "writer_files": 0, "producers": 0,
              "producer_files": 0, "payload_sites": 0, "payload_ok": 0}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.as_posix()
        hits, st = _findings_for(path, rel, tree, source.splitlines())
        findings.extend(hits)
        for k, v in st.items():
            totals[k] += v
        if st["writers"]:
            totals["writer_files"] += 1
        if st["producers"]:
            totals["producer_files"] += 1
    return findings, totals


def anchor_ok() -> str | None:
    if not ANCHOR_FILE.is_file():
        return f"锚点文件不在盘上：{ANCHOR_FILE.as_posix()}"
    tree = ast.parse(ANCHOR_FILE.read_text(encoding="utf-8"), filename=str(ANCHOR_FILE))
    names = {t.id for st in tree.body if isinstance(st, ast.Assign)
             for t in st.targets if isinstance(t, ast.Name)}
    missing = [n for n in TOPIC_CONSTANTS if n not in names]
    if missing:
        return f"模块级常量缺失：{', '.join(missing)}（主题名改名 ⇒ 本门无从判定）"
    funcs = {f.name for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
             for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for needed in (ENVELOPE, PRODUCER_FUNC, *EVENT_PUBLISHERS):
        if needed not in funcs:
            return f"类里找不到 `{needed}()`（改名/挪走会让本门静默全绿）"
    return None


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO / "src"
    if not root.is_absolute():
        root = REPO / root
    err = anchor_ok()
    if err:
        print(f"✗ 出向 MQTT 写者门禁读不到锚点：{err}")
        return 2
    findings, totals = check(root)
    if findings:
        print(f"✗ 出向 MQTT 写者门禁发现 {len(findings)} 处（现场豁免 {totals['exempted']} 处）：")
        for f in findings:
            print(f"  {f}")
        print(f"  修法：出向消息走 `af_mqtt_bridge`；事件由 `{PRODUCER_FUNC}()` 产生；"
              f"载荷用 `{ENVELOPE}()`。确实要破例就地写 `# mqtt-writers: exempt(理由)`。")
        return 1
    # 绿色行的每个数字都是本轮实测计数，不写"只在/全部"这类没数过的断言（铁律 #5）
    print(f"✓ 出向 MQTT 写者门禁干净（出向写者调用点 {totals['writers']} 处、分布在 "
          f"{totals['writer_files']} 个文件；事件生产者 {totals['producers']} 处、分布在 "
          f"{totals['producer_files']} 个文件；桥内 `{INTERNAL_PUBLISH}(topic, payload)` "
          f"{totals['payload_sites']} 处，其中载荷来自 `{ENVELOPE}()` 的 {totals['payload_ok']} 处；"
          f"现场豁免 {totals['exempted']} 处）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

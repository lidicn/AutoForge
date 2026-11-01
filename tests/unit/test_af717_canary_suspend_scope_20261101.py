"""AF-717 判据（Chief 阻断项，2026-11-01）：canary 观察窗的无人值守分支纳入
HIGH_RISK_AFTER_SUSPEND 射程。

背景：撤掉运行期确认闸（裁定 20261031·Q1 甲）后，canary 观察窗是运行期**仅剩的
挂起**——`af_executor._do` 在 `canary.duration` 下 `suspend(kind="canary_observe")`，
到期 `resume("on_timeout")`、恢复后按 `{kind,"default"}` 选边继续 run。而
`Node.is_suspending` 只认 ask/wait ⇒ 带 canary 的 do 节点此前漏在射程外；撤闸前
L2 do 必挂 confirm 闸被拦，撤闸后直接放行 ⇒ 盲区成为实际可达通道。

四条腿（AF-Verify 口径：两向变异都要有牙 + ask 收窄不被回滚）：
  ① 正例腿：canary do 的 on_timeout→L2 ⇒ 必报 HIGH_RISK_AFTER_SUSPEND；
  ② 放宽变异腿：射程条件（do+canary）被拿掉 ⇒ 腿①形状判据必须红（闸失去牙）；
  ③ 收紧变异腿（借 AF-716 同族形状）：do 允许集去掉 on_cancel ⇒ AF-716 leg1 红；
  ④ ask 收窄保持腿：ask 的 yes 边指向 L2 ⇒ 仍 0 ERROR（裁定 20261031 的收窄
     没被本修法回滚）。
另：canary do 的合法形状（on_timeout→pass）保持 0 ERROR（⛔ 不动 canary，Q2 甲）。
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

from autoforge.af_ir import load_graph
from autoforge.af_scanner import StaticScanner


def _fresh_scanner():
    """独立加载 af_scanner（归一包名，保相对导入可用）——变异腿在独立实例上改类属性，
    ⛔ 不污染当前进程的 StaticScanner。"""
    import autoforge.af_scanner as _m
    spec = importlib.util.spec_from_file_location("autoforge.af_scanner_mut", SCANNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, _m

SCANNER_PATH = Path(__file__).resolve().parents[2] / "src" / "autoforge" / "af_scanner.py"


def _canary_ir(timeout_to: str = "d2") -> dict:
    """on1 → d1(do+canary, L1) --on_timeout--> <target> --on_timeout--> p1。"""
    return {
        "ir_version": "0.2.1", "id": "af717_canary_shape", "name": "canary 射程形状",
        "version": 1, "mode": "single",
        "nodes": [
            {"id": "on1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "d1", "kind": "do", "name": "d1", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.y"}, "canary": {"duration": "30s", "auto_rollback": True}},
            {"id": "d2", "kind": "do", "name": "d2", "adapter": "mock", "action": "climate.turn_on",
             "params": {"entity_id": "climate.x"}, "requires_confirm": True,
             "canary": {"duration": "15m", "auto_rollback": True}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [
            {"from": "on1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": timeout_to, "kind": "on_timeout"},
            {"from": "d2", "to": "p1", "kind": "on_timeout"},
        ],
    }


def _errors(ir: dict) -> list[tuple[str, str]]:
    scan = StaticScanner(load_graph(ir)).scan()
    return [(d.code, d.node_id) for d in scan.diagnostics if d.level == "error"]


def test_leg1_canary_timeout_to_l2_is_red():
    """腿①：canary do 的 on_timeout 分支下游 L2 ⇒ HIGH_RISK_AFTER_SUSPEND。"""
    errs = _errors(_canary_ir())
    assert ("HIGH_RISK_AFTER_SUSPEND", "d2") in errs, errs


def test_leg1b_canary_timeout_to_pass_stays_green():
    """补腿：canary do 的 on_timeout→pass 合法（⛔ 不动 canary 本身，正例不误伤）。"""
    errs = _errors(_canary_ir(timeout_to="p1"))
    assert errs == [], errs


def _mutated_scanner(source: str, tag: str):
    """把 af_scanner 源码改写后装成独立模块（名带包前缀，保相对导入可用）。
    变异走源文本级（最硬：不依赖任何可被绕过的类属性/共享类），先断言变异真落进源码。"""
    name = f"autoforge._af717_mut_{tag}"
    spec = importlib.util.spec_from_loader(
        name,
        loader=None,
    )
    mod = type(sys)(name)
    mod.__package__ = "autoforge"
    mod.__file__ = str(SCANNER_PATH)
    sys.modules[name] = mod
    try:
        exec(compile(source, str(SCANNER_PATH), "exec"), mod.__dict__)
    finally:
        pass
    return mod


def _scanner_source() -> str:
    return SCANNER_PATH.read_bytes().decode("utf-8")


def test_leg2_relax_mutant_makes_leg1_shape_green():
    """腿②放宽向：把 do+canary 从挂起点判据拿掉 ⇒ 腿①形状必须转绿（判据失去牙=红）。"""
    src = _scanner_source()
    OLD_GUARD = 'suspending = node.is_suspending or node.canary is not None'
    assert src.count(OLD_GUARD) == 1, "放宽变异锚未命中，⛔ 等价变异"
    relaxed = src.replace(OLD_GUARD, "suspending = node.is_suspending")
    mod = _mutated_scanner(relaxed, "relax")
    ir = _canary_ir()
    errs = [(d.code, d.node_id) for d in mod.StaticScanner(load_graph(ir)).scan().diagnostics
            if d.level == "error"]
    assert ("HIGH_RISK_AFTER_SUSPEND", "d2") not in errs, (
        "放宽变异后腿①形状仍被拦 ⇒ 判据钉不住射程（假绿）")
    # 当前实现（未放宽）必须红——两向对照在同一测试内自证
    assert ("HIGH_RISK_AFTER_SUSPEND", "d2") in _errors(ir)


def test_leg3_tighten_mutant_keeps_af716_red():
    """腿③收紧向（AF-716 同族）：do 允许集去掉 on_cancel ⇒ AF-716 leg1 形状必须红。"""
    src = _scanner_source()
    OLD_SET = '"do": frozenset({"then", "on_error", "default", "on_timeout", "on_cancel"})'
    assert src.count(OLD_SET) == 1, "收紧变异锚未命中，⛔ 等价变异"
    tightened = src.replace(OLD_SET, '"do": frozenset({"then", "on_error", "default", "on_timeout"})')
    mod = _mutated_scanner(tightened, "tight")
    ir = {
        "ir_version": "0.2.1", "id": "tight_probe", "name": "tight", "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "d1", "kind": "do", "name": "d1", "adapter": "mock", "action": "light.turn_on",
             "params": {"entity_id": "light.y"}, "requires_confirm": True,
             "canary": {"duration": "15m", "auto_rollback": True}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [
            {"from": "t1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "on_cancel"},
        ],
    }
    errs = [(d.code, d.node_id) for d in mod.StaticScanner(load_graph(ir)).scan().diagnostics
            if d.level == "error"]
    assert ("EDGE_KIND_MISMATCH", "d1") in errs, "收紧变异未被检出 ⇒ 闸无牙"
    # 当前实现（未收紧）该形状必须绿——AF-716 leg1 的同一形状
    assert _errors(ir) == []


def test_leg4_ask_yes_edge_stays_green():
    """腿④：ask 的 yes 边指向 L2 ⇒ 仍 0 ERROR（裁定 20261031 收窄未被回滚）。

    防的是"顺手把 ask 的 yes/no 又吞回射程"（chief 修法与 AF-710 的收窄挨着改，
    最容易互相覆盖）。反向对照：同一节点形状的 on_timeout→L2 必须红（腿①系）。
    """
    ir = {
        "ir_version": "0.2.1", "id": "ask_yes_shape", "name": "ask yes", "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "t", "trigger": {"type": "state", "entity_id": "light.x", "to": "on"}},
            {"id": "q1", "kind": "ask", "name": "q", "prompt": "要开吗？", "room": "study",
             "timeout": "10m"},
            {"id": "d1", "kind": "do", "name": "d1", "adapter": "mock", "action": "climate.turn_on",
             "params": {"entity_id": "climate.x"}, "requires_confirm": True,
             "canary": {"duration": "15m", "auto_rollback": True}},
            {"id": "p1", "kind": "pass", "name": "p"},
        ],
        "edges": [
            {"from": "t1", "to": "q1", "kind": "then"},
            {"from": "q1", "to": "d1", "kind": "yes"},
            {"from": "q1", "to": "p1", "kind": "no"},
            {"from": "q1", "to": "p1", "kind": "on_timeout"},
            {"from": "d1", "to": "p1", "kind": "on_timeout"},
        ],
    }
    assert _errors(ir) == []

"""AF-707（静默死图：边 kind 不匹配 ⇒ 实例报 done）回归钉，2026-10-31。

依据：PM 裁定（补码，不豁免，进 v1.14.25）＋台账 AF-707 行（证据
`audit_mcp/study_ac_edgekind_proof_20261031.json`）。病灶链：图写错（if 真值
分支边 kind 写成 `yes`）⇒ 执行器 `pick_edge` 匹配不上 ⇒ 实例静默终止、终态报
`done`、report 全 `inferred`，而 build 与 sim **零报错**——自动化看起来部署
成功，实际一件事没干（假绿族）。

修法三层（锚点按符号名钉，PM 新规矩二）：
1. 静态闸（主攻）：`StaticScanner._check_edge_kind_contract`——节点 kind 的
   出边 kind 不在 `ALLOWED_OUT_EDGE_KINDS` ⇒ ERROR `EDGE_KIND_MISMATCH`，
   build 期即拦（**本批唯一保留的一层**）；
2. 执行器兜底：**本批已撤回**（PM 裁决 2026-10-31）——「pick_edge 落空 ⇒ 终态
   failed」把 8 条既有判据编码的终态语义从 done 改成 failed（确认闸无人应答／
   超时／denial、canary 链断、体验者 no-op），落点正是 DCD 在裁的「取消运行期
   二次授权」面＝抢裁；改按**窄口径**另开卡：只在「边 kind 非法导致落空」时报
   failed，不改合法终止路径的终态语义，等 DCD 裁定后落；
3. 顺带：`_check_high_risk_after_suspend` 的重复 `continue`（并发双写残留）删除。

⇒ 假绿洞在 build 期已闭合：边 kind 写错的图过不了静态闸 ⇒ 不会部署 ⇒ 运行期
不可能出现那种静默终止。允许集的口径＝**执行器现读**：`do` 允许 `on_timeout`
（canary 观察期到期）、任何节点允许 `on_cancel`（全局中断路径）、`yes`/`no`
只属于 `ask`（if 的真分支必须是 then）。

变异自证（摘掉静态闸 ⇒ 判据①红）由实现者手工执行并附命令输出。
"""

from __future__ import annotations

from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import CHECKS, CODE_HINT, ERROR, StaticScanner


def _study_ac_shape(edge_kind: str) -> dict:
    """书房活图形状（台账证据同款）：c1(kind=if) 真值分支出边 → d1(do)。"""
    return {
        "ir_version": "0.3.0", "id": "study_ac", "name": "study_ac", "version": 1, "mode": "single",
        "nodes": [
            {"id": "t1", "kind": "on", "name": "触发",
             "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
            {"id": "c1", "kind": "if", "name": "热了",
             "expr": {"op": "eq", "left": {"var": "entity.binary_sensor.m"}, "right": {"const": "on"}}},
            {"id": "d1", "kind": "do", "name": "开空调", "adapter": "ha",
             "action": "light.turn_on", "params": {"entity_id": "light.x"}},
            {"id": "p1", "kind": "pass", "name": "结束"},
        ],
        "edges": [
            {"from": "t1", "to": "c1", "kind": "then"},
            {"from": "c1", "to": "d1", "kind": edge_kind},   # ← 病灶位：yes / then
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }


def _scan(ir: dict) -> list:
    return StaticScanner(Graph([load_automation(ir)])).scan().diagnostics


# ── 判据① 静态闸：书房形状（yes 边）build 必报 ERROR ─────────────────


def test_yes_edge_on_if_reports_edge_kind_mismatch():
    """if 真值分支出边 kind=yes ⇒ EDGE_KIND_MISMATCH ERROR（build 期即拦）。"""
    codes = {d.code for d in _scan(_study_ac_shape("yes"))}
    assert "EDGE_KIND_MISMATCH" in codes, f"静默死图形状零报错：{sorted(codes)}"
    level = [d.level for d in _scan(_study_ac_shape("yes")) if d.code == "EDGE_KIND_MISMATCH"][0]
    assert level == ERROR


def test_then_edge_on_if_stays_green():
    """正常 then 图回归绿——不误伤（口径回归腿）。"""
    codes = {d.code for d in _scan(_study_ac_shape("then"))}
    assert "EDGE_KIND_MISMATCH" not in codes, sorted(codes)


def test_new_code_registered_in_both_catalogs():
    """新码双目录同源：CHECKS + CODE_HINT 都有、hint 非空（B.8 两层门）。"""
    assert "EDGE_KIND_MISMATCH" in CHECKS
    assert CODE_HINT.get("EDGE_KIND_MISMATCH", "").strip()


# ── 判据② 执行器兜底：pick_edge 落空不得以 done 收场 ─────────────────


def _run_instance(ir: dict) -> list[dict]:
    """用服务层 simulate（与验收同口径，FakeHA 内存轨）真跑一遍，取实例明细。"""
    from autoforge import af_service

    out = af_service.simulate({"automations": [ir]},
                              seed={"binary_sensor.m": "off", "light.x": "off"},
                              events=[{"entity_id": "binary_sensor.m", "state": "on"}])
    return out.get("instances") or []


# ── 判据② 执行器兜底：**压后**（PM 裁决 2026-10-31）────────────────────
# 「pick_edge 落空 ⇒ 终态 failed」那一层已从本批撤回（理由见文件头）：它改的是
# 既有判据编码的终态语义，落点在 DCD 正裁的授权面。假绿洞在 **build 期**闭合——
# yes 形状过不了静态闸 ⇒ 不会部署 ⇒ 运行期不可能静默终止。执行器兜底改按窄口径
# 另开卡（只在「边 kind 非法导致落空」时报 failed），等 DCD 裁定后落。


def test_yes_shape_never_reaches_runtime_as_silence():
    """yes 形状在 build 期即被拒（ERROR 级）⇒ 不可能部署成静默终止。"""
    diags = _scan(_study_ac_shape("yes"))
    assert any(d.code == "EDGE_KIND_MISMATCH" and d.level == ERROR for d in diags), [
        (d.code, d.level) for d in diags
    ]


def test_healthy_edge_instance_reaches_done():
    """对照腿：then 图正常走完（终态含 done）——静态闸不误伤正常路径。"""
    instances = _run_instance(_study_ac_shape("then"))
    assert instances, "没有实例被触发"
    st = {str(i.get("state")) for i in instances}
    assert "done" in st, f"正常图没跑到 done（误伤）：{st}"

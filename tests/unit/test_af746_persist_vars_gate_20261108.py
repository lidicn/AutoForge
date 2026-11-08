# -*- coding: utf-8 -*-
"""AF-746 判据 · 跨实例持久变量（IR `vars.<名>.persist` 接运行事实旁车）（2026-11-08）· 卡名 `AF卡_v7_完整自动化`。

**为什么需要**（出资人 19:20 规格 §一／PM 评审 §三，台账 §1.184 后一段）：v7 的关机段铁律是
「空调不是自动化开的就不处理」，靠一枚**归属变量**判定。而改前的 `ctx.vars` 是**实例内存**：
实例一消亡（开机实例 done）就失忆，容器一重启更是什么都不剩 ⇒ 关机段要么永远不动、要么把
出资人手动开的空调也关掉（正好违反那条铁律）。规格 §二洞6 直接点名：**必须持久化**。

**修法**（⛔ 不新建库/目录，复用既有运行事实旁车 `<archive>.runtime.json`）：
`VarDecl.persist`（IR 可选键，默认 false＝语义不变）→ `set` 写穿 `vars.<名>` →
新实例 `spawn` 先读回那份事实再执行。类型强转仍只有一处真源（`_coerce_declared`）。

**牙**（群规⑧）：J2 是"真的落盘"（正腿），J3/J4 是**新 Runtime**（＝容器重启后重新装载）读回，
⛔ 不是同一实例里的自我复读；J5 是反腿（没声明 persist 的变量⛔ 不许顺手落盘，防隐式状态膨胀）；
J6 钉坏值 fail-soft 必须**留痕**（⛔ 静默落回）；J9 钉写失败⛔ 不许打断执行链。
变异自证见报告：删 `set` 写穿 ⇒ J2 红；删 spawn 读回 ⇒ J3/J4 红。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from autoforge import af_service as svc
from autoforge.af_ir import Graph, IRValidationError, load_automation
from autoforge.af_runtime import build_runtime
from autoforge.af_spec import compile_spec, render_spec

#: 两条变量：`ac_owned`（v7 归属，持久）＋ `scratch`（普通实例变量，⛔ 不落盘）。
IR: dict = {
    "ir_version": "0.2.1",
    "id": "a_persist",
    "name": "AF746 持久变量",
    "version": 1,
    "mode": "single",
    "vars": {
        "ac_owned": {"type": "boolean", "value": False, "persist": True},
        "scratch": {"type": "string", "value": "x"},
    },
    "nodes": [
        {"id": "a1", "kind": "on", "name": "有人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.motion"}},
        {"id": "s1", "kind": "set", "name": "记归属", "var": "ac_owned", "value": True},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [
        {"from": "a1", "to": "s1", "kind": "then"},
        {"from": "s1", "to": "p1", "kind": "then"},
    ],
}


def _rt(root: Path):
    """新建 Runtime（store_root 打开运行事实落盘 ⇒ 持久变量接线启用）。"""
    return build_runtime(Graph([load_automation(IR)]), store_root=str(root))


def _facts_path(root: Path) -> Path:
    """运行事实旁车路径：归档名 == 自动化 id（项目既定不变量）。"""
    return Path(root) / "a_persist.runtime.json"


def _fire(rt) -> None:
    from autoforge.af_bus import BusEvent

    rt.publish(BusEvent.of("binary_sensor.motion", "on"))


# ── J1 声明面：persist 可读，schema 只放宽这一枚键（⛔ 不做成"随便加键都收"）──────

def test_j1_decl_parses_persist_and_schema_still_rejects_unknown_keys():
    auto = load_automation(IR)
    assert auto.vars["ac_owned"].persist is True, "persist 没解析出来 ⇒ 后面全都白谈"
    assert auto.vars["scratch"].persist is False, "缺省必须为 false（旧 IR 语义不变）"

    bad = deepcopy(IR)
    bad["vars"]["ac_owned"]["persistt"] = True  # 手滑键
    with pytest.raises(IRValidationError):
        load_automation(bad)


# ── J2 set 真落盘（正腿；同时反腿：没声明 persist 的变量⛔ 不许落）────────────

def test_j2_set_node_writes_through_to_runtime_facts(tmp_path):
    rt = _rt(tmp_path)
    _fire(rt)

    path = _facts_path(tmp_path)
    assert path.is_file(), f"持久变量没落盘（找不到旁车 {path}）"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["vars"]["ac_owned"] is True, f"set 没写穿：{data.get('vars')}"
    assert isinstance(data["vars_updated_at"]["ac_owned"], str), (
        "归属改变时刻必须留痕（排查'手动开的空调为什么被关'只有当前值不够）"
    )
    assert "scratch" not in data["vars"], "没声明 persist 的变量被顺手落盘了（隐式状态膨胀）"


# ── J3 新 Runtime（＝容器重启后重新装载）能读回 ──────────────────────────────

def test_j3_persist_var_survives_a_new_runtime(tmp_path):
    rt = _rt(tmp_path)
    _fire(rt)

    rt2 = _rt(tmp_path)  # 全新 Runtime：等价于重启后重新装载
    inst = rt2.instances.spawn(load_automation(IR))

    assert inst.ctx.vars["ac_owned"] is True, "重启后归属丢了 ⇒ v7 关机段会失忆"
    assert inst.ctx.context.get("persist_vars_loaded") == ["ac_owned"], (
        f"没有'从盘上读回了什么'的正例证据：{inst.ctx.context.get('persist_vars_loaded')!r}"
    )


# ── J4 清除也要持久（归属闭环：AC→off 任何来源都清，且清完不许回潮）─────────────

def test_j4_clearing_the_flag_is_persisted_too(tmp_path):
    rt = _rt(tmp_path)
    _fire(rt)
    inst = rt.instances.all()[0]
    rt.instances.set_var(inst, "ac_owned", False)

    rt2 = _rt(tmp_path)
    inst2 = rt2.instances.spawn(load_automation(IR))
    assert inst2.ctx.vars["ac_owned"] is False, (
        "清除没落盘 ⇒ 下次重启又把归属读回来，手动开的空调会被误关"
    )


# ── J5 反腿：没声明 persist 的变量仍是实例内存（语义不变）──────────────────

def test_j5_non_persist_var_stays_instance_memory(tmp_path):
    rt = _rt(tmp_path)
    inst = rt.instances.spawn(load_automation(IR))
    rt.instances.set_var(inst, "scratch", "hello")
    assert inst.ctx.vars["scratch"] == "hello"

    rt2 = _rt(tmp_path)
    inst2 = rt2.instances.spawn(load_automation(IR))
    assert inst2.ctx.vars["scratch"] == "x", "普通变量不该跨实例存活（否则既有 IR 语义被改）"


# ── J6 盘上坏值：fail-soft 落回声明初值，且必须留痕（⛔ 静默）──────────────

def test_j6_corrupt_persisted_value_falls_back_with_a_trace(tmp_path):
    _facts_path(tmp_path).write_text(
        json.dumps({"schema": 1, "archive": "a_persist", "vars": {"ac_owned": "maybe"}}),
        encoding="utf-8",
    )
    rt = _rt(tmp_path)
    inst = rt.instances.spawn(load_automation(IR))

    assert inst.ctx.vars["ac_owned"] is False, "坏值必须落回声明初值，⛔ 不许带病当 True 用"
    err = str(inst.ctx.context.get("persist_vars_error") or "")
    assert "coerce:ac_owned" in err, f"落回要留痕（读回执/实例快照能看见）：{err!r}"


# ── J7 读面：既有列表卡片要能看见持久变量（⛔ 只能进容器翻文件）─────────────

def test_j7_list_card_exposes_persisted_vars(tmp_path):
    rt = _rt(tmp_path)
    _fire(rt)

    fields = svc._runtime_facts_fields(svc.GraphStore(str(tmp_path)), "a_persist")
    assert fields["vars"].get("ac_owned") is True, f"列表读面读不到归属：{fields.get('vars')}"


# ── J8 AF-Spec 往返不许丢 persist（否则人工编辑一次就静默降级成内存变量）──────

def test_j8_spec_roundtrip_keeps_persist():
    text = render_spec(Graph([load_automation(IR)]))
    assert '"persist": true' in text, f"spec 文本没带 persist：\n{text}"

    again = list(compile_spec(text))[0]
    assert again.vars["ac_owned"].persist is True


# ── J9 写失败⛔ 不许打断执行链，但必须留痕 ────────────────────────────────

def test_j9_write_failure_does_not_break_execution(tmp_path):
    rt = _rt(tmp_path)

    def _boom(archive, name, value):
        raise OSError("disk full (判据桩)")

    rt.instances.var_writer = _boom
    _fire(rt)

    inst = rt.instances.all()[0]
    assert inst.ctx.vars["ac_owned"] is True, "内存值必须照常生效（写盘失败≠赋值失败）"
    err = str(inst.ctx.context.get("persist_vars_error") or "")
    assert "write:ac_owned" in err, f"写盘失败必须留痕，⛔ 不许静默丢状态：{err!r}"

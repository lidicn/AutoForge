# -*- coding: utf-8 -*-
"""conf 分级引擎（影子/金丝雀）端到端验收 harness（v2.6，2026-10-22 出资人指示第 3 条）。

**这是"受控绕过"的验收，不是闸门弱化**：低置信（0.60–0.85）带设备写的 IR 会被 G2 静态门
（`af_scanner.py::_check_shadow` 的 SHADOW_WRITES_DEVICE ERROR）拒绝——那是设计（编译期与
运行期 band 对齐），本 harness 原样保留该门，不走 StaticScanner/af_save/forge watch，
直接 `load_graph` + `af_runtime_ext.install` 在内存里复现运行期 band 路由的全链路：

    conf 0.7 → 影子档拦截（不下发）→ 只读比对（MISSED/MATCHED 落账）
    → streak 3 次 → 晋升 auto + 挂 canary → 观察期满 → 转正 strip → 真下发放行

登记在册、**不在本 harness 射程**的缺口（问题册 gap #10）：
- SSE 订阅（grading.observe）生产面未接线 → InterventionDetector 收不到状态事件；
- af_watch 聚合器活在 forge watch 子进程，serve 进程读不到（/api/evidence/prod 恒空）。
本 harness 的 verdict 记账走 runner 内存与 shadow_log，两条缺口都不影响下面的断言。

用法：
    PYTHONPATH=src python scripts/verify_conf_grading.py        # 合并前（worktree）
    docker exec autoforge-api python scripts/verify_conf_grading.py   # 发布后（镜像内）

全程 FakeHA/内存/临时目录，不连真 HA、不写 /data、不碰真设备。每步打印实测值，
FAIL 即退出码 1——缺项不许读成绿（verify_adm_window 口径）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

# 镜像内 autoforge 装在 site-packages；worktree 里靠 PYTHONPATH=src。两处都能走。
from autoforge.af_canary_supervisor import CanaryPolicy, DEFAULT_CANARY_SPEC
from autoforge.af_cli import build_runtime  # noqa: F401  (re-export 面，真实 import 在下面)
from autoforge.af_ir import load_graph
from autoforge.af_runtime import build_runtime as _build_runtime
from autoforge.af_runtime_ext import install
from autoforge.af_shadow import ShadowPolicy
from autoforge.af_vhass.fake import FakeHAAdapter, seed_from_graph

FAILED: list[str] = []


def check(step: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {step}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILED.append(step)


def main() -> int:
    # ── 手工 IR：confidence=0.7 + turn_on do（正是 G2 静态门会拦的形状）──────────
    # ir_version/version 必填（af_ir.validate_automation 的 root schema）
    IR = {"ir_version": "0.2.1", "version": 1, "automations": [{
        "id": "verify_shadow_auto",
        "name": "验收影子",
        "confidence": 0.7,
        "ir_version": "0.2.1",
        "version": 1,
        "mode": "restart",
        "nodes": [
            {"id": "a1", "kind": "on",
             "trigger": {"type": "state", "entity_id": "binary_sensor.motion", "to": "on"}},
            {"id": "d1", "kind": "do", "adapter": "ha", "action": "turn_on",
             "params": {"entity_id": "light.study_main"}},
        ],
        "edges": [{"from": "a1", "to": "d1", "kind": "then"}],
    }]}
    graph = load_graph(IR)

    # 1) runtime + FakeHA（复刻 af_cli._make_runtime 仿真链路：四处 states 同步赋值 + FakeHAAdapter）
    # persist_dir 指进程临时目录：install 从 runtime.persist_dir 取 persist 路径（不碰 /data）
    tmp = tempfile.mkdtemp(prefix="conf-grading-verify-")
    runtime = _build_runtime(graph, persist_dir=tmp)  # VirtualTimeSource，conf.seed → 0.7 → band shadow
    ha = seed_from_graph(graph, {"binary_sensor.motion": "off", "light.study_main": "off"},
                         clock=runtime.clock)
    runtime.states = ha
    runtime.instances.states = ha
    runtime.scheduler.states = ha
    runtime.executor.states = ha
    adapter = FakeHAAdapter(ha)
    runtime.adapters.register(adapter)  # 覆盖默认 HAAdapter(dry_run=True) 的 "ha" 槽位

    # 2) 挂分级引擎（缩短 compare/streak/观察期——生产 dataclass 默认是 300s/3/24h，
    #    无 env 旋钮，验收走 install kwargs）
    grading = install(
        runtime,
        shadow_policy=ShadowPolicy(compare_after=1.0, streak_to_promote=3),
        canary_policy=CanaryPolicy(observation_hours=0.001),  # 3.6s 观察期
    )

    aid = "verify_shadow_auto"
    target = next(a for a in graph.automations if a.id == aid)
    node = target.nodes["d1"]

    # emit 的去重键是 (entity_id, state, last_changed)：同状态重复 emit 会命中 DUPLICATE
    # （去重集按 ~60s 周期修剪，虚拟时钟走不到）。每次带唯一 last_changed = 真实 HA 的
    # 每次状态变更都带新 last_changed，语义等价。
    _tick_n = [0]

    def _fire(state: str) -> int:
        _tick_n[0] += 1
        return len(runtime.emit("binary_sensor.motion", state,
                                last_changed=f"2026-09-14T08:00:{_tick_n[0]:02d}+00:00"))

    print("== 1. 影子档（conf 0.7）==")
    conf_val = runtime.conf.get(aid)
    band = grading.shadow.band_of(aid)
    check("conf 种子 = 0.7", abs(conf_val - 0.7) < 1e-9, f"conf={conf_val}")
    check("band = shadow", band == "shadow", f"band={band}")

    print("== 2. 影子档不下发 ==")
    n = _fire("on")  # 触发实例（P1-12：emit 先 set_state 再 publish）
    check("实例被触发", n >= 1, f"fired={n}")
    check("adapter 未被调用", adapter.calls == [], f"calls={adapter.calls}")

    print("== 3. 只读比对落账（MISSED / MATCHED 双向）==")
    # 目标灯仍是 off → 期望 on 落空 → MISSED（单写者 turn_on 在影子档的经典形状）
    runtime.advance(2.0)  # compare_after=1.0 → 下一次 tick 驱动 scheduler._run_delayed → compare
    recs = grading.shadow.log.dump()
    missed = [r for r in recs if r.get("verdict") == "missed"]
    check("MISSED 判定落账", len(missed) >= 1, f"verdicts={[r.get('verdict') for r in recs]}")
    conf_after_miss = runtime.conf.get(aid)
    check("MISSED 扣分（-0.02）", conf_after_miss < 0.7, f"conf={conf_after_miss}")

    # 现在让期望态成真：外部把灯翻到 on（影子档不下发，设备由别的手翻转）
    ha.set("light.study_main", "on")
    _fire("off")
    _fire("on")  # 新实例 → 新影子记录 → 期望 on 已成真
    runtime.advance(2.0)
    recs = grading.shadow.log.dump()
    matched = [r for r in recs if r.get("verdict") == "matched"]
    check("MATCHED 判定落账", len(matched) >= 1, f"verdicts={[r.get('verdict') for r in recs]}")

    print("== 4. streak 3 次 → 晋升 auto + 挂 canary ==")
    # 再来两轮 MATCHED（streak_to_promote=3，MISSED 已断过一次 streak）
    for _ in range(2):
        _fire("off")
        _fire("on")
        runtime.advance(2.0)
    conf_now = runtime.conf.get(aid)
    band_now = grading.shadow.band_of(aid)
    check("conf 晋升 = 1.0", abs(conf_now - 1.0) < 1e-9, f"conf={conf_now}")
    check("band = auto", band_now == "auto", f"band={band_now}")
    rec = grading.canary.record(aid)
    check("canary 进入观察期", rec is not None and rec.status == "observing",
          f"status={getattr(rec, 'status', None)}")
    check("do 节点 strip canary", getattr(node, "canary", None) is None,
          f"canary={getattr(node, 'canary', None)}")

    print("== 6. 转正后真下发放行（与步骤 2 闭环）==")
    calls_before = len(adapter.calls)
    _fire("off")
    _fire("on")
    check("adapter 被调用（band 路由放行）", len(adapter.calls) > calls_before,
          f"calls={adapter.calls}")

    print("== 7. 持久化形状（/data 形状预演）==")
    grading.persist()
    names = ["shadow_log.json", "feedback.json", "intervention.json",
             "canary_state.json", "proposals.json"]
    for name in names:
        path = os.path.join(tmp, name)
        ok = os.path.exists(path)
        parsed = None
        if ok:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    parsed = json.load(fh)
            except ValueError:
                ok = False
        check(f"{name} 存在且可解析", ok and parsed is not None)

    print()
    if FAILED:
        print(f"CONF-GRADING VERIFICATION FAILED：{len(FAILED)} 项 -> {FAILED}")
        return 1
    print("CONF-GRADING VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        print("CONF-GRADING VERIFICATION FAILED：harness 自身异常（见上方 traceback）")
        sys.exit(1)

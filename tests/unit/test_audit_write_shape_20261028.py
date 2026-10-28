"""audit_write 形状归一（生产崩溃 2026-10-28 体验者预演现场）。

根因链：conf 分级（AUTOFORGE_CONF_GRADING=1）开着时 ShadowRunner 拦截动作 →
audit_write 把**裸 dict** 塞进 AuditLog.add → runtime.audit 混型 →
simulate 返回序列化 `[e.to_dict() for e in runtime.audit]` 在 dict 上炸
`'dict' object has no attribute 'to_dict'`。只有"事件匹配触发器→动作真执行"的
路径才走 audit_write ⇒ 体验者报的「seed/events 任一参数就崩」实际是
「不带注入触发时图根本不执行动作」——它对"没注入触发≠IR 错"的判断也是对的。

钉三条：
1. 真 AuditLog 收到的必须是 AuditEvent（混型 = 生产崩溃复现）；
2. 测试替身（FakeAudit 契约=收 dict）不破；
3. 字段归一：kind→type、杂字段进 data、at 接受 datetime/ISO 字符串。
"""

from __future__ import annotations

from datetime import datetime, timezone

from autoforge.af_audit import AuditEvent, AuditLog
from autoforge.af_feedback import audit_write


def test_real_audit_log_receives_audit_event_not_dict():
    audit = AuditLog()
    audit_write(audit, at=datetime.now(timezone.utc), kind="shadow_intercepted",
                automation_id="a", instance_id="i", node_id="n",
                action="climate.turn_on", params={"entity_id": "climate.ac"},
                record_id="r", adapter_called=False)
    assert all(isinstance(e, AuditEvent) for e in audit), "混型 = 生产崩溃复现"
    # 序列化路径（生产崩点）必须活着
    out = [e.to_dict() for e in audit]
    assert out[-1]["type"] == "shadow_intercepted"
    assert out[-1]["data"]["params"] == {"entity_id": "climate.ac"}


def test_fake_audit_dict_contract_not_broken():
    """conftest.FakeAudit 的契约是收 dict（dict(entry) 可迭代）——替身消费者不许破。"""

    class FakeAudit:
        def __init__(self):
            self.entries = []

        def append(self, entry):
            self.entries.append(dict(entry))
            return entry

    fake = FakeAudit()
    ret = audit_write(fake, at=datetime.now(timezone.utc), kind="canary_started",
                      automation_id="a")
    assert isinstance(ret, dict), "返回值保持 dict 兼容（调用方有读返回的）"
    assert fake.entries[-1]["kind"] == "canary_started"


def test_field_normalization_kind_to_type_and_at_string():
    audit = AuditLog()
    audit_write(audit, at="2026-10-28T00:00:00+00:00", kind="canary_drift",
                automation_id="a", verdict="x")
    e = list(audit)[-1]
    assert isinstance(e.at, datetime) and e.type == "canary_drift"
    assert e.data == {"verdict": "x"}


def test_mixed_real_sequence_end_to_end_like_production():
    """端到端仿生产：AuditEvent（runtime 自己 add 的）+ audit_write 条目混在一个 log，
    全量 to_dict 必须全活——这就是生产崩的那行代码。"""
    audit = AuditLog()
    audit.add(AuditEvent(type="conf_grading_enabled", at=datetime.now(timezone.utc),
                         message="conf grading enabled"))
    audit_write(audit, at=datetime.now(timezone.utc), kind="shadow_intercepted",
                automation_id="a", action="climate.turn_on")
    audit_write(audit, at=datetime.now(timezone.utc), kind="canary_promoted",
                automation_id="a")
    out = [e.to_dict() for e in audit]
    assert len(out) == 3 and all(isinstance(o, dict) for o in out)
    assert [o["type"] for o in out] == [
        "conf_grading_enabled", "shadow_intercepted", "canary_promoted"]

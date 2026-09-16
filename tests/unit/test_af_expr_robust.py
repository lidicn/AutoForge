"""`not_is_on` / `not_is_off` 守卫运算符：状态缺失（漂移）时仍执行同步动作。

真机场景（2026-09-15）：连按后 HA 状态读取间歇失败 → 旧 `is_off`/`is_on` 抛
UnknownEntity 被 `if` 节点软失效 → 合法开关同步被静默丢弃。新运算符把未知视为
"需要动作"，避免自伤且不引入交叉触发死循环（语义与 is_off/is_on 一致，仅对未知更宽容）。
"""
import pytest
from autoforge.af_ir.expr import evaluate
from autoforge.af_state import UnknownEntity


def _resolver(values):
    def resolve(name, declared):
        if name.startswith("entity."):
            eid = name[len("entity."):]
            if eid not in values:
                raise UnknownEntity(eid)
            return values[eid]
        raise UnknownEntity(name)
    return resolve


def test_not_is_on_true_when_off():
    assert evaluate({"op": "not_is_on", "value": {"var": "entity.lamp"}}, _resolver({"lamp": "off"})) is True


def test_not_is_on_false_when_on():
    # 已是 on → 无需动作（仍需防交叉触发死循环）
    assert evaluate({"op": "not_is_on", "value": {"var": "entity.lamp"}}, _resolver({"lamp": "on"})) is False


def test_not_is_on_true_when_unknown():
    # 关键：状态缺失（漂移）→ 视为需要动作，不软失效
    assert evaluate({"op": "not_is_on", "value": {"var": "entity.lamp"}}, _resolver({})) is True


def test_not_is_off_true_when_on():
    assert evaluate({"op": "not_is_off", "value": {"var": "entity.lamp"}}, _resolver({"lamp": "on"})) is True


def test_not_is_off_false_when_off():
    assert evaluate({"op": "not_is_off", "value": {"var": "entity.lamp"}}, _resolver({"lamp": "off"})) is False


def test_not_is_off_true_when_unknown():
    assert evaluate({"op": "not_is_off", "value": {"var": "entity.lamp"}}, _resolver({})) is True


def test_old_is_off_still_raises_on_unknown():
    # 旧运算符保持严格语义（未知即异常），确保改动不破坏既有行为
    with pytest.raises(UnknownEntity):
        evaluate({"op": "is_off", "value": {"var": "entity.lamp"}}, _resolver({}))

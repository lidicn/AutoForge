"""收件箱名单/字段表的**派生**判据（计划 §七 卡1，契约 §1.3；0.3.2 规格 §三.1"谁定 schema 谁把校验"）。

卡1 的载荷 schema 有唯一真源：`homesdk.presence`。AF 侧任何"字段名清单""动作名单""长度上限"
只要手抄第二份，就会在库侧改动的下一次静默失配——本批前面就踩过一次（出向事件载荷多发一个
`node_id`，逐字段对契约的测试一直绿，因为测的是另一条路径）。所以这里不测"抄得对不对"，
测的是**根本没抄**：AF 的名单与字段表必须与库侧的派生结果恒等。

`_len_bounded` / `ts` 口径之类不在这里重测（那是库侧的职责，`test_af_homesdk_time.py` 与
`test_af_mqtt_bridge.py` 已按合同判定）；本文件只管"AF 有没有留下第二份可漂移的表"。
"""
from __future__ import annotations

import inspect

import pytest

from autoforge import af_mqtt_bridge
from autoforge.af_adapters.base import L0_READONLY, L2_RISKY, L3_DANGEROUS, classify_action

# 取桥**自己引用的那份** `_presence`：这里要证的是"AF 读的表 = AF 用的表"，
# 另起一条 import 路径会在库侧重命名时把红落在错的地方。
from autoforge.af_mqtt_bridge import (
    _INBOX_INTERNAL_ARGS,
    FORBIDDEN_SUBSCRIPTIONS,
    INBOX_KINDS,
    INBOX_PREFIX,
    _inbox_fields,
    _presence,
)


def test_kind_list_is_the_registered_topic_suffixes():
    """名单 = `INBOX_TOPICS` 去掉前缀，不是 AF 自己点名的三个字符串。"""
    assert INBOX_KINDS == frozenset(t[len(INBOX_PREFIX):] for t in _presence.INBOX_TOPICS)


def test_prefix_is_not_drifted_into_an_empty_list():
    """反空洞：前缀写错时派生名单会**静默变空**，门就永远"名单里没有可投的动作"。

    这条腿只认一个事实：库侧登记的每个主题都真的以 AF 的前缀开头，所以派生不丢项。
    """
    assert _presence.INBOX_TOPICS and all(t.startswith(INBOX_PREFIX) for t in _presence.INBOX_TOPICS)
    assert len(INBOX_KINDS) == len(_presence.INBOX_TOPICS)


def test_every_kind_has_a_callable_publisher():
    """主题登记了但函数缺席 ⇒ 投不出去。AF 判的是 `ADM_ERR_INTERNAL`，不是猜一个载荷形态硬发。"""
    for kind in sorted(INBOX_KINDS):
        assert callable(getattr(_presence, kind, None)), kind


@pytest.mark.parametrize("kind", sorted(INBOX_KINDS))
def test_field_table_equals_the_library_signature(kind):
    """必填/可选键**逐位**等于库侧签名的派生结果（顺序也要一致：位置参数按它铺）。"""
    func = getattr(_presence, kind)
    required: list[str] = []
    optional: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        if name in _INBOX_INTERNAL_ARGS or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        (optional if param.default is not inspect.Parameter.empty else required).append(name)

    assert _inbox_fields(kind) == (tuple(required), tuple(optional)), kind


def test_mechanism_args_are_not_open_to_ir_authors():
    """`client`/`trace_id`/`qos` 是机制层入参：开放给 IR 作者就等于允许伪造事件号或降 QoS。"""
    assert {"client", "trace_id", "qos"} == set(_INBOX_INTERNAL_ARGS)
    for kind in sorted(INBOX_KINDS):
        required, optional = _inbox_fields(kind) or ((), ())
        assert not (set(required) | set(optional)) & _INBOX_INTERNAL_ARGS, kind


def test_inbox_domain_is_classified_l0_and_unknown_domain_is_not():
    """风险分级：`inbox` 走 L0（只投递、播不播由 DB 的 Sentinel 判）。

    后半句是这条腿的真判据：**未知 domain 的缺省档仍是 L2**。留在缺省档时
    `af_scanner` 会对每个 inbox 节点报 `L2_NEEDS_CONFIRM`/`L2_NEEDS_CANARY`（ERROR），
    于是 `do x inbox.speak {…}` 连编译都过不去——卡1 的验收自己就红了。
    反例（未登记的 domain）必须继续判 L2，否则等于把缺省档整体调松。
    """
    for kind in sorted(INBOX_KINDS):
        assert classify_action("inbox", f"inbox.{kind}") == L0_READONLY, kind
        assert classify_action("inbox", kind) == L0_READONLY, kind
    assert classify_action("sms", "sms.send") == L2_RISKY


def test_inbox_is_not_l3_even_when_action_names_look_destructive():
    """`is_destructive` 的删除类关键字仍优先于 domain 表：这是**该拒的**（L3 要人工确认）。

    记下这条不是找 bug，是钉住"域表 L0 不能盖掉删除类判定"的优先级——反过来把它放宽，
    `inbox.delete_all` 就会从人工确认掉到免确认投递。
    """
    assert classify_action("inbox", "inbox.remove_everything") == "L3"


def test_subscription_guard_and_publish_share_one_prefix():
    """投递前缀与拒订前缀必须是同一枚常量，不能两处各抄一遍字面量。

    抄两遍时改一处会得到"AF 往 `butler/inbox/…` 发，却按另一个前缀拒订"：护栏在代码里看着
    还在，实际管不到自己发出去的那一族——正是契约 §7.3 要消灭的那类静默。
    """
    assert f"{INBOX_PREFIX}#" in FORBIDDEN_SUBSCRIPTIONS
    assert f"{INBOX_PREFIX}*" in FORBIDDEN_SUBSCRIPTIONS
    for topic in _presence.INBOX_TOPICS:
        assert topic in FORBIDDEN_SUBSCRIPTIONS, topic

    # 前缀字面量在桥模块里只许出现一次（就是 `INBOX_PREFIX` 的定义）。needle 是拼出来的，
    # 写死就等于本测试自己成了第二份抄本。
    occurrences = inspect.getsource(af_mqtt_bridge).count('"' + INBOX_PREFIX + '"')
    assert occurrences == 1, occurrences

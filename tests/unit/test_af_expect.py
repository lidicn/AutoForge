"""v1.2 / v2 M4 af_expect 状态语义单测（复用既有模块，验证 pass/fail/unverified）。

M4 的三栏映射建立在 expect 的三态之上：verified←pass/fail，non_simulable←unverified。
本文件锁定该基础语义不被回归破坏。evaluate_expects 只依赖 `auto.expects()`，
故用最小假对象承载 expects，避免触发 schema 校验（与 M4 关注的语义解耦）。
"""

from __future__ import annotations

from autoforge.af_expect import evaluate_expects


class _Auto:
    def __init__(self, expects):
        self._expects = expects

    def expects(self):
        return self._expects


class _States:
    def __init__(self, data: dict):
        self._d = data

    def get(self, eid):
        return self._d.get(eid)

    def snapshot(self, ids):
        class S:
            attributes = {e: {"x": self._d.get(e)} for e in ids}

        return S()


def test_expect_pass():
    rep = evaluate_expects(_Auto([{"entity_id": "light.a", "state": "on"}]), _States({"light.a": "on"}))
    assert rep["items"][0]["status"] == "pass"


def test_expect_fail():
    rep = evaluate_expects(_Auto([{"entity_id": "light.a", "state": "on"}]), _States({"light.a": "off"}))
    assert rep["items"][0]["status"] == "fail"


def test_expect_unverified_when_entity_absent():
    # 实体不在状态源 → 不可验证（unverified），绝不编值
    rep = evaluate_expects(_Auto([{"entity_id": "light.a", "state": "on"}]), _States({}))
    assert rep["items"][0]["status"] == "unverified"

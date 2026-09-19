"""WO-AF-001 + WO-AF-002 回归测试。

WO-AF-001：classify_answer 收口到 homesdk.consent，unknown 映射到 no。
WO-AF-002：ask 应答两条入口收敛成 resolve_ask，ask_id 不匹配绝不 fallback。
"""

from unittest.mock import MagicMock

import pytest

from autoforge.af_executor import AskSession, NodeExecutor, classify_answer


# ── WO-AF-001：classify_answer 回归 ──

class TestClassifyAnswerHomesdk:
    """同意判定收口到 homesdk.consent。"""

    @pytest.mark.parametrize("text", ["不要", "别开", "不好", "不要创建", "关掉它", "", "不可"])
    def test_no_cases(self, text):
        assert classify_answer(text) == "no"

    @pytest.mark.parametrize("text", ["好的", "好的，打开吧", "是", "开", "yes", "确认"])
    def test_yes_cases(self, text):
        assert classify_answer(text) == "yes"

    def test_unknown_maps_to_no(self):
        """homesdk 返回 unknown 时，AF 映射到 no（收紧，不走 default 放行）。"""
        assert classify_answer("关掉它") == "no"

    def test_no_local_word_tables(self):
        """物理删除本地词表，不是注释掉。"""
        import autoforge.af_executor as mod
        assert not hasattr(mod, "_YES_WORDS")
        assert not hasattr(mod, "_NO_WORDS")


# ── WO-AF-002：resolve_ask 回归 ──

def _make_executor():
    """创建 NodeExecutor，instances 用 mock（resolve_ask 不碰 instances）。"""
    executor = NodeExecutor(instances=MagicMock(), adapters=None, states=MagicMock())
    return executor


class TestResolveAsk:
    """ask_id 精确匹配，不匹配绝不 fallback。"""

    def test_ask_id_exact_match(self):
        executor = _make_executor()
        executor.pending_asks["inst-1"] = AskSession(
            instance_id="inst-1", node_id="q1", room="study", created_at=1.0, prompt="开吗？"
        )
        result = executor.resolve_ask("inst-1", "study")
        assert result is not None
        assert result.instance_id == "inst-1"

    def test_ask_id_mismatch_returns_none_no_fallback(self):
        """ask_id 给了却不命中 → None，绝不 fallback 到同房间最旧。"""
        executor = _make_executor()
        executor.pending_asks["inst-1"] = AskSession(
            instance_id="inst-1", node_id="q1", room="study", created_at=1.0, prompt="开吗？"
        )
        result = executor.resolve_ask("不存在的ID", "study")
        assert result is None

    def test_ask_id_none_falls_back_to_room_oldest(self):
        """ask_id 为 None 时才允许按 room 取最旧。"""
        executor = _make_executor()
        executor.pending_asks["inst-1"] = AskSession(
            instance_id="inst-1", node_id="q1", room="study", created_at=2.0, prompt="开阀吗？"
        )
        executor.pending_asks["inst-2"] = AskSession(
            instance_id="inst-2", node_id="q2", room="study", created_at=1.0, prompt="开灯吗？"
        )
        result = executor.resolve_ask(None, "study")
        assert result is not None
        assert result.instance_id == "inst-2"  # 最旧

    def test_empty_pending_returns_none(self):
        """pending_asks 为空时不抛异常，返回 None。"""
        executor = _make_executor()
        result = executor.resolve_ask("any-id", "study")
        assert result is None


class TestAnswerWithResolveAsk:
    """answer() 走 resolve_ask，ask_id 不匹配不唤醒任何实例。"""

    def test_answer_wrong_ask_id_returns_none_no_resume(self):
        """ask_id 不匹配 → 返回 None，resume 不被调用。"""
        executor = _make_executor()
        executor.pending_asks["inst-1"] = AskSession(
            instance_id="inst-1", node_id="q1", room="study", created_at=1.0, prompt="开吗？"
        )
        executor.resume = MagicMock()
        executor.instances.get.return_value = None  # 即使有实例也不该被取到

        result = executor.answer(room="study", text="好", ask_id="不存在的ID")
        assert result is None
        executor.resume.assert_not_called()

    def test_answer_correct_ask_id_resumes_only_that_instance(self):
        """两个挂起 ask 同房间，传第一个的 ask_id → 只有第一个被唤醒。"""
        executor = _make_executor()
        executor.pending_asks["inst-1"] = AskSession(
            instance_id="inst-1", node_id="q1", room="study", created_at=1.0, prompt="开阀吗？"
        )
        executor.pending_asks["inst-2"] = AskSession(
            instance_id="inst-2", node_id="q2", room="study", created_at=2.0, prompt="开灯吗？"
        )
        mock_inst1 = MagicMock()
        mock_inst2 = MagicMock()
        executor.instances.get.side_effect = lambda iid: mock_inst1 if iid == "inst-1" else mock_inst2
        resumed = []
        executor.resume = MagicMock(side_effect=lambda inst, kind: (resumed.append(inst), inst)[1])

        result = executor.answer(room="study", text="好", ask_id="inst-1")
        assert result is mock_inst1
        assert resumed == [mock_inst1]
        # 第二个仍挂起
        assert "inst-2" in executor.pending_asks

    def test_answer_empty_pending_no_exception(self):
        """传 ask_id 且 pending_asks 为空 → 不抛异常，返回 None。"""
        executor = _make_executor()
        executor.resume = MagicMock()
        result = executor.answer(room="study", text="好", ask_id="inst-1")
        assert result is None
        executor.resume.assert_not_called()

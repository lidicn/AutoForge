"""v1.5.0 错误知识库单测（调研 §2.11：有序正则 + 类别化建议 + 有界 500）。"""

from autoforge.af_error_knowledge import (
    CATEGORY_ADVICE,
    KNOWN_CATEGORIES,
    ErrorKnowledge,
    classify,
    explain,
)


def test_first_match_wins():
    """有序正则：更具体的「白名单」规则排前，不应被泛化的 IR_SEMANTIC 抢走。"""
    msg = "出站主机 x 不在白名单内（IR 未通过静态扫描）"
    assert classify(msg) == "HTTP_NOT_WHITELISTED"


def test_upstream_not_blaming_agent():
    """兜底口径：宁可归上游/网关，不冤枉 agent。"""
    assert classify("连接 HA 超时：ConnectionError") == "UPSTREAM_HA"
    assert classify("上游 502 Bad Gateway") == "GATEWAY_HTTP"


def test_ir_schema_and_semantic():
    assert classify("IR 校验失败：edges/0: 'kind' is a required property") == "IR_SCHEMA"
    assert classify("拒绝归档") == "IR_SEMANTIC"


def test_every_category_has_advice():
    """精确类别**必非空**建议（不落 other）。"""
    for cat in KNOWN_CATEGORIES:
        assert CATEGORY_ADVICE[cat].strip(), cat


def test_unknown_fallback_has_advice():
    info = explain("完全无特征的文本 xyzzy")
    assert info["category"] == "UNKNOWN"
    assert info["matched"] is False
    assert info["advice"]


def test_bounded_500(tmp_path):
    kb = ErrorKnowledge(tmp_path, max_records=500)
    for i in range(600):
        kb.record(f"出站主机 h{i} 不在白名单内")
    recent = kb.recent(limit=1000)
    assert len(recent) == 500  # 有界
    assert "h599" in recent[-1]["message"]  # 保留最近的


def test_similar_returns_same_category(tmp_path):
    kb = ErrorKnowledge(tmp_path)
    kb.record("引用了不存在的实体：light.x")
    kb.record("出站主机 x 不在白名单内")
    sim = kb.similar("引用了不存在的实体：light.y")
    assert sim and all(r["category"] == "ENTITY_NOT_FOUND" for r in sim)


def test_counts(tmp_path):
    kb = ErrorKnowledge(tmp_path)
    kb.record("引用了不存在的实体：a")
    kb.record("引用了不存在的实体：b")
    assert kb.counts().get("ENTITY_NOT_FOUND") == 2

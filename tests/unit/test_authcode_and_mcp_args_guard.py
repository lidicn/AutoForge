"""授权码 + MCP 参数面两道安全判据各自**必须能变红**（铁律 #8）。

审计包（`docs/audit/AutoForge安全审计报告.zip`，`fp-authcode-bruteforce`）给的根因是三件事：
6 位码熵不足、`validate()` 不消耗、MCP 面无速率限制。HEAD 上前两条早已由 `3fbbab9` 落地
（8 位、一次性 `consume`、单码失败 10 次软锁 5 分钟），但**整条修复此前没有任何测试钉住**——
`tests/` 里对 `auth_code` 与 `AuthCode` 两个词的全仓检索为空。没有反例的安全修复等于没有修复：下一个改
`validate()` 的人不会看见任何门红。

v2.6 语义修复（gap #9，2026-10-22）：一次性 `consume` 只保留给**短期码**；**长期码**回归设计口径
「不撤销一直可用」（docs/design/咨询_用户WebUI后端确认_20260924.md:37）——consume 只计
`use_count`、不清码，抗重放由高熵 + 失败窗口/软锁 + 撤销承担。历史已被 v2.5 消耗掉的
长期码不自动复活（见 `test_legacy_consumed_long_code_stays_invalid`）。

第三条是**本批才发现还活着的**：单码那层计数只在码**存在**时累加（`record_failure()` 遇未知码
直接 return），而 10⁸ 码空间的实际枚举路径恰恰是"试不存在的码"——审计侧实测吞吐 195 万次/秒，
8 位熵只把穷举从 0.51 秒推到约 51 秒，量级不够。所以补了存储级失败尝试窗口，并把"计数器就是
那道防线"本身做成反例（把阈值调到极大，拦截必须随之消失）。

`dispatch()` 的未声明参数键拒绝同理：不给反例，"拒绝"就只是注释里的一句承诺。
"""
from __future__ import annotations

import json
import re
import time

import pytest

from autoforge.af_auth import AuthCodeStore
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore

_BY_NAME = {t[0]: t for t in TOOLS}


def _text(content: list[dict]) -> str:
    return content[0]["text"]


# ── 授权码：消耗 / 锁定 / 窗口 ───────────────────────────────────────


@pytest.fixture
def acs(tmp_path) -> AuthCodeStore:
    return AuthCodeStore(tmp_path / ".auth" / "auth_codes.json")


def test_code_is_8_digits(acs):
    """审计根因第 1 条（10⁶ 可 0.5s 穷举）的回归位。"""
    code = acs.create("long").code
    assert re.fullmatch(r"[0-9]{8}", code), code


def test_validate_is_query_only(acs):
    """审计回归项 1：`validate()` 反复查询不消耗、不改状态（消耗/计数由调用方显式做）。"""
    code = acs.create("long").code
    assert all(acs.validate(code) for _ in range(5))
    assert acs._codes[code]["use_count"] == 0
    assert acs._codes[code]["consumed"] is False


def test_short_code_consume_burns_it(acs):
    """审计回归项 2（v2.5 口径对短期码保留）：短期码一次性——consume 后 validate/consume 全灭。"""
    code = acs.create("short").code
    assert acs.validate(code) is True
    assert acs.consume(code) is True
    assert acs.validate(code) is False
    assert acs.consume(code) is False


def test_long_code_is_reusable_until_revoked(acs):
    """v2.6（gap #9）：长期码不撤销一直可用——consume 只计 use_count、不清码；revoke 才是终点。"""
    code = acs.create("long").code
    for i in range(1, 4):
        assert acs.consume(code) is True, f"第 {i} 次 consume"
        assert acs.validate(code) is True, f"第 {i} 次 consume 后仍应有效"
        assert acs._codes[code]["consumed"] is False
        assert acs._codes[code]["use_count"] == i
    assert acs.revoke(code) is True
    assert acs.validate(code) is False
    assert acs.consume(code) is False


def test_long_code_use_count_persists_across_restart(acs):
    """v2.6（gap #9）：use_count 随 `_persist`/`_load` 回路跨重启存活（钉 BUG-20 读前重读口径）。"""
    code = acs.create("long").code
    assert acs.consume(code) is True
    assert acs.consume(code) is True
    revived = AuthCodeStore(acs._path)  # 同路径新实例 = 模拟重启
    rows = revived.list()
    assert len(rows) == 1
    assert rows[0]["code"] == code
    assert rows[0]["use_count"] == 2
    assert rows[0]["consumed"] is False
    assert revived.validate(code) is True


def test_legacy_consumed_long_code_stays_invalid(acs):
    """v2.6（gap #9）不迁移决策的钉子：盘上已被 v2.5 语义消耗（consumed=true）的长期码
    **不自动复活**——无法与真烧掉的码区分，部署快速通道上自动复活凭据是错误默认。"""
    legacy = {
        "code": "12345678",
        "kind": "long",
        "created_at": time.time(),
        "expires_at": time.time() + 86400,
        "revoked": False,
        "consumed": True,  # v2.5 时期被一次性 consume 烧掉的长码
        "failed_attempts": 0,
        "locked_until": None,
    }
    acs._path.parent.mkdir(parents=True, exist_ok=True)
    acs._path.write_text(json.dumps([legacy]), encoding="utf-8")
    store = AuthCodeStore(acs._path)
    assert store.validate("12345678") is False
    assert store.consume("12345678") is False


def test_per_code_lockout_after_threshold_and_expires(acs, monkeypatch):
    """审计回归项 3/4：同一枚真实码连错 10 次 → 软锁；锁过期 → 恢复。

    AF38-R 后软锁定是**双判据**（单调钟副本抗阶跃 + 盘上墙钟跨重启恢复，取更严格者）：
    模拟"锁定窗口已过"必须把两条腿都抬起来——只推墙钟，单调钟副本还在锁定期内，仍拒。
    """
    code = acs.create("long").code
    for _ in range(acs.FAILURE_THRESHOLD - 1):
        assert acs.record_failure(code) is False
    assert acs.record_failure(code) is True          # 第 10 次触发锁定
    assert acs.validate(code) is False
    rec = acs._codes[code]
    monkeypatch.setattr(time, "time", lambda: rec["locked_until"] + 1)
    acs._lockout_until_mono.pop(code, None)          # 单调钟腿也越过窗口（AF38-R）
    # AF38-R：存储级窗口基准也改单调钟——真实时间没过 60s，窗口还满着；
    # 300s 真流逝里窗口早滚完了，这里等价于"窗口已过"。
    acs._window_hits = 0
    assert acs.validate(code) is True


def test_unknown_codes_are_counted_store_wide(acs):
    """本批补的那一条：不存在的码不落单记录，但必须进存储级窗口——否则枚举路径完全不限速。"""
    real = acs.create("long").code
    guesses = [f"{i:08d}" for i in range(acs.ATTEMPT_LIMIT)]
    assert real not in guesses
    for g in guesses:
        assert acs.validate(g) is False
        assert acs.record_failure(g) is False        # 未知码：不锁"该码"，但窗口 +1
    assert acs.validate(real) is False               # 窗口已满：真实码也不给快速通道


def test_window_blocks_then_self_heals(acs, monkeypatch):
    """失败方向的裁定：超限一律回落人审队列；窗口过了自动恢复，不需人工解锁。"""
    real = acs.create("long").code
    for _ in range(acs.ATTEMPT_LIMIT):
        acs.record_failure("99999999")
    assert acs.validate(real) is False
    monkeypatch.setattr(acs, "_window_start", time.time() - acs.ATTEMPT_WINDOW_S - 1)
    acs._window_hits = 0
    assert acs.validate(real) is True


def test_the_counter_is_what_blocks(acs, monkeypatch):
    """反例（铁律 #8）：把阈值抬到窗口打不满，拦截必须随之消失——
    证明上一条的红来自计数器，不是别的偶然条件。"""
    real = acs.create("long").code
    monkeypatch.setattr(AuthCodeStore, "ATTEMPT_LIMIT", 10 ** 6)
    for _ in range(20):
        acs.record_failure("12345678")
    assert acs.validate(real) is True


def test_revoked_code_never_validates(acs):
    code = acs.create("long").code
    assert acs.revoke(code) is True
    assert acs.validate(code) is False


# ── dispatch()：未声明参数键必须被拒 ────────────────────────────────


def test_undeclared_argument_is_rejected(tmp_path):
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_health", {"probe": 1}, store)
    assert is_error is True
    assert "probe" in _text(content) and "未声明" in _text(content)
    # 拒的不是"工具不存在"，也不许把内部异常当理由
    assert "未知工具" not in _text(content)


def test_declared_arguments_still_accepted(tmp_path):
    """绿色一侧：声明过的键（含审计点名的 `allow_bulk`）照常进 handler。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_health", {}, store)
    assert is_error is False, _text(content)
    content, is_error = dispatch("af_enable_by_tag",
                                 {"tag": "nope", "enabled": True, "allow_bulk": True}, store,
                                 {"subject": "rw", "scopes": ["read", "write"]})
    assert is_error is False, _text(content)
    assert "未声明" not in _text(content)


def test_scope_guard_runs_before_argument_check(tmp_path):
    """缺 scope 的令牌先吃 scope 拒绝——未声明键的报错会把 schema 形状泄露给无权限主体。"""
    store = GraphStore(tmp_path)
    content, is_error = dispatch("af_save", {"ir": {}, "name": "x", "ghost": 1}, store,
                                 {"subject": "ro", "scopes": ["read"]})
    assert is_error is True
    assert "权限" in _text(content) and "未声明" not in _text(content)


def test_bulk_bypass_flag_is_declared_on_all_three_write_tools():
    """`allow_bulk` 是爆炸半径护栏的绕过位：它必须出现在 `tools/list` 里，不能只存在于实现。"""
    for tool in ("af_save", "af_enable_by_tag", "af_import_store"):
        props = _BY_NAME[tool][2].get("properties") or {}
        assert "allow_bulk" in props, tool

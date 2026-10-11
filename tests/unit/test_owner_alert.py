"""owner 告警线（裁定 20261011 §3 Q8.1）：三档留痕进收件箱，且这条线自己绝不反过来咬主路径。

判据分成两半各有理由：
- **行为腿**跑真 `logging` 链（`logger.error()` 一路走到 handler），因为级别门、`propagate`、
  `exc_info` 这三件事只有真派发才证得了——自造 `LogRecord` 直接调 `emit` 会让"WARNING 不告警"
  这条判据在 handler 根本没挂上时照样绿。
- **AST 腿**钉 `af_cli.serve` 里"装在 `uvicorn.run` 之前"与 `_should_alert` 的判据形状：这两格
  一旦被搬走就是安静失效（告警线还在、只是永远不会挂上），运行期任何一条腿都读不出来。
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from autoforge import af_alert
from autoforge.af_alert import (
    ALERT_MAX,
    ALERT_TTL_S,
    OwnerAlertHandler,
    _trim_alert_dedupe,
    active_owner_alert,
    install_owner_alert,
)

CLI_PATH = Path(__file__).resolve().parents[2] / "src/autoforge/af_cli.py"
SCOPE = "autoforge.pytest_alert"


class FakeBridge:
    def __init__(self, *, raise_exc: Exception | None = None, result: dict | None = None,
                 on_publish=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._raise = raise_exc
        self._result = result
        self._on_publish = on_publish

    def publish_inbox(self, kind: str, *, fields=None, dry_run: bool = False) -> dict:
        self.calls.append((kind, dict(fields or {})))
        if self._on_publish is not None:
            self._on_publish()
        if self._raise is not None:
            raise self._raise
        if self._result is not None:
            return self._result
        return {"published": True, "topic": f"butler/inbox/{kind}", "trace_id": "trace-abc"}


@pytest.fixture
def alert_logger():
    """一个 `autoforge.*` 族内的干净 logger：临时摘掉继承来的 handler，用完原样装回。"""
    lg = logging.getLogger(SCOPE)
    saved_handlers, saved_propagate = list(lg.handlers), lg.propagate
    for handler in lg.handlers:
        lg.removeHandler(handler)
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    yield lg
    for handler in lg.handlers:
        lg.removeHandler(handler)
    for handler in saved_handlers:
        lg.addHandler(handler)
    lg.propagate = saved_propagate


@pytest.fixture
def alert_logs():
    """接住 `_report` 自己写的那族 WARNING／INFO（告警线的故障面只有在这里才看得见）。"""
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(f"{record.levelname} {record.getMessage()}")

    target = logging.getLogger("autoforge.alert")
    handler = Collect()
    saved_propagate, saved_level = target.propagate, target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    yield lines
    target.removeHandler(handler)
    target.propagate = saved_propagate
    target.setLevel(saved_level)


def _attach(bridge, logger_name: str = SCOPE) -> OwnerAlertHandler:
    handler = OwnerAlertHandler(bridge, now_fn=lambda: 1_000.0)
    logging.getLogger(logger_name).addHandler(handler)
    return handler


def test_three_levels_publish_and_warning_stays_in_log(alert_logger, alert_logs):
    """ERROR／EXCEPTION／CRITICAL 各投一条；WARNING 一条都不投（三档就是 `>= ERROR` 那半轴）。"""
    bridge = FakeBridge()
    handler = _attach(bridge)
    alert_logger.error("DEVICE_DISPATCH_FAILED 下发失败")
    try:
        raise RuntimeError("链路断了")
    except RuntimeError:
        alert_logger.exception("WATCH_TICK_CRASH 心跳异常")
    alert_logger.critical("STORE_CORRUPT 存储不可读")
    alert_logger.warning("NOT_A_FAILURE 只是提醒")

    assert [kind for kind, _ in bridge.calls] == ["notify"] * 3
    assert handler.sent == 3 and handler.failed == 0
    assert "OWNER_ALERT_SENT" in alert_logs[-1]
    assert not any("NOT_A_FAILURE" in line for line in alert_logs)


def test_exception_payload_carries_the_raised_type(alert_logger):
    bridge = FakeBridge()
    _attach(bridge)
    try:
        raise ValueError("预算超了")
    except ValueError:
        alert_logger.exception("BUDGET_EXCEEDED 触发预算上限")
    title, body = bridge.calls[0][1]["title"], bridge.calls[0][1]["body"]
    assert "ValueError: 预算超了" in body and "BUDGET_EXCEEDED" in title


def test_other_logger_family_is_excluded(alert_logs):
    """挂到 root 上也只收 `autoforge` 这一族：`httpx`／`uvicorn` 的 ERROR 不该刷爆管家收件箱。

    这枚 handler 挂在 root 而不是挂在子 logger 上，是故意造的形状——只挂 SCOPE 的话，"名字门"这条
    判据在 `_should_alert` 整个删掉的情况下照样绿（记录根本走不到 handler），那就是假绿。
    """
    bridge = FakeBridge()
    handler = OwnerAlertHandler(bridge, now_fn=lambda: 1_000.0)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("httpx").error("THIRD_PARTY_BOOM 第三方库报错")
        logging.getLogger("uvicorn.error").critical("SERVER_CRASH 服务器崩了")
        logging.getLogger("autoforge.pump").error("OUR_FAILURE 本仓的故障")
    finally:
        root.removeHandler(handler)

    assert len(bridge.calls) == 1 and handler.sent == 1
    assert "OUR_FAILURE" in bridge.calls[0][1]["title"]
    assert not any("THIRD_PARTY_BOOM" in line or "SERVER_CRASH" in line for line in alert_logs)


def test_same_shape_is_merged_within_window(alert_logger):
    bridge = FakeBridge()
    handler = _attach(bridge)
    alert_logger.error("PUMP_JAM 水泵无响应")
    alert_logger.error("PUMP_JAM 水泵无响应：客厅泵 timeout=3")
    assert len(bridge.calls) == 1 and handler.suppressed == 1
    alert_logger.error("LIGHT_JAM 灯下发失败")
    assert "已合并同型告警 1 条" in bridge.calls[-1][1]["body"]


def test_dedupe_is_capped_and_expires_without_reads(alert_logger):
    """有界缓存约定的判据腿：纯写入（没人读抑制表）也会被裁回 TTL 与硬上限之内。"""
    deduped: dict[str, float] = {}
    now = 1_000.0
    for index in range(ALERT_MAX + 40):  # 只写不读
        deduped[f"key-{index}"] = now
    assert _trim_alert_dedupe(deduped, now, ALERT_TTL_S, ALERT_MAX) == 40
    assert len(deduped) == ALERT_MAX
    assert _trim_alert_dedupe(deduped, now + ALERT_TTL_S, ALERT_TTL_S, ALERT_MAX) == ALERT_MAX
    assert not deduped

    # 回收必须挂在投递路径上，而不是等某次 stats() 才被顺带做掉。
    bridge = FakeBridge()
    handler = _attach(bridge)
    for index in range(ALERT_MAX + 5):
        handler.deduped[f"prefilled-{index}"] = now - ALERT_TTL_S - 1
    alert_logger.error("GATE_CHECK_FAILED 门校验失败")
    assert len(handler.deduped) <= ALERT_MAX


def test_missing_channel_reports_instead_of_bubbling(alert_logger, alert_logs, monkeypatch):
    """emit 时桥不位 ⇒ 一枚带码 WARNING ＋ 计数，⛔ 不把异常抛回业务线程。"""
    monkeypatch.setattr(af_alert, "_linkage_bridge", lambda: None)
    handler = _attach(None)
    alert_logger.error("MQTT_BRIDGE_DOWN 桥不在位")
    assert handler.no_channel == 1 and handler.sent == 0
    assert any("OWNER_ALERT_NO_CHANNEL" in line for line in alert_logs)


@pytest.mark.parametrize("failure", [
    pytest.param(RuntimeError("socket closed"), id="publish-raises"),
    pytest.param({"published": False, "code": "ADM_ERR_BROKER_UNREACHABLE"}, id="publish-refused"),
])
def test_publish_failure_is_counted_and_never_bubbles(alert_logger, alert_logs, failure):
    bridge = FakeBridge(raise_exc=failure) if isinstance(failure, Exception) else FakeBridge(
        result=failure)
    handler = _attach(bridge)
    alert_logger.error("LINK_DOWN 联动链路断")
    assert handler.failed == 1 and handler.sent == 0
    assert any("OWNER_ALERT_PUBLISH_FAILED" in line for line in alert_logs)


def test_install_refuses_without_bridge_and_says_so(alert_logs, monkeypatch):
    """没桥就不装，并且明说一句：装着一条投不出去的线，比不装更容易让人以为已经通知过了。"""
    monkeypatch.setattr(af_alert, "_linkage_bridge", lambda: None)
    name = f"{SCOPE}.install"
    try:
        assert install_owner_alert(None, logger_name=name) is None
        assert active_owner_alert(logging.getLogger(name)) is None
        assert any("OWNER_ALERT_NOT_INSTALLED" in line for line in alert_logs)
    finally:
        logging.getLogger(name).handlers.clear()


def test_install_is_idempotent():
    name = f"{SCOPE}.twice"
    logger = logging.getLogger(name)
    bridge = FakeBridge()
    try:
        first = install_owner_alert(bridge, logger_name=name)
        second = install_owner_alert(FakeBridge(), logger_name=name)
        assert first is second
        assert len([h for h in logger.handlers if isinstance(h, OwnerAlertHandler)]) == 1
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)


def test_payload_passes_library_validation(alert_logger):
    """真源对撞：标题／正文的长度按 `homesdk.presence` 的上限截，并真过一遍库侧载荷校验。

    只比自己抄的那两个数会假绿——`_clip` 少留 1 字预算时，"截断后的字符串"恰好比上限多一枚
    省略号，库侧 `PresenceError` 会在真投递里把它拒成 `ADM_ERR_PAYLOAD_INVALID`。
    """
    from homesdk import presence

    from autoforge import af_mqtt_bridge

    long_message = "客厅主灯下发失败：" + ("泵体无响应已重试三次 " * 90)
    bridge = FakeBridge()
    handler = _attach(bridge)
    try:
        raise TimeoutError(long_message)
    except TimeoutError:
        alert_logger.exception("DEVICE_DISPATCH_FAILED " + long_message)
    assert handler.sent == 1
    fields = bridge.calls[0][1]
    assert len(fields["title"]) <= presence.INBOX_MAX_TITLE
    assert len(fields["body"]) <= presence.INBOX_MAX_BODY
    assert sorted(fields) == ["body", "title"], f"notify 只收 title/body：{sorted(fields)}"

    dry = af_mqtt_bridge.inbox_publish("notify", fields=fields, dry_run=True)
    assert "code" not in dry and dry["dry_run"] is True
    assert dry["payload"]["title"] == fields["title"] and dry["payload"]["body"] == fields["body"]


def test_reentrant_error_logging_does_not_loop(alert_logger):
    """投递路径自己冒 ERROR 时不许递归：`emit → publish → 日志 → emit` 是同一次调用里的自我复制。"""
    calls: list[int] = []

    def publish_again():
        calls.append(1)
        logging.getLogger(f"{SCOPE}.inner").error("OWNER_ALERT_SELF_ALARM 告警线内部又冒了 ERROR")

    handler = _attach(FakeBridge(on_publish=publish_again))
    alert_logger.error("ORIGIN_FAILURE 原始故障")
    assert len(calls) == 1 and handler.sent == 1


def test_handle_error_stays_silent(capsys):
    record = logging.LogRecord(SCOPE, logging.ERROR, "af_alert.py", 1, "boom", (), None)
    handler = OwnerAlertHandler(FakeBridge())
    handler.handleError(record)
    captured = capsys.readouterr()
    assert captured.err == "" and handler.failed == 1


# ── AST 腿：搬走就安静失效的那两格 ─────────────────────────────────────


def _serve_tree() -> ast.FunctionDef:
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serve":
            return node
    raise AssertionError("af_cli.py 里读不出 serve 函数 ⇒ 本判据没有射程")


def test_serve_installs_the_alert_line_before_uvicorn_run():
    serve = _serve_tree()
    installs, runs = [], []
    for node in ast.walk(serve):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "install_owner_alert":
                installs.append(node.lineno)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "run" \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "uvicorn":
                runs.append(node.lineno)
    assert installs, "serve 里不再调用 install_owner_alert ⇒ 三档告警线今天不会挂上"
    assert runs, "读不出 uvicorn.run 的射程，判据无从对撞"
    assert max(installs) < min(runs), f"装在 run 之后（{installs} vs {runs}）＝字典配置会把它清掉"


def test_should_alert_gate_is_levelno_ge_error():
    tree = ast.parse((CLI_PATH.parent / "af_alert.py").read_text(encoding="utf-8"))
    target = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "_should_alert"), None)
    assert target is not None, "_should_alert 读不出 ⇒ 级别门没有射程"
    compares = [n for n in ast.walk(target) if isinstance(n, ast.Compare)]
    levelno_ge_error = [
        n for n in compares
        if "levelno" in ast.unparse(n.left)
        and any(isinstance(op, ast.GtE) for op in n.ops)
        and ast.unparse(n.comparators[0]) == "logging.ERROR"
    ]
    assert levelno_ge_error, "级别门不再是 `levelno >= logging.ERROR`（改成 == ERROR 就漏掉 EXCEPTION 档）"
    assert not [n for n in compares if any(isinstance(op, ast.Eq) for op in n.ops)
                and "logging.ERROR" in ast.unparse(n)]


def test_emit_never_raises_to_the_calling_thread():
    """`emit` 整段必须包在 `except Exception` 里：标准库的兜底（`handleError` 打 traceback）不是兜底。

    这一格判为静态而不是行为：要行为地证明"任何异常都不外泄"需要枚举全部抛点，而搬走那层
    `try` 之后，剩下的测试仍可能因为当次恰好没抛而全绿。
    """
    tree = ast.parse((CLI_PATH.parent / "af_alert.py").read_text(encoding="utf-8"))
    emit = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "emit"
                 and any(isinstance(c, ast.Try) for c in ast.walk(n))), None)
    assert emit is not None, "emit 里读不出 try ⇒ 告警线的异常会直接落进业务线程"
    catches = [
        ast.unparse(h.type) for node in ast.walk(emit) if isinstance(node, ast.Try)
        for h in node.handlers if h.type is not None
    ]
    assert any(name in ("Exception", "BaseException") for name in catches), f"兜底捕获的形状变了：{catches}"

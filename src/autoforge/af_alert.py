"""owner 告警线（裁定 20261011 §3 Q8.1）：`ERROR`／`EXCEPTION`／`CRITICAL` 三档进 `butler/inbox/notify`。

第六轮审计 ARCH-07 那一格的原话是「有留痕纪律，但没有指标后端、没有追踪、没有分级日志约定」。
复测把后半句收成了 `docs/可观测性清单.md` §1.6 那张成文表，而前半句里真正缺的是**交到人眼前**这一步：
一枚 `ERROR` 落进日志之后，没有任何通道把它送到 owner 眼前。裁定给的不是自建采集栈（甲档已驳回，
AF 不建 Prometheus），而是**一条最小告警**：进程内一枚 logging handler，把三档留痕投进管家收件箱。

四条形状是本模块的承重墙，动它们之前先读完：

1. **永不抛**：`emit()` 整段包裹，任何失败降成一枚带码 `WARNING`；标准库的 `handleError` 也改成静默
   ——默认实现会把 traceback 打到 stderr，而告警线反过来咬主路径就是本模块的定义性缺陷。
2. **重入闩**（`threading.local`）：投递路径自己会写日志（现读 `af_mqtt_bridge._account_publish_error`
   里那枚 `logger.warning`）。今天它们都在 `WARNING` 档、被级别门挡在门外；闩挡的是**将来**有人把那句
   改成 `ERROR`——`emit → publish → 日志 → emit` 是一条会在同一次调用里自我复制的链，而仓内此前
   **没有任何 logging handler**，没有先例可抄，所以闩必须自带。
3. **级别门 ＋ 名字射程**（`_should_alert`）：只认 `levelno >= ERROR`，且只认 `autoforge` 这一族。
   `uvicorn.error` 收不到不是遗漏，是两条路都现读证明装不进去：
   - 直接挂会被清掉：`logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)` 之后
     `logging.getLogger("uvicorn.error").handlers` 从 1 变 **0**；
   - 挂到 root 也接不到：那份 `LOGGING_CONFIG["loggers"]["uvicorn"]["propagate"] is False`，
     实测 root 上的 handler 收得到 `httpx` 的 ERROR、收不到 `uvicorn.error` 的。
   serve 在 `uvicorn.run()` 之前装钩子，而上面两件事都发生在 `run()` 里面 ⇒ 这一族今天盖不住。
   补它要动 `af_api` 的 lifespan 或 serve 的 `log_config`（运行时契约），已作补问递裁，见清单 §八。
4. **dedupe 有界**（`ALERT_MAX` ＋ `ALERT_TTL_S` ＋ `_trim_alert_dedupe`）：常驻服务里"只增不减"就是缺陷，
   而且回收必须在**没人读**的时候也成立——判据腿在 `tests/unit/test_owner_alert.py`，出处登记在
   `af_bounded_caches.BOUNDED_CACHES`（那条约定正是同一族缺陷的防复发规则）。

`trace_id` 的口径写在账上：本模块**不**从 `record.args` 里挖调用方线程的那枚号（那要求每处埋点都把
id 塞进 args，全仓没有这个约定），而是用收件箱自己的 `trace_id` 作关联号（`inbox_publish` 每次现场
生成，成功与失败都原样带回）。所以"告警 ↔ 对端那次投递"能对上，"告警 ↔ 触发它的那一次 HTTP 请求"
今天对不上——后者是 Q8.2（request id）的射程，排在登录线窗口之后。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

__all__ = [
    "AF_LOGGER_FAMILY",
    "ALERT_MAX",
    "ALERT_TTL_S",
    "OwnerAlertHandler",
    "install_owner_alert",
    "active_owner_alert",
]

#: 本模块自己写留痕用的 logger。⛔ 这一族只能 `warning`／`info`：挂上去的 handler 就住在
#: `logging.getLogger("autoforge")` 上，这里冒出一枚 `error` 等于让告警线给自己发告警。
logger = logging.getLogger("autoforge.alert")

#: 告警射程的族名根。`logging.getLogger("autoforge")` 靠名字层级接住全部 `autoforge.*` 子 logger，
#: handler 因此**只挂一处**——逐文件挂需要枚举整个包，而新增模块会让那份枚举安静地少一格。
AF_LOGGER_FAMILY = "autoforge"

#: 同型告警的抑制窗（秒）与抑制表的硬上限。两腿齐全＋`_trim_alert_dedupe` 的纯写回收腿，
#: 一起登记在 `af_bounded_caches.BOUNDED_CACHES`。
ALERT_TTL_S = 900.0
ALERT_MAX = 256

#: 收件箱长度上限真源在库侧（`homesdk.presence`），这里只在取不到时才落回兜底值——
#: 把 80／500 抄进仓内就是立起第二份可以各自漂移的表（§二之九十六 那条纪律）。
_TITLE_FALLBACK = 80
_BODY_FALLBACK = 500
#: 同一次失败里 traceback 的层数上限：500 字的预算要留给"哪一行炸了"，不是留给二十层框架栈。
_MAX_TB_FRAMES = 6
_CODE_MAX_LEN = 60


#: 重入闩：一个 handler 服务全部线程，所以闩必须是 thread-local 而不是实例属性。
_in_emit = threading.local()


def _clip(text: str, limit: int) -> str:
    """按库侧长度截断，并在预算里留出一条"被截过"的尾巴。

    先留 1 字预算再切，是因为逐字节比长度会**恒不满足**库侧的 `len(...) <= 上限`：只切 `limit` 字
    再把省略号拼上去，那条投递就会在载荷校验处被拒，而拒它的原因正是"我已经通知过了"这件事。
    """
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


def _limits() -> tuple[int, int]:
    try:
        from homesdk.presence import INBOX_MAX_BODY, INBOX_MAX_TITLE

        return int(INBOX_MAX_TITLE), int(INBOX_MAX_BODY)
    except Exception:  # noqa: BLE001 —— 库缺席时仍要把告警投出去，长度按兜底值守
        return _TITLE_FALLBACK, _BODY_FALLBACK


def _short(text: object) -> str:
    return " ".join(str(text).split())


def _message(record: logging.LogRecord) -> str:
    """安全取格式化后的消息：`getMessage()` 自己抛（格式串与 args 不匹配）时退回原始 `record.msg`。

    这一格不能省：留痕本身坏掉（`"值 = %d"` 配了一个字符串参数）恰恰是最需要告警的时刻，
    而 handler 因为格式化炸了就一条都发不出去——"日志格式写错"会伪装成"没有故障"。
    """
    try:
        return str(record.getMessage())
    except Exception:  # noqa: BLE001
        return str(record.msg)


def _first_segment(text: str) -> str:
    for sep in ("\n", "\r", "：", ":"):
        text = text.split(sep)[0]
    return _short(text)[:60]


def _describe(record: logging.LogRecord) -> str:
    """异常现场优先取 `exc_info` 那一行：`logger.error("…")` 的 message 常常不含抛出的类型。"""
    if record.exc_info:
        return _exception_phrase(record)
    return _message(record)


def _exception_phrase(record: logging.LogRecord) -> str:
    try:
        exc = record.exc_info[1]
    except Exception:  # noqa: BLE001
        return ""
    return _short(f"{type(exc).__name__}: {exc}")


def _traceback_tail(record: logging.LogRecord) -> str:
    try:
        lines = logging.Formatter().formatException(record.exc_info).splitlines()
    except Exception:  # noqa: BLE001
        return ""
    keep: list[str] = []
    for line in reversed(lines):
        keep.append(line)
        if sum(1 for s in keep if s.lstrip().startswith("File \"")) >= _MAX_TB_FRAMES:
            break
    return "\n".join(reversed(keep))


def _code_of(record: logging.LogRecord) -> str:
    """消息首 token 若是全大写码就取它（与 `scripts/check_observability.py` 同口径，取不到为空）。"""
    text = _message(record).strip()
    token = text.split()[0] if text else ""
    return token if len(token) >= 6 and token.replace("_", "").isalnum() and token.isupper() else ""


def _dedupe_key(record: logging.LogRecord) -> str:
    code = _code_of(record)
    # 无码的那一枚取首个分隔符前的短句：`"设备下发失败"` 与 `"设备下发失败：客厅灯 timeout=3"` 是同型。
    shape = code if code else _first_segment(_describe(record))
    return f"{record.levelno}\x1f{record.name}\x1f{shape}"


def _trim_alert_dedupe(deduped: dict[str, float], now: float, ttl: float, cap: int) -> int:
    """把抑制表裁回"窗口内 ＋ 上限内"，返回被裁掉的条数。

    先按 TTL 清过期、再把最老的挤到上限以下（dict 保序＝插入序）。**不要求有人读**——这条腿
    存在的意义就是"没人订阅它也长不大"，与 §五 那条有界缓存约定同构。
    """
    removed = 0
    for key in [k for k, ts in deduped.items() if now - ts >= ttl]:
        del deduped[key]
        removed += 1
    overflow = len(deduped) - cap
    if overflow > 0:
        for key in list(deduped)[:overflow]:
            del deduped[key]
            removed += 1
    return removed


def _linkage_bridge() -> Any:
    try:
        from .af_mqtt_bridge import current_bridge
    except Exception:  # noqa: BLE001 —— 联动面缺席（没装 paho／没接线）
        return None
    try:
        return current_bridge()
    except Exception:  # noqa: BLE001
        return None


# 告警线自己的故障只写**带码 `WARNING`**，⛔ 不升 `ERROR`：升 ERROR 会被本 handler 接住再投一次，
# 而那一趟大概率仍失败 ⇒ "失败→告警→失败"自激。级别门、重入闩、这一条不升级，三样叠起来才堵死循环。
# 码必须写在**首参字面量**里而不是 `%s` 参数里：`check_observability.py` 只从首参取具名码，
# 写成参数就等于让这五枚码从"新码不登记即红"的那道门里溜出去。


class OwnerAlertHandler(logging.Handler):
    """一条留痕一次投递；投不出去就留痕，绝不抛回主路径、绝不重试成风暴。"""

    def __init__(self, bridge: Any, *, now_fn: Callable[[], float] = time.monotonic) -> None:
        # 级别门第一道在 handler 上（`record.levelno < self.level` 由 `logging.Handler.handle` 判掉），
        # 第二道在 `_should_alert`（名字射程）。两道分开是因为它们会各自被改坏：只留第一道会让
        # 第三方库的 ERROR 也进收件箱，只留第二道会让 `WARNING` 洪泛把通知线刷爆。
        super().__init__(level=logging.ERROR)
        self._bridge = bridge
        self._now = now_fn
        self._lock = threading.Lock()
        # 计数器是四个 int，不是容器；唯一会增长的是下面这张抑制表，它的两条腿登记在
        # `af_bounded_caches.BOUNDED_CACHES`（真源只有那一处，所以这里不再叠一条就地豁免注释——
        # 两份口径就是一对"注册表被删掉时门却照样绿"的暗门）。
        self.deduped: dict[str, float] = {}
        self.sent = 0
        self.suppressed = 0
        self.failed = 0
        self.no_channel = 0

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_in_emit, "owner_alert", False):
            return
        _in_emit.owner_alert = True
        try:
            self._emit(record)
        except Exception as exc:  # noqa: BLE001 —— 告警线绝不反过来打断留痕，也不许让标准库打 traceback
            self.failed += 1
            logger.warning(
                "OWNER_ALERT_DELIVERY_FAILED 告警线自身抛出 %s：%s（这一条三档留痕未投出）",
                type(exc).__name__, exc,
            )
        finally:
            _in_emit.owner_alert = False

    def _emit(self, record: logging.LogRecord) -> None:
        if not self._should_alert(record):
            return
        bridge = self._bridge if self._bridge is not None else _linkage_bridge()
        if bridge is None:
            self.no_channel += 1
            logger.warning(
                "OWNER_ALERT_NO_CHANNEL %s 的 %s 留痕没有通知通道（联动桥不在位）：%s",
                record.name, record.levelname, _describe(record),
            )
            return
        now = self._now()
        with self._lock:
            _trim_alert_dedupe(self.deduped, now, ALERT_TTL_S, ALERT_MAX)
            key = _dedupe_key(record)
            if key in self.deduped:
                self.suppressed += 1
                return
            # 先记账再投递：投递失败也算"这一窗内试过了"，否则一枚每次都抛的故障会每次都真投一次。
            self.deduped[key] = now
        title, body = _alert_payload(record, self.suppressed)
        try:
            result = bridge.publish_inbox("notify", fields={"title": title, "body": body})
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            logger.warning(
                "OWNER_ALERT_PUBLISH_FAILED %s 档告警投递抛出 %s：%s",
                record.levelname, type(exc).__name__, exc,
            )
            return
        if not (isinstance(result, dict) and result.get("published")):
            self.failed += 1
            code = result.get("code") if isinstance(result, dict) else None
            logger.warning(
                "OWNER_ALERT_PUBLISH_FAILED %s 档告警未被收件箱接受（code=%s）：%s",
                record.levelname, code, _describe(record),
            )
            return
        self.sent += 1
        logger.info(
            "OWNER_ALERT_SENT %s 档告警已投收件箱（trace_id=%s，此前合并同型告警 %d 条）",
            record.levelname,
            str(result.get("trace_id") or ""),
            self.suppressed,
        )

    @staticmethod
    def _should_alert(record: logging.LogRecord) -> bool:
        # `>= ERROR` 而不是 `== ERROR`：Python 把 `logger.exception()` 记成 **ERROR(40) ＋ exc_info**，
        # `logger.critical()` 记成 CRITICAL(50)，所以"三档"在 levelno 轴上就是同一条右半边。
        # 名字射程只认 `autoforge` 族：`uvicorn.error` 那一族的两种挂法都被现读否掉（模块 docstring 第 3 条）。
        return record.levelno >= logging.ERROR and record.name.startswith(AF_LOGGER_FAMILY)

    def stats(self) -> dict[str, int]:
        """运维读数：投出去／被合并／投失败／无通道，加当前抑制表深度。

        这五个数就是"这条线是不是活的"的全部证据。`/api/state` 那张机器脸属 #79 窗（Q13 的告示
        同一格），今天只有 handler 自己在进程内可查。
        """
        with self._lock:
            return {
                "sent": self.sent,
                "suppressed": self.suppressed,
                "failed": self.failed,
                "no_channel": self.no_channel,
                "dedupe_depth": len(self.deduped),
            }

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 —— 标准库的钩子名
        """静默标准库的 traceback 打印：`emit` 已经自带兜底，走到这里说明兜底自己抛了。

        默认实现会把整段 traceback 写进 stderr，而 stderr 就是容器日志——一条告警线的故障
        不该以"刷屏"的形态出现，也不该再冒一枚 ERROR 让它自己追自己。
        """
        self.failed += 1


def _alert_payload(record: logging.LogRecord, merged: int) -> tuple[str, str]:
    """标题＝级别＋出处＋一句话，正文＝消息全文＋异常行＋有限的栈尾，两半都按库侧上限截。"""
    title_limit, body_limit = _limits()
    code = _code_of(record)
    phrase = _describe(record)
    head = f"AF {record.levelname}｜{record.name}｜{code or _first_segment(phrase)}"
    title = _clip(head, title_limit)
    parts = [f"留痕消息：{_short(phrase)}", f"出处：{record.filename}:{record.lineno}（{record.funcName}）"]
    if code:
        parts.append(f"具名码：{code}")
    if record.exc_info:
        tail = _traceback_tail(record)
        if tail:
            parts.append(f"异常现场（尾部 {_MAX_TB_FRAMES} 层）：\n{tail}")
    if merged:
        parts.append(f"本进程此前已合并同型告警 {merged} 条（{int(ALERT_TTL_S)} 秒窗口内不重复打扰）")
    return title, _clip("\n".join(parts), body_limit)


def install_owner_alert(
    bridge: Any = None, *, logger_name: str = AF_LOGGER_FAMILY
) -> OwnerAlertHandler | None:
    """把三档告警线挂到 `logging.getLogger("autoforge")`；重复调用不重挂（幂等）。

    **没桥就不挂，并且明说一句**（`af_conflict_runtime._notify_guard_blind` 立的同一口径：对端拿不到
    的通知不叫通知，而"装着一条投不出去的线"比"不装"更容易让人以为已经通知过了）。返回 handler 只为
    测试与运维可检视，调用方不需要留住引用。
    """
    target = logging.getLogger(logger_name)
    existing = active_owner_alert(target)
    if existing is not None:
        return existing
    if bridge is None:
        bridge = _linkage_bridge()
    if bridge is None:
        logger.warning(
            "OWNER_ALERT_NOT_INSTALLED 联动桥不在位 ⇒ 三档告警线不挂"
            "（ERROR／EXCEPTION／CRITICAL 继续只落日志，不进收件箱）"
        )
        return None
    handler = OwnerAlertHandler(bridge)
    target.addHandler(handler)
    return handler


def active_owner_alert(target: logging.Logger | None = None) -> OwnerAlertHandler | None:
    """取回已挂上的那枚 handler（幂等检查与测试用）；没挂返回 None。"""
    for handler in (target or logging.getLogger(AF_LOGGER_FAMILY)).handlers:
        if isinstance(handler, OwnerAlertHandler):
            return handler
    return None

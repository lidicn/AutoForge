"""联动入向事件（在场 / 设备健康）的**只落盘、可回灌**队列（计划 §七 卡3，契约 §1.5）。

为什么要独立一条队列而不是复用 `af_insight_queue`：那条队列里每一条都**等人判定**
（approve 才进 `af_pending`），所以它满了必须**拒收**——丢一条提案就等于"MA 从没投过"。
本队列装的是对端持续播的**状态快照**：旧一条的价值低于新一场，所以满/超龄时**裁最旧**，
而不是把新事件挡在门外（挡住新的就等于"设备掉线了 AF 不知道"，正是契约 §1.5 要消灭的那件事）。

三条边界：

- **本模块不碰总线、不碰运行时**。收（paho 回调线程）与消费（常驻 tick 线程）在两个线程上，
  `EventBus` 没有锁；把线程交接做成"落盘 + 水位线"而不是"回调里直接 publish"，
  队列本身就是那条接缝（重启不丢也是同一条接缝给的）。
- **落盘形状由本模块决定，载荷键由调用方（桥）按契约表裁剪**。契约 §1.2 那两行的 member 子键
  明确**不含 `via_raw`**，所以成员字典只按 `_MEMBER_KEYS` 逐键取，多余键一律不带——
  对端哪天多发一个私有字段，不会经 AF 的归档扩散。
- **文本封顶**：`subject`/`data` 里的字符串按 `TEXT_LIMIT` 裁，`members` 按 `MEMBERS_LIMIT` 裁。
  载荷来自 broker，不封顶就等于让对端决定本机磁盘和面板宽度（桥侧 `TRANSPORT_*` 同一口径）。

分层：L1，只依赖标准库、`af_atomic`、`af_time` 与 `af_feedback` 的时间垫片。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .af_atomic import atomic_write_text
from .af_feedback import clock_now
from .af_time import SystemTimeSource

__all__ = [
    "DEFAULT_LIMIT",
    "KINDS",
    "KIND_DEVICE_HEALTH",
    "KIND_PRESENCE",
    "MEMBERS_LIMIT",
    "TEXT_LIMIT",
    "device_data_of",
    "members_of",
    "LinkageFeed",
    "LinkageRecord",
    "TRIGGER_MAX_AGE_S",
    "TRIGGER_NAME",
    "TTL_S",
    "UNREADABLE_MAX",
]

#: 队列里只有这两类。键名就是**子目录名**，所以它在盘上是可见的：`{root}/presence/…`。
KIND_PRESENCE = "presence"
KIND_DEVICE_HEALTH = "device_health"
KINDS: tuple[str, ...] = (KIND_PRESENCE, KIND_DEVICE_HEALTH)

#: 每类各自封顶（不是两类共享 500）：共享上限时"设备健康刷得多"会把在场快照挤出去，
#: 而这两条线各自对应不同自动化，被挤掉的那条就是静默失聪。
DEFAULT_LIMIT = 500
#: 超龄只裁盘、不再当触发用；一天的快照对"按状态触发"已经没有意义，但留档期不能比一天还短
#: （否则"昨晚谁在家"这类事后查账就读不到了）。
TTL_S = 24 * 3600.0
#: 触发侧的年龄闸：重启后队列里未消费的旧条目**只留档不触发**。没有这一档，
#: "重启即回放"会让一条小时级的旧掉线快照在开机瞬间触发一次真实下发。
TRIGGER_MAX_AGE_S = 120.0
#: 坏 JSON 清单的环形上限（与桥的 `MAX_HISTORY` 同族：诊断型只写清单）。
UNREADABLE_MAX = 50
TEXT_LIMIT = 120
MEMBERS_LIMIT = 32

#: 契约 §1.2 `ma/presence` 的 member 子键白名单（**不含 `via_raw`**）。
_MEMBER_KEYS = ("name", "member_id", "room", "via", "confidence", "last_seen", "trigger")
#: 契约 §1.2 `ma/device-health` 的载荷键（`from` 是 Python 关键字，落盘用别名键承载）。
_DEVICE_KEYS = ("device_id", "status", "entity_id", "from", "to", "stable_id")

#: 队列 kind → 自动化 `on event.<name>` 用的事件名。**不新立触发类型**：
#: IR 的 `trigger.type` 枚举里已有 `event`，总线侧 `EVENT_ENTITY_PREFIX` 就是这条通道，
#: 另立一族会同时破 `ir.schema.json` 与调度器的匹配分支（卡1 同一课）。
TRIGGER_NAME: dict[str, str] = {
    KIND_PRESENCE: "ma_presence",
    KIND_DEVICE_HEALTH: "ma_device_health",
}


@dataclass(frozen=True)
class LinkageRecord:
    """一条落盘的入向事件。`data` 是**已裁剪**的契约载荷，`subject` 是给人读的稳定身份。"""

    event_id: str
    kind: str
    topic: str
    trace_id: str
    received_at: float
    subject: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def trigger_name(self) -> str:
        return TRIGGER_NAME.get(self.kind, "")

    def as_trigger_data(self) -> dict[str, Any]:
        """注入总线时带的载荷（事件名已表达"哪一类"，这里给的是"哪一次、关于谁"）。"""
        return {
            "topic": self.topic,
            "kind": self.kind,
            "trace_id": self.trace_id,
            "subject": self.subject,
            "event_id": self.event_id,
            **self.data,
        }


def _clip(value: Any, limit: int = TEXT_LIMIT) -> str:
    return str(value)[:limit]


def members_of(raw: Any) -> list[dict[str, Any]]:
    """成员列表逐键取（白名单外的键不带），每项文本封顶。"""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return out
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        kept: dict[str, Any] = {}
        for key in _MEMBER_KEYS:
            if key not in item:
                continue
            value = item[key]
            kept[key] = _clip(value) if isinstance(value, str) else value
        out.append(kept)
        if len(out) >= MEMBERS_LIMIT:
            break
    return out


def device_data_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """设备健康载荷裁剪：只带契约那几键，文本封顶（`from` 另存 `from_state`）。"""
    data: dict[str, Any] = {}
    for key in _DEVICE_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        name = "from_state" if key == "from" else key
        data[name] = _clip(value) if isinstance(value, str) else value
    return data


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))


class LinkageFeed:
    """`{root}/{kind}/{ms}-{event_id}.json` 的文件队列；TTL + 硬上限两条腿都在 `_trim` 里。

    水位线（`_watermark`）是**文件名**不是时间戳：同一毫秒可以落多条，按时间推进会把它们当成
    已消费而静默丢掉。文件名前缀是 13 位毫秒、后缀是 `event_id`，字典序即时间序。
    """

    def __init__(
        self,
        root: Path | str,
        *,
        clock: Any | None = None,
        limit: int = DEFAULT_LIMIT,
        ttl_s: float = TTL_S,
    ) -> None:
        self.root = Path(root)
        self.clock = clock if clock is not None else SystemTimeSource()
        self.limit = int(limit)
        self.ttl_s = float(ttl_s)
        self.unreadable: list[str] = []  # bounded-cache: exempt(坏文件诊断环形清单：唯一写入口 `_load()` 就地按 UNREADABLE_MAX 裁头，只在读侧产生、不参与任何判定。没有 TTL 腿——"盘上有一条读不出来"这件事不该自行愈合，所以它不进 BOUNDED_CACHES 给一条不存在的腿盖章)
        self._watermark = ""

    # ---- 写 ---- #

    def append(self, record: LinkageRecord) -> LinkageRecord:
        directory = self._dir(record.kind)
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write(directory / self._name(record), record.to_dict())
        self._trim(record.kind)
        return record

    # ---- 读 ---- #

    def list_recent(self, kind: str | None = None, *, limit: int = 50) -> list[LinkageRecord]:
        out: list[LinkageRecord] = []
        for _key, path in self._entries(kind, reverse=True):
            rec = self._load(path)
            if rec is not None:
                out.append(rec)
            if len(out) >= limit:
                break
        return out

    def poll_new(self, *, max_age_s: float = TRIGGER_MAX_AGE_S) -> list[LinkageRecord]:
        """取"本次进程还没消费过"的条目，并推进水位线。

        超龄的条目**照样算消费过**（水位线推进）但不返回：它只该留档，不该在几分钟后
        还被当成触发。这就是"重启不丢"与"重启不回放"的交界——记录不丢，触发不补。
        """
        now = clock_now(self.clock)
        fresh: list[LinkageRecord] = []
        for key, path in self._entries():
            if key <= self._watermark:
                continue
            self._watermark = key
            rec = self._load(path)
            if rec is None:
                continue
            if now - rec.received_at > max_age_s:
                continue
            fresh.append(rec)
        return fresh

    def stats(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "limit": self.limit,
            "ttl_s": self.ttl_s,
            "per_kind": {kind: len(self._paths(self._dir(kind))) for kind in KINDS},
            "unreadable": list(self.unreadable),
            "watermark": self._watermark,
        }

    # ---- 内部 ---- #

    def _dir(self, kind: str) -> Path:
        return self.root / kind

    @staticmethod
    def _name(record: LinkageRecord) -> str:
        return f"{int(record.received_at * 1000):013d}-{record.event_id}.json"

    @staticmethod
    def _key(kind: str, path: Path) -> str:
        """跨目录的排序键：**毫秒在前、kind 居中**。

        按 `{kind}/{文件名}` 排会让 `device_health` 整个目录永远排在 `presence` 前面——
        于是"先收一条在场、再收一条更晚的设备健康"时，后者的键字典序更小、被水位线当成
        已消费，那条掉线事件就静默不触发了。
        """
        return f"{path.name[:13]}|{kind}|{path.name[13:]}"

    @staticmethod
    def _paths(directory: Path) -> list[Path]:
        return sorted(p for p in directory.glob("*.json")) if directory.is_dir() else []

    def _entries(self, kind: str | None = None, *, reverse: bool = False) -> list[tuple[str, Path]]:
        kinds = KINDS if kind is None else (kind,)
        out: list[tuple[str, Path]] = []
        for one in kinds:
            out.extend((self._key(one, path), path) for path in self._paths(self._dir(one)))
        out.sort(key=lambda item: item[0], reverse=reverse)
        return out

    def _load(self, path: Path) -> LinkageRecord | None:
        try:
            return LinkageRecord(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as exc:
            # 记账而不是咽下：坏文件会让"这条收到过"变成"这条没收到"
            self.unreadable.append(f"{path.name}: {type(exc).__name__}: {exc}")
            if len(self.unreadable) > UNREADABLE_MAX:
                del self.unreadable[: len(self.unreadable) - UNREADABLE_MAX]
            return None

    def _trim(self, kind: str) -> None:
        """两条腿都在这里：先按 TTL 删超龄，再按硬上限删最旧。

        判据 E 要的是"纯写不读也必须被回收"——本方法只由 `append()` 触发，与有没有人读无关。
        """
        directory = self._dir(kind)
        paths = self._paths(directory)
        if not paths:
            return
        now = clock_now(self.clock)
        cutoff = int((now - self.ttl_s) * 1000)
        survivors: list[Path] = []
        for path in paths:
            try:
                stamp = int(path.name[:13])
            except ValueError:
                survivors.append(path)
                continue
            if stamp >= cutoff:
                survivors.append(path)
            else:
                path.unlink(missing_ok=True)
        for path in survivors[: max(0, len(survivors) - self.limit)]:
            path.unlink(missing_ok=True)

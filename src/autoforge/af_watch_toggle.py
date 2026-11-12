# -*- coding: utf-8 -*-
"""AF-PM 裁定 2026-11-13 · 停用是安全阀，必须**即时**生效（机制 B：启停旁车热翻转）。

## 现场（出资人 2026-11-13 00:10 报障）

书房空调自动化「明明停用了，依然能够触发」：

- store：`/data/study_ac_smart_v7/v4.json` 的 `enabled = False`，saved_at 23:53 —— **停用确实落盘了**；
- 运行面：`/data/watch-ir-*/deployed_ir.json`（重启前那份）仍是 `enabled = True`；
- `/api/health`：`drifted=true`、`matches_store=false` —— 系统自己早就知道跑的不是 store 现状；
- 后果：停用后 19 分钟仍在触发（`last_triggered=2026-11-13T00:17:57+08:00`）；
- 处置：`docker restart autoforge-api` 让 watch 重装 store 现状，重启后 5 分钟零触发。

## 为什么不是「停旧 → 起新」（D4 2026-11-05 的处置）

D4 把「禁用后 watch 仍咬旧快照」的修法定成**重启 watcher**。对**内容变更**那是对的，
对**停用**是错的：

1. 停用是**只翻一位**的安全动作。为了关掉一条自动化而重装整份 IR，等于让一次
   「关」顺带做一次「部署」——撞「一次审批即生效」的授权契约（卡红线 1）；
2. 重启会**打断在跑实例**（`af_ask_purifier` 正在等的 60s ask 被腰斩），
   而关一条自动化本不该影响别的自动化；
3. 重启后 `ir_deployment_sha` 变了 —— 停用的**判据**因此无从证明「没重装 IR」。

于是 `af_service._ensure_watcher(enabled=False)` 的「停旧起新」在本批被**热翻转取代**：
停用只写本模块的旁车，不 stop、不 start、不重装。D4 的原始症状（被禁用的档继续触发）
由「下一次派发前读旁车」直接消除，且**即时**（不是 D4 那种「重启完才生效」）。

## 机制（卡面候选 B，本批采用）

`<store_root>/watch.enabled.json` 是一份**启停旁车**：

```json
{"version": 1, "enabled": {"<automation_id>": false}, "updated_at": "…", "note": "…"}
```

- **写侧**（API 进程）：`af_service.enable_by_tag` 与 `af_service.set_automation_enabled`
  在落盘成功后调 `merge_overrides()`，把**本次涉及的全部自动化 id** 的启停位写进去。
- **读侧**（watch 进程）：`EnabledOverrideGate.apply()` 在**每次 `Scheduler.handle_event`
  之前**跑一遍，按 `(mtime_ns, size, ino)` 缓存——文件没变时成本≈一次 `stat`，
  变了才反序列化，然后把 override **热翻进内存图的 `auto.enabled`**。

## 为什么是 B 而不是 A/C

- **C（停用即重启 watcher）**：卡面已列明「打断在跑实例、语义不干净」，且不满足判据 4
  （重启必然重写部署 IR ⇒ `ir_deployment_sha` 变）。排除。
- **A（停用路径主动通知 watcher）**：API 与 watch 是**两个进程**（`Popen(["forge","watch",…])`），
  没有现成 IPC；要新造信号/套接字通道，而**延迟收益为零**——B 的读点在派发前，
  「下一次事件」本来就是判据要求的全部时效。
- **B**：无新进程模型、无新协议、写侧只在既有落盘之后追加一次原子写；
  读侧一次 `stat`。**失败面对齐**：旁车读不出时保留上一份已知 override
  （⛔ 不让「旁车损坏」把已停用的自动化重新武装）。

## 与「启用」的关系（⛔ 别把免码扩到启用面）

本模块只搬运**启停位**，不碰授权码闸：`af_service._auth_code_check` 的两族语义
（停用免码 / 启用要码）**一个字都没改**。启用面之所以也写旁车，是为了让「停用后再启用」
需要的**内存图与 store 一致**（判据 5）——不写回去，watch 内存里那一位会永远停在禁用。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .af_atomic import atomic_write_text
from .af_flock import FileLock

logger = logging.getLogger(__name__)

#: 启停旁车文件名（与 `watch.reconcile.json` / `watch.pid` 同族，落在 store 根）。
ENABLED_OVERRIDE_FILE = "watch.enabled.json"

#: 写侧互斥锁（读侧不加锁：写是 `os.replace` 原子替换，读者只会看到旧份或新份）。
ENABLED_OVERRIDE_LOCK = "watch.enabled.lock"

#: 旁车版本（形状变更时递增；读侧认不出版本 ⇒ 当作无 override，⛔ 不抛）。
_VERSION = 1


def override_path(store_root: str | Path) -> Path:
    """旁车路径。"""
    return Path(store_root) / ENABLED_OVERRIDE_FILE


def read_overrides(store_root: str | Path, *, strict: bool = False) -> dict[str, bool]:
    """读整份 override 表。

    默认 **fail-soft**（缺文件／坏 JSON／形状不认识 ⇒ 空表）：**写侧**要的就是这个
    ——`merge_overrides` / `clear_overrides` 做读-改-写时读到坏文件应当当空表，
    由它自己 `os.replace` 把旁车修好。

    `strict=True` 只给 **watch 读侧**（`EnabledOverrideGate._refresh`）用：文件
    **存在但读不出**必须抛出，让调用方**保留上一份已知 override**。
    ⛔ 别在 fail-soft 里返回空表了事——那会把「旁车损坏」变成「没有停用位」，
    等于**把已停用的自动化重新武装**（安全阀朝失效方向倒，2026-11-13 那 19 分钟
    事故的形状）。缺文件仍然返回空表：没有旁车就是没有 override，这是真值不是失败。
    """
    path = override_path(store_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        if strict:
            raise
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        # json.JSONDecodeError ⊂ ValueError
        if strict:
            raise
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"启停旁车顶层不是对象：{type(data).__name__}")
        return {}
    table = data.get("enabled")
    if not isinstance(table, dict):
        if strict:
            raise ValueError(f"启停旁车的 enabled 面不是对象：{type(table).__name__}")
        return {}
    out: dict[str, bool] = {}
    for key, value in table.items():
        # 只认真布尔：字符串 "false" 之类的脏值当作没有 override，⛔ 不猜。
        if isinstance(value, bool):
            out[str(key)] = value
    return out


def merge_overrides(
    store_root: str | Path,
    mapping: Mapping[str, bool],
    *,
    note: str = "",
    lock_timeout: float = 10.0,
) -> dict[str, bool]:
    """把 `mapping` 合并进旁车（**读-改-写**，跨进程互斥），返回合并后的整表。

    为什么是合并而不是整份覆盖：`enable_by_tag` 只动一个标签下的归档，
    `set_automation_enabled` 只动一个归档——整份覆盖会把**别人**刚写的位抹掉。

    ⛔ 失败必须抛出：调用方（服务层）要据此如实回报「停用没生效」，
    不能让一次写失败静默成「已停用」。
    """
    path = override_path(store_root)
    lock = FileLock(Path(store_root) / ENABLED_OVERRIDE_LOCK, timeout=lock_timeout)
    with lock:
        merged = read_overrides(store_root)
        merged.update({str(k): bool(v) for k, v in mapping.items()})
        payload: dict[str, Any] = {
            "version": _VERSION,
            "enabled": merged,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if note:
            payload["note"] = str(note)
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return merged


def clear_overrides(store_root: str | Path, automation_ids: Iterable[str]) -> dict[str, bool]:
    """把指定自动化从旁车里摘掉（归档被删时用），返回剩下整表。"""
    drop = {str(x) for x in automation_ids}
    path = override_path(store_root)
    lock = FileLock(Path(store_root) / ENABLED_OVERRIDE_LOCK, timeout=10.0)
    with lock:
        merged = {k: v for k, v in read_overrides(store_root).items() if k not in drop}
        atomic_write_text(
            path,
            json.dumps({"version": _VERSION, "enabled": merged,
                        "updated_at": datetime.now(timezone.utc).isoformat()},
                       ensure_ascii=False, indent=2, sort_keys=True),
        )
    return merged


class EnabledOverrideGate:
    """watch 侧读口：把启停旁车**热翻**进内存图（每次派发前跑，带 stat 缓存）。

    用法（`af_scheduler.Scheduler.handle_event` 顶部）：:

        self.disable_gate.apply(self.graph)

    ⛔ 这是**唯一**能让「store 说停用」传到「watch 内存」的路径——绕过它，
    停用就退回到「等下一次重启」（2026-11-13 现场：19 分钟、仍在触发）。
    """

    __slots__ = ("_path", "_stamp", "_map", "_last_error")

    def __init__(self, store_root: str | Path):
        self._path = override_path(store_root)
        self._stamp: tuple[int, int, int] | None = None
        self._map: dict[str, bool] = {}  # bounded-cache: exempt(启停旁车的整份镜像：唯一写入口 `_refresh()` 每次 `read_overrides()` **整表重建**（`self._map = table`），不随事件累积；键域＝旁车文件里出现过的自动化 id，上界就是 store 里配了启停位的归档数。没有 TTL 腿——旁车不在就清空成空表，而"清空"正是"没有 override"的真值，配 TTL 只会让停用位自己过期失效（那正是 2026-11-13 现场 19 分钟事故的机制）)
        self._last_error: str = ""

    # ── 诊断面 ────────────────────────────────────────────────────
    @property
    def overrides(self) -> dict[str, bool]:
        """当前生效的 override 表（只读副本，供 health / 判据取数）。"""
        return dict(self._map)

    @property
    def last_error(self) -> str:
        """上次读旁车失败的原因（空串＝没失败过）；用于「停用没生效」的现场定位。"""
        return self._last_error

    # ── 读 ────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        try:
            st = self._path.stat()
        except FileNotFoundError:
            self._stamp, self._map = None, {}
            self._last_error = ""
            return
        except OSError as exc:  # noqa: BLE001 —— 读不出就让上一份继续生效
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("WATCH_ENABLED_OVERRIDE_READ_FAILED stat %s err=%r", self._path, exc)
            return
        stamp = (int(st.st_mtime_ns), int(st.st_size), int(getattr(st, "st_ino", 0) or 0))
        if stamp == self._stamp:
            return
        # ⛔ 这里必须用 strict 版：fail-soft 的 `read_overrides` 在坏 JSON 时给空表，
        # 赋给 `_map` 就等于「没有停用位」——`apply()` 的 `if not self._map: return 0`
        # 会让内存图里那一位回到 IR 的 `True`，**已停用的自动化重新武装**。
        # 安全阀只能朝「关着」失效：读不出就保留上一份已知 override。
        try:
            table = read_overrides(self._path.parent, strict=True)
        except (OSError, ValueError) as exc:
            # 记下 stamp：坏文件没被替换之前别每次事件重读重告警；
            # 一旦有人修好（`os.replace` 换 ino／改大小），stamp 变，下一次自然重读。
            self._stamp = stamp
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "WATCH_ENABLED_OVERRIDE_READ_FAILED parse %s err=%r（保留上一份 %d 位 override）",
                self._path, exc, len(self._map),
            )
            return
        self._stamp = stamp
        self._map = table
        self._last_error = ""

    # ── 应用 ──────────────────────────────────────────────────────
    def apply(self, graph: Any) -> int:
        """把 override 热翻进内存图，返回本次**真翻了**几位。

        `graph` 可以是任何 `for auto in graph` 产出带 `.id` / `.enabled` 对象的可迭代物。
        返回值只用于判据与日志；⛔ 调用方不得据「翻了 0 位」推断「没停用」——
        旁车没变时本来就是 0 位。
        """
        self._refresh()
        if not self._map:
            return 0
        flipped = 0
        for auto in graph:
            try:
                want = self._map.get(str(getattr(auto, "id", "")))
                if want is None:
                    continue
                if bool(getattr(auto, "enabled", True)) != want:
                    auto.enabled = want
                    flipped += 1
            except Exception as exc:  # noqa: BLE001 —— 单条翻不动不许拖死整轮派发
                logger.warning("WATCH_ENABLED_OVERRIDE_APPLY_FAILED auto=%r err=%r", auto, exc)
        if flipped:
            logger.info("WATCH_ENABLED_OVERRIDE_APPLIED flipped=%d（停用/启用即时生效）", flipped)
        return flipped

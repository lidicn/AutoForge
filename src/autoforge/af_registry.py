"""HA **websocket** 注册表抓取（可选能力，需 `pip install -e ".[ha]"`）。

**为什么需要它**：HA 的 REST `/api/states` 只给 `state` + `attributes`，
**不暴露** `device_id` / `area_id` / `platform` / `config_entry_id`——这些只在
websocket 的三个注册表里。v1.1.0 因此只能从 `attributes.area` 取区域（真实缺陷：
大量实体根本没这个属性）。

四个 ws 命令（比常见实现**多取一个**）：
| id | 命令 | 拿什么 |
|---|---|---|
| 1 | `config/entity_registry/list` | entity_id → device_id / area_id / platform / config_entry_id |
| 2 | `config/device_registry/list` | device_id → area_id（**区域常挂在 device 上**） |
| 3 | `config/area_registry/list` | area_id → 区域名 |
| 4 | `config/config_entry/list` | config_entry_id → **真集成名**（domain） |

> ⚠️ **坑 1（必记）**：`platform ≠ integration`。`entity_registry` 里的 `platform`
> 是**平台实现名**（如 `xiaomi_miot`），真集成名要走第 4 个命令
> `config_entry_id → domain`。只在**没有** `config_entry_id` 时才回退用 `platform`。
>
> ⚠️ **坑 2（必记）**：**area 解析链必须是 `entity.area_id → device.area_id`**，
> 大量实体的 `area_id` 为空、区域挂在 device 上。只看 entity 会丢掉大半区域。

**降级语义（与 AutoFlow 冻结版本逐条对齐，不可自行发挥）**：
1. **失败静默返回空、不抛异常**——不能只靠 `except`，必须**判空**；
2. **各能力组独立 try**——集成（第 4 组）失败**不得连坐** area / device；
3. **绝不把已知值清零**——AutoFlow 2026-07-16 真实事故：一次 ws 失败导致全库 `area=""`；
4. 缺失 → 空串，**核心 catalog 能力不受损**（无 websockets 时 `resolve` 照常工作）。
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping

from .af_adapters.http import guarded_open, host_of

__all__ = [
    "RegistrySnapshot",
    "fetch_registries",
    "rest_areas_fallback",
    "WEBSOCKETS_MISSING_HINT",
]

#: 未安装可选依赖时的提示（原样回传给调用方，便于 agent/人知道怎么修）
WEBSOCKETS_MISSING_HINT = "未安装可选依赖 websockets：`pip install -e \".[ha]\"`"


class RegistrySnapshot:
    """四个注册表的抓取结果。**任何一组失败只是该组为空，不得影响其他组**。"""

    __slots__ = (
        "entity_index",
        "device_area",
        "area_names",
        "entry_domains",
        "available",
        "reasons",
    )

    def __init__(
        self,
        entity_index: dict[str, dict[str, str]] | None = None,
        device_area: dict[str, str] | None = None,
        area_names: dict[str, str] | None = None,
        entry_domains: dict[str, str] | None = None,
        available: bool = True,
        reasons: list[str] | None = None,
    ):
        #: entity_id → {"device_id","area_id","platform","config_entry_id"}
        self.entity_index: dict[str, dict[str, str]] = entity_index or {}
        #: device_id → area_id
        self.device_area: dict[str, str] = device_area or {}
        #: area_id → 区域名
        self.area_names: dict[str, str] = area_names or {}
        #: config_entry_id → 真集成名（domain）
        self.entry_domains: dict[str, str] = entry_domains or {}
        self.available: bool = available
        self.reasons: list[str] = list(reasons or [])

    # ── 派生查询（都带「缺失 → 空串」语义，绝不抛）──────────────────────
    def device_id_of(self, entity_id: str) -> str:
        return (self.entity_index.get(entity_id) or {}).get("device_id") or ""

    def platform_of(self, entity_id: str) -> str:
        return (self.entity_index.get(entity_id) or {}).get("platform") or ""

    def config_entry_of(self, entity_id: str) -> str:
        return (self.entity_index.get(entity_id) or {}).get("config_entry_id") or ""

    def integration_of(self, entity_id: str) -> str:
        """**真集成名**：config_entry_id → domain；拿不到才回退 `platform`。

        ⚠️ 直接把 `platform` 当集成名是隐性错误（platform 是平台实现名，不是集成名）——
        所以 `integration_source` 会标明这个值到底是权威来源还是回退。
        """
        entry_id = self.config_entry_of(entity_id)
        if entry_id:
            domain = self.entry_domains.get(entry_id) or ""
            if domain:
                return domain
        return self.platform_of(entity_id)

    def integration_source_of(self, entity_id: str) -> str:
        """`integration` 的可信度标注：`config_entry`（权威）｜`platform`（回退）｜`""`（都没有）。

        ⚠️ 实测（2026-09-16，HA 2026.9.2）：该版本**未注册**任何 config_entry 列表命令
        （`config/config_entry/list` 等 4 个候选名全部 `unknown_command`），
        因此当前线上 `integration` 实际恒为 `platform` 回退——消费者必须看这个字段判断可信度。
        """
        entry_id = self.config_entry_of(entity_id)
        if entry_id and self.entry_domains.get(entry_id):
            return "config_entry"
        return "platform" if self.platform_of(entity_id) else ""

    def area_id_of(self, entity_id: str) -> str:
        """**area 解析链**：`entity.area_id` → `device.area_id`（区域常挂在 device 上）。"""
        reg = self.entity_index.get(entity_id) or {}
        direct = reg.get("area_id") or ""
        if direct:
            return direct
        device_id = reg.get("device_id") or ""
        if device_id:
            return self.device_area.get(device_id) or ""
        return ""

    def area_name_of(self, area_id: str) -> str:
        return self.area_names.get(area_id) or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reasons": self.reasons,
            "counts": {
                "entities": len(self.entity_index),
                "devices": len(self.device_area),
                "areas": len(self.area_names),
                "config_entries": len(self.entry_domains),
            },
        }


def _empty(reason: str) -> RegistrySnapshot:
    return RegistrySnapshot(available=False, reasons=[reason])


# ─────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────


def fetch_registries(
    base_url: str,
    token: str,
    timeout: float = 25.0,
    opener: Callable[..., Any] | None = None,
) -> RegistrySnapshot:
    """抓四个注册表。**永不抛异常**——任何失败都降级为本组为空。"""
    try:
        import websockets  # noqa: F401
    except Exception:
        return _empty(WEBSOCKETS_MISSING_HINT)

    try:
        return _run_coroutine(_ws_fetch(base_url, token, timeout))
    except Exception as exc:  # 传输层/解析任何异常都降级，不连坐调用方
        return _empty(f"websocket 注册表抓取失败：{type(exc).__name__}: {exc}")


def _run_coroutine(coro: Any) -> RegistrySnapshot:
    """在同步上下文里跑协程；若已有运行中的事件循环则另起线程（FastAPI/uvicorn 场景）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _ws_fetch(base_url: str, token: str, timeout: float) -> RegistrySnapshot:
    import websockets

    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    # ⚠️ 必须放大帧上限：`websockets` 默认 max_size=1 MiB，而 `config/entity_registry/list`
    # 在中等规模 HA（实测 2888 实体）就会超过它 → 服务端发 1009「message too big」并断连。
    # 不设这一项的表现是「四个组全空」，极易被误判成"没权限/没数据"。
    async with websockets.connect(
        ws_url, open_timeout=timeout, close_timeout=10, max_size=64 * 1024 * 1024
    ) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        await ws.recv()  # auth_ok（失败会在后续 result 里体现）

        commands = {
            1: "config/entity_registry/list",
            2: "config/device_registry/list",
            3: "config/area_registry/list",
            4: "config/config_entry/list",
        }
        for cid, command in commands.items():
            await ws.send(json.dumps({"id": cid, "type": command}))

        raw: dict[int, Any] = {}
        failures: list[str] = []
        for _ in range(len(commands)):
            try:
                message = json.loads(await ws.recv())
            except Exception as exc:
                # ⚠️ 不能静默 `continue`：连接级故障（如帧超限被 1009 断连）也会被这里吞掉，
                # 表现为「四个组全空」而看不出真因（2026-09-16 NAS 实测踩到）。记录原因。
                failures.append(f"ws.recv 失败：{type(exc).__name__}: {exc}")
                break
            if not isinstance(message, dict) or message.get("type") != "result":
                continue
            cid = message.get("id")
            if message.get("success") is False:
                # 显式记录被拒原因（如 `unknown_command` / `unauthorized`），
                # 否则症状只是「该组为空」，看不出是"没数据"还是"命令不存在"。
                code = (message.get("error") or {}).get("code") or "unknown"
                failures.append(f"命令 {commands.get(cid, cid)!r} 被拒：{code}")
            raw[cid] = message.get("result")

        return _build_snapshot(raw, failures)


def _build_snapshot(
    raw: Mapping[Any, Any], failures: list[str] | None = None
) -> RegistrySnapshot:
    """**各能力组独立构建**：任一组缺失/异常只让该组为空，绝不连坐其他组。"""
    reasons: list[str] = list(failures or [])

    # ── 组 1：entity_registry ────────────────────────────────────────
    entity_index: dict[str, dict[str, str]] = {}
    try:
        for item in _as_list(raw.get(1)):
            entity_id = item.get("entity_id")
            if not entity_id:
                continue
            entity_index[str(entity_id)] = {
                "device_id": str(item.get("device_id") or ""),
                "area_id": str(item.get("area_id") or ""),
                "platform": str(item.get("platform") or ""),
                "config_entry_id": str(item.get("config_entry_id") or ""),
            }
    except Exception as exc:
        reasons.append(f"entity_registry 解析失败：{type(exc).__name__}")
    if not entity_index:
        reasons.append("entity_registry 为空")

    # ── 组 2：device_registry（device_id → area_id）───────────────────
    device_area: dict[str, str] = {}
    try:
        for item in _as_list(raw.get(2)):
            device_id = item.get("id")
            if device_id:
                device_area[str(device_id)] = str(item.get("area_id") or "")
    except Exception as exc:
        reasons.append(f"device_registry 解析失败：{type(exc).__name__}")

    # ── 组 3：area_registry ──────────────────────────────────────────
    area_names: dict[str, str] = {}
    try:
        for item in _as_list(raw.get(3)):
            area_id = item.get("area_id")
            if area_id:
                area_names[str(area_id)] = str(item.get("name") or area_id)
    except Exception as exc:
        reasons.append(f"area_registry 解析失败：{type(exc).__name__}")

    # ── 组 4：config_entry（**集成失败不得连坐 area/device**）──────────
    entry_domains: dict[str, str] = {}
    try:
        for item in _as_list(raw.get(4)):
            entry_id = item.get("entry_id")
            if entry_id:
                entry_domains[str(entry_id)] = str(item.get("domain") or "")
    except Exception as exc:
        # 只记原因：integration 缺失仅影响 `integration_of` 回退到 platform
        reasons.append(f"config_entry 解析失败（已回退 platform）：{type(exc).__name__}")

    return RegistrySnapshot(
        entity_index=entity_index,
        device_area=device_area,
        area_names=area_names,
        entry_domains=entry_domains,
        available=bool(entity_index) or bool(area_names),
        reasons=reasons,
    )


def _as_list(value: Any) -> list[Any]:
    """注册表结果规整为 list；None / 非 list → 空列表（**判空，不抛**）。"""
    if isinstance(value, list):
        return value
    return []


# ─────────────────────────────────────────────────────────────────────
# REST 兜底（area_registry 为空时）
# ─────────────────────────────────────────────────────────────────────


def rest_areas_fallback(
    base_url: str,
    token: str,
    timeout: float = 10.0,
    opener: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """`GET /api/areas` 兜底。**依 HA 版本可能 404** → 返回空 dict（不抛）。

    降级链的一环：ws `area_registry` 为空时用它；它也失败就真的没有区域信息，
    由 catalog 显式 `area_warning` 告知（绝不把已知区域清零）。
    """
    url = f"{base_url.rstrip('/')}/api/areas"
    req = urllib.request.Request(
        url, method="GET", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        # F15（第十四轮审计 OUTB-01）：`(opener or urllib.request.urlopen)(...)` 这种写法
        # bandit 的 B310 认不出来（被调者是布尔表达式），但它走的仍是默认 opener——
        # 跟 3xx 且不重校验 Location。缺省档换成收口点，注入档（测试）保持不变。
        resp_cm = (
            opener(req, timeout=timeout)
            if opener is not None
            else guarded_open(req, allowed_hosts=(host_of(base_url),), timeout=timeout)
        )
        with resp_cm as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError):
        return {}
    out: dict[str, str] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("area_id"):
                out[str(item["area_id"])] = str(item.get("name") or item["area_id"])
    elif isinstance(payload, dict):
        for key, value in payload.items():
            out[str(key)] = str(value.get("name") or key) if isinstance(value, dict) else str(value)
    return out

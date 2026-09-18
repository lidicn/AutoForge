"""设备目录（Device Catalog）—— 原生的「自然语言设备名 → 真实 entity_id」解析层。

**为什么需要它**：AutoForge 的 IR 只认裸 `entity_id` 字符串，而 Agent 拿到的是人话
（「书房吊灯」「显示器挂灯」）。此前这一步被外包给 MA（memory-agent），导致
「用户 → Agent → MA → 回来写 IR」跨服务 + 跨鉴权 + 跨网络两跳，且两边设备视图可能不一致。

本模块把这一步做进平台自身：**拉一次 HA 全量状态落本地缓存，之后解析毫秒级返回**。

设计原则（对齐 IR 安全语义，参考前身 autoflow `gateway.resolve_entity` 的取舍）：
1. **不过滤域**：同一设备名可能对应 `light` / `switch` / `cover`，**全部返回**，
   由 Agent 看 `domain + friendly_name` 自己挑——不要预设它是 light 还是 switch。
2. **绝不静默猜域**：`resolve_best()` 只在**无歧义**（唯一候选 / top 置信度=high）时自动采纳，
   有歧义返回 `None`，逼 Agent 显式调 `af_resolve_entity` 从候选里选。
3. **area 是提示不是硬约束**：区域解析失败自动放宽到全局，避免「设备未分配区域」把正确设备整段排除。
4. **防 DoS 三纪律**：
   - `entity_id` 形态的字符串**不做全目录模糊扫描**（编造型 ID 不会命中友好名，扫描纯浪费）；
   - 目录读取带 `(mtime, size)` 缓存，O(N) 次调用不重复读盘解析；
   - 列表查询强制分页（上限 200）+ 透明回报 `truncated` / `next_offset`，杜绝静默截断。

**与 MA 的分工**（职责不重叠）：
- **AF（本模块）**：执行事实——「书房吊灯的 entity_id 是哪个？它有几种状态？现在什么状态？」
- **MA**：语义身份——「谁在家？这家人作息如何？这两个设备历史上怎么联动过？」
- 因此 **MA 不再是我方的前置依赖**，而是可选增强：MA 挂了，自动化照写不误。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .af_adapters import DEFAULT_HA_URL, HATransport
from .af_affordance import affordance_for, domain_of
from .af_flock import FileLock
from .af_store import DEFAULT_STORE_ROOT, atomic_write_text

__all__ = [
    "DeviceCatalog",
    "CATALOG_VERSION",
    "MAX_LIST_LIMIT",
    "ENTITY_ID_RE",
]

#: 目录文件格式版本（便于未来迁移）
CATALOG_VERSION = 1

#: 单页上限——防「全屋 3000 实体一次撑爆 agent 上下文」
MAX_LIST_LIMIT = 200

#: `entity_id` 形态（`domain.object_id`，全小写、无空格/中文）
ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

#: 从 HA attributes 里挑出**与解析/写 IR 相关**的小子集，避免目录文件被大 attributes 撑爆
_KEEP_ATTRS = (
    "friendly_name",
    "area",
    "area_name",
    "unit_of_measurement",
    "device_class",
    "options",  # select 的枚举
    "hvac_modes",
    "fan_modes",
    "preset_modes",
    "source_list",  # media_player 信号源
    "min",
    "max",
)

#: 置信度排序权重（越小越优先）
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}

# ── v1.6.0 决策智能：连通性档位（集成优选 / 弱信号降权）───────────────
#: 连通性档位：`local`（本地局域网）> `cloud`（云）> `polling`（轮询）> `unknown`（中性）。
#: ⚠️ 未知档位必须**中性**——只影响排序，**绝不**过滤（v1.1.0 fail-closed 纪律不变）。
CONNECTIVITY_TIERS = ("local", "cloud", "polling", "unknown")
_TIER_RANK = {"local": 0, "cloud": 1, "polling": 2, "unknown": 3}
#: 集成名/平台名 → 档位（子串匹配；先 local 后 cloud 后 polling，命中即返回）。
_TIER_LOCAL = (
    "esphome", "mqtt", "zigbee", "zha", "deconz", "zwave", "shelly", "tasmota",
    "sonoff", "localtuya", "miot", "miio", "homekit", "matter", "thread",
    "bluetooth", "tplink", "hue", "yeelight",
)
_TIER_CLOUD = ("cloud", "xiaomi_home", "tuya", "smart_life", "meross", "google", "alexa")
_TIER_POLLING = ("template", "rest", "scrape", "command_line", "demo", "input_", "generic")


def _tier_of(meta: Mapping[str, Any]) -> str:
    """从 `integration`/`platform` 推连通性档位；无法判定 → `unknown`（中性）。"""
    name = str(meta.get("integration") or meta.get("platform") or "").strip().lower()
    if not name:
        return "unknown"
    for key in _TIER_LOCAL:
        if key in name:
            return "local"
    for key in _TIER_CLOUD:
        if key in name:
            return "cloud"
    for key in _TIER_POLLING:
        if key in name:
            return "polling"
    return "unknown"


def _is_offline(meta: Mapping[str, Any]) -> bool:
    """此刻是否不可用（弱信号廉价信号，零常驻采样）。"""
    return bool(meta.get("offline_now")) or str(meta.get("state")) in ("unavailable", "unknown")


def _actionable_rank(meta: Mapping[str, Any]) -> int:
    """可动作域优先（0）/ 只读域靠后（1）——**只影响排序，绝不过滤**。

    存在意义（v1.7.1，2026-09-17 NL 实测暴露）：同名干扰时只读候选会挤掉可控设备。
    实测 `resolve("客厅电视")` 只有 `sensor.…_客厅电视昨日播放时长`（只读）命中，
    而真正可下发的 `media_player.…_play_control` 排在后面甚至不出现，
    导致 Agent 误把「播放时长 sensor」当成「电视」。按「有无可调服务」排序后，
    可控设备（light/switch/climate/media_player…）稳定排在只读统计量之前。

    判据不硬编码域名单，而是复用 `af_affordance` 的服务词汇表：
    `services` 为空即只读（sensor / binary_sensor / event / sun …）。
    """
    domain = str(meta.get("domain") or domain_of(str(meta.get("entity_id") or "")))
    return 0 if affordance_for(domain).get("services") else 1


def _service_count(meta: Mapping[str, Any]) -> int:
    """该实体域可调服务数量（能力面大小）。

    只读兜底升级（`readonly_upgrade`）里用它挑主力设备：同一控制词组下，
    「播放控制 media_player（6 个服务）」比「音量 number（1 个服务）」更可能是用户说的那台设备，
    「...开关 switch（3 个）」又比「模式 select（1 个）」更可能是本体。
    """
    domain = str(meta.get("domain") or domain_of(str(meta.get("entity_id") or "")))
    return len(affordance_for(domain).get("services") or ())


#: 中文设备名常见后缀：原名无候选时剥离后重试
#: （实测 `resolve("油烟机灯光")` 无候选，而 `resolve("油烟机")` 能命中「米家跨界吸油烟机S1 灯光」）。
_QUERY_SUFFIXES: tuple[str, ...] = (
    "灯光", "开关", "插座", "面板", "传感器", "状态", "控制", "灯",
)


def _strip_query_suffix(q: str) -> str:
    """剥离中文设备名后缀；剥不动返回空串（长后缀优先，避免「灯光」被「灯」先吃掉）。"""
    for suffix in _QUERY_SUFFIXES:
        if len(q) > len(suffix) and q.endswith(suffix):
            return q[: -len(suffix)].strip()
    return ""


def _area_of(attrs: Mapping[str, Any] | None) -> str:
    """从（已裁剪的）attributes 里取区域名——**仅作为解析链的最后一环**。

    HA REST `/api/states` **不暴露 area 注册表**，只有部分集成会在 attributes 里带
    `area` / `area_name`。v1.1.0 只走这条路（真实缺陷：大量实体无此属性）；
    v1.6.0 起优先走注册表解析链 `entity.area_id → device.area_id`，本函数降为兜底。
    """
    if not attrs:
        return ""
    return str(attrs.get("area") or attrs.get("area_name") or "")


def _meta_area(meta: Mapping[str, Any]) -> str:
    """实体的**最终区域**：注册表解析结果优先，attributes 兜底。

    统一读取口——所有需要「这个实体在哪个房间」的地方都走它，避免又退化成只看 attributes。
    """
    return str(meta.get("area") or _area_of(meta.get("attributes")))


def _enrich(meta: dict[str, Any], registry: Any, attrs: Mapping[str, Any] | None) -> None:
    """v1.6.0 P0：用 websocket 注册表富化条目。

    - `integration` 走 `config_entry_id → domain`（**真集成名**），无 entry 才回退 `platform`；
      ⚠️ 直接拿 `platform` 当集成名是隐性错误。
    - `area` 走解析链 `entity.area_id → device.area_id → attributes`；区域常挂在 device 上。
    - `offline_now` / `last_changed`：P1 的**廉价部分**，零常驻采样，本来就在 `/api/states` 快照里。
      （本轮不据此改排序——`connectivity_tier` 启发式属 P1，未做。）
    """
    entity_id = str(meta.get("entity_id", ""))
    area_id = registry.area_id_of(entity_id)
    meta["device_id"] = registry.device_id_of(entity_id)
    meta["platform"] = registry.platform_of(entity_id)
    meta["integration"] = registry.integration_of(entity_id)
    meta["integration_source"] = registry.integration_source_of(entity_id)
    meta["area_id"] = area_id
    meta["area"] = registry.area_name_of(area_id) or _area_of(attrs)
    meta["offline_now"] = str(meta.get("state")) in ("unavailable", "unknown")
    meta["last_changed"] = str(meta.get("last_changed") or "")


#: 注册表派生字段：**缺失时沿用旧值**，绝不用空串覆盖已知值
#: （AutoFlow 2026-07-16 事故：一次 ws 失败把全库 area 清零）
_PRESERVE_ON_EMPTY = (
    "device_id",
    "platform",
    "integration",
    "integration_source",
    "area_id",
    "area",
)


def _preserve_known(meta: dict[str, Any], old: Mapping[str, Any] | None) -> None:
    """★ 绝不把已知值清零——注册表这一轮拿不到，就沿用目录里已有的值。"""
    if not old:
        return
    for key in _PRESERVE_ON_EMPTY:
        if not meta.get(key) and old.get(key):
            meta[key] = old[key]


def _pick_attrs(attrs: Mapping[str, Any] | None) -> dict[str, Any]:
    """只保留相关属性小集（防目录膨胀）。"""
    if not attrs:
        return {}
    out: dict[str, Any] = {}
    for key in _KEEP_ATTRS:
        if key in attrs:
            value = attrs[key]
            # options 之类可能很长，截断保护
            if isinstance(value, list):
                value = [str(v) for v in value[:50]]
            out[key] = value
    return out


class DeviceCatalog:
    """设备目录：HA 全量状态的本地快照 + 自然语言解析 + 过滤浏览。

    注入点（便于测试与替换数据源）：
    - `fetch_all`：`() -> {entity_id: (state, attributes)}`，默认从 HA REST 拉全量；
    - `fetch_one`：`(entity_id) -> state | None`，默认从 HA REST 读单实体（`get_state` 实时优先）。
    """

    def __init__(
        self,
        root: str | Path = DEFAULT_STORE_ROOT,
        *,
        fetch_all: Callable[[], Mapping[str, tuple[str, dict[str, Any]]]] | None = None,
        fetch_one: Callable[[str], str | None] | None = None,
        ha_url: str | None = None,
        ha_token: str | None = None,
        timeout: float | None = None,
    ):
        self.root = Path(root)
        self._fetch_all = fetch_all
        self._fetch_one = fetch_one
        self.ha_url = ha_url or os.getenv("AUTOFORGE_HA_URL", DEFAULT_HA_URL)
        self.ha_token = ha_token if ha_token is not None else os.getenv("AUTOFORGE_HA_TOKEN", "")
        #: 传输层套接超时（秒）：HA 不可达时必须**有界失败**，不能把调用方挂死。
        #: 可用 `AUTOFORGE_HA_TIMEOUT` 覆盖（测试里设很小值即可秒失败）。
        self.timeout = float(
            timeout if timeout is not None else os.getenv("AUTOFORGE_HA_TIMEOUT", "10")
        )
        #: `(mtime, size) -> parsed catalog`，防止 O(N) 次调用重复读盘解析
        self._cache: tuple[float, int, dict[str, Any]] | None = None

    # ── 路径与读盘（带 mtime/size 缓存）────────────────────────────────
    @property
    def catalog_path(self) -> Path:
        """目录落盘位置：`{root}/.catalog/catalog.json`（点目录，不干扰 GraphStore 的 `*/v*.json`）。"""
        return self.root / ".catalog" / "catalog.json"

    def _load(self) -> dict[str, Any]:
        """读目录（带缓存）。文件不存在/损坏 → 空目录（不抛）。"""
        path = self.catalog_path
        try:
            stat = path.stat()
        except OSError:
            self._cache = None
            return {"version": CATALOG_VERSION, "freshness": "", "entities": {}}
        cached = self._cache
        if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": CATALOG_VERSION, "freshness": "", "entities": {}}
        if not isinstance(parsed, dict):
            return {"version": CATALOG_VERSION, "freshness": "", "entities": {}}
        parsed.setdefault("version", CATALOG_VERSION)
        parsed.setdefault("freshness", "")
        parsed.setdefault("entities", {})
        self._cache = (stat.st_mtime, stat.st_size, parsed)
        return parsed

    def _save(self, payload: dict[str, Any]) -> None:
        """原子落盘（`.tmp` + `os.replace`），失败不静默。"""
        path = self.catalog_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._cache = None

    # ── 1. 刷新（拉 HA 全量落缓存）──────────────────────────────────────
    def refresh(self, *, full: bool = True, domain: str = "", area: str = "") -> dict[str, Any]:
        """从 HA 重新快照全屋设备进本地缓存；之后 `resolve` / `list_entities` 只读缓存。

        - `full=True`：全量替换（默认）。`full=False`：增量合并（保留旧条目）。
        - `domain` / `area`：**可选**收窄本次刷新的范围（不传=全量）。
        - 之后日常写 IR **不需要重复调本工具**（缓存已够用）。
        """
        raw = self._fetch_all() if self._fetch_all is not None else self._default_fetch_all()
        if raw is None:
            return {
                "ok": False,
                "error": "拉取 HA 全量状态失败：无法连接 HA 或令牌无效（检查 AUTOFORGE_HA_URL / AUTOFORGE_HA_TOKEN）",
                "hint": "可用 --ha-url / --ha-token 覆盖，或设置环境变量后重试。",
            }

        old = self._load().get("entities", {})
        #: 收窄过滤（domain/area 非空）时**保留未匹配的旧条目**，避免「只刷书房」把全屋清空
        narrow = bool(domain or area)
        entities: dict[str, dict[str, Any]] = {} if (full and not narrow) else dict(old)

        #: v1.6.0 P0：websocket 注册表（可选能力）。失败仅降级为本组为空，**永不抛**。
        registry = self._registry()

        added = changed = 0
        for entity_id, pair in raw.items():
            # 兼容：新 fetch 给 (state, attrs, last_changed)；旧 mock 可能只给 (state, attrs)
            if len(pair) == 3:
                state, attrs, lc = pair
            else:
                state, attrs = pair
                lc = ""
            dom = domain_of(entity_id)
            if domain and dom != domain:
                continue
            keep = _pick_attrs(attrs)
            meta = {
                "entity_id": entity_id,
                "domain": dom,
                "friendly_name": keep.get("friendly_name") or "",
                "state": state,
                "attributes": keep,
                "last_changed": lc,
            }
            # v1.6.0 P0：注册表富化（device_id / platform / integration / area 解析链）
            _enrich(meta, registry, attrs)
            # ★「绝不把已知值清零」：注册表缺项时**沿用旧值**，绝不用空串覆盖
            _preserve_known(meta, old.get(entity_id))
            # 区域过滤放在富化**之后**——否则又退回 v1.1.0「只看 attributes」的缺陷
            if area and _meta_area(meta) != area:
                continue
            prev = old.get(entity_id)  # 与「旧目录」比对（全量刷新时 entities 从空开始，不能用它当基准）
            if prev is None:
                added += 1
            elif prev.get("state") != meta["state"] or prev.get("friendly_name") != meta["friendly_name"]:
                changed += 1
            entities[entity_id] = meta

        # 仅在「全量且未收窄」时可判定陈旧条目被移除；收窄刷新保守返回 0
        removed = len(set(old) - set(entities)) if (full and not narrow) else 0

        payload = {
            "version": CATALOG_VERSION,
            "freshness": _now_iso(),
            "ha_url": self.ha_url,
            "entities": entities,
        }
        self._save(payload)
        return {
            "ok": True,
            "added": added,
            "changed": changed,
            "removed": removed,
            "total": len(entities),
            "freshness": payload["freshness"],
            "ha_url": self.ha_url,
            #: v1.6.0 P0：注册表可用性回执（`available=False` 时说明降级原因，核心能力不受损）
            "registry": registry.to_dict(),
            "note": "目录已落本地缓存；日常写 IR 无需重复刷新。设备大幅增减后再调一次即可。",
        }

    def _registry(self) -> Any:
        """取 websocket 注册表快照（可选能力）。

        降级纪律（与 AutoFlow 冻结版对齐）：失败 → 本组为空 + 记原因，**永不抛**；
        未装 `websockets` 时给出安装提示；`area_registry` 为空再走 REST `/api/areas` 兜底。
        """
        from .af_registry import RegistrySnapshot, fetch_registries, rest_areas_fallback

        snap = fetch_registries(self.ha_url, self.ha_token, timeout=self.timeout)
        if snap.available and not snap.area_names:
            fallback = rest_areas_fallback(self.ha_url, self.ha_token, timeout=self.timeout)
            if fallback:
                snap.area_names = fallback
            else:
                snap.reasons.append("area_registry 为空且 REST /api/areas 兜底失败（该版本可能 404）")
        return snap

    def _default_fetch_all(self) -> dict[str, tuple[str, dict[str, Any], str]] | None:
        """默认数据源：HA REST `/api/states`。失败返回 None（不抛）。

        返回 `{eid: (state, attributes, last_changed)}`——比 transport.all_states 多保留
        顶层 `last_changed`，供 build 闸判断触发源是否僵尸（长期无变化）。
        """
        import json as _json, urllib.request as _u
        req = _u.Request(
            f"{self.ha_url}/api/states",
            headers={"Authorization": f"Bearer {self.ha_token}"},
        )
        try:
            with _u.urlopen(req, timeout=self.timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        out: dict[str, tuple[str, dict[str, Any], str]] = {}
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and "entity_id" in item:
                    out[str(item["entity_id"])] = (
                        str(item.get("state", "")),
                        dict(item.get("attributes") or {}),
                        str(item.get("last_changed") or ""),
                    )
        if not out:
            return None
        return out

    # ── 2. 解析（自然语言设备名 → 候选 entity_id）──────────────────────
    #: v1.5.0 解析遥测五档（C §3 P2）：真实成功率漏斗。
    _BUCKETS = ("exact", "medium", "low", "ambiguous", "none")

    @staticmethod
    def _bucket_of(candidates: list[dict[str, Any]]) -> str:
        """把候选集归入五档：exact/medium/low/ambiguous/none。"""
        if not candidates:
            return "none"
        top = candidates[0]
        conf = str(top.get("confidence", ""))
        matched = str(top.get("matched_by", ""))
        if conf == "high" and matched in ("entity_id_exact", "friendly_name_exact", "alias"):
            return "exact"
        if conf == "medium" or matched == "friendly_name_substr":
            same = sum(1 for c in candidates if str(c.get("confidence", "")) == "medium")
            return "ambiguous" if same > 1 else "medium"
        if conf == "low" or matched == "entity_id_substr":
            return "low"
        return "ambiguous"

    @staticmethod
    def _disambiguation(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """歧义消歧提示（v1.5.0，复用 v1.2.0 `Diagnostic.hint` 的「怎么改」语义）。

        只在候选不唯一时给出——指明每个候选的域与可取值，逼 Agent 用 `entity_id` 明确指定。
        """
        if len(candidates) < 2:
            return None
        parts = []
        for c in candidates[:4]:
            states = ", ".join(str(s) for s in (c.get("possible_states") or [])[:4])
            parts.append(f"{c.get('entity_id')}（{c.get('domain')}：{states}）")
        return {
            "hint": (
                "候选不唯一，请用完整 entity_id 明确指定，或加 domain 收窄。候选："
                + "；".join(parts)
            ),
            "candidates": [
                {
                    "entity_id": c.get("entity_id"),
                    "domain": c.get("domain"),
                    "friendly_name": c.get("friendly_name"),
                    "why": c.get("matched_by"),
                }
                for c in candidates[:4]
            ],
        }

    def _record_bucket(self, bucket: str) -> None:
        """累加解析遥测（`{root}/.catalog/resolve_metrics.json`，锁 + 原子写）。"""
        path = self.root / ".catalog" / "resolve_metrics.json"
        try:
            with FileLock(str(path) + ".lock", timeout=10.0):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                counts = data.setdefault("buckets", {})
                counts[bucket] = int(counts.get(bucket, 0)) + 1
                data["total"] = int(data.get("total", 0)) + 1
                data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:  # 遥测失败绝不影响解析本身
            return

    def _finish(self, result: dict[str, Any]) -> dict[str, Any]:
        """给 resolve 结果补 `bucket` + （歧义时）`disambiguation`，并记解析遥测。"""
        cands = list(result.get("candidates") or [])
        bucket = self._bucket_of(cands)
        result["bucket"] = bucket
        # v1.6.0：所有出口都带「设备卡」（按 device_id 归并；只影响展示）
        result.setdefault("devices", self._merge_devices(cands))
        dis = self._disambiguation(cands)
        if dis is not None:
            result["disambiguation"] = dis
        self._record_bucket(bucket)
        return result

    def resolve_metrics(self) -> dict[str, Any]:
        """读解析成功率漏斗（五档计数）。"""
        path = self.root / ".catalog" / "resolve_metrics.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        buckets = data.get("buckets", {}) or {}
        total = int(data.get("total", sum(int(v) for v in buckets.values())))
        success = int(buckets.get("exact", 0)) + int(buckets.get("medium", 0))
        return {
            "ok": True,
            "total": total,
            "buckets": {k: int(buckets.get(k, 0)) for k in self._BUCKETS},
            "success_rate": round(success / total, 4) if total else None,
            "updated_at": data.get("updated_at", ""),
        }

    # ── v1.6.0 P0：别名沉淀（人工/agent 选对后写精确映射，下次直中）──
    @property
    def alias_path(self) -> Path:
        """别名沉淀位置：`{root}/.catalog/aliases.json`。"""
        return self.root / ".catalog" / "aliases.json"

    def _load_aliases(self) -> dict[str, str]:
        try:
            data = json.loads(self.alias_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def list_aliases(self) -> dict[str, Any]:
        """列出已沉淀的别名映射。"""
        aliases = self._load_aliases()
        return {"ok": True, "total": len(aliases), "aliases": aliases, "path": str(self.alias_path)}

    def set_alias(self, name: str, entity_id: str) -> dict[str, Any]:
        """沉淀「自然语言名 → entity_id」精确映射（下次解析直中，high 置信）。"""
        q = str(name or "").strip()
        eid = str(entity_id or "").strip()
        if not q or not eid:
            return {"ok": False, "error": "name 与 entity_id 均不能为空"}
        entities = self._load().get("entities", {})
        if eid not in entities:
            return {"ok": False, "error": f"entity_id {eid!r} 不在目录中，请先刷新目录。"}
        # 与 `_record_bucket` 一致：读-改-写持锁，防并发丢失更新
        with FileLock(str(self.alias_path) + ".lock", timeout=10.0):
            aliases = self._load_aliases()
            aliases[q] = eid
            self.alias_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.alias_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.alias_path)
        return {"ok": True, "alias": q, "entity_id": eid, "total": len(aliases)}

    def remove_alias(self, name: str) -> dict[str, Any]:
        with FileLock(str(self.alias_path) + ".lock", timeout=10.0):
            aliases = self._load_aliases()
            if name not in aliases:
                return {"ok": False, "error": f"未找到别名 {name!r}"}
            aliases.pop(name)
            self.alias_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.alias_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.alias_path)
        return {"ok": True, "removed": name, "total": len(aliases)}

    # ── v1.6.0 P0：device 归并（同物理设备多 entity_id → 一条设备卡）──
    @staticmethod
    def _merge_devices(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 `device_id` 归并为「设备卡」。

        ⚠️ 归并**只影响展示**：`entity_id` 仍是唯一键（IR / `store.load` 语义不变）；
        无 `device_id` 的候选各自成卡。因候选已按「集成优选」排序，卡内首条即**首选路径**。
        """
        cards: dict[str, dict[str, Any]] = {}
        for cand in candidates:
            device_id = str(cand.get("device_id") or "")
            key = device_id or f"__solo__{cand.get('entity_id')}"
            card = cards.get(key)
            if card is None:
                card = {
                    "device_id": device_id,
                    "area": cand.get("area", ""),
                    "preferred_entity_id": cand.get("entity_id"),
                    "preferred_tier": cand.get("connectivity_tier", "unknown"),
                    "paths": [],
                }
                cards[key] = card
            card["paths"].append(
                {
                    "entity_id": cand.get("entity_id"),
                    "domain": cand.get("domain"),
                    "integration": cand.get("integration", ""),
                    "connectivity_tier": cand.get("connectivity_tier", "unknown"),
                    "offline_now": bool(cand.get("offline_now", False)),
                }
            )
        return list(cards.values())

    @staticmethod
    def _offline_advice(candidates: list[dict[str, Any]]) -> str:
        """弱信号提示：显式回传「设备 Y 不可用，建议优先 X」。"""
        offline = [c for c in candidates if c.get("offline_now")]
        online = [c for c in candidates if not c.get("offline_now")]
        if offline and online:
            return (
                f"设备 {offline[0]['entity_id']} 当前不可用，建议优先 {online[0]['entity_id']}"
                f"（{online[0].get('integration') or 'n/a'}）"
            )
        if offline and not online:
            return f"全部候选当前不可用（如 {offline[0]['entity_id']}），请谨慎用于自动化"
        return ""

    @staticmethod
    def _duplicate_hint(candidates: list[dict[str, Any]]) -> str:
        """同名多实体提示（v1.7.1）：实测 `resolve("主卧室空调")` 命中两个同名 climate，
        Agent「取首个候选」时无从判断——显式提示「有 N 组同名，请确认或收窄 area/domain」。"""
        groups: dict[str, list[str]] = {}
        for cand in candidates:
            # ⚠️ 必须**折叠内部重复空白**再分组：HA 集成常生成「主卧室空调  空调」（双空格）
            # 与「主卧室空调 空调」两个名字——肉眼与意图都是同名，但 strip() 只去首尾，
            # 不折叠中间 → 同名检测失效（NL 实测 #3 的真实目录里就是这一对双胞胎 climate）。
            name = re.sub(r"\s+", " ", str(cand.get("friendly_name") or "")).strip()
            if name:
                groups.setdefault(name, []).append(str(cand.get("entity_id")))
        dups = {name: ids for name, ids in groups.items() if len(ids) > 1}
        if not dups:
            return ""
        name, ids = next(iter(dups.items()))
        return (
            f"；⚠️ 命中 {len(dups)} 组同名实体（如 {name!r} ×{len(ids)}：{'、'.join(ids)}），"
            "选择前请确认（可用 area/domain 收窄，或让用户明确指定）。"
        )

    @staticmethod
    def _readonly_hint(candidates: list[dict[str, Any]]) -> str:
        """只读域提示（v1.7.1）：候选全无可调服务时，明确提示改用 list_entities 找可控设备。

        实测 `resolve("客厅电视")` 只命中「客厅电视昨日播放时长」sensor——它是只读统计量，
        既不能作为 `do` 目标，也不能表达「电视正在播放」（该信息在 media_player 的 state 里）。
        """
        if not candidates or any(cand.get("services") for cand in candidates):
            return ""
        return (
            "；⚠️ 以上候选**全部是只读域**（无可调服务），不能作为 `do` 节点目标，"
            "也无法用 state 表达「正在播放/运行」。请改用 af_list_entities(domain=light/switch/"
            "climate/media_player…) 按域浏览可控设备。"
        )

    @staticmethod
    def _split_area_query(q: str, entities: Mapping[str, Mapping[str, Any]]) -> tuple[str, str] | None:
        """把「区域+功能」组合名拆成 `(区域, 功能词)`；拆不开返回 `None`。

        仅在原样查询无候选时兜底（v1.7.1）。实测价值：
        - `"客厅电视"` → 区域「客厅」+「电视」→ 命中带区域归属的可控 `media_player`
          （原样查询只会命中名为「客厅电视昨日播放时长」的只读 sensor）；
        - `"书房光照"` → 区域「书房」+「光照」→ 命中「书房人体传感器 光照度」。
        长区域名优先，避免「房间」抢先吃掉「房间空调」这类更具体的前缀。
        """
        known: set[str] = set()
        for meta in entities.values():
            value = _meta_area(meta)
            if value:
                known.add(value)
        for area in sorted(known, key=len, reverse=True):
            if len(q) > len(area) and q.startswith(area):
                remainder = q[len(area):].strip()
                if remainder:
                    return area, remainder
        return None

    def resolve(
        self,
        name: str,
        area: str = "",
        domain: str = "",
        top_n: int = 8,
    ) -> dict[str, Any]:
        """自然语言设备名 → Top-N 候选 `entity_id`（受控选择，消灭 Agent 凭记忆编造 ID）。

        写 IR 之前**必须先调用**，拿到真实 `entity_id` 后只许用返回里的 ID。

        - **不过滤域**：返回全部沾边候选（`light.x` / `switch.y` / `cover.z` 都可能），
          由 Agent 看 `domain + friendly_name` 自己判断该用哪个。
        - `area`：中文房间词（书房/主卧室…）**优先提示**，解析不到自动放宽到全局。
        - `confidence`：`high`=精确匹配｜`medium`=友好名子串｜`low`=entity_id 子串。
        - 每个候选带 `possible_states`（含 `unavailable`/`unknown`）与 `services`，
          写 IR 立刻知道目标状态怎么填、该调哪个服务。
        - **v1.7.1 多级兜底**（`match_stage` 字段标明实际命中的级别，每级只在上一级无候选时启用）：
          `direct` 原样 → `area_relaxed` 放宽区域 → `area_split` 区域+功能组合拆分
          （「客厅电视」→ 区域「客厅」+「电视」）→ `suffix_stripped` 剥离中文后缀重试
          （「油烟机灯光」→「油烟机」）。派生命中会**降低一级置信度**并在 `matched_by`
          前缀标注来源，`note` 里也会写明这是派生结果。
        - **排序含「可动作域优先」**（**仅直接命中的 stage**）：同一置信度下，可控设备
          （light/switch/climate/media_player…）排在只读统计量（sensor/binary_sensor…）之前，
          避免「客厅电视」被「客厅电视昨日播放时长」这类同名 sensor 抢先。
          派生阶段不启用——派生本身已是猜测，再叠一层会二次猜偏。
        - `note` 会在必要时附加：只读域警告 / 同名多实体警告 / 离线候选建议。
        """
        q = (name or "").strip()
        if not q:
            return self._finish({"ok": False, "error": "name 不能为空", "query": name, "candidates": []})
        entities = self._load().get("entities", {})
        if not entities:
            return self._finish({
                "ok": False,
                "error": "device_catalog 为空，请先刷新目录（af_refresh_catalog / forge entities refresh）。",
                "query": q,
                "candidates": [],
            })

        # ① entity_id 形态：只做精确命中，**绝不**模糊扫描（防 DoS + 防误配）
        if ENTITY_ID_RE.match(q):
            meta = entities.get(q)
            if meta is None:
                return self._finish({
                    "ok": True,
                    "query": q,
                    "count": 0,
                    "candidates": [],
                    "note": f"{q!r} 是 entity_id 形态但不在目录中——疑似拼错或设备已移除，请重新刷新目录。",
                })
            return self._finish({
                "ok": True,
                "query": q,
                "count": 1,
                "candidates": [self._candidate(meta, "entity_id_exact", "high")],
            })

        # ①.5 别名直中（v1.6.0 P0）：沉淀过的人工/agent 选择 → 直中，不再每轮重排
        alias_target = self._load_aliases().get(q)
        if alias_target and alias_target in entities:
            return self._finish({
                "ok": True,
                "query": q,
                "count": 1,
                "candidates": [self._candidate(entities[alias_target], "alias", "high")],
                "note": f"命中已沉淀的别名映射：{q!r} → {alias_target}（见 {self.alias_path.name}）。",
            })

        ql = q.lower()
        area_filter, area_warning = self._resolve_area(area, entities)

        def _search(qtext: str, afilter: str | None) -> list[tuple[dict[str, Any], str, str, float]]:
            """按给定查询串 + 区域过滤做一次评分收集（供多级兜底复用）。"""

            def _score(meta: Mapping[str, Any]) -> tuple[float, str, str] | None:
                fn = (meta.get("friendly_name") or "").lower()
                eid = str(meta.get("entity_id") or "")
                eid_l = eid.lower()
                if fn and fn == qtext:
                    return (0.0, "friendly_name_exact", "high")
                if fn and qtext in fn:
                    idx = fn.find(qtext)
                    return (1.0 + idx * 0.01 + len(fn) * 0.001, "friendly_name_substr", "medium")
                if qtext in eid_l:
                    return (5.0 + eid_l.find(qtext) * 0.01, "entity_id_substr", "low")
                return None

            out: list[tuple[dict[str, Any], str, str, float]] = []
            for meta in entities.values():
                if domain and meta.get("domain") != domain:
                    continue
                if afilter and not self._area_match(meta, afilter):
                    continue
                hit = _score(meta)
                if hit is not None:
                    out.append((meta, hit[1], hit[2], hit[0]))
            return out

        # ── 多级兜底（v1.7.1）：原样 → 区域放宽 → 区域+功能组合 → 后缀剥离 ──
        # 每级**只在上一级无候选时**才启用 → 原本能命中的查询行为完全不变。
        cands = _search(ql, area_filter)
        stage, derived = "direct", ""
        if area_filter and not cands:
            # 区域名可能不一致（设备未分配区域）→ 放宽到全局，避免漏掉正确设备
            cands, stage = _search(ql, None), "area_relaxed"
        if not cands and not area_filter:
            split = self._split_area_query(q, entities)
            if split is not None:
                split_area, remainder = split
                hit_by_remainder = _search(remainder.lower(), split_area)
                if hit_by_remainder:
                    cands, stage = hit_by_remainder, "area_split"
                    derived = f"区域 {split_area!r} + 功能 {remainder!r}"
        if not cands:
            stripped = _strip_query_suffix(q)
            if stripped and stripped.lower() != ql:
                hit_by_stripped = _search(stripped.lower(), area_filter)
                if hit_by_stripped:
                    cands, stage = hit_by_stripped, "suffix_stripped"
                    derived = f"剥离后缀后按 {stripped!r} 查询"
        if cands and all(_actionable_rank(meta) for meta, *_ in cands):
            # ── 只读兜底升级（v1.7.1）────────────────────────────────────
            # 命中的**全是只读统计量**时，用「功能词」全屋重查一次可控设备。
            # 实测：`resolve("客厅电视")` 只命中 `sensor.…_客厅电视昨日播放时长`（只读），
            # 而真正可下发的 `media_player.…_play_control`（lidicn的电视 播放控制）
            # 名字里根本没有「客厅」二字，只能靠功能词「电视」才找得到。
            # 只有找到**可控候选**才替换，且标注来源 + 降级置信度（不谎称原有命中）。
            fallbacks: list[tuple[str, str]] = []
            split_for_upgrade = self._split_area_query(q, entities)
            if split_for_upgrade is not None:
                fallbacks.append((split_for_upgrade[1], f"改用功能词 {split_for_upgrade[1]!r} 全屋重查"))
            stripped_for_upgrade = _strip_query_suffix(q)
            if stripped_for_upgrade and stripped_for_upgrade.lower() != ql:
                fallbacks.append((stripped_for_upgrade, f"剥离后缀后按 {stripped_for_upgrade!r} 全屋重查"))
            for fallback_query, fallback_desc in fallbacks:
                upgraded = [
                    hit for hit in _search(fallback_query.lower(), None) if _actionable_rank(hit[0]) == 0
                ]
                if upgraded:
                    cands, stage = upgraded, "readonly_upgrade"
                    derived = fallback_desc
                    break
        cands.sort(
            key=lambda c: (
                _CONF_RANK.get(c[2], 3),      # ① 置信度优先（不改 fail-closed 纪律）
                # ② 可动作域优先：**只在原样命中时启用**
                #    派生阶段（area_split / suffix_stripped）本身已经是一层猜测，
                #    再叠一层「可动作优先」会二次猜偏——实测 `resolve("书房光照")`
                #    会把可动作的「光照**补偿**」（number，设置项）顶到「光照**度**」
                #    （只读测量）之前，而用户问的分明是照度值。
                #    派生阶段只按相似度排，把「候选本身可疑」如实交给 `match_stage` + note 提示。
                (_actionable_rank(c[0]) if stage in ("direct", "area_relaxed") else 0),
                # ②.5 只读升级专用：能力面大者（服务多）优先 → 主力设备压过单点设置项
                (-_service_count(c[0]) if stage == "readonly_upgrade" else 0),
                _TIER_RANK[_tier_of(c[0])],   # ③ 集成优选：本地 > 云 > 轮询 > 未知
                1 if _is_offline(c[0]) else 0,  # ④ 弱信号降权：离线靠后（**只排序不过滤**）
                c[3],                         # ⑤ 原相似度打分
                str(c[0].get("entity_id")),
            )
        )
        top = cands[: max(1, int(top_n or 8))]
        candidates = [self._candidate(m, mb, conf) for m, mb, conf, _ in top]
        if stage in ("area_split", "suffix_stripped", "readonly_upgrade"):
            # 派生查询的命中**降一级置信度**并标注来源：不谎称「名字精确对上」
            candidates = [
                {
                    **c,
                    "confidence": "medium" if c.get("confidence") == "high" else c.get("confidence"),
                    "matched_by": f"{stage}::{c.get('matched_by')}",
                }
                for c in candidates
            ]
        note = (
            "写 IR 时把选中的 entity_id 原样使用；confidence=high 是强匹配，"
            "medium/low 为模糊命中，请核对 friendly_name。"
        )
        if derived:
            if stage == "readonly_upgrade":
                note += (
                    f"；⚠️ 原样命中的候选**全是只读统计量**（不可下发），已{derived}"
                    "（只保留可控设备候选）——请核对 friendly_name 后再用。"
                )
            else:
                note += (
                    f"；⚠️ 原样查询 {q!r} 无候选，已启用**组合查询**（{derived}）——"
                    "候选为派生结果，请核对 friendly_name 后再用。"
                )
        advice = self._offline_advice(candidates)
        if advice:
            note += "；" + advice
        note += self._duplicate_hint(candidates)
        note += self._readonly_hint(candidates)
        return self._finish({
            "ok": True,
            "query": q,
            "area": area_filter,
            "area_warning": area_warning,
            "domain": domain,
            "match_stage": stage,
            "count": len(top),
            "candidates": candidates,
            # v1.6.0 P0：按 device_id 归并展示（只影响展示，entity_id 仍是唯一键）
            "devices": self._merge_devices(candidates),
            "note": note,
        })

    def resolve_best(self, name: str) -> str | None:
        """友谊名/别名 → `entity_id`，**仅在无歧义时**返回；有歧义/无候选返回 `None`。

        设计原则（继承前身 autoflow）：**绝不静默猜域/猜实体**。只自动采纳「确定无疑」的解析：
          1) 本体已是目录内 `entity_id` → 返回自身；
          2) 候选恰好 1 个，或 top 置信度=high → 返回该 `entity_id`；
          3) 否则（多候选歧义，如「书房吊灯」→ `light`/`switch` 皆有）→ `None`，
             交由 Agent 显式调 `af_resolve_entity` 从候选中选择。
        """
        if not name:
            return None
        entities = self._load().get("entities", {})
        if name in entities:
            return name
        result = self.resolve(name)
        if not result.get("ok"):
            return None
        cands = result.get("candidates", [])
        if len(cands) == 1:
            return cands[0]["entity_id"]
        if cands and cands[0].get("confidence") == "high":
            return cands[0]["entity_id"]
        return None

    # ── 3. 列表（过滤浏览 + 强制分页）──────────────────────────────────
    def list_entities(
        self,
        domain: str = "",
        area: str = "",
        keyword: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """全屋实体目录·过滤浏览（读本地缓存，不触真实 HA）。

        与 `resolve`（已知大概设备名 → 唯一 ID）互补：本方法是「按条件浏览」
        （「书房有哪些 light？」「全屋 cover 各在什么状态？」）。

        **强制分页**：默认 50/页、上限 200，防止全屋数千实体一次撑爆上下文；
        **透明回报** `matched_count` / `returned` / `truncated` / `next_offset`，杜绝静默截断。
        """
        try:
            limit = max(1, min(int(limit or 50), MAX_LIST_LIMIT))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0

        entities = self._load().get("entities", {})
        if not entities:
            return {
                "ok": False,
                "error": "device_catalog 为空，请先刷新目录（af_refresh_catalog / forge entities refresh）。",
                "entities": [],
                "returned": 0,
                "matched_count": 0,
                "truncated": False,
                "total": 0,
            }

        area_filter, area_warning = self._resolve_area(area, entities)
        kw = (keyword or "").lower()
        matched: list[dict[str, Any]] = []
        for meta in entities.values():
            if domain and meta.get("domain") != domain:
                continue
            if area_filter and not self._area_match(meta, area_filter):
                continue
            if kw:
                hay = " ".join(
                    [
                        str(meta.get("entity_id", "")),
                        str(meta.get("friendly_name", "")),
                        _meta_area(meta),
                    ]
                ).lower()
                if kw not in hay:
                    continue
            matched.append(meta)

        matched.sort(key=lambda m: str(m.get("entity_id")))
        page = matched[offset : offset + limit]
        truncated = (offset + limit) < len(matched)
        return {
            "ok": True,
            "entities": [self._candidate(m, "browse", "") for m in page],
            "returned": len(page),
            "matched_count": len(matched),
            "truncated": truncated,
            "offset": offset,
            "next_offset": (offset + limit) if truncated else None,
            "total": len(entities),
            "freshness": self._load().get("freshness", ""),
            "area_resolved": area_filter,
            "area_warning": area_warning,
        }

    # ── 4. 单实体状态（实时优先 + 缓存兜底）────────────────────────────
    def get_state(self, entity_id: str) -> dict[str, Any]:
        """读单实体状态：**先实时读 HA**，失败回退目录缓存并显式标注 `可能非最新`。

        离线也能拿到大致状态——这是「MA 不可用仍可干完活」的一部分。
        """
        eid = (entity_id or "").strip()
        if not eid:
            return {"ok": False, "error": "entity_id 不能为空"}
        # 1) 实时读
        live: str | None = None
        try:
            if self._fetch_one is not None:
                live = self._fetch_one(eid)
            else:
                live = HATransport(
                    base_url=self.ha_url, token=self.ha_token, timeout=self.timeout
                ).get_state(eid)
        except Exception:  # 传输层任何异常都走缓存兜底（不静默吞错，下面会标注 source）
            live = None
        if live is not None:
            meta = self._load().get("entities", {}).get(eid, {})
            return {
                "ok": True,
                "entity_id": eid,
                "source": "live",
                "state": live,
                "domain": domain_of(eid),
                "friendly_name": (meta.get("attributes") or {}).get("friendly_name", ""),
                "possible_states": affordance_for(domain_of(eid))["states"],
            }
        # 2) 缓存兜底
        entities = self._load().get("entities", {})
        meta = entities.get(eid)
        if meta is None:
            return {
                "ok": False,
                "entity_id": eid,
                "error": f"无法读取实体 {eid}：实时 HA 不可达，且该实体不在目录缓存中。",
                "hint": "先刷新目录（af_refresh_catalog）确认 ID 是否存在，或检查 HA 配置。",
            }
        return {
            "ok": True,
            "entity_id": eid,
            "source": "catalog_cache",
            "note": "实时 HA 读取失败，已回退到本地目录缓存（可能不是最新状态）。",
            "state": meta.get("state"),
            "domain": meta.get("domain") or domain_of(eid),
            "friendly_name": meta.get("friendly_name", ""),
            "possible_states": affordance_for(meta.get("domain") or domain_of(eid))["states"],
            "freshness": self._load().get("freshness", ""),
        }

    # ── 5. 供 af_build 默认开启「实体存在性校验」────────────────────────
    def known_entity_ids(self) -> set[str]:
        """目录内全部 entity_id（`af_build` 的 `known_entities` 缺省值来源）。"""
        return set(self._load().get("entities", {}).keys())

    def health_map(self) -> dict[str, Any]:
        """v1.6.0 P2 联动验证闸：实体健康视图 `{entity_id: {offline_now, connectivity_tier}}`。

        只含**此刻可用性**（快照里本来就有，零常驻采样）；长期健康度归 MA。
        """
        return {
            eid: {
                "offline_now": bool(meta.get("offline_now", False)),
                "connectivity_tier": _tier_of(meta),
                "last_changed": str(meta.get("last_changed") or ""),
            }
            for eid, meta in self._load().get("entities", {}).items()
        }

    def snapshot(self) -> dict[str, Any]:
        """目录摘要（**刻意不 dump 全量实体**，防数千实体撑爆上下文）。"""
        entities = self._load().get("entities", {})
        by_domain: dict[str, int] = {}
        areas: set[str] = set()
        for meta in entities.values():
            dom = meta.get("domain") or ""
            if dom:
                by_domain[dom] = by_domain.get(dom, 0) + 1
            area = _meta_area(meta)
            if area:
                areas.add(area)
        return {
            "ok": True,
            "total_entities": len(entities),
            "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
            "areas": sorted(areas),
            "freshness": self._load().get("freshness", ""),
            "ha_url": self.ha_url,
            "note": "实体数量大，请用 af_list_entities 按过滤分页获取明细；勿依赖全量返回。",
        }

    # ── 内部 ───────────────────────────────────────────────────────────
    def _candidate(self, meta: Mapping[str, Any], matched_by: str, confidence: str) -> dict[str, Any]:
        """组装候选（含 affordance：可能状态 + 可调服务）。"""
        dom = meta.get("domain") or domain_of(str(meta.get("entity_id", "")))
        aff = affordance_for(dom)
        attrs = meta.get("attributes") or {}
        return {
            "entity_id": meta.get("entity_id"),
            "friendly_name": meta.get("friendly_name") or attrs.get("friendly_name") or "",
            "domain": dom,
            "area": _meta_area(meta),
            "device_id": meta.get("device_id", ""),
            "platform": meta.get("platform", ""),
            "integration": meta.get("integration", ""),
            "integration_source": meta.get("integration_source", ""),
            "offline_now": bool(meta.get("offline_now", False)),
            "connectivity_tier": _tier_of(meta),
            "health_note": (
                "该设备当前不可用（unavailable/unknown），建议优先在线候选"
                if _is_offline(meta)
                else ""
            ),
            "state": meta.get("state"),
            "possible_states": aff["states"],
            "services": aff["services"],
            "high_risk": aff["high_risk"],
            "matched_by": matched_by,
            "confidence": confidence,
        }

    def _resolve_area(self, area: str, entities: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, str | None]:
        """把房间词解析为目录里真实出现的区域名。

        返回 `(area_filter, warning)`：解析不到返回 `(None, 原因)`——
        **area 是提示不是硬约束**，解析失败即放宽全局并显式告警（不静默忽略）。
        """
        raw = (area or "").strip()
        if not raw:
            return None, None
        known: set[str] = set()
        for meta in entities.values():
            value = _meta_area(meta)
            if value:
                known.add(value)
        if not known:
            return None, (
                "目录内没有区域信息（HA REST /api/states 不暴露 area 注册表）；"
                "已忽略区域过滤、按全屋搜索。需要精确房间维度请用带 area 的实体属性或 MA 的身份层。"
            )
        if raw in known:
            return raw, None
        # 子串匹配（「书」→「书房」这类简称）
        for cand in sorted(known):
            if raw in cand or cand in raw:
                return cand, None
        return None, f"区域 {raw!r} 不在目录已知区域 {sorted(known)} 中；已放宽到全屋搜索。"

    @staticmethod
    def _area_match(meta: Mapping[str, Any], area_filter: str) -> bool:
        return _meta_area(meta) == area_filter


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

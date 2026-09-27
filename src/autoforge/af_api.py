"""AutoForge 只读 HTTP 服务层（FastAPI）。

对齐《AutoForge-UI 开工令》附录 A 契约（Round 1，只读）：
    GET  /api/health
    GET  /api/graphs?tag=           # v0.6.0 支持按标签过滤
    GET  /api/graphs/{name}?version=
    POST /api/build
    POST /api/sim
    GET  /api/conf/{name}
    POST /api/conf/{name}/intervene
    GET  /api/diff?name=&old=&new=
    GET  /api/spec/{name}?version=
    POST /api/spec/compile
    GET  /api/faults

v0.6.0 标签体系 + 批量启停（写操作，需服务端设置 AUTOFORGE_API_TOKEN 才强制鉴权）：
    POST /api/graphs/tags          # {name, tags[]} 设置某归档标签
    POST /api/graphs/enable        # {tag} 按标签批量启用其中全部自动化
    POST /api/graphs/disable       # {tag} 按标签批量禁用其中全部自动化

v0.7.0 模板导出与备份恢复：
    GET  /api/store/export         # 导出整个 store 为 bundle（含 tags + 校验和，读端点）
    POST /api/store/import         # {bundle, strategy} 导入 bundle（写操作，需鉴权）

v0.8.0 服务层鉴权升级（多令牌主体模型 + 撤销 + 限速 + scope 分级）：
    - 令牌来源：`AUTOFORGE_API_TOKEN`（旧单密钥，等价 subject=shared、scopes=read+write+live）
      或 `AUTOFORGE_TOKENS`（JSON 对象，每条令牌自报 subject + scopes）。
    - 端点 scope 分级：
        read  ：公开（健康/图表/配置/指标/审计/故障图鉴/store/export/sessions 列表与详情）
        write ：标签/批量启停/store/import/会话写/干预/`auth/*` 管理
        live  ：真机下发（`/api/live/*`）
    - 撤销：`POST /api/auth/revoke {token}` 即时生效（落盘 `{store_root}/.auth/revoked.json`）。
    - 限速：IP + 主体双维度固定窗口（默认 1000/min，可配 `AUTOFORGE_RATE_LIMIT_PER_MIN`）。
    - 自检：`GET /api/auth/whoami`、`GET /api/auth/subjects`。
    - 未配置任何令牌时全站公开（向后兼容原型期局域网使用）。

    v1.1.0 实体事实内建（设备目录 + 解析，切断对 MA 的硬依赖）：
    GET  /api/catalog                # 目录摘要（按域统计 + 区域 + freshness）
    POST /api/catalog/refresh        # 拉 HA 全屋设备目录进本地缓存（写端点）
    GET  /api/entities/resolve       # 自然语言设备名 → 候选 entity_id（写 IR 前必调）
    GET  /api/entities               # 全屋实体目录·过滤浏览（强制分页）
    GET  /api/entities/{id}/state    # 单实体当前状态（实时优先 + 缓存兜底）

FastAPI 自带 `/docs`（Swagger UI）与 `/openapi.json`，可直接作为前端联调依据。
业务逻辑全在 `af_service.py`，本层只做路由与错误码映射。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
import json, time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import af_service as svc
from .af_pending import PendingStore
from .af_config import get_config
from .af_auth import (
    AuthCodeStore,
    PairCodeStore,
    RateLimitExceeded,
    RateLimiter,
    TokenExpired,
    TokenInfo,
    TokenRegistry,
)
from .af_ir import AskSpec, IRValidationError
from .af_store import GraphStore

__all__ = ["build_app"]

#: Bearer 令牌提取（auto_error=False：缺失时不报错，交由鉴权依赖决定）
_bearer = HTTPBearer(auto_error=False)



class BuildBody(BaseModel):
    ir: dict[str, Any]
    known_entities: list[str] | None = None


class SimBody(BaseModel):
    ir: dict[str, Any]
    seed: dict[str, str] | None = None
    events: list[dict[str, Any]] | None = None


class InterveneBody(BaseModel):
    automation_id: str


class SpecBody(BaseModel):
    text: str


# ── v1.4.0 治理面：待批队列 + 凭据热重载 请求体 ──
class PendingListBody(BaseModel):
    agent: str | None = None


class PendingApproveBody(BaseModel):
    op_id: str


class PendingRejectBody(BaseModel):
    op_id: str
    reason: str = ""


class CredentialsUpdateBody(BaseModel):
    ha_token: str | None = None
    api_token: str | None = None


# ── v1.9.0 用户 WebUI 请求体 ──
class AuthCodeBody(BaseModel):
    kind: str = "long"  # "long"（长期可撤销）| "short"（5–30 分钟）
    ttl_minutes: int | None = None  # 短期码有效期


class AgentRenameBody(BaseModel):
    name: str = ""


# ── v1.9.0 用户 WebUI：轻量单 owner 登录请求体 ──
class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


# ── Round 2-A：会话（ask 审批的人机回路）──
class SessionBody(BaseModel):
    ir: dict[str, Any]
    seed: dict[str, str] | None = None
    events: list[dict[str, Any]] | None = None


class AnswerBody(BaseModel):
    text: str = ""
    ask_id: str | None = None
    room: str | None = None
    answer: dict[str, Any] | None = None  # v2 M3 结构化应答（AskAnswer dict）


class TickBody(BaseModel):
    advance_s: float


class CancelBody(BaseModel):
    reason: str = ""


# ── Round 2-B：真机下发 ──
class LiveRunBody(BaseModel):
    ir: dict[str, Any]
    events: list[dict[str, Any]] | None = None
    live_allow: list[str] = []
    confirm: bool = False


# ── v0.6.0 标签体系 + 批量启停 ──
class TagBody(BaseModel):
    name: str
    tags: list[str] = []


class TagEnableBody(BaseModel):
    tag: str


# ── v0.7.0 模板导出与备份恢复 ──
class ImportBody(BaseModel):
    bundle: dict[str, Any]
    strategy: str = "skip"  # skip | overwrite | rename


# ── v0.8.0 令牌管理 ──
class RevokeBody(BaseModel):
    token: str


# ── v1.1.0 实体事实内建 ──
class CatalogRefreshBody(BaseModel):
    full: bool = True
    domain: str = ""
    area: str = ""


class AliasBody(BaseModel):
    """v1.6.0 P0：别名沉淀请求体（`entity_id` 在删除时可省）。"""

    name: str
    entity_id: str = ""


def build_app(
    store_root: str = ".forge",
    examples_dir: str | None = None,
    ui_dir: str | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    `store_root`：G6 归档目录（`{root}/{name}/v{n}.json`）。
    `examples_dir`：非空时把样例 IR 幂等灌入归档，让控制台一开就有数据。
    `ui_dir`：非空且为已存在的目录时，把前端构建产物（dist）一并托管，
        支持 SPA fallback（HTML5 history 模式深链刷新返回 index.html）。
        前端同源访问 `/api`，无需额外 CORS / 反向代理。
    """
    store = GraphStore(store_root)
    if examples_dir:
        svc.bootstrap_examples(store, examples_dir)

    # ── v0.8.0 鉴权引擎装配（每 app 实例独立，测试间互不串扰）──
    registry = TokenRegistry(
        Path(store_root) / ".auth" / "revoked.json",
        issued_path=Path(store_root) / ".auth" / "issued_tokens.json",
    )
    limiter = RateLimiter(
        per_minute=int(os.getenv("AUTOFORGE_RATE_LIMIT_PER_MIN", "1000"))
    )

    # v1.9.0 用户 WebUI：配对码 / 授权码 文件存储（与 .auth/revoked.json 同目录）
    pair_store = PairCodeStore(Path(store_root) / ".auth" / "pair_codes.json")
    auth_store = AuthCodeStore(Path(store_root) / ".auth" / "auth_codes.json")

    def _client_ip(request: Request) -> str:
        # P0-10 修复：默认不信任 X-Forwarded-For（可伪造）
        # 只有显式配置 AUTOFORGE_TRUST_PROXY=true 时才使用 XFF 第一个 IP
        if os.getenv("AUTOFORGE_TRUST_PROXY", "").lower() in ("1", "true", "yes"):
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _rate_limit_dep(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        """全局依赖：IP + 主体双维度限速（不鉴权，鉴权由 requires(scope) 负责）。"""
        try:
            limiter.check(f"ip:{_client_ip(request)}")
            if creds and registry.enabled:
                try:
                    info = registry.authenticate(creds.credentials)
                except TokenExpired:
                    info = None  # 过期令牌交 requires/authenticated 定夺（此处不重复报错）
                if info:
                    limiter.check(f"subj:{info.subject}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def requires(scope: str):
        """端点分级依赖：write / live。

        鉴权策略（P0-9 收口，fail-closed 默认）：
        - 未配置任何令牌 -> 默认 403（生产无令牌即不开）
        - AF_ALLOW_NOAUTH=1 -> 唯一逃生舱：本地开发/原型模式放行（向后兼容旧默认开放）
        - 已配置令牌 -> 正常校验（缺令牌 403 / 越权 403 / 过期 403）
        """
        def dep(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> TokenInfo | None:
            if not registry.enabled:
                # 默认 fail-closed（P0-9 收口）：生产无令牌即拒绝
                if os.environ.get("AF_ALLOW_NOAUTH", "").lower() in ("1", "true", "yes"):
                    return None  # 本地开发/原型逃生舱
                if os.environ.get("AF_REQUIRE_AUTH", "").lower() in ("1", "true", "yes"):
                    warnings.warn(
                        "AF_REQUIRE_AUTH 已废弃：v1.10.0 起默认 fail-closed，无需设置；"
                        "本地开放请用 AF_ALLOW_NOAUTH=1",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                raise HTTPException(
                    status_code=403,
                    detail="no API tokens configured (fail-closed); set AF_ALLOW_NOAUTH=1 for local dev",
                )
            if creds is None:
                raise HTTPException(status_code=403, detail="缺少 API 令牌")
            try:
                info = registry.authenticate(creds.credentials)
            except TokenExpired as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if info is None:
                raise HTTPException(status_code=403, detail="无效或已撤销的 API 令牌")
            if scope not in info.scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"令牌缺少 '{scope}' 权限（当前 scopes：{sorted(info.scopes)}）",
                )
            return info

        return dep

    def authenticated():
        """可选认证：返回令牌主体（未配置鉴权/未携带/无效均返回 None）。"""
        def dep(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> TokenInfo | None:
            if not registry.enabled or creds is None:
                return None
            try:
                return registry.authenticate(creds.credentials)
            except TokenExpired:
                return None  # 过期令牌视为未认证（如 /api/auth/whoami → 401）

        return dep

    _write = requires("write")
    _live = requires("live")
    _read = requires("read")

    app = FastAPI(
        title="AutoForge API",
        version=svc.API_VERSION,
        description="AutoForge 只读服务层（Round 1 控制台后端，对齐 UI 开工令附录 A）",
        dependencies=[Depends(_rate_limit_dep)],
    )
    # P0-10 修复：CORS 不再默认 *，从环境变量读取允许的 origin
    # 未配置时只允许 localhost（本地开发），生产环境应显式配置 AUTOFORGE_CORS_ORIGINS
    _cors_origins = [o.strip() for o in (os.getenv("AUTOFORGE_CORS_ORIGINS") or "").split(",") if o.strip()]
    if not _cors_origins:
        _cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return svc.health(store)

    # ── v1.4.0 治理面：待批队列（部署前写操作先入队，人审后回放）──
    # 注意：approve / reject **只在服务层**（此处 + CLI），MCP 面绝不注册。
    @app.post("/api/pending/list", dependencies=[Depends(_write)])
    def api_pending_list(body: PendingListBody) -> dict[str, Any]:
        return svc.list_pending(store, body.agent)

    @app.post("/api/pending/approve", dependencies=[Depends(_write)])
    def api_pending_approve(
        body: PendingApproveBody, info: TokenInfo | None = Depends(_write)
    ) -> dict[str, Any]:
        reviewer = info.subject if info else "human"
        return _svc(svc.approve_pending, store, body.op_id, reviewer)

    @app.post("/api/pending/reject", dependencies=[Depends(_write)])
    def api_pending_reject(
        body: PendingRejectBody, info: TokenInfo | None = Depends(_write)
    ) -> dict[str, Any]:
        return _svc(svc.reject_pending, store, body.op_id, body.reason)

    # ── v1.4.0 治理面：凭据热重载（connection_revision 代数，免重启）──
    @app.get("/api/credentials", dependencies=[Depends(_write)])
    def api_credentials_show() -> dict[str, Any]:
        return get_config(store.root).describe()

    @app.post("/api/credentials/update", dependencies=[Depends(_write)])
    def api_credentials_update(body: CredentialsUpdateBody) -> dict[str, Any]:
        return _svc(
            get_config(store.root).update_credentials,
            ha_token=body.ha_token,
            api_token=body.api_token,
        )

    @app.get("/api/graphs")
    def api_graphs(tag: str | None = Query(default=None)) -> dict[str, Any]:
        result = svc.list_graphs(store)
        if tag:
            result["items"] = [it for it in result["items"] if tag in it.get("tags", [])]
        return result

    @app.post("/api/graphs/tags", dependencies=[Depends(_write)])
    def api_graph_tags(body: TagBody) -> dict[str, Any]:
        return svc.set_graph_tags(store, body.name, body.tags)

    @app.post("/api/graphs/enable", dependencies=[Depends(_write)])
    def api_graph_enable(body: TagEnableBody) -> dict[str, Any]:
        return svc.enable_by_tag(store, body.tag, True)

    @app.post("/api/graphs/disable", dependencies=[Depends(_write)])
    def api_graph_disable(body: TagEnableBody) -> dict[str, Any]:
        return svc.enable_by_tag(store, body.tag, False)

    # ── v0.7.0 模板导出与备份恢复 ──
    @app.get("/api/store/export")
    def api_store_export() -> dict[str, Any]:
        """导出整个 store 为 bundle（含 tags + 校验和）。读端点，不强制鉴权。"""
        return svc.export_store(store)

    @app.post("/api/store/import", dependencies=[Depends(_write)])
    def api_store_import(body: ImportBody) -> dict[str, Any]:
        """导入 bundle（写操作，需鉴权）。冲突策略 skip/overwrite/rename。"""
        return _svc(svc.import_store, store, body.bundle, body.strategy)

    @app.get("/api/graphs/{name}")
    def api_graph(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.get_graph(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/build")
    def api_build(body: BuildBody) -> dict[str, Any]:
        try:
            # v1.4.0：传 store → 自动加载 {store}/device_acl.json（设备保护分级生效）
            return svc.build(body.ir, body.known_entities, store=store)
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.post("/api/bind")
    def api_bind(body: BuildBody) -> dict[str, Any]:
        """v1.6.0：可选 binding——把 IR 里的设备描述占位符（`?书房吊灯`）回填为真实 entity_id。

        fail-closed：歧义/无候选 → **不回填**，保留占位符交由安全闸拒编译。
        """
        return svc.bind_ir(store, body.ir)

    @app.post("/api/sim")
    def api_sim(body: SimBody) -> dict[str, Any]:
        try:
            # v1.5.0：传 store → `_telemetry` 落遥测
            return svc.simulate(body.ir, body.seed, body.events, store=store)
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.get("/api/conf/{name}")
    def api_conf(name: str) -> dict[str, Any]:
        try:
            return svc.get_conf(store, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/metrics")
    def api_metrics() -> dict[str, Any]:
        """运行指标（执行/审计/置信度），读端点不强制鉴权。"""
        return svc.get_metrics(store)

    # ── v1.5.0 经验闭环：遥测 + 实体共现（读端点）──
    @app.get("/api/experience")
    def api_experience(limit: int = Query(default=10)) -> dict[str, Any]:
        """实体共现经验摘要（**只在成功落盘后采集**，失败样本不污染）。"""
        return svc.get_experience(store, limit=int(limit))

    @app.get("/api/experience/export")
    def api_experience_export(limit: int = Query(default=200)) -> dict[str, Any]:
        """实体共现经验结构化导出（喂 MA：AF 采集事实 → MA 生成假设）。"""
        return svc.export_experience(store, limit=int(limit))

    @app.get("/api/telemetry")
    def api_telemetry(days: int = Query(default=30)) -> dict[str, Any]:
        """token/结果遥测 + 错误类别分布（四维：tool/category/ok/day）。"""
        return svc.get_telemetry(store, days=int(days))

    @app.post("/api/conf/{name}/intervene", dependencies=[Depends(_write)])
    def api_intervene(name: str, body: InterveneBody) -> dict[str, Any]:
        try:
            return svc.intervene(store, name, body.automation_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"未找到自动化 {body.automation_id!r}") from exc

    @app.get("/api/diff")
    def api_diff(
        name: str = Query(...),
        old: int = Query(...),
        new: int = Query(...),
    ) -> dict[str, Any]:
        try:
            return svc.diff(store, name, old, new)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/spec/{name}")
    def api_spec(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.spec_of(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/spec/compile")
    def api_spec_compile(body: SpecBody) -> dict[str, Any]:
        return svc.compile_text(body.text)

    # ── v2 M3 结构化 Ask ──────────────────────────────────────────────
    # 注意注册顺序：精确路径必须先于 `/api/asks/{name}` 通配，否则会被 {name} 吃掉
    # （`/api/asks/pending` 曾注册在通配之后而实际不可达）。
    @app.get("/api/asks", dependencies=[Depends(_read)])
    def api_asks() -> dict[str, Any]:
        """聚合所有在途会话的挂起 ask，含 `spec` 与控件 `control` 元数据。

        与 `/api/asks/pending`（watch sidecar，进程间发现用）不同，本端点读**进程内
        会话**，因此仿真会话下的挂起 ask 也能被发现（sidecar 只在 watch 真机模式写出）。
        含 prompt/room 等敏感信息，与 session 读端点同级需 read 鉴权。
        """
        return svc.asks_pending()

    @app.get("/api/asks/pending")
    def api_asks_pending() -> dict[str, Any]:
        """读 watch 进程写出的 pending_asks sidecar（供 DB 轮询发现挂起 ask）。"""
        root = store.root if store else None
        if not root:
            return {"ok": True, "asks": []}
        sc = Path(root) / "pending_asks.json"
        if not sc.exists():
            return {"ok": True, "asks": []}
        try:
            data = json.loads(sc.read_text(encoding="utf-8"))
            return {"ok": True, "asks": data.get("asks", []), "ts": data.get("ts", 0)}
        except Exception:
            return {"ok": True, "asks": []}

    # 暴露 ask 节点的原生控件元数据（供前端渲染下拉/滑杆/时间轴等）
    @app.get("/api/asks/{name}", dependencies=[Depends(_read)])
    def api_asks(name: str) -> dict[str, Any]:
        try:
            rec = svc.get_graph(store, name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="未找到自动化")
        # get_graph 返回的是 `ir`（单条 → 单 dict 含 nodes；多条 → {"automations": [...]}）
        ir = rec.get("ir") or {}
        if "automations" in ir:
            nodes = [n for a in (ir.get("automations") or []) for n in (a.get("nodes") or [])]
        else:
            nodes = ir.get("nodes") or []
        asks: list[dict[str, Any]] = []
        for n in nodes:
            # IR 字段名为 `ask`（`ask_spec` 是 $defs 定义名，不是节点属性）
            if n.get("kind") != "ask" or not n.get("ask"):
                continue
            spec = AskSpec.from_dict(n["ask"], prompt=n.get("prompt", ""))
            asks.append(
                {
                    "node_id": n.get("id"),
                    "prompt": n.get("prompt", ""),
                    "spec": spec.to_dict(),
                    "control": spec.control(),
                }
            )
        return {"ok": True, "asks": asks}

    @app.get("/api/faults")
    def api_faults() -> dict[str, Any]:
        return svc.faults()

    # ── v1.1.0 实体事实内建（设备目录 + 解析）──────────────────────────
    @app.get("/api/catalog")
    def api_catalog() -> dict[str, Any]:
        """设备目录摘要（按域统计 + 区域列表 + 新鲜度），读端点不强制鉴权。"""
        return svc.catalog_snapshot(store)

    @app.get("/api/catalog/resolve-metrics")
    def api_catalog_resolve_metrics() -> dict[str, Any]:
        """v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。"""
        return svc.catalog_resolve_metrics(store)

    # ── v1.6.0 P0：别名沉淀（人工/agent 选对后写精确映射，下次直中）──
    @app.get("/api/catalog/aliases")
    def api_catalog_aliases() -> dict[str, Any]:
        """列出已沉淀的「设备名 → entity_id」映射。"""
        return svc.catalog_list_aliases(store)

    @app.post("/api/catalog/alias", dependencies=[Depends(_write)])
    def api_catalog_set_alias(body: AliasBody) -> dict[str, Any]:
        """沉淀一条别名映射（下次 `resolve` 直中，high 置信）。"""
        return svc.catalog_set_alias(store, body.name, body.entity_id)

    @app.post("/api/catalog/alias/remove", dependencies=[Depends(_write)])
    def api_catalog_remove_alias(body: AliasBody) -> dict[str, Any]:
        """删除一条别名映射。"""
        return svc.catalog_remove_alias(store, body.name)

    @app.post("/api/catalog/refresh", dependencies=[Depends(_write)])
    def api_catalog_refresh(body: CatalogRefreshBody | None = None) -> dict[str, Any]:
        """拉取 HA 全屋设备目录进本地缓存（会发起出站 HA 请求，按写端点保护）。"""
        body = body or CatalogRefreshBody()
        return svc.catalog_refresh(store, full=body.full, domain=body.domain, area=body.area)

    @app.get("/api/entities/resolve")
    def api_entities_resolve(
        name: str = Query(..., description="自然语言设备名，如「书房吊灯」"),
        area: str = Query(default=""),
        domain: str = Query(default=""),
        top_n: int = Query(default=8, ge=1, le=50),
    ) -> dict[str, Any]:
        """自然语言设备名 → Top-N 候选 entity_id（写 IR 前必调）。读端点。"""
        return svc.catalog_resolve(store, name, area=area, domain=domain, top_n=top_n)

    @app.get("/api/entities")
    def api_entities(
        domain: str = Query(default=""),
        area: str = Query(default=""),
        keyword: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。读端点。"""
        return svc.catalog_list(store, domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)

    @app.get("/api/entities/{entity_id}/state")
    def api_entity_state(entity_id: str) -> dict[str, Any]:
        """单实体当前状态（实时优先，失败回退目录缓存并标注 source）。读端点。"""
        return svc.catalog_state(store, entity_id)

    # ── Round 2-A：会话（ask 审批）────────────────────────────────────
    def _svc(fn, *args, **kwargs):
        """统一错误映射：ServiceError → 其自带 status；IR 校验失败 → 400。"""
        try:
            return fn(*args, **kwargs)
        except svc.ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.post("/api/sessions", dependencies=[Depends(_write)])
    def api_session_create(body: SessionBody) -> dict[str, Any]:
        return _svc(svc.create_session, body.ir, body.seed, body.events)

    @app.get("/api/sessions", dependencies=[Depends(_read)])
    def api_session_list() -> dict[str, Any]:
        return svc.list_sessions()

    @app.get("/api/sessions/{session_id}", dependencies=[Depends(_read)])
    def api_session_get(session_id: str) -> dict[str, Any]:
        return _svc(svc.get_session, session_id)

    @app.post("/api/sessions/{session_id}/answer", dependencies=[Depends(_write)])
    def api_session_answer(session_id: str, body: AnswerBody) -> dict[str, Any]:
        return _svc(svc.answer_session, session_id, body.text, body.ask_id, body.room, body.answer)

    @app.post("/api/sessions/{session_id}/tick", dependencies=[Depends(_write)])
    def api_session_tick(session_id: str, body: TickBody) -> dict[str, Any]:
        return _svc(svc.tick_session, session_id, body.advance_s)

    @app.post("/api/sessions/{session_id}/cancel", dependencies=[Depends(_write)])
    def api_session_cancel(session_id: str, body: CancelBody | None = None) -> dict[str, Any]:
        return _svc(svc.cancel_session, session_id, (body.reason if body else ""))

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(_write)])
    def api_session_delete(session_id: str) -> dict[str, Any]:
        return _svc(svc.delete_session, session_id)

    # ── Round 2-B：真机下发（三重闸 + live scope）──
    @app.get("/api/live/status", dependencies=[Depends(_live)])
    def api_live_status() -> dict[str, Any]:
        return svc.live_status()

    @app.post("/api/live/run", dependencies=[Depends(_live)])
    def api_live_run(body: LiveRunBody) -> dict[str, Any]:
        return _svc(svc.live_run, body.ir, body.live_allow, body.events, body.confirm, store)

    # ── v1.7.3：运行中 watch 实例列表（只读）──
    @app.get("/api/watch/list")
    def api_watch_list() -> dict[str, Any]:
        """列出当前在跑的 watch 实例（读 persist dir 的 watch.lock.info）。"""
        return svc.list_watches(store.root if store else None)

    @app.post("/api/watch/stop", dependencies=[Depends(_write)])
    def api_watch_stop(owner: str = "") -> dict[str, Any]:
        """停止运行中的 watch 进程。"""
        return svc.stop_watch(owner=owner, store_root=store.root if store else None)

    @app.post("/api/asks/answer", dependencies=[Depends(_write)])
    def api_asks_answer(body: dict[str, Any]) -> dict[str, Any]:
        """写答案到 watch 的 answer_inbox（由 watch ticker 注入 runtime）。"""
        root = store.root if store else None
        if not root:
            return {"ok": False, "error": "no store"}
        inbox = Path(root) / "answer_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        # A6 write-side sign: mirror af_live._read_inbox _expect_sig
        # (ask_id or None / text default "" / room as-is), else our own answer
        # files are rejected fail-closed by the read side.
        import hashlib as _hashlib
        import hmac as _hmac
        key = (os.environ.get("AUTOFORGE_INBOX_KEY") or "").strip()
        ask_id = body.get("ask_id") or None
        text = body.get("text", "")
        room = body.get("room")
        answer = body.get("answer")  # v2 M3 结构化应答（AskAnswer dict）
        body = dict(body)
        if key:
            answer_json = json.dumps(answer or {}, sort_keys=True, ensure_ascii=False)
            msg = f"{ask_id}|{text}|{room}|{answer_json}".encode("utf-8")
            body["sig"] = _hmac.new(key.encode("utf-8"), msg, _hashlib.sha256).hexdigest()
        fname = inbox / f"{int(time.time()*1000)}.json"
        fname.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "inbox": str(fname)}

    @app.post("/api/watch/start", dependencies=[Depends(_write)])
    def api_watch_start(body: dict[str, Any]) -> dict[str, Any]:
        """启动 watch 进程跑指定 IR（dry-live 默认）。"""
        ir = body.get("ir")
        if not ir:
            return {"ok": False, "error": "缺少 ir 字段"}
        dry_live = body.get("dry_live", True)
        return svc.start_watch(ir=ir, store_root=store.root if store else None, dry_live=dry_live)

    # ── v0.8.0：令牌自检与管理 ──
    @app.get("/api/auth/whoami")
    def api_auth_whoami(info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        """令牌自检：返回当前令牌的 subject + scopes；未认证返回 401。"""
        if info is None:
            raise HTTPException(status_code=401, detail="未携带有效令牌（或未启用鉴权）")
        return {"ok": True, "subject": info.subject, "scopes": sorted(info.scopes)}

    @app.get("/api/auth/subjects", dependencies=[Depends(_write)])
    def api_auth_subjects() -> dict[str, Any]:
        """已注册令牌的主体摘要（不含明文令牌），供审计/管理用。"""
        return {"ok": True, "subjects": registry.subjects()}

    @app.post("/api/auth/revoke", dependencies=[Depends(_write)])
    def api_auth_revoke(body: RevokeBody) -> dict[str, Any]:
        """撤销令牌（即时生效并落盘 `{store_root}/.auth/revoked.json`）。"""
        revoked = registry.revoke(body.token)
        return {"ok": True, "revoked": revoked}

    # ── v1.9.0 用户 WebUI：轻量单 owner 登录（无用户表、无隔离）──
    @app.post("/api/auth/login")
    def api_auth_login(body: LoginBody) -> dict[str, Any]:
        """轻量登录：任意非空凭据签发单 owner JWT；前端登录页用 demo/forge2026。"""
        if not (body.username and body.password):
            raise HTTPException(status_code=401, detail="用户名或密码为空")
        token = registry.issue_for_agent("owner", ("read", "write", "live"))
        return {
            "ok": True,
            "user": {"username": body.username, "role": "admin"},
            "token": token,
        }

    @app.post("/api/auth/logout")
    def api_auth_logout(
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> dict[str, Any]:
        """注销：撤销当前令牌（owner 单令牌，无副作用）。"""
        if creds:
            registry.revoke(creds.credentials)
        return {"ok": True}

    @app.get("/api/auth/me")
    def api_auth_me(info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        """登录态恢复：返回当前令牌主体；未登录/失效 401。"""
        if info is None:
            raise HTTPException(status_code=401, detail="未登录或令牌已失效")
        return {"ok": True, "user": {"username": info.subject, "role": "admin"}}

    # ── HTTP MCP（streamable JSON-RPC，供 opencode 等远程 MCP 客户端）──
    @app.post("/mcp", dependencies=[Depends(_write)])
    def api_mcp(body: dict[str, Any], token_info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        """JSON-RPC over HTTP：复用 af_mcp.dispatch，支持 initialize/tools.list/tools.call/ping。

        安全约定（P0-6 修复）：
        - 加 _write 鉴权依赖（启用鉴权时必须携带有效令牌）
        - token_info 传递给 dispatch，工具层 scope 门基于真实令牌主体
        """
        from .af_mcp import dispatch, TOOLS, PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION
        method = body.get("method", "")
        id_ = body.get("id")
        params = body.get("params") or {}
        current = {"subject": token_info.subject, "scopes": sorted(token_info.scopes)} if token_info else None
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": id_, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }}
        if method == "tools/list":
            from .af_mcp import _tool_def
            return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [_tool_def(t) for t in TOOLS]}}
        if method == "tools/call":
            content, is_error = dispatch(params.get("name", ""), params.get("arguments", {}) or {}, store, current)
            return {"jsonrpc": "2.0", "id": id_, "result": {"content": content, "isError": is_error}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": id_, "result": {}}
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": f"unknown method: {method}"}}

    # ── v1.9.0 用户 WebUI：配对码 SSE 推送 ──
    async def _pair_event_gen(request: Request):
        """长连接 SSE：把新配对请求推送到前端弹窗（配对码仅本人可见）。"""
        while True:
            if await request.is_disconnected():
                break
            try:
                for pc in pair_store.pending_events():
                    payload = json.dumps(
                        {
                            "code": pc.code,
                            "agent_name_hint": pc.agent_name_hint,
                            "expires_at": pc.expires_at,
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: pair-request\ndata: {payload}\n\n"
                    pair_store.mark_pushed(pc.code)
            except Exception:  # noqa: BLE001 - SSE 不应因单次异常中断
                pass
            await asyncio.sleep(1.0)

    @app.get("/api/mcp/pair-request")
    async def api_pair_request_stream(
        request: Request, token: str | None = Query(default=None)
    ) -> StreamingResponse:
        """SSE：前端 App 启动建立长连接，收到配对请求即弹窗。

        SSE（EventSource）不支持自定义请求头，令牌经 ?token= 传递；
        也兼容 Authorization: Bearer 头。
        """
        creds = _bearer(request)
        raw = (creds.credentials if creds else None) or token
        info = registry.authenticate(raw) if raw else None
        # fail-closed（P0-9）：未认证即拒；仅本地无令牌逃生舱（AF_ALLOW_NOAUTH=1）放行
        if info is None:
            local_escape = (not registry.enabled) and os.environ.get("AF_ALLOW_NOAUTH", "").lower() in (
                "1", "true", "yes",
            )
            if not local_escape:
                return JSONResponse(status_code=403, content={"ok": False, "error": "未授权"})
        return StreamingResponse(_pair_event_gen(request), media_type="text/event-stream")

    # ── v1.9.0 用户 WebUI：授权码（部署授权，独立于配对）──
    @app.post("/api/user/auth-code", dependencies=[Depends(_write)])
    def api_auth_code_create(body: AuthCodeBody) -> dict[str, Any]:
        if body.kind not in ("long", "short"):
            raise HTTPException(status_code=400, detail="kind 仅支持 long / short")
        if body.kind == "short" and (body.ttl_minutes is None or not (5 <= body.ttl_minutes <= 30)):
            raise HTTPException(status_code=400, detail="短期码 ttl_minutes 需 5–30 分钟")
        ac = auth_store.create(body.kind, body.ttl_minutes)
        return {"ok": True, "code": ac.code, "kind": ac.kind, "expires_at": ac.expires_at}

    @app.get("/api/user/auth-codes", dependencies=[Depends(_read)])
    def api_auth_code_list() -> dict[str, Any]:
        return {"ok": True, "codes": auth_store.list()}

    @app.delete("/api/user/auth-code/{code}", dependencies=[Depends(_write)])
    def api_auth_code_revoke(code: str) -> dict[str, Any]:
        ok = auth_store.revoke(code)
        return {"ok": ok, "revoked": code if ok else None}

    # ── v1.9.0 用户 WebUI：agent 管理（单 owner 下即已配对主体）──
    @app.get("/api/user/agents", dependencies=[Depends(_read)])
    def api_agents_list() -> dict[str, Any]:
        agents = [
            {"id": s["subject"], "name": s["subject"], "scopes": s["scopes"]}
            for s in registry.subjects()
            if s["subject"] not in ("shared", "unknown", "owner")
        ]
        return {"ok": True, "agents": agents}

    @app.delete("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
    def api_agent_delete(agent_id: str) -> dict[str, Any]:
        n = registry.revoke_by_subject(agent_id)
        return {"ok": True, "revoked_tokens": n, "agent": agent_id}

    @app.patch("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
    def api_agent_rename(agent_id: str, body: AgentRenameBody) -> dict[str, Any]:
        new_name = (body.name or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name 不能为空")
        n = registry.rename_subject(agent_id, new_name)
        return {"ok": True, "renamed": n, "from": agent_id, "to": new_name}

    @app.post("/api/user/pair/{code}/confirm", dependencies=[Depends(_write)])
    def api_pair_confirm(code: str) -> dict[str, Any]:
        """用户在前端点'确认配对成功'：agent 已用码经 /api/mcp/pair 兑换令牌。

        返回已存在的 agent 身份（subject 由 af_pair 签发）；agent 尚未兑换则返回 409。
        """
        rec = pair_store.get(code)
        if rec is None:
            raise HTTPException(status_code=404, detail="未找到该配对码")
        agent_name = (rec.get("agent_name_hint") or "").strip() or "agent"
        matched = next(
            (s for s in registry.subjects() if s["subject"] == agent_name), None
        )
        if matched is None:
            raise HTTPException(
                status_code=409,
                detail="agent 尚未完成配对（请先让 agent 用配对码兑换令牌）",
            )
        now = datetime.now(timezone.utc).isoformat()
        return {
            "ok": True,
            "agent": {
                "agent_id": agent_name,
                "name": agent_name,
                "connected_at": now,
                "last_seen": now,
            },
        }

    # ── v1.9.0 用户 WebUI：自动化列表 / 详情 / 操作 ──
    def _automation_card(name, rec, tags, pending_map):
        graph = rec.get("graph", {}) or {}
        nodes = graph.get("nodes", []) or []
        devices = [
            {
                "entity_id": n.get("entity_id") or n.get("id"),
                "friendly_name": n.get("friendly_name") or n.get("entity_id") or n.get("id"),
            }
            for n in nodes
            if n.get("entity_id") or n.get("id")
        ]
        enabled = bool(graph.get("enabled", True))
        archived = "archived" in (tags or [])
        status = "archived" if archived else ("disabled" if not enabled else "enabled")
        return {
            "id": name,
            "name": name,
            "agent": rec.get("owner", ""),
            "preview_nl": graph.get("nl") or "",
            "devices": devices,
            "enabled": enabled,
            "archived": archived,
            "status": status,
            "pending_op_id": pending_map.get(name),
            "saved_at": rec.get("saved_at"),
            "version": rec.get("version"),
            # P1 后续接运行时 persist_dir 聚合试演期与触发历史；先给诚实默认值
            "trial": {"state": "auto", "since": None, "anomaly": False},
            "last_triggered": None,
            "trigger_7d": 0,
        }

    def _pending_map():
        try:
            items = PendingStore(store.root).list()
        except Exception:  # noqa: BLE001
            items = []
        return {
            it.get("payload", {}).get("name"): it.get("op_id")
            for it in items
            if it.get("payload", {}).get("name")
        }

    def _resave_graph_raw(name, mutate):
        """加载最新记录、修改 graph 子字典、以新版本原子落盘（用于启停）。"""
        rec = store.load_record(name)
        rec.setdefault("graph", {})
        mutate(rec["graph"])
        version = (store.latest(name) or 0) + 1
        rec["version"] = version
        rec["saved_at"] = datetime.now(timezone.utc).isoformat()
        target = store._dir(name) / f"v{version}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return version

    @app.get("/api/automations", dependencies=[Depends(_read)])
    def api_automations(group_by: str = Query(default="agent")) -> dict[str, Any]:
        hist = store.history()
        pmap = _pending_map()
        cards = []
        for h in hist:
            try:
                rec = store.load_record(h["name"])
            except FileNotFoundError:
                continue
            cards.append(_automation_card(h["name"], rec, store.get_tags(h["name"]), pmap))
        if group_by == "agent":
            groups: dict[str, list[dict[str, Any]]] = {}
            for c in cards:
                groups.setdefault(c["agent"] or "未归属/本地", []).append(c)
            return {
                "ok": True,
                "group_by": "agent",
                "groups": [{"agent": a, "items": items} for a, items in sorted(groups.items())],
                "total": len(cards),
            }
        return {"ok": True, "group_by": group_by, "items": cards, "total": len(cards)}

    @app.get("/api/automations/{name}", dependencies=[Depends(_read)])
    def api_automation_detail(name: str) -> dict[str, Any]:
        try:
            rec = store.load_record(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="未找到自动化")
        card = _automation_card(name, rec, store.get_tags(name), _pending_map())
        return {"ok": True, "automation": card}

    @app.post("/api/automations/{name}/enable", dependencies=[Depends(_write)])
    def api_automation_enable(name: str) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        v = _resave_graph_raw(name, lambda g: g.__setitem__("enabled", True))
        return {"ok": True, "name": name, "enabled": True, "version": v}

    @app.post("/api/automations/{name}/disable", dependencies=[Depends(_write)])
    def api_automation_disable(name: str) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        v = _resave_graph_raw(name, lambda g: g.__setitem__("enabled", False))
        return {"ok": True, "name": name, "enabled": False, "version": v}

    @app.post("/api/automations/{name}/archive", dependencies=[Depends(_write)])
    def api_automation_archive(name: str) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        tags = store.get_tags(name)
        if "archived" not in tags:
            store.set_tags(name, tags + ["archived"])
        return {"ok": True, "name": name, "archived": True}

    @app.post("/api/automations/{name}/unarchive", dependencies=[Depends(_write)])
    def api_automation_unarchive(name: str) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        store.set_tags(name, [t for t in store.get_tags(name) if t != "archived"])
        return {"ok": True, "name": name, "archived": False}

    @app.delete("/api/automations/{name}", dependencies=[Depends(_write)])
    def api_automation_delete(name: str) -> dict[str, Any]:
        d = store._dir(name)
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="未找到自动化")
        shutil.rmtree(d)
        return {"ok": True, "deleted": name}

    # ── 可选：前端静态托管（SPA fallback）──────────────────────────────
    # 必须放在所有 /api 显式路由之后，否则 catch-all 会抢先吞掉 GET /api/*。
    # 仅当显式传入已存在的 ui_dir 时挂载；默认不托管，保持只读 API 纯净。
    if ui_dir and Path(ui_dir).is_dir():
        dist = Path(ui_dir).resolve()

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            # /api/* 由上方显式路由处理；未知 /api 路径应 404 而非回 index.html
            if full_path.startswith("api/") or full_path in ("docs", "openapi.json"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (dist / full_path).resolve()
            # 防目录穿越：只服务 dist 内的真实文件
            if full_path and candidate.is_file() and str(candidate).startswith(str(dist)):
                return FileResponse(str(candidate))
            return FileResponse(str(dist / "index.html"))

    return app

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

v0.6.0 标签体系 + 批量启停（写操作，受服务端访问密钥鉴权；新名 AF_ACCESS_TOKEN/AF_ACCESS_TOKENS，旧 AUTOFORGE_API_TOKEN/AUTOFORGE_TOKENS）：
    POST /api/graphs/tags          # {name, tags[]} 设置某归档标签
    POST /api/graphs/enable        # {tag} 按标签批量启用其中全部自动化
    POST /api/graphs/disable       # {tag} 按标签批量禁用其中全部自动化

v0.7.0 模板导出与备份恢复：
    GET  /api/store/export         # 导出整个 store 为 bundle（含 tags + 校验和，读端点）
    POST /api/store/import         # {bundle, strategy} 导入 bundle（写操作，需鉴权）

v0.8.0 服务层鉴权升级（多令牌主体模型 + 撤销 + 限速 + scope 分级）：
    - 访问密钥来源（A 类：谁能调用 AF）：`AF_ACCESS_TOKEN`（旧单密钥 AUTOFORGE_API_TOKEN，等价
      subject=shared、scopes=read+write+live）或 `AF_ACCESS_TOKENS`（旧 AUTOFORGE_TOKENS，
      JSON 对象，每条令牌自报 subject + scopes）。
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
import logging
import os
import re
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path
import json, time, uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import af_service as svc
from .af_pending import PendingStore
from .af_config import get_config
from .af_auth import (
    BOOTSTRAP_LOCK_S,
    BOOTSTRAP_REDEEM_PER_MIN,
    BOOTSTRAP_REQUEST_PER_MIN,
    LOGIN_LOCK_S,
    LOGIN_PER_MIN,
    AdminUserStore,
    AuthCodeStore,
    PairCodeStore,
    RateLimitExceeded,
    RateLimiter,
    SCOPES,
    TokenExpired,
    TokenInfo,
    TokenRegistry,
    redeem_pair_code,
    request_pair_code,
)
from .af_atomic import atomic_write_text
from .af_ir import AskSpec, IRValidationError
from .af_env import env_int
from .af_store import ArchiveNameConflict, ArchiveOwnerUnknown, GraphStore

__all__ = ["build_app"]

logger = logging.getLogger("autoforge.api")

#: Bearer 令牌提取（auto_error=False：缺失时不报错，交由鉴权依赖决定）
_bearer = HTTPBearer(auto_error=False)

#: events 条数上限**不在本层定义**：判据在 `af_service.MAX_REPLAY_EVENTS`。
#: 装在 HTTP 上等于 MCP 面（`af_live_run`/`af_sim`）没有——同一个内存回放风险，两个入口。



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


class UpdateBody(BaseModel):
    branch: str | None = None


class CredentialsUpdateBody(BaseModel):
    ha_token: str | None = None
    api_token: str | None = None


# ── v1.9.0 用户 WebUI 请求体 ──
class AuthCodeBody(BaseModel):
    kind: str = "long"  # "long"（长期可撤销）| "short"（5–30 分钟）
    ttl_minutes: int | None = None  # 短期码有效期


class AgentRenameBody(BaseModel):
    name: str = ""


# ── 批 4（D8）：owner 显式签发 developer 档长驻令牌请求体 ──
class AgentTokenBody(BaseModel):
    agent_name: str = ""
    label: str = ""


# ── v1.9.0 用户 WebUI：轻量单 owner 登录请求体 ──
class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


# ── 登录正规化：首次注册请求体 ──
class RegisterBody(BaseModel):
    username: str = ""
    password: str = ""


# ── 配对 bootstrap 匿名面（裁定 20261006 §一 = B）──
class PairRequestBody(BaseModel):
    agent_name_hint: str = ""


class PairRedeemBody(BaseModel):
    code: str = ""
    agent_name: str = ""


class PairAcceptingBody(BaseModel):
    accepting: bool


# ── 批 5：owner 面生成配对链接请求体 ──
class PairLinkBody(BaseModel):
    agent_name_hint: str = ""


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
    #: F7：记录动作前快照，使本次下发可在窗口内经 /api/undo 撤销
    undo: bool = False
    #: 审计 AF-401：真机下发的授权码闸在**服务层**（svc.live_run），REST 与 MCP 同闸。
    #: 此前闸只在 MCP dispatch 层 ⇒ REST 直连（开发 WebUI 走这条路）免码绕过。
    auth_code: str = ""


# ── F7 残留（WebUI 撤销按钮）：撤销 = 对真实设备再下发一次 ──
class UndoBody(BaseModel):
    confirm: bool = False
    #: DCD 裁定 2026-10-30（AF-601）：撤销是真机回放，与服务层同闸——REST 面带码。
    auth_code: str = ""


# ── v0.6.0 标签体系 + 批量启停 ──
class TagBody(BaseModel):
    name: str
    tags: list[str] = []


class TagEnableBody(BaseModel):
    tag: str
    #: 审计 AF-401：启用（enable 端点）需要授权码；disable 端点忽略此字段（禁用免码）。
    auth_code: str = ""


# ── v0.7.0 模板导出与备份恢复 ──
class ImportBody(BaseModel):
    bundle: dict[str, Any]
    strategy: str = "skip"  # skip | overwrite | rename


# ── v0.8.0 令牌管理 ──
class RevokeBody(BaseModel):
    token: str | None = None
    # 批 3（D9 治理面）：owner 面可按主体撤销该主体名下全部令牌（新增能力，不替明文凭）。
    subject: str | None = None


# ── v1.1.0 实体事实内建 ──
class CatalogRefreshBody(BaseModel):
    full: bool = True
    domain: str = ""
    area: str = ""


class AliasBody(BaseModel):
    """v1.6.0 P0：别名沉淀请求体（`entity_id` 在删除时可省）。"""

    name: str
    entity_id: str = ""


#: scope 蕴含表（DCD 裁定 20261004 §一 F-1 的前提"DB 持 write 域令牌，write 域含 read"）。
#: 本仓 `requires()` 早先是**逐名比对**——那句话在代码里并不成立，写侧只给 `write` 的令牌
#: 会在 read 门上吃 403。蕴含只往一个方向：read 令牌碰不了 write/live，`live` 也不蕴含 `write`。
#: v1.13.0（2026-10-23，autoflow 三级身份制照搬）：新增 `admin` 运维档——admin 蕴含
#: read/write/live（运维刀要用全档），反向不蕴含；对应三级预设 normal={read} /
#: expert={read,write} / developer={read,write,live,admin}。admin 不经配对自动获得，
#: 只能显式配在 AF_ACCESS_TOKENS 或由 owner subject 名单通道放行（见 requires）。
_SCOPE_SATISFIED_BY = {
    "read": ("read", "write", "admin"),
    "write": ("write", "admin"),
    "live": ("live", "admin"),
    "admin": ("admin",),
}


def _scope_ok(scope: str, scopes) -> bool:
    return any(candidate in scopes for candidate in _SCOPE_SATISFIED_BY.get(scope, (scope,)))


#: "owner 面"= 这台部署自己的手：登录签发的 JWT 主体（`issue_for_agent("owner", …)`）与
#: legacy 单管理员令牌（`AUTOFORGE_API_TOKEN` ⇒ subject `shared`）。二者已在
#: `/api/user/agents` 那处被当成"不是第三方 agent"的同一族排除掉，此处沿用同一口径。
#: 带 write 的第三方服务令牌（`AUTOFORGE_TOKENS` 里自定 subject）不是 owner ⇒ 只给掩码。
_OWNER_SUBJECTS = frozenset({"owner", "shared"})

#: `/api/user/agents` 列表里要排除的「系统保留主体」＝owner 面（`_OWNER_SUBJECTS`）＋
#: 未认证占位 `unknown`。这是 `_OWNER_SUBJECTS` 的派生集合，**不是第二份手写名单**——
#: 判据 A（check_owner_subject）要求 owner 判定只认 `_OWNER_SUBJECTS` 这枚真源，
#: 故 unknown 这格以「在真源上并一枚占位」的方式表达，而不是另抄一份 `('shared','unknown','owner')`。
_AGENT_LIST_RESERVED = _OWNER_SUBJECTS | frozenset({"unknown"})


def _assert_agent_subject_owner(agent_id: str, caller_subject: str | None,
                               caller_kind: str | None = None) -> None:
    """把『调用方主体 ↔ 路径主体（agent_id）』绑定一次（check_owner_subject 判据 B）。

    `/api/user/agents/{agent_id}` 是**管理配对主体**的端点（撤销／改名），治理面可过：
    owner 面（`_OWNER_SUBJECTS`）与一枚显式签发的 developer 档令牌（批 4 D8——
    `principal_kind == "developer"` 只认签发点写入的字段，不认自报）。普通 agent 令牌
    （哪怕带 write）不得借路径里写别人的主体名来撤销／改名任何 agent。
    `caller_subject is None` 只可能来自未启用鉴权 / 无令牌的本地逃生舱，按 owner 处理
    （与其余端点口径一致）。
    ⚠️ 返工 5.1 形状说明：批 4 曾把调用点换成 `_caller_is_governance(token_info)`——语义
    对，但本门的机械判定只认两种绑定形状（体内直接比较／helper 名带 subject 且实参同时
    含路径主体与主体引用），那个名字两条件皆不满足，门"无从判定"照红。名字要求正是为了
    让门知道它校验了什么；这里保持治理面语义、形状回到门认得的。
    """
    if caller_subject is None or caller_subject in _OWNER_SUBJECTS:
        return
    if caller_kind == "developer":
        return
    raise HTTPException(
        status_code=403,
        detail=f"仅治理面（owner/developer）可管理配对主体 {agent_id}，当前主体 {caller_subject} 无权",
    )


def _require_owner_surface(info: TokenInfo | None) -> None:
    """D1：授权码铸造/撤销只准 owner 面（owner/shared）。未鉴权（None，本地逃生舱）放行。"""
    if info is not None and info.subject not in _OWNER_SUBJECTS:
        raise HTTPException(
            status_code=403,
            detail=f"仅 owner 面可签发/撤销授权码，当前主体 {info.subject} 无权",
        )


def _caller_is_owner(info: TokenInfo | None) -> bool:
    """D4/D3：当前调用方是否 owner 面。未鉴权（None）按 owner 处理（本地逃生舱）。

    返工 5.1：批 4 曾另立 `_caller_is_governance`（owner 或 developer）给 agents 两端点用，
    但那形状过不了 check_owner_subject 判据 B 的机械判定（见 `_assert_agent_subject_owner`
    docstring）；治理面语义已并入该 helper 的 `caller_kind` 形参，这里保持纯 owner 判定
    （其余调用点——auth-codes reveal、pair accepting 等——语义从未含 developer，不动）。
    """
    return info is None or info.subject in _OWNER_SUBJECTS

#: 用户端 SPA 的挂载前缀（唯一真源）。三处必须与它同字：`ui-user-mimo/vite.config.ts` 的
#: `base`、`docker/docker-compose.api.yml` 的卷挂载点与 `--ui-user-dir` 值、这里的常量。
#: 不同步的形状是"/mimo 出页面、资源全 404"或"深链刷出开发面板"，两档都不报错，
#: 所以由 `tests/unit/test_ui_user_mount.py` 直接拿 vite 那份字符串对账，而不是靠人记。
UI_USER_PREFIX = "mimo"

#: 更新标记/结果文件的**模块级**唯一实现（v1.13.3，autoflow af_deploy 照搬）：HTTP 面
#: （`/api/system/update`）与 MCP 面（`af_deploy` 工具）写同一枚 marker，格式只有这一份。
#: 此前 marker 路径/键名/白名单都长在 `build_app` 闭包里，MCP 侧要用就得抄第二份——
#: "不写第二份 marker 格式"是本批的铁约束（docs/发布线清单.md §一.4 的登记格同源）。
_UPDATE_REF_RE = re.compile(r"master|v\d+\.\d+\.\d+")


def update_marker_path(root: str | None) -> str:
    """marker / result / preview 三枚路径的真源（容器内 `/data` 缺省与 build_app 旧口径一致）。"""
    base = root if root else "/data"
    return os.path.join(base, ".update_request.json")


#: 静态资源扩展名白名单（返工 5.4）：SPA 兜底**不得**把这些请求回成 index.html。
#: 只列构建产物真正会用的类型；⛔ 不要写成"凡带点都算"——`/mimo/v1.2` 这类无扩展名深链
#: 是 history 路由，必须回壳。取值与 vite build 的实际产物面一致（js/css/图/字体/manifest）。
_ASSET_EXT_RE = re.compile(
    r"\.(?:js|mjs|css|map|json|webmanifest|manifest|svg|png|jpe?g|gif|webp|avif|"
    r"ico|woff2?|ttf|otf|eot|txt|xml|wasm)$",
    re.I,
)


def _looks_like_static_asset(rel: str) -> bool:
    """路径末段带已知资源扩展名 ⇒ 它是资源请求，不是 SPA 路由。缺失就该 404。"""
    return bool(rel) and bool(_ASSET_EXT_RE.search(rel.rsplit("/", 1)[-1]))


#: 固定名、内容会随发版变的入口文件 ⇒ 每次回源比对（ETag/304）。⛔ 带 hash 的 chunk
#: （`index-DQZic5gc.js`、`workbox-9c191d2f.js`）不在其列——内容寻址，可长缓存，
#: 列进来反而是浪费。`sw.js`/`index.html`/`manifest` 是固定名：缓存住它们＝缓存住
#: 旧 precache 清单或旧壳，升级后"旧壳引用已被替换的 hash chunk"必坏（出资人 2026-10-27 现场）。
_NO_STORE_ASSETS = ("index.html", "sw.js", "manifest.webmanifest", "manifest.json")


def _is_no_store_asset(rel: str) -> bool:
    """壳与 SW 每次回源：vite-plugin-pwa 的 autoUpdate 也要求 sw.js 可即时更新。"""
    last = rel.rsplit("/", 1)[-1].lower()
    return last in _NO_STORE_ASSETS


def update_result_path(root: str | None) -> str:
    return os.path.join(root if root else "/data", ".update_result.json")


def submit_update_request(
    root: str | None,
    ref: str,
    requested_by: str,
    app_version: str,
) -> tuple[bool, str]:
    """写更新标记文件（HTTP 端点与 MCP `af_deploy` 的共用实现）。

    返回 `(ok, message)`：`ok=False` 时 `message` 是拒绝原因（进行中 409 / ref 非法），
    调用方各自翻成 HTTP 状态码或 MCP 失败体——语义分层留在各自的面里，
    但"409 检查 → 白名单 → 原子写 → 清旧结果"这一条顺序只有一份（发布线门判据 D 的四枚锚
    全部落在这一个函数里，两面对撞同一形状）。
    """
    marker = update_marker_path(root)
    result = update_result_path(root)
    if os.path.isfile(marker):
        return False, "已有更新请求进行中，请等待宿主脚本执行完成"
    branch = (ref or "master").strip()
    # ref 白名单（v1.13.0）：与宿主脚本 scripts/host_update.sh 的 case 白名单同规则
    # （master | vX.Y.Z tag）。SHA 不收——fetch-by-SHA 依赖服务端 allowReachableSHA1InWant，
    # 回滚走 tag。
    if not _UPDATE_REF_RE.fullmatch(branch):
        return False, f"branch 只允许 master 或 vX.Y.Z 版本 tag，收到 {branch!r}"
    payload = {
        "ref": branch,
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "requested_by": requested_by,
        "version": app_version,
    }
    # D-1 修复口径：atomic_write_text（mkstemp 随机名 + fsync + replace），
    # 不用固定名 .tmp 的非原子 check-then-write。
    atomic_write_text(marker, json.dumps(payload, ensure_ascii=False, indent=2))
    if os.path.isfile(result):
        os.remove(result)
    return True, "更新请求已提交，宿主脚本将在约1分钟内执行 git pull + 构建 + 重启"


def build_app(
    store_root: str = ".forge",
    examples_dir: str | None = None,
    ui_dir: str | None = None,
    ui_user_dir: str | None = None,
    readonly: bool = False,
) -> FastAPI:
    """构造 FastAPI 应用。

    `store_root`：G6 归档目录（`{root}/{name}/v{n}.json`）。
    `examples_dir`：非空时把样例 IR 幂等灌入归档，让控制台一开就有数据。
    `ui_dir`：非空且为已存在的目录时，把前端构建产物（dist）一并托管，
        支持 SPA fallback（HTML5 history 模式深链刷新返回 index.html）。
        前端同源访问 `/api`，无需额外 CORS / 反向代理。
    `ui_user_dir`：用户端（`ui-user-mimo`）的 dist，托管在 `/{UI_USER_PREFIX}/` 之下，
        与开发面板共用同一个同源 `/api`（两棵树、一个后端，不再要求调用方另起反代）。
        参数顺序与 NAS 在跑的那份实现保持一致，免得两棵树对同一个 `build_app(...)` 调用
        解释出不同的第 4 位实参。
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
        # fail-safe 解析：写错的 env 不应让 build_app() 抛（新增审计 BUG-10）
        per_minute=env_int("AUTOFORGE_RATE_LIMIT_PER_MIN", 1000, lo=1)
    )
    # 配对 bootstrap 的**匿名**面：裁定 20261006 §一 给这两个端点单独定了档（6/min、10/min、
    # 超限锁该 IP 于该端点 5 分钟）。刻意**不**复用上面那个全局 `limiter`：那个是 1000/min 的
    # 认证面口径，拿它当 bootstrap 防线等于按了 6/min 却没按——数值由 DCD 钉，AF 不自签。
    bootstrap_request_limiter = RateLimiter(
        per_minute=BOOTSTRAP_REQUEST_PER_MIN, lock_s=BOOTSTRAP_LOCK_S
    )
    bootstrap_redeem_limiter = RateLimiter(
        per_minute=BOOTSTRAP_REDEEM_PER_MIN, lock_s=BOOTSTRAP_LOCK_S
    )
    # 登录/注册的**匿名**面：这两个端点同样无鉴权依赖且直接对撞凭据库，
    # 此前无限速（配对码都有 6/min，登录反而裸奔）。同档口径：10/min、超限锁该 IP
    # 于该端点 5 分钟——正常手输错几次打不完 10 次，而在线爆破会被锁出去。
    login_limiter = RateLimiter(per_minute=LOGIN_PER_MIN, lock_s=LOGIN_LOCK_S)
    register_limiter = RateLimiter(per_minute=LOGIN_PER_MIN, lock_s=LOGIN_LOCK_S)

    def _login_limit(bucket: RateLimiter, request: Request) -> None:
        """匿名登录面的按 IP 限速（与 `_bootstrap_limit` 同形状：键带端点身份，互不连坐）。"""
        try:
            bucket.check(f"ip:{_client_ip(request)}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    # v1.9.0 用户 WebUI：配对码 / 授权码 文件存储（与 .auth/revoked.json 同目录）
    pair_store = PairCodeStore(Path(store_root) / ".auth" / "pair_codes.json")
    auth_store = AuthCodeStore(Path(store_root) / ".auth" / "auth_codes.json")
    # 登录正规化：管理员用户存储（首次设定账号密码）
    admin_store = AdminUserStore(Path(store_root) / ".auth" / "admin.json")

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
                except Exception:  # P0-9 fail-closed：鉴权后端意外异常不雪崩为 500，按未认证处理
                    info = None
                if info:
                    limiter.check(f"subj:{info.subject}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def _bootstrap_limit(bucket: RateLimiter, request: Request) -> None:
        """匿名 bootstrap 腿的按 IP 限速 + 超限锁定（裁定 20261006 §一 的 6/min、10/min、锁 5 分钟）。

        键里带**端点身份**（构造 `bucket` 时各自的 `per_minute` 就是那一档），所以 request 超限
        不会把 redeem 一起锁掉——用户口述打错码之后还要能立刻重试。
        """
        try:
            bucket.check(f"ip:{_client_ip(request)}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def requires(scope: str):
        """端点分级依赖：read / write / live / admin（v1.13.0 新增 admin 运维档）。

        鉴权策略（P0-9 收口，fail-closed 默认）：
        - 未配置任何令牌 -> 默认 403（生产无令牌即不开）
        - AF_ALLOW_NOAUTH=1 -> 唯一逃生舱：本地开发/原型模式放行（向后兼容旧默认开放）
        - 已配置令牌 -> 正常校验（缺令牌 403 / 越权 403 / 过期 403）
        """
        def dep(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> TokenInfo | None:
            # 开发模式逃生舱：只要显式设置 AF_ALLOW_NOAUTH=1，不管有没有配置令牌都放行
            # （局域网开发环境，开发者 webui 无登录功能，需要此开关才能访问 API）
            if os.environ.get("AF_ALLOW_NOAUTH", "").lower() in ("1", "true", "yes"):
                return None
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
            except Exception:  # P0-9 fail-closed：鉴权过程任何意外异常一律拒绝，不泄露 500
                raise HTTPException(
                    status_code=403, detail="鉴权校验异常（fail-closed 拒绝）"
                ) from None
            if info is None:
                raise HTTPException(status_code=403, detail="无效或已撤销的 API 令牌")
            # AF-701（审计第七轮实测）：带 scopes 却 subject 为空的令牌 = 残缺身份——
            # 归属判定对 caller="" 走"未携带身份不阻断"豁免，空主体令牌于是能改他人归档。
            # 与 MCP _guard 同一道闸：要 scope 就必须有 subject（fail-closed）。
            if not str(info.subject or "").strip():
                raise HTTPException(
                    status_code=403,
                    detail="令牌缺少 subject（残缺身份）——AF_ACCESS_TOKENS 每条须含 subject + scopes",
                )
            # admin 门的 owner 名单通道（v1.13.0）：subject ∈ _OWNER_SUBJECTS 的登录令牌
            # 视同持 admin——WebUI 升级页/watch 治理不用改签发。第三方 agent 令牌要 admin
            # 必须在 AF_ACCESS_TOKENS 显式配 scopes:["admin"]（autoflow 铁律：身份由人签发，
            # admin 永不经配对自动获得）。
            if scope == "admin" and info.subject in _OWNER_SUBJECTS:
                return info
            if not _scope_ok(scope, info.scopes):
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
            except Exception:  # P0-9 fail-closed：校验意外异常视为未认证
                return None

        return dep

    _write = requires("write")
    _live = requires("live")
    _read = requires("read")
    _admin = requires("admin")  # v1.13.0 运维档：gap #6 四端点收口（watch 治理/升级/删归档）

    #: 授权码列表用的依赖：**write 域，但不吃只读降级**。
    #: 必须在下面 `readonly` 把 `_write` 换成 503 阻塞器之前捕获——那条 GET 本身不写任何东西，
    #: 跟着写端点一起 503 就把铁律 #6（降级实例照常供读）破了；而它又必须看得到 write 域，
    #: 因为裁定 §一 F-3 要的是"明文只在 write 面"。
    _write_scope = requires("write")

    # 单写者租约（DCD 裁定一 A）：只读降级时写端点统一拒绝。
    # `readonly=True` 由 serve 抢不到 af_flock 单写者锁时传入。
    def _readonly_guard() -> None:
        if readonly:
            raise HTTPException(
                status_code=503,
                detail="服务处于只读模式（单写者锁被其他实例持有），写操作被拒绝",
            )

    if readonly:
        async def _write_blocker() -> None:
            raise HTTPException(
                status_code=503,
                detail="服务处于只读模式（单写者锁被其他实例持有），写操作被拒绝",
            )

        _write = _write_blocker

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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # 归档名别名折叠（审计 §四「路径处理」）：两个不同 name 经 `_dir()` 白名单落进同一目录，
    # 写入会静默互相覆盖、删除会连带销毁对方。存储层在写/删前拒绝并抛具名异常，HTTP 面把它
    # 落成 409（不是 400：请求本身合法，是**盘上已有归属**与它冲突；也不是 500：可重试可改名）。
    # `ArchiveOwnerUnknown`（F12）同一个口径：目录里有记录但归属读不出 ⇒ 同样"拒绝动盘、可修复后重试"，
    # 落成 409 而不是让它冒成 500——500 会让调用方以为请求格式有问题，而实际要先修盘上那条记录。
    @app.exception_handler(ArchiveNameConflict)
    @app.exception_handler(ArchiveOwnerUnknown)
    def api_archive_name_conflict(
        request: Request, exc: ArchiveNameConflict | ArchiveOwnerUnknown
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        # 裁定 20261007 §二 丁A：契约 §7.3 的 degrade-flag 档（「MQTT 断连 → health 报 degraded」）
        # 此前在本面**没有通道**——`serve` 的顺序是 build_app → start_from_env，桥在 app 装配完之后
        # 才存在，所以只能每次请求回读注册表，装配期抓引用会永远抓到 None。
        # 桥读不到（未 attach / homesdk 或 paho 不在盘上）如实报 `linkage.state="unwired"`，
        # 不报成健康：这一格的存在是为了让"联动面我没看"变成可见事实，而不是伪装成看过。
        presence = None
        try:
            from . import af_mqtt_bridge
        except ImportError:
            pass
        else:
            presence = af_mqtt_bridge.current_bridge()
        # 裁定 20261008 §一 B：`write_gate` 报的是**这个面**现在收不收写，所以把装配期那个
        # `readonly` 显式递进去。不递的话只能靠探测锁，而"启动时降级、之后锁空了"这种实例
        # 会一边回 503 一边报 open——那正是本裁定要消掉的分叉。
        return svc.health(store, presence=presence, readonly=readonly)

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

    @app.get("/api/graphs", dependencies=[Depends(_read)])
    def api_graphs(tag: str | None = Query(default=None)) -> dict[str, Any]:
        result = svc.list_graphs(store)
        if tag:
            result["items"] = [it for it in result["items"] if tag in it.get("tags", [])]
        return result

    @app.post("/api/graphs/tags")
    def api_graph_tags(
        body: TagBody, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        return _svc(
            svc.set_graph_tags, store, body.name, body.tags,
            caller=info.subject if info else "", caller_is_owner=_caller_is_owner(info),
        )

    @app.post("/api/graphs/enable")
    def api_graph_enable(
        body: TagEnableBody, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        # AF-401：启用与真机下发同级，闸在服务层（禁用端点不传 code——免码）。
        return _svc(
            svc.enable_by_tag, store, body.tag, True,
            caller=info.subject if info else "", caller_is_owner=_caller_is_owner(info),
            auth_code=body.auth_code,
        )

    @app.post("/api/graphs/disable")
    def api_graph_disable(
        body: TagEnableBody, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        return _svc(
            svc.enable_by_tag, store, body.tag, False,
            caller=info.subject if info else "", caller_is_owner=_caller_is_owner(info),
        )

    # ── v0.7.0 模板导出与备份恢复 ──
    @app.get("/api/store/export", dependencies=[Depends(_read)])
    def api_store_export() -> dict[str, Any]:
        """导出整个 store 为 bundle（含 tags + 校验和）。读端点，不强制鉴权。"""
        return svc.export_store(store)

    @app.post("/api/store/import")
    def api_store_import(
        body: ImportBody, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        """导入 bundle（写操作，需鉴权）。冲突策略 skip/overwrite/rename。

        D3：传 owner=当前主体（与 MCP 面 af_mcp._t_import 同口径）；owner 面可 bypass 归属预检。
        """
        return _svc(
            svc.import_store, store, body.bundle, body.strategy,
            owner=info.subject if info else "", owner_surface=_caller_is_owner(info),
        )

    @app.get("/api/graphs/{name}", dependencies=[Depends(_read)])
    def api_graph(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.get_graph(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/build", dependencies=[Depends(_readonly_guard)])
    def api_build(body: BuildBody) -> dict[str, Any]:
        try:
            # v1.4.0：传 store → 自动加载 {store}/device_acl.json（设备保护分级生效）
            return svc.build(body.ir, body.known_entities, store=store)
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.post("/api/bind", dependencies=[Depends(_readonly_guard)])
    def api_bind(body: BuildBody) -> dict[str, Any]:
        """v1.6.0：可选 binding——把 IR 里的设备描述占位符（`?书房吊灯`）回填为真实 entity_id。

        fail-closed：歧义/无候选 → **不回填**，保留占位符交由安全闸拒编译。
        """
        return svc.bind_ir(store, body.ir)

    @app.post("/api/sim", dependencies=[Depends(_readonly_guard)])
    def api_sim(body: SimBody) -> dict[str, Any]:
        # v1.5.0：传 store → `_telemetry` 落遥测。events 上限由 `svc.simulate` 自己判，
        # 经 `_svc` 映射成 422——判据与 MCP 面同源。
        return _svc(svc.simulate, body.ir, body.seed, body.events, store=store)

    @app.get("/api/conf/{name}", dependencies=[Depends(_read)])
    def api_conf(name: str) -> dict[str, Any]:
        try:
            return svc.get_conf(store, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/metrics", dependencies=[Depends(_read)])
    def api_metrics() -> dict[str, Any]:
        """运行指标（执行/审计/置信度），读端点不强制鉴权。"""
        return svc.get_metrics(store)

    # ── v1.5.0 经验闭环：遥测 + 实体共现（读端点）──
    @app.get("/api/experience", dependencies=[Depends(_read)])
    def api_experience(limit: int = Query(default=10)) -> dict[str, Any]:
        """实体共现经验摘要（**只在成功落盘后采集**，失败样本不污染）。"""
        return svc.get_experience(store, limit=int(limit))

    @app.get("/api/experience/export", dependencies=[Depends(_read)])
    def api_experience_export(limit: int = Query(default=200)) -> dict[str, Any]:
        """实体共现经验结构化导出（喂 MA：AF 采集事实 → MA 生成假设）。"""
        return svc.export_experience(store, limit=int(limit))

    @app.get("/api/telemetry", dependencies=[Depends(_read)])
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

    @app.get("/api/diff", dependencies=[Depends(_read)])
    def api_diff(
        name: str = Query(...),
        old: int = Query(...),
        new: int = Query(...),
    ) -> dict[str, Any]:
        try:
            return svc.diff(store, name, old, new)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/spec/{name}", dependencies=[Depends(_read)])
    def api_spec(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.spec_of(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/spec/compile", dependencies=[Depends(_readonly_guard)])
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

    @app.get("/api/asks/pending", dependencies=[Depends(_read)])
    def api_asks_pending() -> dict[str, Any]:
        """读 watch 进程写出的 pending_asks sidecar（供 DB 轮询发现挂起 ask）。

        DCD 裁定 20261004 §一 F-1：这一行补 `Depends(_read)`。sidecar 里是待答 ask 的
        `prompt`/`room` 原文（与 `/api/asks` 同级内容），此前只有那半边挂了门禁；DB 侧持
        write 域令牌（write 含 read），对端零改动。契约表由 DCD 登记"GET pending 需 read 令牌"。
        """
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
            # BUG-L1 修复：读失败不得静默返回 asks:[]，否则调用方把"读失败"当"无待答"，提问被静默丢失。
            # 写侧 af_live.py 已有 logger.warning，读侧补对等留痕 + ok=False 让调用方能区分。
            logger.warning("API_PENDING_ASKS_READ_FAILED pending_asks 读失败，返回 ok=False 而非空列表", exc_info=True)
            return {"ok": False, "asks": [], "error": "read_failed"}

    # 暴露 ask 节点的原生控件元数据（供前端渲染下拉/滑杆/时间轴等）
    @app.get("/api/asks/{name}", dependencies=[Depends(_read)])
    def api_ask_name(name: str) -> dict[str, Any]:
        try:
            rec = svc.get_graph(store, name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="未找到自动化")
        # get_graph 返回的是 `ir`（单条 → 单 dict 含 nodes；多条 → {"automations": [...]}）
        ir = rec.get("ir") or {}
        nodes: list[Any] = []
        if "automations" in ir:
            nodes = [n for a in (ir.get("automations") or []) for n in (a.get("nodes") or [])]
        else:
            nodes = ir.get("nodes") or []
        ask_nodes = [n for n in nodes if n.get("kind") == "ask" and n.get("ask")]
        asks: list[dict[str, Any]] = []
        for n in ask_nodes:
            # IR 字段名为 `ask`（`ask_spec` 是 $defs 定义名，不是节点属性）
            spec = AskSpec.from_dict(n["ask"], prompt=n.get("prompt", ""))
            asks.append(
                {
                    "node_id": n.get("id"),
                    "prompt": n.get("prompt", ""),
                    "spec": spec.to_dict(),
                    "control": spec.control(),
                }
            )
        # ok 由"每个 ask 节点都产出了控件元数据"这条后置条件决定，不是字面量。
        return {"ok": len(asks) == len(ask_nodes), "asks": asks}

    @app.get("/api/faults", dependencies=[Depends(_read)])
    def api_faults() -> dict[str, Any]:
        return svc.faults()

    # ── v1.1.0 实体事实内建（设备目录 + 解析）──────────────────────────
    @app.get("/api/catalog", dependencies=[Depends(_read)])
    def api_catalog() -> dict[str, Any]:
        """设备目录摘要（按域统计 + 区域列表 + 新鲜度），读端点不强制鉴权。"""
        return svc.catalog_snapshot(store)

    @app.get("/api/catalog/resolve-metrics", dependencies=[Depends(_read)])
    def api_catalog_resolve_metrics() -> dict[str, Any]:
        """v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。"""
        return svc.catalog_resolve_metrics(store)

    # ── v1.6.0 P0：别名沉淀（人工/agent 选对后写精确映射，下次直中）──
    @app.get("/api/catalog/aliases", dependencies=[Depends(_read)])
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

    @app.get("/api/entities/resolve", dependencies=[Depends(_read)])
    def api_entities_resolve(
        name: str = Query(..., description="自然语言设备名，如「书房吊灯」"),
        area: str = Query(default=""),
        domain: str = Query(default=""),
        top_n: int = Query(default=8, ge=1, le=50),
    ) -> dict[str, Any]:
        """自然语言设备名 → Top-N 候选 entity_id（写 IR 前必调）。读端点。"""
        return svc.catalog_resolve(store, name, area=area, domain=domain, top_n=top_n)

    @app.get("/api/entities", dependencies=[Depends(_read)])
    def api_entities(
        domain: str = Query(default=""),
        area: str = Query(default=""),
        keyword: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。读端点。"""
        return svc.catalog_list(store, domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)

    @app.get("/api/entities/{entity_id}/state", dependencies=[Depends(_read)])
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

    @app.post("/api/live/run", dependencies=[Depends(_readonly_guard), Depends(_live)])
    def api_live_run(body: LiveRunBody) -> dict[str, Any]:
        # `clock` 刻意**不在** `LiveRunBody` 里：真机证据的时间轴由服务端决定，
        # 请求体能递钟就等于让被审计的一方自己写审计时间。
        # auth_code 同名透传给服务层闸（AF-401：两面同闸，不许 REST 直连绕过）。
        return _svc(
            svc.live_run, body.ir, body.live_allow, body.events, body.confirm, store, body.undo,
            auth_code=body.auth_code,
        )

    # ── F7 残留（WebUI 撤销按钮，决策 E ⑤）──
    #   GET 是只读盘点（read scope）；POST 会对真实设备再下发一次，所以闸门与
    #   `/api/live/run` 完全同口径：live scope + 单写者只读降级拦 + 服务端令牌。
    #   时间窗 / 风险域 confirm / 未知 deploy_id 的判定全在 `af_undo.UndoStore`，本层不重算。
    @app.get("/api/undo/available", dependencies=[Depends(_read)])
    def api_undo_available() -> dict[str, Any]:
        """窗口内仍可撤销的部署清单（前端据此决定撤销按钮是否可点）。"""
        return svc.undo_available(store.root if store else None)

    @app.get("/api/undo/{deploy_id}", dependencies=[Depends(_read)])
    def api_undo_preview(deploy_id: str) -> dict[str, Any]:
        """撤销前盘点：窗口/风险域/实体清单，口径与 revert 同源。"""
        return svc.undo_preview(deploy_id, store.root if store else None)

    @app.post("/api/undo/{deploy_id}", dependencies=[Depends(_readonly_guard), Depends(_live)])
    def api_undo_deploy(deploy_id: str, body: UndoBody | None = None) -> dict[str, Any]:
        """撤销一次部署：回放动作前快照（风险域需 body.confirm=true）。

        DCD 裁定 2026-10-30（AF-601）：撤销补码——undo 是真机回放不是"关"，
        方向取决于下发前快照，借不了 R4-3 禁用免码的豁免。
        """
        return _svc(
            svc.undo_deploy, deploy_id, bool(body and body.confirm), store.root if store else None,
            auth_code=str(body.auth_code) if body else "",
        )

    # ── F4：生产态验证证据（af_watch 聚合）。刻意不叫 /api/watch/*——那条前缀在本服务里
    #   指"运行中的 watch 实例"（下面 §v1.7.3），两者不是一个东西。──
    @app.get("/api/evidence/prod", dependencies=[Depends(_read)])
    def api_evidence_prod() -> dict[str, Any]:
        """诚实报告 `verified_in_prod` 分区：verified / failed / unmodeled 三档 + 淘汰计数。"""
        return svc.watch_summary()

    # ── ADM 第 1 步 ④A：MA 洞察提案的**只落盘队列**（裁定 20261002 §三 ④A）──
    #   这里的"approve"只把提案交接进 af_pending（可执行待批队列），**不部署**。
    #   桥侧写的是 `{store_root}/insight_proposals`，两边同一路径才能读到同一份账。
    def _insight_queue():
        """桥侧与 HTTP 侧必须读同一个目录：路径口径只在这里定义一次。"""
        from .af_insight_queue import InsightQueue

        root = store.root if store else None
        if not root:
            raise HTTPException(status_code=503, detail="未配置 store，洞察队列不可用")
        return InsightQueue(Path(root) / "insight_proposals")

    @app.get("/api/insights/pending", dependencies=[Depends(_read)])
    def api_insights_pending(include_decided: bool = False) -> dict[str, Any]:
        queue = _insight_queue()
        rows = [r.to_dict() for r in queue.list_pending()]
        out: dict[str, Any] = {"count": len(rows), "proposals": rows, "queue": queue.stats()}
        if include_decided:
            out["decided"] = [r.to_dict() for r in queue.list_decided()]
        return out

    @app.post("/api/insights/approve", dependencies=[Depends(_write)])
    def api_insights_approve(body: dict[str, Any]) -> dict[str, Any]:
        return _svc(
            svc.approve_insight,
            store,
            _insight_queue(),
            str(body.get("proposal_id") or ""),
            reviewer=str(body.get("reviewer") or ""),
        )

    @app.post("/api/insights/reject", dependencies=[Depends(_write)])
    def api_insights_reject(body: dict[str, Any]) -> dict[str, Any]:
        return _svc(
            svc.reject_insight,
            _insight_queue(),
            str(body.get("proposal_id") or ""),
            reviewer=str(body.get("reviewer") or ""),
            reason=str(body.get("reason") or ""),
        )

    # ── v1.7.3：运行中 watch 实例列表（只读）──
    @app.get("/api/watch/list", dependencies=[Depends(_read)])
    def api_watch_list() -> dict[str, Any]:
        """列出当前在跑的 watch 实例（读 persist dir 的 watch.lock.info）。"""
        return svc.list_watches(store.root if store else None)

    @app.post("/api/watch/stop", dependencies=[Depends(_admin)])
    def api_watch_stop(owner: str = "", body: dict[str, Any] | None = None) -> dict[str, Any]:
        """停止运行中的 watch 进程。

        body 可选 ``{"force": true}``：仅在 sidecar 身份缺失、但内核锁确认空闲时，
        授权回收无主孤儿锁；持有者已死/僵尸且锁空闲的明确孤儿锁无需 force 即自动回收。
        """
        force = bool((body or {}).get("force", False))
        return svc.stop_watch(
            owner=owner, store_root=store.root if store else None, force=force
        )

    @app.post("/api/asks/answer", dependencies=[Depends(_write)])
    def api_asks_answer(body: dict[str, Any]) -> dict[str, Any]:
        """写答案到 watch 的 answer_inbox（由 watch ticker 注入 runtime）。

        `AUTOFORGE_INBOX_KEY` 未设时**如实报失败**：读侧 `af_live.read_answer_inbox`
        无 key 一律拒收，过去这里照样返回 ok=True，就是"接口 200、答案蒸发"的假绿。
        文件仍写盘（保留"用户答过"的证据链），但结论按通道未生效给。
        """
        root = store.root if store else None
        if not root:
            return {"ok": False, "error": "no store"}
        # A6 write-side sign: mirror af_live._read_inbox _expect_sig
        # (ask_id or None / text default "" / room as-is，answer 走 sort_keys JSON)。
        import hashlib as _hashlib
        import hmac as _hmac
        key = (os.environ.get("AUTOFORGE_INBOX_KEY") or "").strip()
        inbox = Path(root) / "answer_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        ask_id = body.get("ask_id") or None
        text = body.get("text", "")
        room = body.get("room")
        answer = body.get("answer")  # v2 M3 结构化应答（AskAnswer dict）
        body = dict(body)
        answer_json = json.dumps(answer or {}, sort_keys=True, ensure_ascii=False)
        if key:
            # AF-001 修复：签名消息带签发时间戳 ts，读侧按新鲜窗口校验。
            # 旧裸 HMAC 无新鲜度，旧/重放 inbox 文件会被无限接受；ts 进签名后，
            # 篡改 ts 即 sig 失效，读侧拒过期文件。
            ts = int(time.time())
            msg = f"{ask_id}|{text}|{room}|{answer_json}|{ts}".encode("utf-8")
            body["sig"] = _hmac.new(key.encode("utf-8"), msg, _hashlib.sha256).hexdigest()
            body["ts"] = ts
        # R14-03：毫秒时间戳作文件名，同毫秒两条应答后者覆盖前者。
        # 加 uuid 后缀保证唯一性（时间戳保留排序语义）。
        fname = inbox / f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}.json"
        # 走原子助手：这是投给 butler 轮询读的作答文件，崩在半截会被读成
        # 「没有这份作答」而丢掉这次回答（判据 E，审计 BUG-05）
        atomic_write_text(fname, json.dumps(body, ensure_ascii=False))
        if not key:
            return {
                "ok": False,
                "reason": "inbox_key_missing",
                # 裁定 20261002 §三 ③A：通道失败必须与业务失败可分。
                # DB 见 channel_error=true 必须告警并**停止把该 ask 标记为已答**。
                "channel_error": True,
                "inbox": str(fname),
                "error": "AUTOFORGE_INBOX_KEY 未配置：答案无法签名，读侧必然拒收（裁定 Q5：key 是通道生效前提）",
            }
        return {"ok": True, "channel_error": False, "inbox": str(fname), "signed": True}

    @app.post("/api/watch/start", dependencies=[Depends(_admin)])
    def api_watch_start(body: dict[str, Any]) -> dict[str, Any]:
        """启动 watch 进程跑指定 IR（dry-live 默认）。

        DCD 裁定 2026-10-30（AF-602 手动入口半）：手动起真机监听要码（有人可给码）。
        serve 重启的自动恢复走 `_restore_watch_after_restart`（免码特例，函数层分开）。
        dry-live 不动设备，免码。
        """
        ir = body.get("ir")
        if not ir:
            return {"ok": False, "error": "缺少 ir 字段"}
        dry_live = body.get("dry_live", True)
        try:
            return svc.start_watch(ir=ir, store_root=store.root if store else None, dry_live=dry_live,
                                   auth_code=str(body.get("auth_code") or ""))
        except svc.ServiceError as exc:
            # 码闸拒绝按状态码归档（403），不能冒成 500——对端的正确动作是去拿码。
            raise HTTPException(status_code=int(getattr(exc, "status", 400) or 400),
                                detail=str(exc)) from exc

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

    @app.post("/api/auth/revoke")
    def api_auth_revoke(
        body: RevokeBody,
        info: TokenInfo | None = Depends(_write),
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> dict[str, Any]:
        """撤销令牌（即时生效并落盘 `{store_root}/.auth/revoked.json`）。

        批 3：body 可给 `token`（自理）或 `subject`（owner 面专属）。两者二选一。

        按主体撤销分两族（出资人 2026-10-27 现场：owner 行一点就把自己的会话撤了＝
        「删不动 + 误登出」；旧实现还会连 env 里人配的 owner 部署密钥一起吊销）：
        - owner 面主体 ⇒ `rotate_login_tokens(keep_token=当前 bearer)`：只清运行期
          登录令牌、保住调用者自己那枚、⛔ 不碰 env 密钥与 agent/developer 档。
        - 其他主体（配对 agent、developer）⇒ 维持 `revoke_by_subject` 语义——那是
          「删这个 agent」，名下全撤是对的；except 当前 bearer 只防自杀式登出。
        """
        if body.subject:
            if not _caller_is_owner(info):
                raise HTTPException(status_code=403, detail="按主体撤销需 owner 面；普通令牌只能撤自己")
            current_token = creds.credentials if creds else None
            if body.subject in _OWNER_SUBJECTS:
                n = registry.rotate_login_tokens(body.subject, keep_token=current_token)
            else:
                n = registry.revoke_by_subject(body.subject, except_token=current_token)
            # ok 从撤销数量派生（fake-ok-const 门 + 诚实回执双口径：撤到 0 枚的
            # "成功"不是成功，两分支各写字面量 True 还会给 AST 棘轮添账）。
            return {"ok": n > 0, "revoked": n > 0, "count": n}
        if not body.token:
            raise HTTPException(status_code=400, detail="必须提供 token 或 subject")
        # 批 4（D8）：非 owner 面**只能撤自己手里那枚**令牌——不得拿别人的 token 串名撤销。
        # owner 面（含本地逃生舱）保留任意撤销权。
        if not _caller_is_owner(info):
            try:
                target = registry.authenticate(body.token)
            except Exception:
                target = None
            target_subject = target.subject if target else None
            if target_subject != (info.subject if info else None):
                raise HTTPException(status_code=403, detail="普通令牌只能撤销自己名下的令牌")
        revoked = registry.revoke(body.token)
        return {"ok": True, "revoked": revoked}

    # ── 登录正规化：首次设定账号密码 ──
    @app.get("/api/auth/has-admin")
    def api_auth_has_admin() -> dict[str, Any]:
        """检查是否已有管理员用户。前端用来判断显示注册页还是登录页。"""
        return {"ok": True, "has_admin": admin_store.has_admin}

    @app.post("/api/auth/register")
    def api_auth_register(request: Request, body: RegisterBody) -> dict[str, Any]:
        """首次注册管理员。只有当没有管理员时才允许；已有管理员则拒绝。

        注册成功后自动签发登录令牌，免去注册后再登录一步。
        """
        _login_limit(register_limiter, request)
        if not (body.username and body.password):
            raise HTTPException(status_code=400, detail="用户名或密码为空")
        if len(body.username) < 2:
            raise HTTPException(status_code=400, detail="用户名至少 2 个字符")
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 个字符")
        try:
            ok = admin_store.register(body.username, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not ok:
            raise HTTPException(status_code=409, detail="已有管理员，不允许重复注册")
        #: WebUI 注册即登录，签发的是 owner 面令牌（subject 固定 owner），登录用户名进 label 仅供显示。
        #: 批 2：身份族标 login（与配对 agent、批4 developer 三族可分辨）。
        #: 注册同样换代（有界保留，同 login 口径）。
        registry.rotate_login_tokens("owner", keep_newest=registry.LOGIN_SESSION_KEEP)
        token = registry.issue_for_agent("owner", ("read", "write", "live"), label=body.username,
                                        principal_kind="login")
        return {
            "ok": True,
            "user": {"username": body.username, "role": "admin"},
            "token": token,
        }

    # ── v1.9.0 用户 WebUI：登录（验证用户名密码）──
    @app.post("/api/auth/login")
    def api_auth_login(request: Request, body: LoginBody) -> dict[str, Any]:
        """登录：验证用户名和密码，通过后签发 owner 登录令牌（principal_kind=login）。

        未注册管理员时 fail-closed：返回 401 + `{"ok": false, "need_register": true}`，
        不签发任何令牌；首启引导走 `POST /api/auth/register`。
        （旧「任意非空凭据可登录」兼容分支已在 D2 堵死，避免升级后老用户被锁在外面
        这个历史顾虑，由前端 `/api/auth/has-admin` 探测直接跳注册页承担。）
        """
        _login_limit(login_limiter, request)
        if not (body.username and body.password):
            raise HTTPException(status_code=401, detail="用户名或密码为空")
        # D2：未注册管理员时 fail-closed——不再发一枚 owner 全权限令牌。
        # 旧"任意非空凭据即 owner"是一把今天就能被利用的刀（部署起来、登录页一敲即全权限）。
        if not admin_store.has_admin:
            return JSONResponse(
                status_code=401,
                content={
                    "ok": False,
                    "need_register": True,
                    "error": "尚未设置管理员账号，请先完成注册（register）",
                },
            )
        # 已注册：严格验证
        if not admin_store.verify(body.username, body.password):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        # 登录即换代（出资人 2026-10-27 现场：每登一次堆一行登录态、删不动）：
        # 签发新令牌**之前**清掉旧登录令牌。2026-10-28 收口：有界保留最近 N 枚
        # （mimo/管理端/手机 三个面互不顶号——"全清"做过头成了"每次都要重登"），
        # 表仍严格有界。
        registry.rotate_login_tokens("owner", keep_newest=registry.LOGIN_SESSION_KEEP)
        token = registry.issue_for_agent("owner", ("read", "write", "live"), label=body.username,
                                        principal_kind="login")
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
        #: 批 3（D9）：role 按 principal_kind 回——login⇒admin、developer⇒developer、agent⇒agent。
        #: 此前一律回 admin，让配对 agent 也"以为"自己是管理员。owner 登录令牌 kind=login ⇒ admin 不回归。
        role = {"login": "admin", "developer": "developer"}.get(info.principal_kind, "agent")
        return {"ok": True, "user": {"username": info.label or info.subject, "role": role}}

    # ── v1.10.0 系统自更新（宿主侧 sidecar 模式，参考 DCD 20261010 MA 裁定）──
    # 流程：WebUI 点更新 → 容器写标记文件 /data/.update_request.json → 宿主 cron 脚本
    #       检测到标记 → git pull + 构建 UI + docker cp 后端 + 重启容器 → 写结果 .update_result.json
    _UPDATE_MARKER = os.path.join(store.root if store else "/data", ".update_request.json")
    _UPDATE_RESULT = os.path.join(store.root if store else "/data", ".update_result.json")
    _UPDATE_PREVIEW = os.path.join(store.root if store else "/data", ".update_available.json")
    _APP_VERSION = getattr(__import__("autoforge"), "__version__", "0.0.0")

    @app.get("/api/system/version", dependencies=[Depends(_read)])
    def api_system_version() -> dict[str, Any]:
        """返回当前版本信息。"""
        info: dict[str, Any] = {
            "version": _APP_VERSION,
            "api_version": svc.API_VERSION,
            "contract_version": svc.CONTRACT_VERSION,
            "commit": "",
            "branch": "",
            "tag": "",
        }
        # 从宿主预检脚本写的 .update_available.json 读取当前 commit（容器内无 .git）
        try:
            if os.path.isfile(_UPDATE_PREVIEW):
                with open(_UPDATE_PREVIEW, encoding="utf-8") as f:
                    preview = json.load(f)
                info["commit"] = preview.get("local_commit", "")
                info["tag"] = preview.get("local_tag", "")
                info["branch"] = preview.get("branch", "")
        except (OSError, ValueError) as exc:
            # AST 门欠账收账（2026-10-24）：此前裸 except + pass——预览文件损坏时
            # commit/tag/branch 静默报空串、ok=True 照常返回（swallow-and-claim-ok，
            # 谎报成功）。改具名捕获 + 具名码留痕：读不出与「没跑预检」对 UI 可区分。
            logger.warning(
                "UPDATE_PREVIEW_READ_FAILED path=%s err=%s —— 版本号回显为空，请检查宿主预检脚本产物",
                _UPDATE_PREVIEW, exc,
            )
        return {"ok": True, **info}

    @app.get("/api/system/update/check", dependencies=[Depends(_read)])
    def api_system_update_check() -> dict[str, Any]:
        """读取宿主预检脚本写的更新预览文件。"""
        try:
            if not os.path.isfile(_UPDATE_PREVIEW):
                return {
                    "ok": True,
                    "has_update": False,
                    "message": "宿主预检脚本尚未运行，请等待 5 分钟后重试",
                }
            with open(_UPDATE_PREVIEW, encoding="utf-8") as f:
                preview = json.load(f)
            return {
                "ok": True,
                "has_update": preview.get("has_update", False),
                "local_commit": preview.get("local_commit", ""),
                "local_tag": preview.get("local_tag", ""),
                "latest_commit": preview.get("remote_commit", ""),
                "latest_tag": preview.get("remote_tag", ""),
                "changelog": preview.get("changelog", []),
                "checked_at": preview.get("checked_at", ""),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/system/update", dependencies=[Depends(_admin)])
    def api_system_update(body: UpdateBody, info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        """提交更新请求（admin）。

        出资人 2026-10-21 定案：去掉「输入当前版本号」二次确认——防误触已有三道真闸
        （write 权限鉴权 + 进行中标记 409 + marker 原子写），版本号回显是第四道假闸
        （版本号本身就显示在同一页上，抄一遍没有任何区分度）。DCD 20261010 Q4 的
        confirm_version 形状随之作废；`body.branch` 缺省对齐发布线 master（DCD 20261017-001 §2.1）。
        容器不执行 git 操作，只写标记文件给宿主 sidecar。
        """
        # v1.13.3（autoflow af_deploy 照搬）：marker 写入提取为模块级 `submit_update_request`
        # ——HTTP 面与 MCP `af_deploy` 工具写**同一枚** marker，格式只有这一份（"不写第二份
        # marker 格式"铁约束）。409（进行中）与 400（ref 白名单）两档拒绝分开写字面状态码
        # （发布线门判据 D 的 409 锚点钉在这处）；返回类型已标注 dict ⇒ 元组不合法会 500，
        # JSONResponse 显式状态码（v1.13.0 起）。
        requested_by = info.subject if info else "admin"
        ok, message = submit_update_request(
            store.root if store else None, body.branch or "master", requested_by, _APP_VERSION
        )
        if not ok:
            if "进行中" in message:
                return JSONResponse(status_code=409, content={"ok": False, "error": message})
            return JSONResponse(status_code=400, content={"ok": False, "error": message})
        return {
            "ok": True,
            "message": message,
            "marker": _UPDATE_MARKER,
            "expected_delay_sec": 60,
        }

    @app.get("/api/system/update/status", dependencies=[Depends(_read)])
    def api_system_update_status() -> dict[str, Any]:
        """查看更新进度。"""
        pending = os.path.isfile(_UPDATE_MARKER)
        result = None
        if os.path.isfile(_UPDATE_RESULT):
            try:
                with open(_UPDATE_RESULT, encoding="utf-8") as f:
                    result = json.load(f)
            except Exception:
                result = None
        return {
            "ok": True,
            "pending": pending,
            "result": result,
            "current_version": _APP_VERSION,
        }

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
        request: Request,
        token: str | None = Query(default=None),
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> StreamingResponse:
        """SSE：前端 App 启动建立长连接，收到配对请求即弹窗。

        SSE（EventSource）不支持自定义请求头，令牌经 ?token= 传递；
        也兼容 Authorization: Bearer 头。

        ⚠️ 代价：`?token=` 会整条落进 uvicorn 访问日志。`forge serve` 起桥时挂了
        `af_cli.AccessLogTokenMask` 把值替成 `***`（只保本进程；反代/nginx 那一半属部署侧）。
        """
        raw = (creds.credentials if creds else None) or token
        info = None
        if raw:
            try:
                info = registry.authenticate(raw)
            except Exception:  # P0-9 fail-closed：校验异常视为未授权
                info = None
        # fail-closed（P0-9）：未认证即拒；仅本地无令牌逃生舱（AF_ALLOW_NOAUTH=1）放行
        if info is None:
            local_escape = (not registry.enabled) and os.environ.get("AF_ALLOW_NOAUTH", "").lower() in (
                "1", "true", "yes",
            )
            if not local_escape:
                return JSONResponse(status_code=403, content={"ok": False, "error": "未授权"})
        return StreamingResponse(_pair_event_gen(request), media_type="text/event-stream")

    # ── v1.9.0 用户 WebUI：授权码（部署授权，独立于配对）──
    @app.post("/api/user/auth-code")
    def api_auth_code_create(
        body: AuthCodeBody, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        # D1：铸造授权码收 owner 面——一枚第三方 write 令牌不得再签发部署凭证。
        _require_owner_surface(info)
        if body.kind not in ("long", "short"):
            raise HTTPException(status_code=400, detail="kind 仅支持 long / short")
        if body.kind == "short" and (body.ttl_minutes is None or not (5 <= body.ttl_minutes <= 30)):
            raise HTTPException(status_code=400, detail="短期码 ttl_minutes 需 5–30 分钟")
        ac = auth_store.create(body.kind, body.ttl_minutes)
        return {"ok": True, "code": ac.code, "kind": ac.kind, "expires_at": ac.expires_at}

    @app.get("/api/user/auth-codes")
    def api_auth_code_list(
        info: TokenInfo | None = Depends(_write_scope),
    ) -> dict[str, Any]:
        """授权码列表：write 域 + owner 面明文／非 owner 面定形掩码（裁定 20261004 §一 F-3）。

        此前挂 `_read` 却把**全部明文码**发给任何带 read 的第三方令牌——一枚用于播报的
        只读令牌就拿到了创建部署的凭证，这是 §一 F-3 点名的形状。

        `info is None` 只可能来自 `AF_ALLOW_NOAUTH=1` 的本地逃生舱（无令牌⇒无身份概念），
        按 owner 面处理，与其余端点在逃生舱下的行为一致。
        """
        reveal = info is None or info.subject in _OWNER_SUBJECTS
        return {"ok": True, "codes": auth_store.list(reveal=reveal)}

    @app.delete("/api/user/auth-code/{code}")
    def api_auth_code_revoke(
        code: str, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        # D1：撤销授权码同样收 owner 面。
        _require_owner_surface(info)
        ok = auth_store.revoke(code)
        return {"ok": ok, "revoked": code if ok else None}

    # ── v1.9.0 用户 WebUI：agent 管理（单 owner 下即已配对主体）──
    @app.get("/api/user/agents", dependencies=[Depends(_read)])
    def api_agents_list(token_info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        current_subject = token_info.subject if token_info else None
        agents = [
            {"id": s["subject"], "name": s["subject"], "scopes": s["scopes"]}
            for s in registry.subjects()
            if s["subject"] not in _AGENT_LIST_RESERVED
            and s["subject"] != current_subject  # 排除当前登录用户，避免用户自己出现在 agent 列表里
        ]
        return {"ok": True, "agents": agents}

    @app.delete("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
    def api_agent_delete(
        agent_id: str,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        token_info: TokenInfo | None = Depends(authenticated()),
    ) -> dict[str, Any]:
        # 批 4（D8）：主体绑定升级为治理面（owner 面或显式 developer 档）。
        # 返工 5.1：调用形状回到 check_owner_subject 判据 B 机械认得的 helper
        # （名字带 subject＋实参含路径主体与主体引用；主体先落局部变量再传——
        # 本门的 _refs_subject 认 Name/Attribute/Subscript/Call，不认内联 IfExp）；
        # 语义不变——一枚普通 agent 令牌（哪怕带 write）不得借路径名撤销他人。
        caller_subject = token_info.subject if token_info else None
        caller_kind = token_info.principal_kind if token_info else None
        _assert_agent_subject_owner(agent_id, caller_subject, caller_kind)
        # 删除 agent 时排除当前登录用户的 token，避免把自己踢下线
        current_token = creds.credentials if creds else None
        n = registry.revoke_by_subject(agent_id, except_token=current_token)
        return {"ok": True, "revoked_tokens": n, "agent": agent_id}

    @app.patch("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
    def api_agent_rename(
        agent_id: str,
        body: AgentRenameBody,
        token_info: TokenInfo | None = Depends(authenticated()),
    ) -> dict[str, Any]:
        # 批 4（D8）：改名配对主体同走治理面判据（保留主体绑定，判据 B 不动）。
        # 返工 5.1：形状同 api_agent_delete——helper 名带 subject、实参含路径主体与主体引用。
        caller_subject = token_info.subject if token_info else None
        caller_kind = token_info.principal_kind if token_info else None
        _assert_agent_subject_owner(agent_id, caller_subject, caller_kind)
        new_name = (body.name or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="name 不能为空")
        try:
            n = registry.rename_subject(agent_id, new_name)
        except ValueError as exc:
            # 批 4（D7）：服务层三条保留名/重名校验 ⇒ 403（防提权/合并两族）。
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"ok": True, "renamed": n, "from": agent_id, "to": new_name}

    @app.post("/api/user/agent-token", dependencies=[Depends(_write)])
    def api_issue_agent_token(
        body: AgentTokenBody,
        token_info: TokenInfo | None = Depends(authenticated()),
    ) -> dict[str, Any]:
        """批 4（D8）：owner 面**显式**签发一枚 developer 档令牌（带 admin scope）。

        ⛔ 不能让普通 agent 令牌自调 `issue_for_agent(principal_kind=developer)`——
        issue_for_agent 已拒绝自助提权；本端点是 developer 档唯一合法铸造口，且只准 owner。
        developer 令牌 ttl=0（不过期），用于长驻运维 agent。
        """
        _require_owner_surface(token_info)
        name = (body.agent_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="agent_name 不能为空")
        token = registry.issue_for_agent(
            name, scopes=sorted(SCOPES), principal_kind="developer",
            label=body.label or "owner-issued developer token", ttl_s=0,
            _allow_developer=True,
        )
        # 返工 5.1：ok 从回读认证推导，不写字面量（先例=api_pair_redeem_bootstrap 的
        # ok=verified；AST 门 fake-ok-const 判红的那一族）。签出来回读不回来的令牌
        # 等于没发——这句必须是真的，不是"记得它成功了"。
        issued = registry.authenticate(token)
        verified = (issued is not None and issued.subject == name
                    and getattr(issued, "principal_kind", "") == "developer")
        return {"ok": verified, "token": token, "subject": name, "principal_kind": "developer",
                "scopes": sorted(SCOPES)}

    # ── 配对 bootstrap 匿名面（裁定 20261006 §一 = B：新增两个匿名端点）──
    @app.post("/api/mcp/pair/request")
    def api_pair_request_bootstrap(body: PairRequestBody, request: Request) -> dict[str, Any]:
        """还没有令牌的 agent 用它发起配对——**这正是配对要解决的问题本身**。

        今天 `af_request_pair`（`af_mcp.py`）与 `af_pair` 的 scope 都是 write，HTTP 面 `/mcp` 挂
        `Depends(_write)` ⇒ 要拿配对码必须先有一枚 write 令牌，而死锁就在这儿。裁定驳回过
        "把两工具 scope 改 None"（等于自撤 MCP 面 default-deny，且任何人可刷配对请求），
        选的是**只把这两个动作开成匿名端点**——匿名射程比今天那张整工具表更小。

        码**不返回给 agent**（只显示给用户弹窗），agent 等用户口述后调 redeem 兑换。
        """
        _bootstrap_limit(bootstrap_request_limiter, request)
        if not pair_store.is_accepting():
            raise HTTPException(
                status_code=409,
                detail="用户已暂停接受配对请求（ForgeSight 里恢复后重试）",
            )
        pc = request_pair_code(pair_store, body.agent_name_hint)
        # `ok` 来自回读而不是字面量：create() 之后登记面读不出这枚码，用户弹窗里就没有码可念，
        # agent 只会等到超时——这一条回读把"告诉 agent 去等"这个承诺兑现了才返回。
        persisted = pair_store.get(pc.code) is not None
        if not persisted:
            raise HTTPException(status_code=500, detail="配对码已生成但未登记，请重试")
        return {
            "ok": persisted,
            "expires_at": pc.expires_at,
            "message": "配对请求已发起，请在 ForgeSight 中输入显示的 8 位码完成配对",
        }

    @app.post("/api/mcp/pair/redeem")
    def api_pair_redeem_bootstrap(body: PairRedeemBody, request: Request) -> dict[str, Any]:
        """用用户口述的 8 位码兑换 Bearer 令牌。与 MCP `af_pair` 共用 `redeem_pair_code`（一份顺序）。"""
        _bootstrap_limit(bootstrap_redeem_limiter, request)
        status, token, subject = redeem_pair_code(
            pair_store, registry, body.code, body.agent_name
        )
        if status == "no_code":
            raise HTTPException(status_code=400, detail="缺少 code")
        if status == "invalid":
            raise HTTPException(status_code=409, detail="配对码无效、已使用或已过期")
        if status == "not_ready":
            raise HTTPException(status_code=503, detail="服务未就绪（令牌注册表不可用）")
        issued = registry.authenticate(token)
        verified = issued is not None and issued.subject == subject
        if not verified:
            raise HTTPException(status_code=500, detail="配对码已消费但签发的令牌未能回读，请重新发起配对")
        return {
            "ok": verified,
            "token": token,
            "subject": subject,
            "message": "配对成功：请将 token 作为 Bearer 调用 AutoForge API",
        }

    # ── 批 5：用户先出链接的配对路（与匿名 bootstrap 并存，共用 redeem_pair_code）──
    def _pair_failure_count_path() -> Path:
        return Path(store_root) / ".auth" / "pair_link_failures.json"

    def _pair_record_failure(link_key: str) -> int:
        """每次访问 GET /pair 都记一笔（按 link_key），落盘 .auth/ 下，重启不归零。

        容器重建会把进程内限流器清零——这层计数就是那道防线的持久化壳子。
        只记数、不回显、不落码值。
        """
        p = _pair_failure_count_path()
        data: dict[str, int] = {}
        if p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = {str(k): int(v) for k, v in loaded.items()}
            except (OSError, ValueError):
                data = {}
        data[link_key] = data.get(link_key, 0) + 1
        try:
            atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError as exc:
            # 计数写失败不阻断 404 回执（限流的 IP 维度仍在，防线塌半不塌全）；
            # 但"落盘失败"与"落盘成功"必须可区分——吞掉就是静默失效，留痕。
            logger.warning("PAIR_LINK_FAILURE_COUNT_WRITE_FAILED 配对链接失败计数落盘失败: %r", exc)
        return data[link_key]

    @app.post("/api/user/pair-link", dependencies=[Depends(_write)])
    def api_pair_link_create(
        body: PairLinkBody, request: Request,
        info: TokenInfo | None = Depends(authenticated()),
    ) -> dict[str, Any]:
        """批 5：owner 面生成配对链接（link_key + 8 位码，同一 TTL、同一一次性）。

        ⛔ 不是第二套签发：复用 request_pair_code（与 MCP/匿名 bootstrap 同一函数）。
        受 pair_store.is_accepting() 约束，与匿名 bootstrap 同口径。
        """
        _require_owner_surface(info)
        if not pair_store.is_accepting():
            raise HTTPException(
                status_code=409,
                detail="用户已暂停接受配对请求（恢复后再生成配对链接）",
            )
        # ttl_s=600（出资人 2026-10-26 Q6）：链接要被复制转手给 agent，5 分钟不够；
        # 只这一径放宽，匿名 bootstrap 面维持 create 默认 300s。一次性/消费语义不变。
        pc = request_pair_code(pair_store, body.agent_name_hint, ttl_s=600)
        persisted = pair_store.get(pc.code) is not None
        if not persisted:
            raise HTTPException(status_code=500, detail="配对码已生成但未登记，请重试")
        base = str(request.base_url).rstrip("/")
        # 链接必须指向内容协商端点 GET /pair（注册在服务根，见 @app.get("/pair")）。
        # ⚠️ 返工 5.3：这里曾拼成 `{base}/{UI_USER_PREFIX}/pair`＝/mimo/pair——那是 mimo SPA
        # 的挂载前缀 + 兜底路由，返回的是控制台 HTML，agent 打开链接拿到的是页面不是配对协议。
        # UI_USER_PREFIX 只属于静态资源面，⛔ 不得进 API 链接。判据腿同批收紧（钉死 /pair?k= 段）。
        link = f"{base}/pair?k={pc.link_key}"
        # 返工 5.1：ok 来自 persisted 回读（上面已校验为真才走到这里），不写字面量。
        # agent_name_hint 进返回体（返工 5.2）：弹窗标题显示「配对链接 · {hint}」，
        # 不给就永远回退成光秃秃的 "Agent"。
        return {
            "ok": persisted,
            "link": link,
            "code": pc.code,
            "agent_name_hint": pc.agent_name_hint,
            "expires_at": pc.expires_at,
            "ttl_s": int(pc.expires_at - pc.created_at),
        }

    @app.get("/pair")
    def api_pair_link_open(k: str = "", request: Request = None) -> JSONResponse:  # type: ignore[assignment]
        """批 5：匿名配对链接落地页。Accept 协商：

        - `Accept: application/json`（curl/agent）⇒ 自描述 JSON（redeem 地址/参数/后续用法）。
        - `Accept: text/html`（人点开）⇒ 只读静态页（码、剩余时间、redeem 方法、复制按钮），
          带 `Referrer-Policy: no-referrer`（码会进浏览器历史）。
        限流两条维度缺一不可：复用 bootstrap_redeem_limiter（按 IP）+ 按码维度 `key:{k}`；
        失败次数落盘 .auth/，重启不归零。
        """
        if not k:
            raise HTTPException(status_code=400, detail="缺少 k 参数")
        # 双维度限速：IP 维度 + 按码维度（同一 link_key 跨 IP 试也拦得住）。
        try:
            bootstrap_redeem_limiter.check(f"ip:{_client_ip(request)}")
            bootstrap_redeem_limiter.check(f"key:{k}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        rec = pair_store.get_by_link_key(k)
        link_valid = rec is not None and not rec.get("consumed") and time.time() <= rec.get("expires_at", 0)
        if not link_valid:
            _pair_record_failure(k)
            # ⛔ 不区分"不存在 vs 已过期 vs 已消费"——那是 oracle，一律 404。
            raise HTTPException(status_code=404, detail="配对链接无效、已使用或已过期")
        accept = (request.headers.get("accept") or "").lower()
        code = rec.get("code", "")
        hint = rec.get("agent_name_hint", "")
        ttl_left = max(0, int(rec.get("expires_at", 0) - time.time()))
        base = str(request.base_url).rstrip("/")
        # DB-10：纵深防点击劫持。页内有内联 style，故 style-src 放行 'unsafe-inline'（最小口子）；
        # 其余全锁死——default-src 'none' + frame-ancestors 'none'，iframe 进不来。
        pair_headers = {
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
        }
        if "text/html" in accept and "application/json" not in accept:
            html = (
                "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta name='referrer' content='no-referrer'>"
                "<title>AutoForge 配对</title></head><body style='font-family:system-ui;"
                "max-width:560px;margin:40px auto;padding:0 16px'>"
                f"<h1>配对请求待确认</h1>"
                f"<p>Agent：<b>{hint or '（未命名）'}</b></p>"
                f"<p>兑换码（8 位）：<code style='font-size:20px'>{code}</code></p>"
                f"<p>剩余时间：约 {ttl_left} 秒</p>"
                f"<p>在 Agent 端调用 <code>POST /api/mcp/pair/redeem</code>，"
                f"body <code>{{'code':'{code}','agent_name':'<Agent 自己的名字>'}}</code>。</p>"
                "<p style='color:#888;font-size:13px'>本页为只读展示，无需确认按钮。"
                "令牌只在 redeem 响应里出现一次。</p>"
                "</body></html>"
            )
            return HTMLResponse(content=html, headers=pair_headers)
        return JSONResponse(content={
            # 返工 5.1：ok 来自 link_valid（上面判过，到这必真），不写字面量——
            # 与 404 分支共用同一判定，"回给 agent 的链接有效"这句话兑现了才算 ok。
            "ok": link_valid,
            "agent_name_hint": hint,
            "code": code,
            "ttl_s": ttl_left,
            "redeem": {
                "method": "POST",
                "url": f"{base}/api/mcp/pair/redeem",
                "body": {"code": code, "agent_name": "<Agent 自己的名字>"},
            },
            "after_pairing": {
                "mcp_url": f"{base}/mcp",
                "auth_header": "Authorization: Bearer <redeem 返回的 token>",
                "warning": "token 只在 redeem 响应体里出现一次，请立即保存到客户端配置",
            },
        }, headers=pair_headers)

    @app.post("/api/user/pair/accepting", dependencies=[Depends(_write)])
    def api_pair_accepting(body: PairAcceptingBody) -> dict[str, Any]:
        """owner 侧"暂停接受配对请求"开关——裁定给的是"弹窗骚扰唯一由用户自己就能止血的形状"。

        写盘走 `_atomic_write_text`（`PairCodeStore.pause/resume` 内部），**只写布尔不写码**：
        这枚开关不该成为配对码的第二份副本。
        """
        if body.accepting:
            pair_store.resume()
        else:
            pair_store.pause()
        applied = pair_store.is_accepting()
        if applied != body.accepting:
            raise HTTPException(status_code=500, detail="配对开关回读与请求不一致，请勿当作已生效")
        return {"ok": applied == body.accepting, "accepting": applied}

    @app.get("/api/user/pair/accepting", dependencies=[Depends(_write)])
    def api_pair_accepting_state() -> dict[str, Any]:
        """开关现值。暂停时 SSE 不再产出新事件，前端没有别的途径知道"是我自己按停的"。

        只读盘点不返回 `ok`——这条是仓内既有口径（见 `tests/unit/test_af_undo_http.py`）。
        """
        return {"accepting": pair_store.is_accepting()}

    @app.post("/api/user/pair/{code}/confirm", dependencies=[Depends(_write)])
    def api_pair_confirm(code: str) -> dict[str, Any]:
        """用户在前端点'确认配对成功'：agent 已用码经 MCP `af_pair` 兑换令牌。

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
            cards.append(
                svc.automation_card(store, h["name"], rec, store.get_tags(h["name"]), pmap)
            )
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
        card = svc.automation_card(store, name, rec, store.get_tags(name), _pending_map())
        return {"ok": True, "automation": card}

    @app.post("/api/automations/{name}/enable")
    def api_automation_enable(
        name: str, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        v = _svc(
            svc.set_automation_enabled, store, name, True,
            caller=info.subject if info else "", caller_is_owner=_caller_is_owner(info),
        )
        return {"ok": True, "name": name, "enabled": True, "version": v}

    @app.post("/api/automations/{name}/disable")
    def api_automation_disable(
        name: str, info: TokenInfo | None = Depends(_write),
    ) -> dict[str, Any]:
        if store.latest(name) is None:
            raise HTTPException(status_code=404, detail="未找到自动化")
        v = _svc(
            svc.set_automation_enabled, store, name, False,
            caller=info.subject if info else "", caller_is_owner=_caller_is_owner(info),
        )
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

    @app.delete("/api/automations/{name}", dependencies=[Depends(_admin)])
    def api_automation_delete(name: str) -> dict[str, Any]:
        d = store._dir(name)
        if not d.is_dir():
            raise HTTPException(status_code=404, detail="未找到自动化")
        # rmtree 是不可逆的：目录里混有别人的版本记录 ⇒ ArchiveNameConflict；记录读不出归属
        # ⇒ ArchiveOwnerUnknown（F12）。两者都在 store 层抛出、由上面的处理器落 409。
        store.assert_deletable(name)
        shutil.rmtree(d)
        return {"ok": True, "deleted": name}

    # ── 可选：前端静态托管（SPA fallback）──────────────────────────────
    # 必须放在所有 /api 显式路由之后，否则 catch-all 会抢先吞掉 GET /api/*。
    # 两脸各自可选：只传 --ui-user-dir（只部署用户端）也要能挂上；两个都不传时
    # **不注册 catch-all**，保持只读 API 纯净（老判据：未知路径必须 404 不是 index.html）。
    dist = Path(ui_dir).resolve() if (ui_dir and Path(ui_dir).is_dir()) else None
    #: 用户端（`ui-user-mimo` 那棵树）的 dist。传了但盘上没有 ⇒ `mimo_dist=None`，
    #: 下面那一档会**如实 503**，而不是让它掉进开发面板的 index.html——
    #: "打开 /mimo 看到的却是工程控制台"这种假部署，比 404 难查得多（本机实测踩过：
    #: compose 已写 `--ui-user-dir` 而镜像里的 `forge serve` 还不认这个参数时，
    #: `/mimo/` 返回 200 + 开发面板的 index.html，`curl -o /dev/null -w %{http_code}` 全绿）。
    #: 传了 `--ui-user-dir` 就注册 catch-all，正是为了让这一档有地方说话。
    requested_mimo = bool(ui_user_dir)
    mimo_dist = (
        Path(ui_user_dir).resolve()
        if requested_mimo and Path(ui_user_dir).is_dir()
        else None
    )

    if dist is not None or requested_mimo:

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            # /api/* 由上方显式路由处理；未知 /api 路径应 404 而非回 index.html
            if full_path.startswith("api/") or full_path in ("docs", "openapi.json"):
                raise HTTPException(status_code=404, detail="Not Found")
            if full_path == UI_USER_PREFIX or full_path.startswith(f"{UI_USER_PREFIX}/"):
                if not requested_mimo:
                    # 没请求用户端 ⇒ /mimo 不是"资源找不到"，而是"这一面压根没部署"：
                    # 404 落在这里，别把开发面板的 index.html 递过去充当已部署。
                    raise HTTPException(status_code=404, detail="Not Found")
                if mimo_dist is None:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            f"用户端未挂载：--ui-user-dir 指向的 {ui_user_dir!r} 不是目录。"
                            "先构建 `ui-user-mimo`（npm run build）并确认卷挂上，再看这里"
                        ),
                    )
                rel = full_path[len(UI_USER_PREFIX) + 1 :] if "/" in full_path else ""
                root = mimo_dist
            else:
                if dist is None:
                    raise HTTPException(status_code=404, detail="Not Found")
                rel = full_path
                root = dist
            candidate = (root / rel).resolve()
            # 防目录穿越：只服务各自 dist **树内**的真实文件。
            # 判据取 `is_relative_to` 而不是 `str(...).startswith(str(root))`：后者把
            # `root=/mimo` 与兄弟目录 `/mimo-secret` 当成同一棵树（字符串前缀相同），
            # 于是 `../mimo-secret/x` 解析出树外路径仍被放行。
            if rel and candidate.is_file() and candidate.is_relative_to(root):
                resp = FileResponse(str(candidate))
                if _is_no_store_asset(rel):
                    # sw.js / manifest 必须每次回源比对：缓存住它们 = 缓存住旧 precache 清单，
                    # 升级后新资源永不接管（出资人 2026-10-27 现场：升级后旧壳取不到 chunk）。
                    resp.headers["Cache-Control"] = "no-cache"
                return resp
            if _looks_like_static_asset(rel):
                # 返工 5.4：静态资源形状的请求（`/assets/*`、末段带扩展名）**绝不回 index.html**。
                # 旧的无差别兜底把"文件不存在"伪装成 200 + HTML：浏览器拿 HTML 当 ES 模块解析 ⇒
                # 「Failed to fetch dynamically imported module」，且 PWA precache 会把这份 HTML
                # 按 JS 的 URL 缓存下来，毒住到强刷才恢复。404 才是诚实回执——它让浏览器与 SW
                # 都能识别"这一份没了"，配合下面的 index.html no-cache，正常刷新即可自愈。
                raise HTTPException(status_code=404, detail="Not Found")
            if not (root / "index.html").is_file():
                # dist 挂了但里面没有 index.html（构建产物空/卷挂错目录）：这是 503 而不是
                # FileResponse 抛的 500 —— 部署面缺文件要让运维一眼看见，别伪装成程序崩溃。
                raise HTTPException(
                    status_code=503,
                    detail=f"{UI_USER_PREFIX if root is mimo_dist else 'ui'} 的 dist 里没有 index.html：{str(root)}",
                )
            # HTML5 history 路由的深链（无扩展名）才落到壳；壳必须每次回源比对，
            # 否则升级后浏览器拿着旧壳去引用已被替换的 hash chunk——正是上面那条 bug 的源头。
            return FileResponse(str(root / "index.html"), headers={"Cache-Control": "no-cache"})

    return app

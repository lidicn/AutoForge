"""HTTP 适配器 —— 外网出站，默认 L3（IR §8.1）。

两道防线：
1. **编译期**：`af_scanner` 检查 URL 主机是否在白名单内，不在则拦截
2. **运行期**：即使绕过扫描，适配器仍会拒绝非白名单主机（不隐式放行）

⚠️ 这里的 `timeout` 是**传输层**套接超时（防单进程永久挂死），
与 IR 语义层的业务超时无关——后者必须用 `wait` + `on_timeout` 表达。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping
from urllib.error import URLError
from urllib.parse import urlparse
import urllib.request

from .base import CallResult

__all__ = [
    "HTTPAdapter", "DEFAULT_TRANSPORT_TIMEOUT", "OutboundHostNotAllowed",
    "guarded_open", "host_of",
]

#: 传输层超时（秒）——仅用于 socket，不是业务超时
DEFAULT_TRANSPORT_TIMEOUT = 5.0

#: dry_run 意图环的条数上限（稳定性审计 BUG-01 同类：常驻服务里只写不读的日志必须自己封顶）
INTENTS_MAX = 200

class _NoRedirectAllowed(Exception):
    pass

class _WhitelistRedirector(urllib.request.HTTPRedirectHandler):
    """HI-03：跟随 3xx 前必须对 Location 重过白名单，否则白名单形同虚设。"""
    def __init__(self, is_allowed):
        self._is_allowed = is_allowed
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self._is_allowed(newurl):
            raise _NoRedirectAllowed(f"重定向目标不在出站白名单内: {host_of(newurl)}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def host_of(url: str) -> str:
    """取 URL 主机名，非法 URL 返回空串（扫描器与适配器都用它，口径统一）。
    ADM B-09：检测 netloc 中的 @ 凭证注入（如 http://evil@whitelisted.com）。"""
    try:
        parsed = urlparse(url)
        # ADM B-09：@ 在 netloc 中表示 userinfo 注入，实际请求发往 @ 前的主机
        if parsed.netloc and "@" in parsed.netloc:
            return ""
        return parsed.hostname or ""
    except ValueError:
        return ""


class OutboundHostNotAllowed(URLError):
    """出站目标主机不在白名单内（首跳或 3xx 目标）。

    继承 `URLError`（⇒ `OSError`）是有意的：四处第一方出站各有自己的降级链， catch 的都是
    `URLError`/`OSError`/裸 `Exception`。新建一个裸 `Exception` 子类会让"被护栏拦下"变成
    未捕获异常往上冒——那把降级改成了崩溃，比旁路更难查。
    """


def guarded_open(
    req: Any,
    *,
    allowed_hosts: Iterable[str],
    timeout: float | None = None,
) -> Any:
    """出站唯一收口：首跳与跟随的 3xx 目标都必须落在 `allowed_hosts` 内。

    第十四轮审计 F15：`HTTPAdapter` 里两道防线都写齐了（白名单 + 重定向重校验），但
    `af_catalog` / `af_live` / `af_metrics` / `af_registry` 四处第一方出站各自直连 `urlopen`
    ——默认 opener **会跟 3xx 且不对 Location 重校验**，等于护栏建好了却有四条路没走。
    本函数把那条"已经修过的路"变成一个可复用的收口点，四处只需交出各自主机
    （`host_of(自己的 base_url)`），不再各自决定要不要校验。

    `allowed_hosts` 为空 ⇒ 一律拒绝（不隐式放行，与 `HTTPAdapter.is_allowed` 同一口径）。
    """
    hosts = {h for h in allowed_hosts if h}
    target = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    host = host_of(target)
    if not host:
        raise OutboundHostNotAllowed(f"出站目标缺主机名或含 @ 凭证注入：{target!r}")
    if host not in hosts:
        raise OutboundHostNotAllowed(
            f"出站目标不在白名单内：{host}（已配置：{sorted(hosts)}）"
        )
    opener = urllib.request.build_opener(
        _WhitelistRedirector(lambda u: host_of(u) in hosts)
    )
    return opener.open(req, timeout=timeout)


class HTTPAdapter:
    name = "http"

    def __init__(
        self,
        allowed_hosts: tuple[str, ...] = (),
        dry_run: bool = True,
        timeout: float = DEFAULT_TRANSPORT_TIMEOUT,
    ):
        self.allowed_hosts = tuple(allowed_hosts)
        self.dry_run = dry_run
        self.timeout = timeout
        self.intents: list[tuple[str, dict[str, Any]]] = []

    def is_allowed(self, url: str) -> bool:
        return host_of(url) in self.allowed_hosts

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        url = str(params.get("url", ""))
        host = host_of(url)
        if not host:
            return CallResult.fail("缺少或非法 url", action=action, params=dict(params))
        if not self.is_allowed(url):
            return CallResult.fail(
                f"主机未在网络出站白名单内：{host}（已配置：{list(self.allowed_hosts)}）",
                action=action,
                params=dict(params),
            )
        if self.dry_run:
            if len(self.intents) >= INTENTS_MAX:
                del self.intents[0]  # 环形封顶：常驻服务里每次下发都记一条，不裁就只增不减
            self.intents.append((action, dict(params)))
            return CallResult.ok({"dry_run": True, "action": action, "url": url})
        # 真实请求走传输层；G1 默认不会走到这里
        from urllib import request  # 局部导入保持模块轻量

        try:
            req = request.Request(url, method=_method_of(action))
            # 与四处第一方出站共用同一个收口点（F15）：不在这里再手写一遍 build_opener，
            # 否则"要不要重校验 Location"就有了两份可以各自漂移的实现。
            with guarded_open(req, allowed_hosts=self.allowed_hosts, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return CallResult.ok({"status": resp.status, "body": body})
        except _NoRedirectAllowed as exc:
            return CallResult.fail(str(exc), action=action, url=url)
        except OutboundHostNotAllowed as exc:
            return CallResult.fail(str(exc), action=action, url=url)
        except Exception as exc:  # 传输层异常 → 交给 IR 的 on_error
            return CallResult.fail(f"HTTP 请求失败：{exc}", action=action, url=url)


def _method_of(action: str) -> str:
    lowered = action.lower()
    for method in ("get", "post", "put", "delete", "patch"):
        if method in lowered:
            return method.upper()
    return "GET"

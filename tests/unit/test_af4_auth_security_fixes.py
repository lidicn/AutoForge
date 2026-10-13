"""第四期审计·鉴权与 API 安全面 9 条缺陷的修复回归测试。

覆盖（编号对应第四期审计报告）：
- AF13-R / AF13b-R / AF13c-R：admin.json 读不出/形状不对 ⇒ 旧口径 has_admin=False
  ⇒ 任意非空凭据换全权限令牌 + 匿名注册成管理员。修复：poisoned 旗标 fail-closed。
- AF38-R：鉴权面进程内窗口用裸 time.time() ⇒ NTP 前跳后防爆破/软锁定/限速三个刹车
  同时 fail-open。修复：进程内窗口改 time.monotonic()；软锁定双判据（单调钟抗阶跃 +
  盘上墙钟跨重启恢复，取更严格者）；落盘字段保留墙钟。
- AF66：_load_revoked_file 对"能解析但形状不是 list"静默当空名单 ⇒ 已撤销令牌复活。
- AF67：_load_issued_file 读不出/形状不对静默 return ⇒ 已签发令牌重启后消失（API 锁死零日志）。
- AF50c-R：_rewrite_issued 静默 return + items() 不在 try 内 ⇒ 改名删除看似成功、重启复活。
- AF53：_refuse_when_list_file_unreadable 把 0 字节/纯空白文件当"已损坏"拒写 ⇒
  配对码/授权码 create() 锁死。放行做窄：空文件放行，有内容却读不出来仍拒（AF76 教训）。
- AF55：load_secret 把"文件在却读不出来"与"文件不存在"归并 ⇒ 鉴权静默退化全站公开。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import (  # noqa: E402
    AdminUserStore,
    AuthCodeStore,
    PairCodeStore,
    RateLimitExceeded,
    RateLimiter,
    TokenInfo,
    TokenRegistry,
)
from autoforge.af_secrets import load_secret  # noqa: E402


# ── AF13c-R：admin.json 形状不对不得穿出 build_app ─────────────────────


def test_admin_json_wrong_shape_does_not_escape_constructor(tmp_path):
    """admin.json = [1,2,3]：修复前 data.get 抛 AttributeError 穿出 build_app()；
    修复后按损坏 fail-closed（poisoned），构造不炸。"""
    p = tmp_path / "admin.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    store = AdminUserStore(p)  # 修复前在此 AttributeError
    assert store.has_admin is True          # 无法证明"没有管理员"⇒ 按有处理
    assert store.verify("x", "y") is False  # 登录一律拒
    assert store.register("newbie", "password123") is False  # 注册一律拒


def test_admin_json_corrupt_fails_closed(tmp_path):
    """AF13-R：admin.json 存在但 JSON 损坏 ⇒ 旧口径清成 None/has_admin=False。"""
    p = tmp_path / "admin.json"
    p.write_text("{oops not json", encoding="utf-8")
    store = AdminUserStore(p)
    assert store.has_admin is True
    assert store.verify("anyone", "anything") is False
    assert store.register("attacker", "hunter2!") is False


def test_admin_json_missing_is_still_fresh_install(tmp_path):
    """绿色一侧：文件真的不存在 = 首装，注册必须放行（不能 poison 把首装也打死）。"""
    store = AdminUserStore(tmp_path / "nope.json")
    assert store.has_admin is False
    assert store.register("boss", "password123") is True
    assert store.has_admin is True
    assert store.verify("boss", "password123") is True
    assert store.verify("boss", "wrong") is False


# ── AF13-R / AF13b-R：HTTP 面——损坏 admin.json 不得换令牌、不得被注册 ────


def _client(tmp_path: Path, monkeypatch, *, seed_admin: str | None = None) -> TestClient:
    for k in ("AUTOFORGE_API_TOKEN", "AUTOFORGE_TOKENS", "AF_ALLOW_NOAUTH"):
        monkeypatch.delenv(k, raising=False)
    store_root = tmp_path / "store"
    if seed_admin is not None:
        authdir = store_root / ".auth"
        authdir.mkdir(parents=True, exist_ok=True)
        (authdir / "admin.json").write_text(seed_admin, encoding="utf-8")
    return TestClient(build_app(str(store_root)))


def test_corrupt_admin_json_login_401_register_409(tmp_path, monkeypatch):
    """AF13-R/13b-R 端到端：admin.json 损坏时——
    修复前：任意非空凭据 200 + read/write/live 全权限令牌；register 200 落盘。
    修复后：login 401、register 409。"""
    client = _client(tmp_path, monkeypatch, seed_admin="{broken json")

    r = client.post("/api/auth/login", json={"username": "attacker", "password": "x"})
    assert r.status_code == 401, r.text

    r = client.post("/api/auth/register", json={"username": "attacker", "password": "hunter2!"})
    assert r.status_code == 409, r.text

    # has-admin 端点也按"已有管理员"返回（前端显示登录页而非注册页）
    assert client.get("/api/auth/has-admin").json()["has_admin"] is True


def test_fresh_install_register_then_login_still_works(tmp_path, monkeypatch):
    """绿色一侧（防拦太宽）：真首装时注册→登录全链路不受影响。"""
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/auth/has-admin").json()["has_admin"] is False

    r = client.post("/api/auth/register", json={"username": "boss", "password": "secret123"})
    assert r.status_code == 200, r.text
    assert r.json()["token"]

    r = client.post("/api/auth/login", json={"username": "boss", "password": "wrong-pass"})
    assert r.status_code == 401, r.text
    r = client.post("/api/auth/login", json={"username": "boss", "password": "secret123"})
    assert r.status_code == 200, r.text


# ── AF66：撤销名单形状不对 ⇒ poison，不再静默当空 ──────────────────────


def test_revoked_file_wrong_shape_poisons_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "secret")
    revoked = tmp_path / "revoked.json"
    revoked.write_text('{"not": "a list"}', encoding="utf-8")  # 能解析但顶层是对象
    reg = TokenRegistry(revoked)
    assert reg._revoked_poisoned is True
    # 修复前：对象被静默跳过 ⇒ "secret" 正常签发；修复后一律拒
    assert reg.authenticate("secret") is None


# ── AF67：已签发名单损坏 ⇒ 启动即报错，不再静默丢光 ────────────────────


def _clean_env(monkeypatch):
    for k in ("AUTOFORGE_API_TOKEN", "AUTOFORGE_TOKENS", "AUTOFORGE_REVOKED_TOKENS"):
        monkeypatch.delenv(k, raising=False)


def test_issued_file_corrupt_fails_startup(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    revoked = tmp_path / "revoked.json"
    issued = tmp_path / "issued.json"
    issued.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="已损坏"):
        TokenRegistry(revoked, issued_path=issued)


def test_issued_file_wrong_shape_fails_startup(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    revoked = tmp_path / "revoked.json"
    issued = tmp_path / "issued.json"
    issued.write_text('[1, 2, 3]', encoding="utf-8")  # 能解析但顶层是数组
    with pytest.raises(ValueError, match="形状"):
        TokenRegistry(revoked, issued_path=issued)


# ── AF50c-R：rewrite_issued 遇损坏文件抛错，不再"看似成功" ─────────────


def test_rewrite_issued_corrupt_file_raises_on_revoke(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "secret")
    revoked = tmp_path / "revoked.json"
    issued = tmp_path / "issued.json"
    issued.write_text(
        json.dumps({"tok-a": {"subject": "agent-a", "scopes": ["read"]}}),
        encoding="utf-8",
    )
    reg = TokenRegistry(revoked, issued_path=issued)
    assert reg.authenticate("tok-a") is not None
    # 盘上文件被搞坏
    issued.write_text("not json {", encoding="utf-8")
    # 修复前：静默 return ⇒ 返回 1（看似成功）；修复后抛错让调用方感知
    with pytest.raises(ValueError, match="已损坏"):
        reg.revoke_by_subject("agent-a")


def test_rewrite_issued_wrong_shape_raises_on_rename(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", "secret")
    revoked = tmp_path / "revoked.json"
    issued = tmp_path / "issued.json"
    issued.write_text(
        json.dumps({"tok-a": {"subject": "agent-a", "scopes": ["read"]}}),
        encoding="utf-8",
    )
    reg = TokenRegistry(revoked, issued_path=issued)
    # 运行期间盘上文件被改成数组形状（旧实现 items() 会 AttributeError 穿出调用方）
    issued.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ValueError, match="形状"):
        reg.rename_subject("agent-a", "agent-b")


# ── AF53：0 字节/纯空白名单文件不得卡死 create() ──────────────────────


def test_pair_store_create_succeeds_on_zero_byte_file(tmp_path):
    p = tmp_path / "pair_codes.json"
    p.write_text("", encoding="utf-8")  # 0 字节
    store = PairCodeStore(p)
    code = store.create("agent-x")  # 修复前：_persist 护栏当"已损坏"拒写 ⇒ ValueError
    assert code.code and len(code.code) == 8


def test_pair_store_create_succeeds_on_blank_file(tmp_path):
    p = tmp_path / "pair_codes.json"
    p.write_text("   \n  ", encoding="utf-8")  # 纯空白
    store = PairCodeStore(p)
    assert store.create("agent-y").code


def test_pair_store_create_still_refuses_truly_broken_file(tmp_path):
    """窄放行：有内容却读不出来仍拒（AF76 教训——放行不能宽到把损坏也放过去）。"""
    p = tmp_path / "pair_codes.json"
    p.write_text("garbage{{{", encoding="utf-8")
    store = PairCodeStore(p)
    with pytest.raises(ValueError, match="已损坏"):
        store.create("agent-z")


def test_auth_store_create_succeeds_on_zero_byte_file(tmp_path):
    p = tmp_path / "auth_codes.json"
    p.write_text("", encoding="utf-8")
    store = AuthCodeStore(p)
    ac = store.create("long")
    assert ac.code


# ── AF38-R：进程内窗口/软锁定/限速 抗 NTP 墙钟阶跃 ────────────────────


def test_attempt_window_survives_wallclock_forward_jumps(tmp_path, monkeypatch):
    """B 组对照：每次失败后墙钟 +600s（窗口仅 60s）。
    修复前：窗口每次被判过期清零 ⇒ hits 恒为 1 ⇒ 从不拦截；
    修复后：单调钟不受阶跃影响 ⇒ 窗口打满 ⇒ 真实码也不给快速通道。"""
    acs = AuthCodeStore(tmp_path / "auth_codes.json")
    real = acs.create("long").code
    clock = {"t": time.time()}
    monkeypatch.setattr(time, "time", lambda: clock["t"])
    for _ in range(acs.ATTEMPT_LIMIT + 2):
        acs.record_failure("00000000")
        clock["t"] += 600  # 墙钟前跳 600s
    assert acs.validate(real) is False


def test_soft_lock_survives_wallclock_forward_jump(tmp_path, monkeypatch):
    """C 组对照：单码软锁 300s，墙钟 +301s。
    修复前：锁定当场解除；修复后单调钟腿仍锁 ⇒ validate 仍 False。"""
    acs = AuthCodeStore(tmp_path / "auth_codes.json")
    code = acs.create("long").code
    for _ in range(acs.FAILURE_THRESHOLD):
        acs.record_failure(code)
    rec = acs._codes[code]
    assert rec["locked_until"] is not None
    monkeypatch.setattr(time, "time", lambda: rec["locked_until"] + 10)
    assert acs.validate(code) is False


def test_soft_lock_survives_restart_via_persisted_wallclock(tmp_path):
    """F 组对照（不丢语义）：重启后单调钟副本没了，盘上墙钟 locked_until 仍应生效。"""
    p = tmp_path / "auth_codes.json"
    s1 = AuthCodeStore(p)
    code = s1.create("long").code
    for _ in range(s1.FAILURE_THRESHOLD):
        s1.record_failure(code)
    s2 = AuthCodeStore(p)  # 新实例 = 重启
    assert s2.validate(code) is False


def test_rate_limiter_survives_wallclock_forward_jump(monkeypatch):
    """D 组对照：窗口 60s，打满后墙钟 +61s。
    修复前：旧命中全被判过期 ⇒ 不再拦截；修复后单调钟不受影响 ⇒ 仍拦。"""
    limiter = RateLimiter(per_minute=3, window_s=60)
    for _ in range(3):
        limiter.check("ip:x")
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:x")
    jumped = time.time() + 10**9  # 先取真实现值再 patch，避免 lambda 递归
    monkeypatch.setattr(time, "time", lambda: jumped)  # 模拟 NTP 前跳
    with pytest.raises(RateLimitExceeded):
        limiter.check("ip:x")


# ── AF55：secret 文件在却读不出来 ⇒ 抛错，不再静默回退 env ────────────


def test_secret_file_unreadable_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOFORGE_API_TOKEN", raising=False)
    # 存在但 UTF-8 解码失败（≈ 权限/内容损坏那档，不改 Windows ACL 也能触发）
    (tmp_path / "AUTOFORGE_API_TOKEN").write_bytes(b"\xff\xfe\x01bad")
    with pytest.raises(RuntimeError, match="读不出来"):
        load_secret("AUTOFORGE_API_TOKEN")


def test_secret_file_missing_still_falls_back_to_env(tmp_path, monkeypatch):
    """绿色一侧：文件真的不存在仍是正常首装路径。"""
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-value")
    assert load_secret("AUTOFORGE_HA_TOKEN") == "env-value"


def test_secret_file_empty_still_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-value")
    (tmp_path / "AUTOFORGE_HA_TOKEN").write_text("", encoding="utf-8")
    assert load_secret("AUTOFORGE_HA_TOKEN") == "env-value"


# ── AF38-R 附带：_lockout_until_mono 是进程内增长容器，纯写不读也必须回收 ──


def test_lockout_mono_map_is_pruned_on_write_without_reads(tmp_path):
    """有界缓存判据 E 的落点：这张单调钟副本表跟着"被锁过的码数"开，
    不能只在 validate() 读侧懒清——一枚被锁后再没人来 validate 的码，
    要靠 record_failure 的写入口自己退出去。本测试全程不调 validate()。"""
    acs = AuthCodeStore(tmp_path / "auth_codes.json")
    now_mono = time.monotonic()
    # 塞一整张"活的"假表（到期全在未来），把硬上限顶满
    for i in range(acs.LOCKOUT_MONO_MAX):
        acs._lockout_until_mono[f"stale{i:04d}"] = now_mono + acs.LOCKOUT_S + i
    assert len(acs._lockout_until_mono) == acs.LOCKOUT_MONO_MAX
    # 造一枚真码并把它打到锁定——写侧必须先回收再写入，不许破顶
    real = acs.create("long").code
    for _ in range(acs.FAILURE_THRESHOLD):
        acs.record_failure(real)
    assert len(acs._lockout_until_mono) <= acs.LOCKOUT_MONO_MAX
    # 回收动作发生在写入口：最老的假条目被挤掉，真码的单调钟腿仍在
    assert real in acs._lockout_until_mono


def test_lockout_mono_map_drops_expired_on_write(tmp_path):
    """写侧回收的另一腿：已到期条目在写入口直接清掉（不等 validate）。"""
    acs = AuthCodeStore(tmp_path / "auth_codes.json")
    now_mono = time.monotonic()
    for i in range(5):
        acs._lockout_until_mono[f"old{i}"] = now_mono - 1 - i  # 全部已到期
    real = acs.create("long").code
    for _ in range(acs.FAILURE_THRESHOLD):
        acs.record_failure(real)
    assert not any(k.startswith("old") for k in acs._lockout_until_mono)
    assert real in acs._lockout_until_mono

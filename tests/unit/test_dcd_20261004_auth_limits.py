"""DCD 裁定 20261004 §一/§二 里 AF 侧四件的判据。

覆盖四件事，每件都问"这条约定烂掉时长什么样"：

1. **长期码绝对上限**（§一 Q1=A）：默认 180 天、`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS=0` 显式关。
   判据不只测新码——**落盘在裁定之前的老码记的是 `expires_at=None`**，本仓不做数据迁移，
   改为读侧补出到期点，所以老码必须同样有界（否则"上限"对存量一行都不生效）。
2. **`/api/asks/pending` 补 read 门禁**（§一 F-1）：sidecar 里是待答 ask 的 `prompt`/`room`
   原文，此前只有 `/api/asks` 那半边挂了 `Depends(_read)`。
3. **授权面落盘必须原子**（本批读码盘出，不在任何审计报告里）：`af_auth` 四个写点原先是
   裸 `write_text`，而 `_load_*` 把解析失败一律吞成"当没有这份文件"。半截 JSON 因此不是
   "读回上一版"而是**读回空**——撤销黑名单读回空 = 已撤销的令牌全部复活（fail-open）。
4. **paho 上界钉死**（§二 Q2=B）：`>=1.6,<2.1`，两处声明都要有上界。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from autoforge import af_auth  # noqa: E402
from autoforge.af_api import build_app  # noqa: E402
from autoforge.af_auth import AuthCodeStore, TokenRegistry  # noqa: E402

DAY_S = 86400.0
KEY = "AUTOFORGE_AUTH_LONGCODE_TTL_DAYS"


# ── 1. 长期码绝对上限 ────────────────────────────────────────────────


def test_long_code_gets_default_180_day_cap(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    store = AuthCodeStore(tmp_path / "codes.json")
    rec = store.create("long")
    assert rec.expires_at is not None
    assert rec.expires_at - rec.created_at == pytest.approx(180 * DAY_S, abs=2)


def test_zero_explicitly_disables_the_cap(tmp_path, monkeypatch):
    """`0` 是裁定给的"显式关"，不是"配错了回落默认"。"""
    monkeypatch.setenv(KEY, "0")
    store = AuthCodeStore(tmp_path / "codes.json")
    rec = store.create("long")
    assert rec.expires_at is None
    assert store.validate(rec.code) is True


@pytest.mark.parametrize("raw", ["", "  ", "abc", "18x"])
def test_unparsable_value_falls_back_to_default_not_to_off(tmp_path, monkeypatch, raw):
    """配错键名值不该把上限悄悄关掉（关是把防线撤了，回落只是多限 180 天）。"""
    monkeypatch.setenv(KEY, raw)
    store = AuthCodeStore(tmp_path / "codes.json")
    rec = store.create("long")
    assert rec.expires_at == pytest.approx(rec.created_at + 180 * DAY_S, abs=2)


def test_negative_value_counts_as_off(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "-5")
    store = AuthCodeStore(tmp_path / "codes.json")
    assert store.create("long").expires_at is None


def _write_legacy_store(path: Path, age_days: float) -> str:
    """伪造一枚裁定之前落盘的长期码：`expires_at=None`，只有生成时刻可比。"""
    code = "654321"
    created = time.time() - age_days * DAY_S
    path.write_text(
        json.dumps(
            [
                {
                    "code": code,
                    "kind": "long",
                    "created_at": created,
                    "expires_at": None,
                    "revoked": False,
                    "consumed": False,
                    "failed_attempts": 0,
                    "locked_until": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    return code


def test_legacy_long_code_ages_out_without_migration(tmp_path, monkeypatch):
    """存量老码不做迁移也必须受上限约束——否则"180 天"对已经发出去的码一行都不生效。"""
    monkeypatch.setenv(KEY, "180")
    path = tmp_path / "codes.json"
    code = _write_legacy_store(path, 181)
    store = AuthCodeStore(path)
    assert store.validate(code) is False


def test_legacy_long_code_inside_the_cap_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY, "180")
    path = tmp_path / "codes.json"
    code = _write_legacy_store(path, 179)
    store = AuthCodeStore(path)
    assert store.validate(code) is True


def test_list_reports_age_and_the_same_deadline_validation_uses(tmp_path, monkeypatch):
    """管理面显示的到期点必须就是 `validate` 用的那个数字。

    否则 owner 在页面上看到"永不失效"，服务面却在 180 天判它过期——这正是裁定要给
    "距生成多久"要防的那种错位。
    """
    monkeypatch.setenv(KEY, "180")
    path = tmp_path / "codes.json"
    legacy = _write_legacy_store(path, 181)
    store = AuthCodeStore(path)
    fresh = store.create("long")

    rows = {r["code"]: r for r in store.list()}
    assert rows[legacy]["expires_at"] is None
    assert rows[legacy]["expires_at_effective"] == pytest.approx(
        rows[legacy]["created_at"] + 180 * DAY_S, abs=2
    )
    assert rows[legacy]["expires_in_s"] < 0
    assert rows[legacy]["age_s"] == pytest.approx(181 * DAY_S, abs=5)
    assert store.validate(legacy) is False

    assert rows[fresh.code]["age_s"] < 5
    assert rows[fresh.code]["expires_in_s"] == pytest.approx(180 * DAY_S, abs=5)
    assert store.validate(fresh.code) is True


def test_short_code_ttl_semantics_unchanged(tmp_path, monkeypatch):
    """上限只管长期码：短期码仍按 `ttl_minutes` 走，且不受这个键影响。"""
    monkeypatch.setenv(KEY, "0")
    store = AuthCodeStore(tmp_path / "codes.json")
    rec = store.create("short", ttl_minutes=30)
    assert rec.expires_at == pytest.approx(rec.created_at + 30 * 60, abs=2)


# ── 2. `/api/asks/pending` 的 read 门禁 ─────────────────────────────


def _client(tmp_path, monkeypatch, env: dict[str, str], readonly: bool = False) -> TestClient:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(build_app(str(tmp_path / "store"), readonly=readonly))


TOKENS = {
    "tok-reporter": {"subject": "reporter", "scopes": ["read"]},
    #: 故意只给 write，不带 read——否则"write 含 read"这条判据是自证的（令牌里本来就有 read）。
    "tok-bot": {"subject": "bot", "scopes": ["write"]},
    "tok-live": {"subject": "liver", "scopes": ["live"]},
}


def test_asks_pending_now_needs_a_token(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    assert client.get("/api/asks/pending").status_code == 403


def test_asks_pending_accepts_read_and_write_domain(tmp_path, monkeypatch):
    """DB 侧持 write 域令牌（write 含 read）⇒ 对端零改动，这是裁定里那句"不改对端"的实测。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    for token in ("tok-reporter", "tok-bot"):
        r = client.get("/api/asks/pending", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, token
        assert r.json()["ok"] is True


def test_asks_pending_still_refuses_wrong_scope(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    r = client.get(
        "/api/asks/pending", headers={"Authorization": "Bearer tok-live"}
    )
    assert r.status_code == 403


def test_asks_pending_sibling_endpoint_unchanged(tmp_path, monkeypatch):
    """对照组：`/api/asks`（本就有门禁）与 `/api/health`（scope=None 的公开面）不受影响。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    assert client.get("/api/asks").status_code == 403
    assert client.get("/api/health").status_code == 200


def test_read_domain_does_not_imply_write(tmp_path, monkeypatch):
    """蕴含只有一个方向：read 令牌碰不了写面。反向自动放宽等于把 F-1 的收口又打开。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    r = client.post(
        "/api/asks/answer",
        json={"ask_id": "inst-001", "text": "开"},
        headers={"Authorization": "Bearer tok-reporter"},
    )
    assert r.status_code == 403


# ── 3. 授权面落盘：原子写 + 撤销名单读不成即拒绝 ─────────────────────


def test_revoke_list_is_rewritten_via_replace_not_in_place(tmp_path, monkeypatch):
    """目标文件必须是被 `os.replace` 换上去的，且临时名每次不同（固定名会互相截断）。"""
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(af_auth.os, "replace", spy)
    registry = TokenRegistry(revoked_path=tmp_path / ".auth" / "revoked.json")
    registry.revoke("tok-1")
    registry.revoke("tok-2")

    assert len(seen) == 2
    assert all(src != dst for src, dst in seen)
    assert len({src for src, _ in seen}) == 2, f"临时名撞车：{seen}"
    residue = [p.name for p in (tmp_path / ".auth").iterdir() if p.name.endswith(".tmp")]
    assert residue == []


def test_crash_during_persist_leaves_the_old_complete_file(tmp_path, monkeypatch):
    """原子性的正面判据：写崩在 fsync 上时，盘上必须还是**上一版整份**，不是半截。

    这一条是本批的立身判据——裸 `write_text` 的形状在这里会读回截断的 JSON，
    而 `_load_revoked_file` 把它吞成"名单为空"，于是撤销过的令牌重新有效。
    """
    revoked = tmp_path / ".auth" / "revoked.json"
    registry = TokenRegistry(revoked_path=revoked)
    registry.revoke("tok-live-1")
    before = json.loads(revoked.read_text(encoding="utf-8"))
    assert before == ["tok-live-1"]

    def boom(fd):
        raise OSError("simulated crash while flushing")

    monkeypatch.setattr(af_auth.os, "fsync", boom)
    with pytest.raises(OSError):
        registry.revoke("tok-live-2")

    assert json.loads(revoked.read_text(encoding="utf-8")) == before
    residue = [p.name for p in revoked.parent.iterdir() if p.name.endswith(".tmp")]
    assert residue == []


def test_unreadable_revoke_list_refuses_every_token(tmp_path, monkeypatch):
    """撤销名单读不成 ≠ 名单为空：黑名单无法证明"不在册"，一律拒绝。"""
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps({"tok-a": {"subject": "bot", "scopes": ["read", "write"]}}),
    )
    revoked = tmp_path / "revoked.json"
    revoked.write_text('["tok-a"', encoding="utf-8")  # 截断，正如崩溃会留下的形状
    registry = TokenRegistry(revoked_path=revoked)
    assert registry.enabled is True
    assert registry.authenticate("tok-a") is None


def test_readable_revoke_list_does_not_blanket_refuse(tmp_path, monkeypatch):
    """对照组（防"一律拒绝"被写成无条件拒绝）：名单能读出来时，未在册的令牌照常通过。"""
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps({"tok-a": {"subject": "bot", "scopes": ["read", "write"]}}),
    )
    revoked = tmp_path / "revoked.json"
    revoked.write_text(json.dumps(["tok-somebody-else"]), encoding="utf-8")
    registry = TokenRegistry(revoked_path=revoked)
    assert registry.authenticate("tok-a") is not None
    assert registry.authenticate("tok-somebody-else") is None


def test_missing_revoke_file_is_not_poisoned(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps({"tok-a": {"subject": "bot", "scopes": ["read"]}}),
    )
    registry = TokenRegistry(revoked_path=tmp_path / "revoked.json")
    assert registry.authenticate("tok-a") is not None


def test_the_crash_judgement_is_not_vacuous(tmp_path, monkeypatch):
    """反空洞：把助手换成"就地写一半就崩"，证明差别的来源是原子性而不是巧合。

    没有这一条，`test_crash_during_persist_leaves_the_old_complete_file` 有可能只是因为
    根本没写成东西而"碰巧"读到旧内容。这里显式造出旧形状的失败模式（目标文件先被截断、
    再逐份填 ⇒ 崩在中间就是半截），断言它确实把盘写成读不出来的样子。
    """
    monkeypatch.setenv(
        "AUTOFORGE_TOKENS",
        json.dumps({"tok-a": {"subject": "bot", "scopes": ["read"]}}),
    )
    revoked = tmp_path / ".auth" / "revoked.json"
    revoked.parent.mkdir(parents=True, exist_ok=True)
    revoked.write_text(json.dumps(["tok-somebody-else"]), encoding="utf-8")
    registry = TokenRegistry(revoked_path=revoked)
    assert registry.authenticate("tok-a") is not None

    def half_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            fh.write(text[: max(1, len(text) // 2)])
            raise OSError("crash mid-write（旧形状：目标文件已经在被写）")

    monkeypatch.setattr(af_auth, "_atomic_write_text", half_write)
    with pytest.raises(OSError):
        registry.revoke("tok-b")

    with pytest.raises(ValueError):
        json.loads(revoked.read_text(encoding="utf-8"))
    revived = TokenRegistry(revoked_path=revoked)
    assert revived._revoked_poisoned is True
    assert revived.authenticate("tok-a") is None


def test_auth_code_store_persist_is_atomic_and_leaves_no_residue(tmp_path, monkeypatch):
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(af_auth.os, "replace", spy)
    path = tmp_path / ".auth" / "auth_codes.json"
    store = AuthCodeStore(path)
    store.create("long")
    store.create("short", ttl_minutes=5)

    assert len(seen) == 2
    assert all(dst == str(path) for _, dst in seen)
    assert len({src for src, _ in seen}) == 2
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 2
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


# ── 4. paho 上界 ────────────────────────────────────────────────────


def test_paho_is_pinned_with_an_upper_bound_everywhere_it_is_declared():
    """§二 Q2=B：两处声明（`dev` 与 `mqtt`）都要带上界。

    只核 `>=1.6` 的那一半是本批之前的状态；上界才是这次裁定买的东西——paho 2.x 换了回调
    签名，镜像某次重建拿到 2.x 就是"今天能连、重建后连不上"。
    """
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)

    specs: list[str] = list(data["project"].get("dependencies", []))
    for extra, reqs in data["project"].get("optional-dependencies", {}).items():
        specs.extend(f"{extra}:{req}" for req in reqs)
    paho = [s for s in specs if s.split(":", 1)[-1].startswith("paho-mqtt")]
    assert len(paho) >= 2, f"paho 声明处读不成（锚点形状变了）：{paho}"
    for spec in paho:
        assert spec.endswith("paho-mqtt>=1.6,<2.1"), f"上界缺失或口径不符：{spec}"


# ── 5. 明文样例凭据不许进产品码（§一 F-2）─────────────────────────────

#: 射程 = 产品码 + 三棵第一方 UI 树的源码面（裁定 20261004 §一 F-2 的 UI 半边同批收口：
#: `ui-user-mimo/src/api/mock.ts` 那对 `MOCK_CREDENTIALS` 已删，登录页 footer 也不再印口令）。
#: 判据不写成"扫那一个文件"：它一旦被抄进别的注释、夹具或前端文案，本条照红。
SAMPLE_CREDENTIAL = "forge2026"

#: 扫描时跳过的目录（构建产物与依赖树：里面的命中不是本仓的字）
_SCAN_SKIP = frozenset({"node_modules", "dist", ".vite", "__pycache__"})


def _product_source_files(root: Path) -> list[Path]:
    """`os.walk` 就地剪枝：依赖树（node_modules）里有几十万条路径，走进去再过滤会把这条判据
    变成套件里最慢的一条（本仓实测：全仓 ripgrep 20s 不返回）。"""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP]
        for name in filenames:
            if Path(name).suffix in {".py", ".ts", ".tsx", ".vue", ".mjs", ".js"}:
                out.append(Path(dirpath) / name)
    return out


def test_plaintext_sample_credential_is_not_carried_in_product_source():
    """凭据写在 docstring / mock 夹具里 = 把"这套系统有一对通用口令"印进每一个克隆，且永远不会被轮换。"""
    repo = Path(__file__).resolve().parents[2]
    roots = [repo / "src" / "autoforge", repo / "ui", repo / "ui-user", repo / "ui-user-mimo"]
    files: list[Path] = []
    for root in roots:
        files.extend(_product_source_files(root))
    assert len(files) > 100, f"射程读不成（源码面形状变了）：{len(files)} 个文件"
    hits = [
        str(p.relative_to(repo)) for p in files if SAMPLE_CREDENTIAL in p.read_text(encoding="utf-8")
    ]
    assert hits == [], f"明文样例凭据回到了产品码：{hits}"


# ── 6. `/api/user/auth-codes` 的 write 门 + owner/非 owner 分层（§一 F-3）──


#: 两档面必须同形：面板切档不靠字段增减，只靠 `code` 换形状。
ROW_KEYS = {
    "code",
    "kind",
    "created_at",
    "expires_at",
    "age_s",
    "expires_at_effective",
    "expires_in_s",
    "revoked",
    "consumed",
    "failed_attempts",
    "locked_until",
}


def _login_token(client: TestClient) -> str:
    """走真实登录面拿 owner JWT——"owner 面给明文"这条判据的主体由端点自己签，不在测试里伪造。"""
    r = client.post("/api/auth/login", json={"username": "sp", "password": "x"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _seed(client: TestClient, token: str, kind: str = "long", ttl_minutes: int | None = None) -> str:
    body: dict[str, object] = {"kind": kind}
    if ttl_minutes is not None:
        body["ttl_minutes"] = ttl_minutes
    r = client.post(
        "/api/user/auth-code",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _rows(client: TestClient, token: str | None) -> list[dict]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    r = client.get("/api/user/auth-codes", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["codes"]


def test_auth_code_list_refuses_a_read_only_token(tmp_path, monkeypatch):
    """裁定验收原文那条："read 令牌取 auth-codes 列表 403"。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    r = client.get(
        "/api/user/auth-codes", headers={"Authorization": "Bearer tok-reporter"}
    )
    assert r.status_code == 403


def test_owner_face_still_sees_plaintext(tmp_path, monkeypatch):
    """对照档：如果实现退化成"无脑全掩码"，本条先红——否则掩码判据可以靠关掉整个端点变绿。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    owner = _login_token(client)
    code = _seed(client, owner)
    assert code in {row["code"] for row in _rows(client, owner)}


def test_legacy_shared_token_counts_as_the_owner_face(tmp_path, monkeypatch):
    """`AUTOFORGE_API_TOKEN` 的主体是 `shared`（这台部署自己的手），与登录 JWT 同档。

    这一条把"owner 面到底是谁"的口径钉住：本仓 `/api/user/agents` 早就把 shared/owner
    当同一族排除在"第三方 agent"之外，此处沿用；换成第三方 subject 即红。
    """
    client = _client(
        tmp_path, monkeypatch, {"AUTOFORGE_API_TOKEN": "legacy-admin-token"}
    )
    code = _seed(client, "legacy-admin-token")
    assert code in {row["code"] for row in _rows(client, "legacy-admin-token")}


def test_third_party_write_token_gets_the_mask_not_the_code(tmp_path, monkeypatch):
    """同一枚码、同一次进程：owner 面看得见明文，第三方 write 面只看到掩码。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    owner = _login_token(client)
    code = _seed(client, owner)
    assert code in {row["code"] for row in _rows(client, owner)}

    rows = _rows(client, "tok-bot")
    assert rows, "列表读不成（判据会自证为空）"
    assert {row["code"] for row in rows} == {af_auth.CODE_MASK}
    assert all(set(row) == ROW_KEYS for row in rows), "掩码面靠加减字段过关"


def test_masked_face_keeps_the_status_fields(tmp_path, monkeypatch):
    """"只给掩码 + 状态"里的状态不许顺手删掉：owner 看得到哪枚码已消耗/已撤销，非 owner 同样看得到。"""
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    owner = _login_token(client)
    _seed(client, owner, "short", ttl_minutes=10)
    short = [r for r in _rows(client, "tok-bot") if r["kind"] == "short"]
    assert len(short) == 1
    row = short[0]
    assert row["kind"] == "short"
    assert row["revoked"] is False and row["consumed"] is False
    assert row["expires_in_s"] is not None and row["expires_in_s"] > 0


def test_the_mask_does_not_leak_how_long_the_code_is(tmp_path, monkeypatch):
    """6 位老码仍在validate 面兼容 ⇒ 掩码若跟着码长变化，等于指出"这枚只要穷举 10^6"。"""
    store_dir = tmp_path / "store" / ".auth"
    store_dir.mkdir(parents=True)
    legacy = _write_legacy_store(store_dir / "auth_codes.json", age_days=1)
    client = _client(tmp_path, monkeypatch, {"AUTOFORGE_TOKENS": json.dumps(TOKENS)})
    newest = _seed(client, _login_token(client))
    assert len(legacy) != len(newest), f"两枚码等长，本条判据失效：{legacy}/{newest}"

    rows = _rows(client, "tok-bot")
    assert len({row["code"] for row in rows}) == 1, "掩码形状随码长变了"
    assert all(row["code"] == af_auth.CODE_MASK for row in rows)
    assert all(set(row) == ROW_KEYS for row in rows)


def test_readonly_degraded_instance_still_lists_codes(tmp_path, monkeypatch):
    """铁律 #6：只读降级实例照常供读。这条 GET 要 write **域**但不要写端点的 503 阻塞器。

    写成 `Depends(_write)` 会被 `readonly` 那次的重赋值吃掉 ⇒ 降级实例上整个授权码面板 503。
    同批对照：真正的写面（创建码）在降级实例上仍然 503，防止"为了过这条把门整个拆掉"。
    """
    client = _client(
        tmp_path,
        monkeypatch,
        {"AUTOFORGE_TOKENS": json.dumps(TOKENS)},
        readonly=True,
    )
    owner = _login_token(client)
    assert _rows(client, owner) == []
    r = client.post(
        "/api/user/auth-code", json={"kind": "long"}, headers={"Authorization": f"Bearer {owner}"}
    )
    assert r.status_code == 503


def test_noauth_escape_hatch_treats_the_caller_as_owner(tmp_path, monkeypatch):
    """`AF_ALLOW_NOAUTH=1` 下没有身份概念可言：按 owner 面放行，与其余端点在逃生舱下一致。"""
    client = _client(tmp_path, monkeypatch, {"AF_ALLOW_NOAUTH": "1"})
    code = _seed(client, "")
    assert code in {row["code"] for row in _rows(client, None)}

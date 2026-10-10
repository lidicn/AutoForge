"""凭据解析优先级的**射程**判据（安全审计第三轮 F-11／F-12 对撞）。

F-11 报的是「`af_secrets` 声明 `credentials.json > secret 文件 > 环境变量`，而
`load_secret` 只实现后两段 ⇒ 文档承诺的那条路径对令牌完全不通」。现读对撞的结果是
**宣告面的射程写宽了，不是实现错了**：三段链在 `af_config` 读那两个键时是真的
（`get_ha_token` / `get_api_token` 先取 `credentials.json`），而对令牌注册表走的那族
名字（`AUTOFORGE_TOKENS`／`AUTOFORGE_REVOKED_TOKENS`／legacy `AUTOFORGE_API_TOKEN`）
确实只有两段。

本文件因此钉**行为**而不是钉文案：三段链在它成立的地方必须真成立（腿 4／5），
在它不成立的地方不许假装成立（腿 1／2／3／6）。
"""
from __future__ import annotations

from pathlib import Path

from autoforge import af_config, af_secrets

CREDS_MARK = "creds-file-wins-6a1b"
ENV_MARK = "env-fallback-9c3d"
FILE_MARK = "secret-file-wins-4e8f"
TOKENS_NAME = "AUTOFORGE_TOKENS"


def _clean(monkeypatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def _mkdir(path: Path) -> Path:
    """凭据档要落盘，父目录必须先在场（原子写不会替你把树建出来）。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_load_secret_prefers_secret_file_over_env(tmp_path, monkeypatch):
    """第一段（本层之内）：secret 文件在场且非空 ⇒ 压过同名环境变量。"""
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / TOKENS_NAME).write_text(FILE_MARK + "\n", encoding="utf-8")
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(secret_dir))
    monkeypatch.setenv(TOKENS_NAME, ENV_MARK)
    assert af_secrets.load_secret(TOKENS_NAME) == FILE_MARK


def test_load_secret_falls_back_to_env_when_file_missing_or_empty(tmp_path, monkeypatch):
    """第二段：文件缺 / 文件空，两种"没有"都落回 env（旧 `env_file` 兼容面）。"""
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(secret_dir))
    monkeypatch.setenv(TOKENS_NAME, ENV_MARK)
    assert af_secrets.load_secret(TOKENS_NAME) == ENV_MARK

    (secret_dir / TOKENS_NAME).write_text("   \n", encoding="utf-8")
    assert af_secrets.load_secret(TOKENS_NAME) == ENV_MARK
    assert af_secrets.load_secret_or_none("AUTOFORGE_NOT_SET_ANYWHERE") is None


def test_load_secret_never_consults_credentials_json(tmp_path, monkeypatch):
    """`credentials.json` 对 `load_secret` 是**不存在的东西**：把同名文件放到盘上也不许被读。

    这条腿就是 F-11 的本体——旧宣告把三段链写成 `load_secret` 的全局事实。
    """
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(secret_dir))
    _clean(monkeypatch, TOKENS_NAME)
    # 同目录里放一份内容命中的 credentials.json：本层根本不看这个文件名
    (secret_dir / "credentials.json").write_text(
        f'{{"{TOKENS_NAME}": "{CREDS_MARK}"}}', encoding="utf-8"
    )
    assert af_secrets.load_secret(TOKENS_NAME, default="") == ""


def test_config_face_keeps_credentials_file_first(tmp_path, monkeypatch):
    """三段链成立的地方：`af_config` 的两个键上，`credentials.json` 压过 env／secret 文件。"""
    _clean(monkeypatch, "AUTOFORGE_HA_TOKEN", "AUTOFORGE_API_TOKEN")
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", ENV_MARK)
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", ENV_MARK)

    cfg = af_config.get_config(_mkdir(tmp_path / "store"))
    cfg.update_credentials(ha_token=CREDS_MARK, api_token=CREDS_MARK)
    assert cfg.get_ha_token() == CREDS_MARK
    assert cfg.get_api_token() == CREDS_MARK


def test_config_face_falls_back_to_env_when_credentials_empty(tmp_path, monkeypatch):
    """反例：凭据档里没这两条时，链子必须继续往下走，而不是回空串把服务读瞎。"""
    _clean(monkeypatch, "AUTOFORGE_HA_TOKEN", "AUTOFORGE_API_TOKEN")
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", ENV_MARK)
    monkeypatch.setenv("AUTOFORGE_API_TOKEN", ENV_MARK)
    cfg = af_config.get_config(tmp_path / "store_empty")
    assert cfg.get_ha_token() == ENV_MARK
    assert cfg.get_api_token() == ENV_MARK


def test_registry_face_resolves_through_two_stages_only():
    """令牌注册表这一族的**形状锚点**：三枚名字都直调 `load_secret`，不经 `af_config`。

    钉的是"这族名字走两段的 `load_secret`"这一事实（`af_auth.py:166/170/189` 现读）——
    哪天有人给注册表接上 `credentials.json`，本条红，而那时 `af_secrets` 的射程说明要一起改。
    """
    src = (Path(af_secrets.__file__).parent / "af_auth.py").read_text(encoding="utf-8")
    for name in ("AUTOFORGE_API_TOKEN", "AUTOFORGE_TOKENS", "AUTOFORGE_REVOKED_TOKENS"):
        assert f'load_secret("{name}")' in src, name
    assert "credentials.json" not in src


def test_docstring_scopes_the_three_stage_chain():
    """自证：射程说明必须在场。把 docstring 改回全局宣告 ⇒ 本条红。"""
    doc = af_secrets.__doc__ or ""
    assert "射程只有那两个键" in doc
    assert "不在本层" in doc

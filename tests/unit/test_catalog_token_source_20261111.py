"""F1（2026-11-11 线上）：`DeviceCatalog` 绕开统一令牌入口，读路径退回双令牌源。

现场：容器 env 的 `AUTOFORGE_HA_TOKEN` 与 `/data/credentials.json` 的 `ha_token` 都是 183 位
JWT 但**不相等**——文件那枚 `GET /api/` 200，env 那枚被 HA 判 401 并弹「Login attempt failed」
通知。契约 R4-1 要求写路径（`af_service._ha_token`）与读路径（catalog）用同一把钥匙，
`af_catalog.DeviceCatalog.__init__` 却直接 `os.getenv(...)`，读工具"健康"、写工具 401。

判据（卡 §F1 验收）：
1. 凭证文件有值、env 是另一枚 ⇒ 取**文件**那枚；
2. 凭证文件缺失/损坏 ⇒ 回退 env；
3. 两者皆空 ⇒ 空串且不抛。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge.af_catalog import DeviceCatalog


@pytest.fixture(autouse=True)
def _isolate_secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把 secret 文件目录指到临时空目录：⛔ 不让宿主机的 /run/secrets 参与判定。"""
    secret_dir = tmp_path / "_secrets"
    secret_dir.mkdir()
    monkeypatch.setenv("AUTOFORGE_SECRET_DIR", str(secret_dir))


def _write_credentials(root: Path, payload: str) -> None:
    (root / "credentials.json").write_text(payload, encoding="utf-8")


def test_catalog_prefers_credentials_file_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """① 文件有值、env 是另一枚 ⇒ 取文件那枚（F1 现场形状）。"""
    _write_credentials(tmp_path, json.dumps({"ha_token": "file-token"}))
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-legacy-token")

    assert DeviceCatalog(tmp_path).ha_token == "file-token"


def test_catalog_falls_back_to_env_when_credentials_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """② 凭证文件缺失 ⇒ 回退 env（新名优先）。"""
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-legacy-token")

    assert DeviceCatalog(tmp_path).ha_token == "env-token"


def test_catalog_falls_back_to_env_when_credentials_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """② 凭证文件损坏（非法 JSON）⇒ 回退 env，且**不抛**。"""
    _write_credentials(tmp_path, "{ this is not json")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")
    monkeypatch.setenv("AUTOFORGE_HA_TOKEN", "env-legacy-token")

    assert DeviceCatalog(tmp_path).ha_token == "env-token"


def test_catalog_empty_token_without_any_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """③ 两者皆空 ⇒ 空串且不抛（⛔ 不是"启动即炸"，也不是 None）。"""
    monkeypatch.delenv("HA_LONG_LIVED_TOKEN", raising=False)
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)

    catalog = DeviceCatalog(tmp_path)

    assert catalog.ha_token == ""


def test_catalog_explicit_token_parameter_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注入面不变：显式 `ha_token=` 优先于文件（测试与替换数据源依赖这一条）。"""
    _write_credentials(tmp_path, json.dumps({"ha_token": "file-token"}))
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "env-token")

    assert DeviceCatalog(tmp_path, ha_token="injected").ha_token == "injected"

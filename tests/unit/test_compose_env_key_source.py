"""compose 的 environment 键名 ↔ 代码里的读数，双向同源。

起因（NAS 现场）：`docker-compose.api.yml` 把多令牌那枚写成复数 `AUTOFORGE_API_TOKENS`，
而 `af_auth.py` 读的是 `AUTOFORGE_TOKENS`。宿主机的值注入得再认真，也只是喂给一个没人读的
空位——鉴权照旧失败、不留原因，运维只能在部署机上手工补一行。这类"键名手抄漂移"和 MQTT_*
那条同族（`test_mqtt_compose_env.py` 管 wheel 侧那五个），这里管 AF 自己读的 `AUTOFORGE_*`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker" / "docker-compose.api.yml"
SRC = REPO / "src" / "autoforge"

#: 代码里确有其读数、但**故意**不出现在 compose 的键：旧单令牌别名，新部署一律走
#: `AUTOFORGE_TOKENS` 的 JSON 对象。豁免必须带理由，且理由非空——空字符串或漏写都判红。
NOT_DEPLOYED = {
    "AUTOFORGE_API_TOKEN": "v1.4 之前的单令牌别名；compose 只引导写多令牌那份，免得两种配法并存",
}

ENV_LINE_RE = re.compile(r"^\s*-\s+(AUTOFORGE_[A-Z0-9_]+)=\$\{")
SECRET_LINE_RE = re.compile(r"^\s*#\s+-\s+(AUTOFORGE_[A-Z0-9_]+)")
LOAD_SECRET_RE = re.compile(r"""load_secret\(\s*["'](AUTOFORGE_[A-Z0-9_]+)["']""")


def _compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def _live_env_keys(text: str) -> list[str]:
    """`environment:` 段里**会执行**的键（注释行整行跳过，与 MQTT 那条判据同口径）。"""
    keys: list[str] = []
    in_env = False
    for line in text.splitlines():
        if line.strip() == "environment:":
            in_env = True
            continue
        if in_env:
            if line.strip() and not line.lstrip().startswith(("#", "-")):
                in_env = False
                continue
            m = ENV_LINE_RE.match(line)
            if m:
                keys.append(m.group(1))
    return keys


def test_compose_live_env_keys_are_read_by_code():
    """compose 里每一条会执行的 `AUTOFORGE_*`，代码里必须有同名读数。"""
    text = _compose_text()
    keys = _live_env_keys(text)
    assert len(keys) >= 6, f"compose 里数到的 environment 键只有 {keys}——形状改了，这条『干净』没有依据"
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in sorted(SRC.glob("*.py")))
    unseen = [k for k in keys if f'"{k}"' not in corpus and f"'{k}'" not in corpus and k not in corpus]
    assert not unseen, f"compose 引用了代码里没人读的键（值注入会静默失效）：{unseen}"


def test_multitoken_key_is_deployable():
    """R14 现场那一枚：`AUTOFORGE_TOKENS` 必须在 compose 里有一条会执行的引用。"""
    keys = _live_env_keys(_compose_text())
    assert "AUTOFORGE_TOKENS" in keys, keys


def test_stale_plural_token_key_is_gone():
    """复数那枚假键名整份 docker/ 目录都不许再出现（含注释与 secret 路径）。"""
    hits = [p.relative_to(REPO).as_posix() for p in REPO.glob("docker/**/*")
            if p.is_file() and "AUTOFORGE_API_TOKENS" in p.read_text(encoding="utf-8", errors="ignore")]
    assert not hits, f"docker/ 里仍有代码不读的 AUTOFORGE_API_TOKENS：{hits}"


def test_every_token_key_the_code_reads_is_either_deployable_or_exempt():
    """反向：`af_auth` 用 `load_secret(...)` 读的每个键，compose 里要么有引用，要么在豁免里带理由。"""
    from autoforge import af_auth

    src = Path(af_auth.__file__).read_text(encoding="utf-8")
    read_keys = sorted(set(LOAD_SECRET_RE.findall(src)))
    assert read_keys, "af_auth 里一个 load_secret(\"AUTOFORGE_*\") 都没数到——这条判据此刻没有射程"
    text = _compose_text()
    missing = [k for k in read_keys if k not in text and k not in NOT_DEPLOYED]
    assert not missing, f"代码在读、compose 却无处可注入（运维只能手工补，补完还不在版本库）：{missing}"

    # 豁免不能烂掉：列了名字就得真是代码在读的那枚，且理由非空。
    stale = [k for k in NOT_DEPLOYED if k not in read_keys]
    assert not stale, f"豁免名单里的键已不是代码的读数（要么补 compose，要么删豁免）：{stale}"
    hollow = [k for k, why in NOT_DEPLOYED.items() if not str(why).strip()]
    assert not hollow, f"豁免没写理由：{hollow}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

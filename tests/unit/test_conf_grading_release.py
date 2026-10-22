"""compose 的 conf 分级引擎开关必须真的烘进发布面（v2.6，2026-10-22 出资人指示第 2 条）。

`AUTOFORGE_CONF_GRADING` 是影子/金丝雀整套引擎的挂载闸（`af_runtime.py` 的
`Runtime.__post_init__` 读点），此前生产面从未设过 → 引擎在役一次没跑过（问题册 gap #1，
实测 conf 恒种子 1.0、band 恒 auto，影子档物理不可能命中）。这次把它烘进 compose，
开关与发布物同库同源。两个失败形状都不会让任何东西变红，必须钉住：

① 注释掉/抄错键名 ⇒ compose 照起、引擎静默不装——"注释不算覆盖"的同族教训
   （`test_mqtt_compose_env.py::_compose_env` 的口径：只有非注释条目才算数）；
② 键名在 compose 与读点两处手抄，一处改名另一处掉队 ⇒ 开关静默失效。键名单一真源
   纪律同 `test_switch_name_is_the_bridges_own_constant`：compose 的键必须就是
   `af_runtime.py` 源里读的那个字符串。

射程边界照本仓老规矩写明白：引擎"装上之后影子/金丝雀行为对不对"属行为判据，
由 `scripts/verify_conf_grading.py`（端到端验收 harness）当场跑，不由本文件代领 PASS。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "docker-compose.api.yml"
RUNTIME = ROOT / "src" / "autoforge" / "af_runtime.py"

#: v2.6 发布决策：字面量 "1" 烘进入库文件（确定性单源，不依赖 NAS 宿主 .env——
#: 那份 .env 仍是旧键名，迁移是另一笔账）。要关就走 revert commit + 自部署。
EXPECTED_VALUE = "1"


def _compose_env() -> dict[str, str]:
    """`services.autoforge-api.environment` 里**会执行**的条目（注释行整行跳过）。"""
    entries: dict[str, str] = {}
    in_env = False
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        if line.strip() == "environment:":
            in_env = True
            continue
        if not in_env:
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            in_env = False  # environment 段结束（回到 `restart:` 等同级键）
            continue
        key, _, value = stripped[2:].partition("=")
        entries[key] = value
    assert entries, "compose 里一个 environment 条目都没数到（形状改了，报『启用』没有依据）"
    return entries


def test_compose_enables_conf_grading():
    entries = _compose_env()
    assert "AUTOFORGE_CONF_GRADING" in entries, (
        sorted(entries),
        "compose 面没有启用 conf 分级引擎：影子/金丝雀整套不装载（gap #1 回归）",
    )
    assert entries["AUTOFORGE_CONF_GRADING"] == EXPECTED_VALUE, entries["AUTOFORGE_CONF_GRADING"]


def test_switch_name_is_the_runtime_read_sites_own_constant():
    """compose 的键名必须就是 `af_runtime.py` 源里读的那个字符串——不建第二份手抄名单。"""
    src = RUNTIME.read_text(encoding="utf-8")
    assert 'AUTOFORGE_CONF_GRADING' in src, (
        "af_runtime.py 里读不到这个键名：开关的读点改名了，compose 这格没跟着改",
    )
    # 读点形状钉住：os.environ.get("AUTOFORGE_CONF_GRADING", ...) —— 不是别处手抄的 getenv
    assert 'os.environ.get("AUTOFORGE_CONF_GRADING"' in src or \
        "os.environ.get('AUTOFORGE_CONF_GRADING'" in src, src

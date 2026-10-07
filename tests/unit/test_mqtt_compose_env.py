"""compose 的 MQTT 键名必须与**烘进镜像的那枚 wheel** 同源（计划 §5.3 第 10 件，裁定 20261004 18:35 §二）。

裁定要的是"补齐引用、值一律留空"，而"引用"这两个字有两种失败形状，且**都不会让任何东西变红**：
① 键名手抄错一个字母（`MQTT_HOTS`）⇒ compose 照起、桥静默连不上，因为空串与未设置同视，那是 fail-closed
的沉默；② 留空却把默认值顶掉（`os.getenv(K, "1883")` 这类写法在 compose 写 `K=` 时拿到的是空串而不是默认值）
⇒ 端口/心跳被配成不可解析，打开开关那天才炸。所以这里两档都钉住：键名从 wheel 现取（**不建第二份名单**），
留空的语义用真模块当场跑，而不是相信注释里那句话。

射程边界照本仓老规矩写明白：NAS 重烤后"服务照常起、桥 no-op"那半属**窗内验收**（计划第 10 件前置=第 1 件），
不由本文件代领 PASS。真打开开关走非停机窗的配置推送，推送前先预检 `paho_available()` 与 `broker_settings()`
（§二之二十六：起桥排在 `uvicorn.run` 之前且不吞异常，值没配好就是整个 AF 起不来）。
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "docker-compose.api.yml"
DOCKERFILE = ROOT / "docker" / "Dockerfile.api"

MQTT_TOKEN_RE = re.compile(r"MQTT_[A-Z0-9]+(?:_[A-Z0-9]+)*")
WHEEL_FROM_DOCKERFILE_RE = re.compile(r"COPY\s+(docker/homesdk/\S+\.whl)")
SWITCH_LINE_RE = re.compile(r"^AUTOFORGE_MQTT=\$\{AUTOFORGE_MQTT:-([^}]*)\}$")


def _wheel() -> pathlib.Path:
    """wheel 的路径从 `Dockerfile.api` 的 COPY 行现取：部署面真正装的那枚才算数。"""
    hits = WHEEL_FROM_DOCKERFILE_RE.findall(DOCKERFILE.read_text(encoding="utf-8"))
    assert hits, "读不出 Dockerfile.api 的 homesdk COPY 行：这条同源判据此刻没有射程"
    assert len(hits) == 1, f"Dockerfile 里钉了多枚 homesdk wheel：{hits}"
    path = ROOT / hits[0]
    assert path.is_file(), f"Dockerfile 钉死的 wheel 不在盘上：{path}"
    return path


def _wheel_source(member: str) -> str:
    with zipfile.ZipFile(_wheel()) as z:
        return z.read(member).decode("utf-8")


def _wheel_mqtt_keys() -> set[str]:
    """wheel 里 `homesdk/mqtt.py` 真正读的键。f-string 作用域形式（`MQTT_USER_{scoped}`）在源码里
    以尾下划线出现，剥掉尾下划线后与规范键同形 ⇒ 不需要任何人工别名表。
    """
    tokens = MQTT_TOKEN_RE.findall(_wheel_source("homesdk/mqtt.py"))
    return {t.rstrip("_") for t in tokens}


def _compose_env() -> dict[str, str]:
    """`services.autoforge-api.environment` 里**会执行**的条目（注释行整行跳过——§二之四十六
    那条"注释不算覆盖"的同族：注释里出现一个键名不等于容器里有这个环境变量）。
    """
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
    assert entries, "compose 里一个 environment 条目都没数到（形状改了，报『干净』没有依据）"
    return entries


def test_compose_mqtt_keys_are_all_read_by_the_vendored_wheel():
    keys = {k for k in _compose_env() if k.startswith("MQTT_")}
    # 反空洞：一个都没数到就是射程塌了，下面的"子集"会因空集而假绿。
    assert len(keys) >= 5, f"compose 里 MQTT_* 引用少于 5 条，本批要钉的『补齐引用』没做到：{sorted(keys)}"
    wheel_keys = _wheel_mqtt_keys()
    assert wheel_keys, "wheel 的 mqtt 模块里一个 MQTT_* 都没数到"
    invented = sorted(keys - wheel_keys)
    assert not invented, (
        f"compose 引用了 wheel 不读的键：{invented}——桥会静默连不上而 compose 照样起（键名手抄的形状）"
    )


def test_switch_name_is_the_bridges_own_constant():
    """compose 里那格开关的名字必须就是 `af_mqtt_bridge.ENV_ENABLED`，而不是又抄一遍字符串。"""
    from autoforge import af_mqtt_bridge

    entries = _compose_env()
    assert af_mqtt_bridge.ENV_ENABLED in entries, (af_mqtt_bridge.ENV_ENABLED, sorted(entries))


def test_compose_switch_default_is_actually_off_to_the_bridge(monkeypatch):
    """把 compose 写的那个缺省值喂给桥自己的 `env_enabled()`，让它当场判"关"——不是靠注释里那句"缺省关"。"""
    from autoforge import af_mqtt_bridge

    line = next(
        (f"{k}={v}" for k, v in _compose_env().items() if k == af_mqtt_bridge.ENV_ENABLED), ""
    )
    m = SWITCH_LINE_RE.match(line)
    assert m, f"开关行形状不是 `${{AUTOFORGE_MQTT:-X}}`：{line!r}"
    monkeypatch.setenv(af_mqtt_bridge.ENV_ENABLED, m.group(1))
    assert af_mqtt_bridge.env_enabled() is False, f"缺省值 {m.group(1)!r} 在桥那边读出来不是『关』"
    monkeypatch.setenv(af_mqtt_bridge.ENV_ENABLED, "1")
    assert af_mqtt_bridge.env_enabled() is True


def test_empty_values_neither_shadow_defaults_nor_anonymize(monkeypatch):
    """留空必须真等于"没配"：端口落回 wheel 里的 DEFAULT_PORT，缺 host / 缺凭据一律抛而不是往下走。"""
    import homesdk.mqtt as mqtt
    from homesdk.config import MissingEnv

    for key in ("HOMESDK_MQTT_HOST", "MQTT_HOST", "HOMESDK_MQTT_PORT", "MQTT_PORT",
                "HOMESDK_MQTT_KEEPALIVE", "MQTT_KEEPALIVE", "HOMESDK_MQTT_USER", "MQTT_USER",
                "MQTT_USERNAME", "HOMESDK_MQTT_PASSWORD", "MQTT_PASSWORD", "MQTT_PASS", "MQTT_PASSWD"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("MQTT_HOST", "")
    with pytest.raises(MissingEnv):
        mqtt.broker_settings()

    monkeypatch.setenv("MQTT_HOST", "broker.invalid.")
    monkeypatch.setenv("MQTT_PORT", "")
    monkeypatch.setenv("MQTT_KEEPALIVE", "")
    _host, port, keepalive = mqtt.broker_settings()
    assert port == mqtt.DEFAULT_PORT, f"空串顶掉了默认端口：{port} != {mqtt.DEFAULT_PORT}"
    assert keepalive == 60, keepalive

    monkeypatch.setenv("MQTT_USER", "")
    monkeypatch.setenv("MQTT_PASSWORD", "")
    with pytest.raises(mqtt.MqttCredentialsMissing):
        mqtt.resolve_credentials()


def test_the_wheel_lookup_declares_empty_same_as_unset():
    """上一条判据的前提钉在 wheel 里，而不是本机那份可编辑安装：`_lookup` 必须明写"空串与未设置同视"。"""
    src = _wheel_source("homesdk/config.py")
    body = src.split("def _lookup", 1)[1].split("\ndef ", 1)[0]
    assert "raw is not None" in body and "raw.strip()" in body, body


def test_installed_homesdk_is_the_version_the_image_installs():
    """两条活体判据用的是本机 import 到的那份，同源结论才成立：本机与 wheel 必须同一个 0.x.y。"""
    import homesdk

    pinned = re.match(r"homesdk-(\d[^-]*)-", _wheel().name)
    assert pinned, _wheel().name
    assert getattr(homesdk, "__version__", None) == pinned.group(1), (
        homesdk.__file__,
        getattr(homesdk, "__version__", None),
        pinned.group(1),
    )


#: DCD 20261007 §六 Q2 登记的权威摘要（`E:\NAS\homesdk\dist\VERSIONS.txt` 0.3.2 段）。
#: 那次裁定明写"这就是权威值"，理由是 0.3.1 吃过"首投 sha 作废"的亏——0.3.2 只构建过一次、
#: 源码已入库。改这个值只有一条路：新的 DCD 裁定。
AUTHORITATIVE_WHEEL_SHA256 = "19bc83a67a96931c4556caec52f29c190aaa03a0a7036e0b41c242a533fb5505"


def test_vendored_wheel_is_the_exact_bytes_dcd_registered():
    """钉**字节**而不是钉版本号：同名不同内容的 wheel 会让"仓里写着 0.3.2"与"跑的真是那枚 0.3.2"脱钩。

    文件名与 `__version__` 都能自述 0.3.2，而 metadata 是打包时写进去的字符串——它证明不了内容。
    交付面（`Dockerfile.api`/`Dockerfile.test`/`ci.yml`×3）全部按文件名装这一枚，所以这枚字节错了
    就是四处一起装错，且 CI 照样绿。
    """
    path = _wheel()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == AUTHORITATIVE_WHEEL_SHA256, (
        f"{path.relative_to(ROOT)} 的 sha256 与 DCD 登记的权威值不符：{digest}",
        "要么是有人换了 wheel 没走裁定，要么是这条判据的期望值被顺手改过——两者都要停下核对 VERSIONS.txt",
    )

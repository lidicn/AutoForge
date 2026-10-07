#!/usr/bin/env python3
"""ADM 联动停机窗的**窗后验收**：裁定四项做成一条命令，且缺项不许读成绿。

裁定原文（计划 §四）：窗后 AF 侧验收四项，**缺任一项即该步未完成，不许用"配置正确只是没抓包"过账**：
① `compose ps` 服务在；② `/health` 返回 200；③ 抓到一条含家庭墙钟 `ts` 的 `af/automation/fired`；
④ `adm/autoforge/status` 的 retained 快照解码后 `state` 为 `online`。

为什么要有这个脚本：这四件散在交接单 §六 的 EXEMPT 条目里，窗当天靠临时手搓命令——而"手搓"正是过账
出事的形状（少跑一项、把 skip 读成 pass、把 `/health` 打错成 404 就判服务没起）。本脚本把四件做成
**逐项判定 + 三态结论**（PASS / FAIL / UNAVAILABLE）：

- 有任一 UNAVAILABLE ⇒ 退出码 2，正文写明"EXEMPT ≠ VERIFIED，本窗未验收"。**不给"三项绿一项没跑"
  留一条印成绿色的路**（铁律 #5）。
- 有 FAIL ⇒ 退出码 1。
- 四项全 PASS ⇒ 退出码 0，结论行逐项带实测读数（容器状态行、HTTP 码与真路径、事件键集与 `ts` 偏差、
  retained 标志与值）。

判据取自契约表（唯一真源 `ADM联动主题注册表与消息契约.md`），不是"能连上就行"：
- ③ 先查 §1.2 那行的四个键 `{trace_id, ts, automation_id, ref}`，再查 `ts` 与当下 UTC 的偏差
  （超 `--max-skew`，默认 900 秒）——这一条专门拦"把仿真锚点 2026-09-14 08:00 或裸 UTC 当墙钟发出去"
  那类回归（§二之十五 修过的同一族）；最后按 §1.2 的"**事件类永不 retained**"查 `retain is False`，
  因为事件一旦被 retained，新订阅者会收到几天前的"有人回家"。多余的键不判红，只如实打印键集
  （`instance_id` 是 v2.6 前的同值过渡字段；曾多发的 `node_id` 已按裁定 20261004 §一 2 删除）。
- ④ 直接读 paho 的 `msg.retain`：读到一条 `online` **不等于**它是 retained 快照，现发的一条也能长得一样。

连接与凭据**不自己抄一套键名**：走机制层 `homesdk.mqtt`——`broker_settings()` 缺 `MQTT_HOST` 即抛、
`get_client()` 内部取凭据且不做匿名回退。本脚本因此全程不接触口令值，也就没有把它打印出来的风险。
早期草稿用过自造的 `AF_MQTT_*` 环境变量名（第二真源）和外部 `mosquitto_sub` 的 `-W`/`-c` 两个选项
（本机没有该二进制 ⇒ 选项形状无从实测），两处都已纠正并记在 §二之二十五。

纯标准库 + `docker` 命令 + paho（经 `homesdk.mqtt` 取）。paho 声明在 `pyproject.toml` 的 `[mqtt]`
extra 里，交付面/CI 面是否真装到由 `scripts/check_mqtt_runtime_dep.py` 静态钉住（§二之二十六）——
本脚本跑在窗内那台机器上，缺了它这两项只能判 UNAVAILABLE。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

FIRED_TOPIC = "af/automation/fired"
STATUS_TOPIC = "adm/autoforge/status"
# §1.2 事件类表格里 `af/automation/fired` 那一行的载荷列（多余键不判红，只打印实际键集）
FIRED_KEYS = ("trace_id", "ts", "automation_id", "ref")
# AF 真实注册的只有 `/api/health`；`/health` 留作探测项，404 属预期，读数里写清命中的是哪个
HEALTH_PATHS = ("/api/health", "/health")

PASS, FAIL, NA = "PASS", "FAIL", "UNAVAILABLE"


class MqttEnvMissing(RuntimeError):
    """paho / `MQTT_HOST` / 凭据任缺 ⇒ 该项 UNAVAILABLE（环境缺项 ≠ 系统没做好）。"""


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """返回 (退出码, stdout, stderr)；`-1` 表示命令本身不存在。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return -1, "", f"ENOENT:{cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"TIMEOUT:{timeout}s"
    return p.returncode, p.stdout or "", p.stderr or ""


def item_compose(args) -> tuple[str, str]:
    """① 容器在跑：`docker ps` 按名字过滤，不看"命令跑通了"而看状态行含 `Up`。"""
    rc, out, err = _run(["docker", "ps", "--filter", f"name={args.service}",
                         "--format", "{{.Names}} {{.Status}}"], timeout=20)
    if rc == -1:
        return NA, f"本机没有 `docker`（{err}）⇒ 该项只能在跑着 compose 的那台机器上取数"
    if rc != 0:
        return FAIL, f"`docker ps` 退出码 {rc}：{(err or out).strip()[:200]}"
    line = out.strip()
    if not line:
        return FAIL, f"没有名为 `{args.service}` 的运行中容器（过滤后为空）"
    if "Up" not in line and "running" not in line.lower():
        return FAIL, f"容器状态行不含 Up/running：{line[:200]}"
    return PASS, f"运行中：{line[:200]}"


def item_health(args) -> tuple[str, str]:
    """② 健康检查 200：按候选顺序试，命中即停。

    "连不上"在哪台机器上意味着什么，由 ① 的实测决定，不由本脚本猜：容器已确认在跑却连不上 ⇒ FAIL
    （AF 起来了但服务没起）；容器项根本没确认（本机没有 docker）⇒ UNAVAILABLE，开发机上无从判定。
    """
    tried = []
    for path in HEALTH_PATHS:
        url = args.base.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=args.http_timeout) as resp:
                code, body = resp.status, resp.read(4096).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            tried.append(f"{path}→{exc.code}")
            continue
        except Exception as exc:
            where = "容器已在跑却连不上" if args.container_up else "① 未确认容器（本机可能根本没跑 AF）"
            verdict = FAIL if args.container_up else NA
            return verdict, (f"取 `{url}` 失败（{where}）：{type(exc).__name__}: {exc}")
        if code != 200:
            return FAIL, f"`{path}` 返回 {code}（不是 200）"
        keys = ""
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                keys = "，键 " + ",".join(sorted(data)[:6])
        except json.JSONDecodeError:
            keys = "，正文非 JSON"
        return PASS, f"`{path}` 返回 200{keys}（候选 {'/'.join(HEALTH_PATHS)}）"
    return FAIL, f"两个候选路径都不是 200：{'；'.join(tried)}"


def _collect(topic: str, wait: int):
    """连 broker 收 `topic` 的第一条消息（含 retain 标志）；等不到返回 None。

    经机制层取客户端：`broker_settings()` 与 `get_client()` 的抛错语义就是"缺配置"，
    本函数把它转成 `MqttEnvMissing`，好让调用方判 UNAVAILABLE 而不是 FAIL。
    """
    try:
        from homesdk import mqtt as hm
        from homesdk.config import MissingEnv
    except ImportError as exc:
        raise MqttEnvMissing(f"机制层不可用（{exc}）⇒ 该项只能在有 homesdk+paho 的机器上取数")
    if not hm.paho_available():
        raise MqttEnvMissing("homesdk.mqtt.paho_available() 为假（没装 paho-mqtt）")
    try:
        host, port, keepalive = hm.broker_settings()
        client = hm.get_client(peer="af-window-verify")
    except MissingEnv as exc:
        raise MqttEnvMissing(f"机制层缺配置：{type(exc).__name__}: {exc}")
    except ValueError as exc:                      # MQTT_PORT 越界之类：配置在，但配错了
        raise MqttEnvMissing(f"机制层配置不合法：{exc}")
    got: list = []
    client.on_message = lambda _c, _u, m: got.append(m)
    client.connect(host, port, keepalive)
    client.subscribe(topic, qos=hm.QOS)
    client.loop_start()
    try:
        deadline = time.monotonic() + wait
        while not got and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 —— 断开失败不影响已收到的读数
            pass
    return got[0] if got else None


def _msg_text(msg) -> str:
    raw = getattr(msg, "payload", b"")
    return raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)


def _parse_ts(raw: str) -> datetime | None:
    """解析**带偏移**的 ISO 时刻；无偏移返回 None（读不出是不是家庭墙钟，不猜）。"""
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        m = re.search(r"(20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d)([+-]\d\d:?\d\d)?", text)
        if not m:
            return None
        try:
            dt = datetime.fromisoformat(m.group(1) + (m.group(2) or ""))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def item_fired(args) -> tuple[str, str]:
    """③ 抓到一条 fired，且 `ts` 是家庭墙钟口径、且这条不是 retained 事件。"""
    try:
        msg = _collect(FIRED_TOPIC, args.wait)
    except MqttEnvMissing as exc:
        return NA, str(exc)
    except OSError as exc:      # 地址配好了却连不上 = 窗后 broker/服务没起，是 FAIL
        return FAIL, f"连 broker 失败：{type(exc).__name__}: {exc}"
    if msg is None:
        return FAIL, f"{args.wait} 秒内没抓到 `{FIRED_TOPIC}`（已连上 broker）⇒ 窗后没有真实事件流量"
    text = _msg_text(msg)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return FAIL, f"抓到的不是 JSON：{text[:160]}"
    if not isinstance(data, dict):
        return FAIL, f"抓到的不是对象：{type(data).__name__}"
    missing = [k for k in FIRED_KEYS if k not in data]
    if missing:
        return FAIL, f"缺契约 §1.2 事件行的键：{', '.join(missing)}（实际键 {sorted(data)}）"
    if getattr(msg, "retain", False):
        return FAIL, (f"`{FIRED_TOPIC}` 以 retained 送达（retain=True）——契约 §1.2 规定事件类永不 "
                      f"retained，否则新订阅者会收到几天前的『有人回家』")
    ts = _parse_ts(str(data["ts"]))
    if ts is None:
        return FAIL, f"`ts` 读不出带偏移的时刻：{data['ts']!r}（裸 UTC/无偏移判不出家庭墙钟）"
    skew = abs((datetime.now(timezone.utc) - ts).total_seconds())
    if skew > args.max_skew:
        return FAIL, (f"`ts`={data['ts']} 与当下差 {skew:.0f} 秒，超过 {args.max_skew} 秒 ⇒ "
                      f"不是家庭墙钟口径（仿真锚点/错时区就红在这里）")
    return PASS, (f"抓到一条 fired，键 {sorted(data)}，`ts`={data['ts']} 与当下差 {skew:.0f} 秒"
                  f"（≤{args.max_skew}），retain={bool(getattr(msg, 'retain', False))}")


def item_status_retained(args) -> tuple[str, str]:
    """④ `adm/autoforge/status` 的 retained 快照状态为 online——用 `msg.retain` 判，不用两次订阅猜。

    载荷解码走 `homesdk.adm.status.decode_status`（0.3.2 §2.2）：它同时认 status JSON 与
    legacy 裸字面量。本脚本原来写的是 `value.lower() != "online"`——0.3.2 把 presence 的
    retained 载荷换成 JSON 文档之后，那条字面量比较会把一个**健康的在线 AF** 判成 FAIL，
    这正是"各仓手写 status schema"要根治的病，所以这里不留第二份判断。
    """
    try:
        msg = _collect(STATUS_TOPIC, args.wait)
    except MqttEnvMissing as exc:
        return NA, str(exc)
    except OSError as exc:
        return FAIL, f"连 broker 失败：{type(exc).__name__}: {exc}"
    if msg is None:
        return FAIL, f"{args.wait} 秒内没拿到 `{STATUS_TOPIC}`（已连上 broker）⇒ 桥没上线或没发状态"
    value = _msg_text(msg).strip()
    if not getattr(msg, "retain", False):
        return FAIL, (f"拿到的 `{STATUS_TOPIC}` 不是 retained 快照（retain=False），值为 {value[:80]!r}"
                      f"——对端探测在线靠的是 retained，现发一条不算")
    try:
        from homesdk.adm.status import STATE_ONLINE, decode_status
    except ImportError as exc:
        raise MqttEnvMissing(f"机制层不可用（{exc}）⇒ status 解码只能在有 homesdk 的机器上取数")
    try:
        st = decode_status(value)
    except ValueError as exc:
        return FAIL, f"retained 载荷既不是 status JSON 也不是 legacy 字面量：{exc}｜{value[:160]!r}"
    if st.get("state") != STATE_ONLINE:
        return FAIL, f"retained 状态不是 `{STATE_ONLINE}`：{st}"
    return PASS, f"retained=True 且状态读数为 {st!r}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ADM 停机窗的窗后四项验收（缺项不算绿）")
    ap.add_argument("--base", default="http://127.0.0.1:8787", help="AF HTTP 基址（compose 映射 8787）")
    ap.add_argument("--service", default="autoforge", help="`docker ps` 的过滤名")
    ap.add_argument("--wait", type=int, default=60, help="抓事件/状态的等待秒数")
    ap.add_argument("--max-skew", type=int, default=900, help="事件 ts 与当下的最大偏差秒数")
    ap.add_argument("--http-timeout", type=int, default=10)
    ap.set_defaults(container_up=False)   # 单独调用 item_health 时也有确定语义
    args = ap.parse_args(argv[1:])

    compose = item_compose(args)
    args.container_up = compose[0] == PASS      # ② 的"连不上"算红还是算无从判定，取决于①
    results = [("① compose ps 服务在", compose),
               ("② /health 返回 200", item_health(args)),
               (f"③ 抓到一条 {FIRED_TOPIC} 且 ts 是家庭墙钟", item_fired(args)),
               (f"④ {STATUS_TOPIC} retained 状态为 online", item_status_retained(args))]

    verdicts = {v for _, (v, _) in results}
    print("══ ADM 窗后验收四项 ══")
    for name, (verdict, detail) in results:
        print(f"  [{verdict}] {name}：{detail}")
    counts = {v: sum(1 for _, (x, _) in results if x == v) for v in (PASS, FAIL, NA)}
    print(f"读数：PASS {counts[PASS]} / FAIL {counts[FAIL]} / UNAVAILABLE {counts[NA]}（共 {len(results)} 项）")
    if FAIL in verdicts:
        print("结论：窗后验收**未通过**（有 FAIL 项）——按裁定的回滚顺序逐项退，不要把部分绿当完成。")
        return 1
    if NA in verdicts:
        print("结论：窗后验收**不算完成**（EXEMPT ≠ VERIFIED）：有环境缺项，缺的那几项必须在那台机器上"
              "补齐读数后再判。这一行不是绿色通过。")
        return 2
    print("结论：窗后验收四项全 PASS ⇒ 该步可记账（逐项读数见上）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

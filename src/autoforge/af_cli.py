"""`forge` CLI —— build / run / sim 三个子命令。

两道闸串行、**不可调换**（KICKOFF §2-4）：
    `forge build` 验**安全** → `forge sim` 验**逻辑**
`sim` / `run` 内部会先跑 `build`，安全闸不过就**根本不进仿真**。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import typer

from .af_adapters import DEFAULT_HA_URL, HAAdapter, HAStateProvider, HATransport
from .af_conf import ConfidenceStore, AUTO_MIN, SHADOW_LOW
from .af_ir import IRValidationError, load_graph
from .af_nl import render_graph
from .af_runtime import Runtime, build_runtime
from .af_scanner import DeviceGuardRegistry, StaticScanner, live_preflight
from .af_time import SystemTimeSource
from .af_spec import SpecError, compile_spec, graph_to_raw, render_spec
from .af_store import DEFAULT_STORE_ROOT, GraphStore, diff_graphs
from .af_vhass import FakeHAAdapter, seed_from_graph
from .af_metrics import DEFAULT_BUFFER_DIR, Ingester, MetricsAggregator
from .af_config import get_config
from .af_error_knowledge import ErrorKnowledge
from .af_experience import ExperienceStore
from .af_telemetry import TelemetryStore

app = typer.Typer(no_args_is_help=True, help="AutoForge —— Agent 为中心的智能家居自动化平台")

EXIT_OK = 0
EXIT_SCAN_ERROR = 1
EXIT_IR_ERROR = 2


def _load(path: Path):
    try:
        return load_graph(path)
    except IRValidationError as exc:
        typer.echo(f"[IR 校验失败] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)
    except FileNotFoundError:
        typer.echo(f"[找不到文件] {path}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)


def _load_json_mapping(path: str | None) -> dict[str, str]:
    """加载 `{"entity_id": "..."}` 形态的 JSON（实体清单或 ACL 表）。"""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    # 也接受数组形态的实体清单
    return {str(x): "rw" for x in data}


def _known_entities(path: str | None) -> set[str] | None:
    """加载实体白名单（数组或对象），无路径返回 None。"""
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k) for k in data}
    return {str(x) for x in data}


def _gate(
    graph,
    show_nl: bool = True,
    entities_path: str | None = None,
    acl_path: str | None = None,
    guard_path: str | None = None,
    store_root: str = DEFAULT_STORE_ROOT,
) -> bool:
    """第一道闸：静态扫描 + NL 渲染（含覆盖率）。返回是否通过。"""
    known = None
    if entities_path:
        data = json.loads(Path(entities_path).read_text(encoding="utf-8"))
        known = set(data) if isinstance(data, list) else set(data)
    # v1.7.4：CLI build 也加载 entity_health，否则 TRIGGER_STALE 闸在 CLI 模式不生效
    try:
        from .af_catalog import DeviceCatalog
        entity_health = DeviceCatalog(store_root).health_map()
    except Exception:
        import logging
        logging.getLogger("autoforge.cli").warning(
            "device_health load failed, TRIGGER_STALE gate degraded", exc_info=True
        )
        entity_health = None

    # P0-8：CLI 也加载 device_guard（从 store 的 device_acl.json），让 Tier-0 规则在 CLI 模式生效
    # 显式 --guard 优先；否则尝试从 store_root 加载；都没有则用旧 entity_acl 兜底
    device_guard = None
    if guard_path:
        device_guard = DeviceGuardRegistry.from_file(guard_path)
    else:
        try:
            from .af_service import load_device_guard
            from .af_store import GraphStore
            device_guard = load_device_guard(GraphStore(store_root))
        except Exception:
            import logging
            logging.getLogger("autoforge.cli").warning(
                "device_guard load failed, gate running WITHOUT Tier-0 device protection", exc_info=True
            )
            device_guard = None

    if device_guard is not None:
        scanner = StaticScanner(graph, known_entities=known, device_guard=device_guard, entity_health=entity_health)
    else:
        scanner = StaticScanner(graph, known_entities=known, entity_acl=_load_json_mapping(acl_path) or None, entity_health=entity_health)
    result = scanner.scan()
    typer.echo("── 安全闸（forge build）──")
    typer.echo(result.render())
    if show_nl:
        typer.echo("\n── 自然语言（确定性渲染，『看到即跑的』）──")
        typer.echo(render_graph(graph).text)
    return result.ok


def _load_binding(path: Path, bind: bool, root: str):
    """v1.6.0（决策 3）：`--bind` 时先把「设备描述占位符」回填为真实 entity_id 再校验。

    **可选**：不启用 → 行为与 v1.1.0 完全一致（要求精确 entity_id），
    保住「离线编写 IR」与 G7 AF-Spec 零有损往返契约。
    fail-closed：歧义 / 无候选 → 保留占位符，交由安全闸（未知实体）拒编译。
    """
    if not bind:
        return _load(path)
    from . import af_service as svc

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    result = svc.bind_ir(GraphStore(root), payload)
    for item in result["bound"]:
        typer.echo(f"· 已绑定 {item['from']} → {item['to']}（{item['matched_by']}）")
    for item in result["unresolved"]:
        typer.echo(f"⚠️ 未绑定 {item['from']}：{item['reason']}", err=True)
    if not result["bound"] and not result["unresolved"]:
        typer.echo("（IR 无设备描述占位符，binding 无需处理）")
    try:
        return load_graph(result["ir"])
    except IRValidationError as exc:
        typer.echo(f"[IR 校验失败] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)


@app.command()
def build(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    nl: bool = typer.Option(True, "--nl/--no-nl", help="是否渲染自然语言"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出诊断"),
    entities: str = typer.Option("", "--entities", help="已知实体清单 JSON（数组或对象），启用实体存在性校验"),
    acl: str = typer.Option("", "--acl", help="实体读写权限表 JSON：{entity_id: 'rw'|'r'|'-'}"),
    guard: str = typer.Option("", "--guard", help="设备保护规则 JSON（device_acl.json：规则数组 / {entity_id: perm}）"),
    bind: bool = typer.Option(
        False, "--bind",
        help="v1.6.0（可选）：把 IR 里的设备描述占位符（?书房吊灯 / ?light:书房吊灯）回填为真实 entity_id",
    ),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="binding 用的 store 根（设备目录缓存）"),
):
    """编译期安全闸：Schema 校验 + 静态扫描 + 自然语言渲染。"""
    graph = _load_binding(path, bind, root)
    if json_out:
        known = None
        if entities:
            data = json.loads(Path(entities).read_text(encoding="utf-8"))
            known = set(data) if isinstance(data, list) else set(data)
        scanner = (
            StaticScanner(graph, known_entities=known, device_guard=DeviceGuardRegistry.from_file(guard))
            if guard
            else StaticScanner(graph, known_entities=known, entity_acl=_load_json_mapping(acl) or None)
        )
        result = scanner.scan()
        typer.echo(
            json.dumps(
                {
                    "ok": result.ok,
                    "errors": [d.__dict__ for d in result.errors],
                    "warnings": [d.__dict__ for d in result.warnings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise typer.Exit(code=EXIT_OK if result.ok else EXIT_SCAN_ERROR)
    ok = _gate(graph, show_nl=nl, entities_path=entities or None, acl_path=acl or None, guard_path=guard or None)
    raise typer.Exit(code=EXIT_OK if ok else EXIT_SCAN_ERROR)


@app.command()
def conf(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    decay_hours: float = typer.Option(0.0, "--decay-hours", help="模拟经过的小时数（触发置信度衰减）"),
    intervene: str = typer.Option("", "--intervene", help="模拟人工干预（负样本）的 automation id"),
):
    """G4 置信度分级自主：展示每个自动化的置信度、衰减后与自主级别。"""
    graph = _load(path)
    store = ConfidenceStore().seed(graph)
    if decay_hours:
        store.decay_all(decay_hours)
    if intervene:
        store.record_negative(intervene)

    typer.echo("── 置信度分级自主（G4）──")
    typer.echo(f"阈值：auto ≥ {AUTO_MIN}｜shadow ≥ {SHADOW_LOW}｜ask < {SHADOW_LOW}")
    for auto in graph:
        conf = store.get(auto.id)
        band = store.band(auto.id)
        typer.echo(f"· {auto.id}: confidence={conf:.3f} → [{band}]")
    if not list(graph):
        typer.echo("（空图）")


def _mirror_state(runtime: Runtime, entity_id: str, state: Any) -> None:
    """把回放事件的新状态同步进**仿真状态源**（只读状态源自动跳过）。

    为什么必须做：`for` 到期复查（`Scheduler._still_holds`）与实例内后续 `if`
    都从状态源读**当前值**。只发事件而不改状态，`for` 复查永远读到旧值 →
    「离家 10 分钟后」这类持续条件永不成立（2026-09-17 NL 实测 #4 实例从不触发）。
    vhass 驾驶台的 `emit()` 就是"先改状态再发事件"，CLI 这条链路此前漏了这一半。
    """
    provider = getattr(runtime, "states", None)
    setter = getattr(provider, "set", None) or getattr(provider, "set_state", None)
    if not callable(setter):
        return  # 真机/只读状态源：不回写（仿真不得改真实世界）
    try:
        setter(entity_id, str(state))
    except Exception:  # pragma: no cover - 状态源不接受写入时不影响事件回放
        return


def _replay(runtime: Runtime, events: list[dict[str, Any]]) -> None:
    """回放事件序列：`{"entity_id","state","advance_s"?}`。

    v1.7.1：事件词汇与 IR 触发对齐——
    - `to` 可代替 `state`（新状态）；
    - `from` 会作为 `old_state` 注入事件 payload（`trigger.from` 的校验需要它）。
    - `{"advance_s": 600}`（无 `entity_id`）表示"时间前进 600 秒并 tick"，用于驱动 `for` / `wait`。

    为什么必须对齐：`Scheduler._satisfied` 只比对 `event.state` 与 `trigger.to`。
    若调用方按触发词汇写 `"to": "on"`，而这里只认 `"state"`，则事件状态恒为空串，
    `!= trigger.to` 永远成立 → **实例从未触发**，仿真"跑过了"其实是"空跑"
    （2026-09-17 NL 实测：8 条里 6 条是空跑，报告却记为「实例到 done」）。
    """
    for item in events:
        advance = item.get("advance_s")
        if advance:
            runtime.advance(float(advance))
        if "entity_id" in item:
            payload = {
                k: v
                for k, v in item.items()
                if k not in {"entity_id", "state", "to", "from", "last_changed", "advance_s"}
            }
            if "from" in item and "old_state" not in payload:
                payload["old_state"] = item["from"]
            new_state = str(item.get("state", item.get("to", "")))
            _mirror_state(runtime, item["entity_id"], new_state)
            runtime.emit(
                item["entity_id"],
                new_state,
                last_changed=item.get("last_changed"),
                **payload,
            )
        elif advance is None:
            runtime.tick()


def _make_runtime(
    graph,
    seed_path: str | None,
    vhass: str,
    *,
    live: bool = False,
    dry_live: bool = False,
    ha_url: str = DEFAULT_HA_URL,
    ha_token: str = "",
    persist_dir: str | None = None,
    cfg=None,
    undo: bool = False,
    store_root: str = DEFAULT_STORE_ROOT,
):
    if live or dry_live:
        # live：真时钟 + 真 HA 状态 + 真下发（dry_run=False）
        # dry_live：真时钟 + 真 HA 状态，但 do 只记意图不真下发——抓 live 代码路径的 bug，不动设备
        mode_label = "dry-live（真时钟/真状态，do 只记意图不下发）" if dry_live else "真机接线（dry_run=False，真实下发意图）"
        typer.echo(f"· {mode_label}：HA {ha_url}")
        runtime = build_runtime(graph, persist_dir=persist_dir, clock=SystemTimeSource())
        provider = HAStateProvider(base_url=ha_url, token=ha_token, cfg=cfg)
        transport = HATransport(base_url=ha_url, token=ha_token, cfg=cfg)
        runtime.states = provider
        runtime.instances.states = provider
        runtime.scheduler.states = provider
        runtime.executor.states = provider
        adapter = HAAdapter(transport=transport, dry_run=dry_live)
        # F7：真实下发前快照捕获（仅真机 live 生效；dry_live 不触发 recorder）
        undo_deploy_id: str | None = None
        if undo:
            from .af_undo import UndoStore, deploy_id as _new_deploy_id

            _store = UndoStore(store_root)
            undo_deploy_id = _new_deploy_id()

            def _recorder(action, params, pre, _s=_store, _d=undo_deploy_id):
                _s.record_merge(_d, pre)

            adapter.undo_recorder = _recorder
        if dry_live:
            def _on_dry(action, params):
                logging.getLogger(__name__).debug("[DRY-LIVE 意图] %s %s", action, params)
            adapter.on_dry_run = _on_dry
        runtime.adapters.register(adapter)
        return runtime, undo_deploy_id

    seed: dict[str, str] = {}
    if seed_path:
        seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))

    if vhass == "ha":
        # 真 vhass 依赖 pytest-homeassistant 的异步 `hass` 夹具，CLI 这条**同步**链路用不了；
        # 需要在 pytest 里用 VhassHarness（见 tests/acceptance/test_vhass_native.py）。
        try:
            import homeassistant  # noqa: F401
            from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: F401

            typer.echo("· 检测到 HA 环境，但 CLI 为同步链路，仍使用 FakeHA；"
                       "真 vhass 请在 pytest 中用 af_vhass.VhassHarness")
        except Exception as exc:
            typer.echo(f"· 未检测到可用 HA 环境（{exc}），使用 FakeHA")

    typer.echo("· 仿真底座：FakeHA（内置降级实现）")
    runtime = build_runtime(graph, persist_dir=persist_dir)
    ha = seed_from_graph(graph, seed, clock=runtime.clock)
    runtime.states = ha
    runtime.instances.states = ha
    runtime.scheduler.states = ha
    runtime.executor.states = ha
    # ⚠️ v1.7.1 关键修复：fake 链路必须注册 **FakeHAAdapter**。
    # 原实现只用 `build_runtime` 默认的 `HAAdapter(dry_run=True)`——它只记录下发**意图**，
    # 不翻转任何状态。后果：`do` 看似执行成功，实体状态原地不动，
    # `expect` 断言必然 fail/unverified，「跑完了」永远变不成「跑对了吗」。
    # 更隐蔽的是：未建模服务的登记（`unmodeled`）也只发生在 FakeHAAdapter 里，
    # 用 dry_run 适配器时**连「这个动作没法验证」都无从得知**
    # （2026-09-17 NL 实测 #7 的 `media_pause` 边界即此）。
    runtime.adapters.register(FakeHAAdapter(ha))
    return runtime, None


def _unmodeled_actions(runtime: Runtime) -> list[str]:
    """汇总仿真底座未建模的动作（诚实性：这些动作的后果没被验证过）。"""
    out: list[str] = []
    for adapter in runtime.adapters.values():
        out.extend(str(x) for x in (getattr(adapter, "unmodeled", ()) or ()))
    return sorted(set(out))


def _print_expects(graph, runtime: Runtime) -> bool:
    """渲染 `expect` 断言报告——**「跑对了吗」的答案**（v1.2.0 断言闭环在 CLI 的出口）。

    返回是否**全部验证通过**（`fully_verified`）。三态必须分开呈现：
    `pass`/`fail`/`unverified`——把「没验到」当「验过了」就是自欺。
    """
    from .af_expect import evaluate_graph_expects

    var_sources = [inst.ctx.vars for inst in runtime.instances.all()]
    report = evaluate_graph_expects(
        graph, runtime.states, var_sources, unmodeled_actions=_unmodeled_actions(runtime)
    )
    unmodeled = report.get("unmodeled_actions") or []
    if unmodeled:
        typer.echo(f"· 未建模动作（后果无法验证）：{'、'.join(unmodeled)}")
    if not report["declared"]:
        typer.echo("· 后置条件：未声明 expect → 本次只回答了「跑完了吗」，没回答「跑对了吗」")
        return True  # P1-13: no expect = pass (exit 0)
    typer.echo(
        f"· 后置条件（expect）：声明 {report['declared']} 条｜"
        f"通过 {report['passed']}｜失败 {report['failed']}｜未验证 {report['unverified']}"
    )
    for auto_id, per_auto in (report.get("automations") or {}).items():
        for item in per_auto.get("items", ()):
            status = str(item.get("status", "?"))
            if status == "pass":
                mark = "✓ pass"
            elif status == "fail":
                mark = "✗ fail"
            else:
                mark = "? unverified"
            target = item.get("target", "")
            if item.get("kind") == "entity":
                detail = f"期望状态 {item.get('expected')!r}，实际 {item.get('actual')!r}"
            elif item.get("kind") == "entity_attribute":
                detail = (
                    f"期望属性 {item.get('op')} {item.get('expected')!r}，"
                    f"实际 {item.get('actual')!r}"
                )
            else:
                detail = f"{item.get('op')} {item.get('expected')!r}，实际 {item.get('actual')!r}"
            line = f"    - [{mark}] {auto_id} · {target}：{detail}"
            reason = str(item.get("reason") or "")
            if reason:
                line += f"（{reason}）"
            typer.echo(line)
    if report["fully_verified"]:
        typer.echo("· 判定：fully_verified —— 声明过的断言**全部验过**")
    elif report["failed"]:
        typer.echo("· 判定：断言失败 —— 自动化没跑对（详见上面 ✗ 行）")
    else:
        typer.echo("· 判定：未能全部验证（有断言没验到）——不等价于通过")
    return bool(report["fully_verified"])


@app.command()
def sim(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    seed: str = typer.Option(
        "",
        "--seed",
        help='初始状态 JSON：{"entity": "state"} 或 {"entity": {"state": "...", "attributes": {...}}}',
    ),
    events: str = typer.Option("", "--events", help="事件序列 JSON 文件路径"),
    vhass: str = typer.Option("ha", "--vhass", help="仿真底座：ha（pytest-homeassistant）| fake"),
    entities: str = typer.Option("", "--entities", help="已知实体清单 JSON"),
    acl: str = typer.Option("", "--acl", help="实体读写权限表 JSON"),
    live: bool = typer.Option(False, "--live", help="（sim 为仿真链路，忽略；真机请用 forge run --live）"),
):
    """逻辑闸：在仿真里跑一遍（**先过安全闸**）。"""
    graph = _load(path)
    if not _gate(graph, show_nl=False, entities_path=entities or None, acl_path=acl or None):
        typer.echo("\n安全闸未通过，拒绝进入仿真。", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)

    if live:
        typer.echo("· 注意：sim 是仿真链路，--live 被忽略；真机下发请用 `forge run --live`。")

    runtime, _ = _make_runtime(graph, seed or None, vhass)
    typer.echo("\n── 逻辑闸（forge sim）──")
    if events:
        _replay(runtime, json.loads(Path(events).read_text(encoding="utf-8")))
    else:
        runtime.tick()
    _print_stats(runtime)
    expect_ok = _print_expects(graph, runtime)
    # P1-13 修复：expect 断言失败时非 0 退出，外部编排可依赖返回码
    if not expect_ok:
        raise typer.Exit(code=EXIT_SCAN_ERROR)


@app.command()
def run(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    seed: str = typer.Option(
        "",
        "--seed",
        help='初始状态 JSON：{"entity": "state"} 或 {"entity": {"state": "...", "attributes": {...}}}',
    ),
    events: str = typer.Option("", "--events", help="事件序列 JSON 文件路径"),
    vhass: str = typer.Option("fake", "--vhass", help="状态源：fake | ha"),
    entities: str = typer.Option("", "--entities", help="已知实体清单 JSON"),
    acl: str = typer.Option("", "--acl", help="实体读写权限表 JSON"),
    live: bool = typer.Option(False, "--live", help="真机下发（真实操作 HA，需 --confirm + 令牌 + 白名单）"),
    dry_live: bool = typer.Option(False, "--dry-live", help="真时钟/真 HA 状态，但 do 只记意图不下发（抓 live 代码路径 bug，不动设备）"),
    ha_url: str = typer.Option(DEFAULT_HA_URL, "--ha-url", help="HA 地址（--live/--dry-live 时生效）"),
    ha_token: str = typer.Option("", "--ha-token", help="HA 长期访问令牌（缺省读环境变量 AUTOFORGE_HA_TOKEN）"),
    confirm: bool = typer.Option(False, "--confirm", help="真机下发二次确认（--live 必需）"),
    live_allow: str = typer.Option(
        "",
        "--live-allow",
        help="真机可写实体白名单（逗号分隔）；缺省复用 --entities，先不开放全量",
    ),
    undo: bool = typer.Option(
        False, "--undo",
        help="F7：记录动作前快照，允许事后 `forge undo <id>` 回滚本次部署（仅 --live 真机下发生效）",
    ),
    persist_dir: str = typer.Option(
        "", "--persist-dir", help="实例持久化目录（P1：重启后恢复 persist=true 的活跃实例）"
    ),
):
    """内存态 Runtime（回放事件后退出）；`--live` 时真实下发到 HA。"""
    graph = _load(path)
    if not _gate(graph, show_nl=True, entities_path=entities or None, acl_path=acl or None):
        raise typer.Exit(code=EXIT_SCAN_ERROR)

    undo_enabled = undo or os.getenv("AUTOFORGE_UNDO", "") == "1"
    if live or dry_live:
        token = ha_token or os.environ.get("AUTOFORGE_HA_TOKEN", "")
        allow = (
            {x.strip() for x in live_allow.split(",") if x.strip()}
            if live_allow
            else _known_entities(entities or None)
        )
        pre = live_preflight(
            graph,
            known_entities=allow,
            token=token,
            confirm=confirm or dry_live,  # dry-live 不动设备，预检 confirm 检查自动通过
            dry_live=dry_live,
        )
        typer.echo("\n── 真机预检（forge run {}）──".format("--live" if live else "--dry-live"))
        typer.echo(pre.render())
        if not pre.ok:
            typer.echo("真机预检未通过，拒绝启动。", err=True)
            raise typer.Exit(code=EXIT_SCAN_ERROR)
        runtime, undo_id = _make_runtime(
            graph, None, vhass, live=live, dry_live=dry_live,
            ha_url=ha_url, ha_token=token,
            persist_dir=persist_dir or None, undo=undo_enabled,
            store_root=persist_dir or DEFAULT_STORE_ROOT,
        )
    else:
        runtime, undo_id = _make_runtime(graph, seed or None, vhass, persist_dir=persist_dir or None)
    if runtime.persist is not None and runtime.restored:
        typer.echo(f"· 已从 {persist_dir} 恢复 {len(runtime.restored)} 个活跃实例")
    if undo_id:
        typer.echo(f"· 撤销 ID（如需回滚本次部署：forge undo {undo_id}）：{undo_id}")
    typer.echo("\n── Runtime（forge run）──")
    if events:
        _replay(runtime, json.loads(Path(events).read_text(encoding="utf-8")))
    else:
        runtime.tick()
    _print_stats(runtime)
    _print_expects(graph, runtime)


@app.command()
def watch(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    entities: str = typer.Option("", "--entities", help="已知实体清单 JSON"),
    acl: str = typer.Option("", "--acl", help="实体读写权限表 JSON"),
    ha_url: str = typer.Option(DEFAULT_HA_URL, "--ha-url", help="HA 地址（--live/--dry-live 时生效）"),
    ha_token: str = typer.Option("", "--ha-token", help="HA 长期访问令牌（缺省读环境变量 AUTOFORGE_HA_TOKEN）"),
    confirm: bool = typer.Option(False, "--confirm", help="真机下发二次确认（常驻监听必需；--dry-live 不需要）"),
    dry_live: bool = typer.Option(False, "--dry-live", help="真时钟/真 HA 状态常驻，do 只记意图不下发（不动设备）"),
    live_allow: str = typer.Option("", "--live-allow", help="真机可写实体白名单（逗号分隔）"),
    undo: bool = typer.Option(
        False, "--undo",
        help="F7：记录动作前快照，允许事后 `forge undo <id>` 回滚本监听会话的部署（仅真机下发生效）",
    ),
    tick_s: float = typer.Option(1.0, "--tick-s", help="计时器/超时巡检间隔（秒）"),
    persist_dir: str = typer.Option(
        "", "--persist-dir", help="实例持久化目录（P1：重启后恢复 persist=true 的活跃实例）"
    ),
):
    """真机常驻监听：订阅 HA 事件流实时驱动自动化（需 --confirm + 令牌 + 白名单）。

    把 `forge run --live` 的一次性回放升级为常驻运行：持续订阅 HA SSE
    `/api/stream`，每个 `state_changed` 实时进入 Runtime 评估；后台线程按
    `--tick-s` 周期触发 `runtime.tick()` 驱动 `for`/`wait` 计时。Ctrl+C 优雅退出。

    传入 `--persist-dir` 后进程重启即恢复 `persist=true` 的活跃实例（含崩溃期间
    已错过的 `on_timeout`），把"常驻"升级为"崩溃可续跑"。
    """
    graph = _load(path)
    if not _gate(graph, show_nl=True, entities_path=entities or None, acl_path=acl or None):
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    if not confirm and not dry_live:
        typer.echo("真机常驻监听会**持续**真实操作 HA，必须显式 --confirm，拒绝启动。", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)

    cfg = get_config(persist_dir or DEFAULT_STORE_ROOT)
    token = ha_token or cfg.get_ha_token() or os.environ.get("AUTOFORGE_HA_TOKEN", "")
    allow = (
        {x.strip() for x in live_allow.split(",") if x.strip()}
        if live_allow
        else _known_entities(entities or None)
    )
    pre = live_preflight(graph, known_entities=allow, token=token, confirm=confirm or dry_live, dry_live=dry_live)
    typer.echo("\n── 真机预检（forge watch）──")
    typer.echo(pre.render())
    if not pre.ok:
        typer.echo("真机预检未通过，拒绝常驻监听。", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)

    undo_enabled = undo or os.getenv("AUTOFORGE_UNDO", "") == "1"
    runtime, undo_id = _make_runtime(
        graph, None, "fake", live=not dry_live, dry_live=dry_live,
        ha_url=ha_url, ha_token=token,
        persist_dir=persist_dir or None, cfg=cfg, undo=undo_enabled,
        store_root=persist_dir or DEFAULT_STORE_ROOT,
    )
    if runtime.persist is not None and runtime.restored:
        typer.echo(f"· 已从 {persist_dir} 恢复 {len(runtime.restored)} 个活跃实例")
    if undo_id:
        typer.echo(f"· 撤销 ID（如需回滚本会话部署：forge undo {undo_id}）：{undo_id}")
    from . import af_live
    from .af_flock import owner_id

    # v0.9.0 多实例协调：同一 store/persist 目录只允许一个活跃 watcher
    coord = af_live.WatchCoordinator(
        Path(persist_dir) / "watch.lock" if persist_dir else Path(DEFAULT_STORE_ROOT) / "watch.lock",
        holder_info={"graph": str(path), "ha_url": ha_url},
    )
    if not coord.try_acquire():
        holder = coord.holder
        typer.echo(
            "另一 watcher 正在监听（协调锁被持有），拒绝启动第二个实供建监听：\n"
            f"  持有者：{holder.get('watcher', '?')} @ {holder.get('acquired_at', '?')}\n"
            f"  图：{holder.get('graph', '?')}　HA：{holder.get('ha_url', '?')}\n"
            "若持有进程已死，锁会随进程退出自动释放，稍后重试即可。",
            err=True,
        )
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    typer.echo(f"· watcher 协调锁：{coord.info['watcher']}（唯一活跃实例）")

    stream = af_live.HAEventStream(base_url=ha_url, token=token, cfg=cfg)
    stop = threading.Event()
    typer.echo(f"\n── 常驻监听（forge watch）── 订阅 {ha_url}{af_live.SSE_STREAM_PATH}，Ctrl+C 退出")
    ticker = af_live.start_ticker(runtime, max(tick_s, 0.1), stop, sidecar_dir=persist_dir or None)

    def _on(ev, _rt):
        typer.echo(f"  · {ev.entity_id} = {ev.state}")

    try:
        result = af_live.run_watch(runtime, stream.events(), tick_each=0, on_event=_on, stop=stop)
    except KeyboardInterrupt:
        stop.set()
        result = {"published": -1, "stopped": True}
    finally:
        stop.set()
        ticker.join(timeout=2.0)
        coord.release()
    typer.echo(f"· 常驻监听结束，累计处理事件：{result.get('published')}")
    _print_stats(runtime)


@app.command()
def diff(
    old: Path = typer.Argument(..., help="旧 IR 文件（.json）"),
    new: Path = typer.Argument(..., help="新 IR 文件（.json）"),
):
    """G6 版本 diff：对比两份 IR 的节点/边/参数/元信息级差异。"""
    old_graph = _load(old)
    new_graph = _load(new)
    result = diff_graphs(old_graph, new_graph)
    typer.echo(f"── 版本 diff（{old.name} → {new.name}）──")
    typer.echo(result.render())


@app.command()
def undo(
    deploy_id: str = typer.Argument(..., help="forge run --live --undo 打印的撤销 ID"),
    confirm: bool = typer.Option(False, "--confirm", help="风险域（climate/cover/lock 等）二次确认"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="UndoStore 根目录（与部署时一致）"),
    ha_url: str = typer.Option(DEFAULT_HA_URL, "--ha-url", help="HA 地址（撤销真实下发到此）"),
    ha_token: str = typer.Option("", "--ha-token", help="HA 长期访问令牌（缺省读 AUTOFORGE_HA_TOKEN）"),
):
    """回滚一次部署的设备态（F7，决策 E）。

    fail-closed 三道闸：① 未知 deploy_id 拒绝；② 超过时间窗（默认 60s，
    AUTOFORGE_UNDO_WINDOW_S）拒绝，需重新走审批；③ 含风险域（climate/cover/lock）
    且未 --confirm 拒绝；④ 不可映射域跳过告警不阻断其余。
    """
    from .af_undo import UndoStore

    store = UndoStore(root)
    if not store.exists(deploy_id):
        typer.echo(f"[撤销失败] 未知 deploy_id：{deploy_id}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)
    token = ha_token or os.environ.get("AUTOFORGE_HA_TOKEN", "")

    # 惰性适配器：仅在真正需要下发（revert 通过校验）时才构造 HATransport，
    # 这样 unknown/expired/risk 被拒的场景不要求 HA_URL 就绪。
    class _LazyHAAdapter:
        def call(self, action, params):
            from .af_adapters import HAAdapter, HATransport

            if not ha_url:
                raise RuntimeError(
                    "HA_URL 未配置：撤销需真实下发，请设置 AUTOFORGE_HA_URL 或 --ha-url"
                )
            adapter = HAAdapter(
                transport=HATransport(base_url=ha_url, token=token), dry_run=False
            )
            return adapter.call(action, params)

    result = store.revert(deploy_id, _LazyHAAdapter(), confirm=confirm)
    if not result.get("ok"):
        typer.echo(
            f"[撤销被拒] {result.get('reason')}：{result.get('message', '')}", err=True
        )
        raise typer.Exit(code=EXIT_IR_ERROR)
    typer.echo(f"· 已恢复 {len(result['restored'])} 个实体：{result['restored']}")
    if result.get("skipped"):
        typer.echo(f"· 跳过（无法映射，fail-closed）：{result['skipped']}")


store_app = typer.Typer(no_args_is_help=True, help="G6 版本化存储（Graph 快照 + 置信度）")
app.add_typer(store_app, name="store")


# ── v1.4.0 治理面：待批队列（部署前写操作先入队，人审后回放落盘）──
pending_app = typer.Typer(no_args_is_help=True, help="待人工审批的写操作队列")
app.add_typer(pending_app, name="pending")


# ── v1.4.0 治理面：凭据热重载（connection_revision 代数，免重启）──
credentials_app = typer.Typer(no_args_is_help=True, help="HA / API 凭据管理（原子写 + 掩码）")
app.add_typer(credentials_app, name="credentials")


@pending_app.command("list")
def pending_list(root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录")):
    """列出待人工审批的写操作。"""
    from .af_pending import PendingStore

    items = PendingStore(root).list()
    if not items:
        typer.echo("（无待批操作）")
        return
    for it in items:
        typer.echo(f"· {it['op_id']} [{it['tool']}] by={it['submitted_by']}")
        typer.echo(f"    {it['summary']}")
        typer.echo(f"    blast_radius={it['blast_radius']}")


@pending_app.command("approve")
def pending_approve(
    op_id: str = typer.Argument(..., help="待批操作 ID"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """批准并回放落盘（只在 CLI / 服务层，MCP 面不注册）。"""
    from . import af_service as svc

    result = svc.approve_pending(GraphStore(root), op_id, reviewer="cli")
    typer.echo(f"· 已批准 {op_id}：{result.get('name', '')} v{result.get('version', '?')}")


@pending_app.command("reject")
def pending_reject(
    op_id: str = typer.Argument(..., help="待批操作 ID"),
    reason: str = typer.Option("", "--reason", help="拒绝理由"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """拒绝并丢弃待批操作（不落盘）。"""
    from . import af_service as svc

    result = svc.reject_pending(GraphStore(root), op_id, reason=reason)
    typer.echo(f"· 已拒绝 {op_id}：{result}")


@credentials_app.command("show")
def credentials_show(root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录")):
    """显示凭据掩码 + 连接代数（绝不露明文 / 末 4 位）。"""
    info = get_config(root).describe()
    typer.echo(
        f"· ha_token={info['ha_token']}　api_token={info['api_token']}　"
        f"revision={info['connection_revision']}"
    )


@credentials_app.command("update")
def credentials_update(
    ha_token: str = typer.Option("", "--ha-token", help="新的 HA 长期访问令牌"),
    api_token: str = typer.Option("", "--api-token", help="新的 API 令牌"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """原子更新凭据 + 自增连接代数（改令牌免重启 watcher）。"""
    cfg = get_config(root)
    cfg.update_credentials(ha_token=ha_token or None, api_token=api_token or None)
    typer.echo(f"· 凭据已更新，connection_revision={cfg.connection_revision}")


@store_app.command("save")
def store_save(
    path: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    name: str = typer.Option("", "--name", help="归档名（缺省用文件名）"),
    note: str = typer.Option("", "--note", help="本次保存备注"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """保存一个 Graph 版本 + 置信度快照。"""
    graph = _load(path)
    store = GraphStore(root)
    key = name or path.stem
    version = store.save(graph, key, note)
    store.save_conf(ConfidenceStore().seed(graph), key, note)
    typer.echo(f"· 已归档 {key} v{version}（root={root}）")


@store_app.command("log")
def store_log(root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录")):
    """列出归档历史。"""
    history = GraphStore(root).history()
    if not history:
        typer.echo("（无归档记录）")
        return
    for record in history:
        note = f"  {record['note']}" if record.get("note") else ""
        typer.echo(f"· {record['name']} v{record['version']} @ {record['saved_at']}{note}")


@store_app.command("tag")
def store_tag(
    name: str = typer.Argument(..., help="归档名"),
    tags: list[str] = typer.Option([], "--tag", "-t", help="标签（可重复；覆盖式设置）"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """设置某归档的标签（v0.6.0；覆盖式）。"""
    store = GraphStore(root)
    store.set_tags(name, list(tags))
    typer.echo(f"· {name} 标签：{store.get_tags(name)}")


@store_app.command("tags")
def store_tags(root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录")):
    """列出全部标签 → 归档名映射（v0.6.0）。"""
    data = GraphStore(root).all_tags()
    if not data:
        typer.echo("（无标签）")
        return
    for name, ts in sorted(data.items()):
        typer.echo(f"· {name}: {ts}")


@store_app.command("enable")
def store_enable(
    tag: str = typer.Option("", "--tag", "-t", help="按标签批量启用（缺省对全部归档）"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """批量启用自动化（v0.6.0）。--tag 限定标签，否则启用全部归档。"""
    store = GraphStore(root)
    names = _names_for_tag(store, tag)
    affected = svc_enable_disable(store, names, True)
    typer.echo(f"· 已启用 {len(affected)} 个归档：{[a['name'] for a in affected]}")


@store_app.command("disable")
def store_disable(
    tag: str = typer.Option("", "--tag", "-t", help="按标签批量禁用（缺省对全部归档）"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """批量禁用自动化（v0.6.0）。--tag 限定标签，否则禁用全部归档。"""
    store = GraphStore(root)
    names = _names_for_tag(store, tag)
    affected = svc_enable_disable(store, names, False)
    typer.echo(f"· 已禁用 {len(affected)} 个归档：{[a['name'] for a in affected]}")


def _names_for_tag(store: GraphStore, tag: str) -> list[str]:
    """--tag 非空取该标签归档名；空则取全部已归档名。"""
    if tag:
        return [n for n, ts in store.all_tags().items() if tag in ts]
    return _archive_names_safe(store)


def _archive_names_safe(store: GraphStore) -> list[str]:
    seen: list[str] = []
    for record in store.history():
        name = str(record.get("name", ""))
        if name and name not in seen:
            seen.append(name)
    return seen


def svc_enable_disable(store: GraphStore, names: list[str], enabled: bool) -> list[dict[str, Any]]:
    """对给定归档名批量翻转 enabled（直接落最新版本的新版本）。

    与 `af_service.enable_by_tag` 同源逻辑；CLI 这里已展开为具体名字列表，
    逐个保存新版本（API 层按标签入口走 `svc.enable_by_tag`）。
    """
    affected: list[dict[str, Any]] = []
    for name in names:
        try:
            version = store.latest(name)
            if version is None:
                continue
            graph = store.load(name, version)
        except (FileNotFoundError, OSError):
            continue
        for auto in graph:
            auto.enabled = enabled
            auto.raw["enabled"] = enabled
        new_version = store.save(graph, name, note=f"v0.6.0 批量{'启用' if enabled else '禁用'}")
        affected.append({"name": name, "version": new_version})
    return affected


@store_app.command("export")
def store_export(
    out: str = typer.Option("", "--out", "-o", help="导出文件路径（.json）；缺省打印到 stdout"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """v0.7.0 导出整个 store 为可携带 bundle（含 tags + 校验和）。"""
    store = GraphStore(root)
    bundle = store.export_bundle()
    rendered = json.dumps(bundle, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(rendered, encoding="utf-8")
        typer.echo(f"· 已导出 {len(bundle['entries'])} 个归档 → {out}（checksum={bundle['checksum'][:16]}…）")
    else:
        typer.echo(rendered)


@store_app.command("import")
def store_import(
    path: Path = typer.Argument(..., help="bundle 文件路径（.json）"),
    strategy: str = typer.Option("skip", "--strategy", help="冲突策略：skip（默认）/ overwrite / rename"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录（导入目标）"),
):
    """v0.7.0 导入 bundle（写操作）。导入前自动校验 checksum + IR Schema。"""
    store = GraphStore(root)
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"[bundle 读取失败] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)
    try:
        report = store.import_bundle(bundle, strategy)
    except ValueError as exc:
        typer.echo(f"[导入失败] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)
    typer.echo(f"· 导入完成：imported={report['imported']} skipped={report['skipped']} "
               f"renamed={report['renamed']} errors={report['errors']}")


# ── v0.8.0：令牌管理（主体模型 + 撤销黑名单）──────────────────────────
auth_app = typer.Typer(no_args_is_help=True, help="v0.8.0 服务层令牌管理（主体摘要 / 撤销）")
app.add_typer(auth_app, name="auth")


# ── v1.0.1：MCP stdio 服务（供 Agent 接入实测）──────────────────────────
@app.command("mcp")
def mcp_serve(
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录（归档/标签/实例落盘位置）"),
):
    """以 stdio 模式启动 MCP server（供 Claude Desktop / CodeBuddy 等 agent 接入）。

    配置示例（agent 侧 mcpServers）：
        {"autoforge": {"command": "forge", "args": ["mcp"]}}
    写/live 工具受 v0.8.0 scope 限制：用含对应 scope 的令牌启动（AUTOFORGE_TOKENS）即可。
    """
    from .af_mcp import serve_mcp

    typer.echo("AutoForge MCP server（stdio）启动，等待 agent 连接…", err=True)
    serve_mcp(root)


@auth_app.command("list")
def auth_list():
    """列出 env 配置的令牌主体摘要（不含明文令牌）。"""
    from .af_auth import TokenRegistry

    subjects = TokenRegistry().subjects()
    if not subjects:
        typer.echo("（未配置任何令牌：AUTOFORGE_API_TOKEN / AUTOFORGE_TOKENS 均为空，全站公开）")
        return
    for item in subjects:
        typer.echo(f"· {item['subject']}: scopes={item['scopes']}")


@auth_app.command("revoke")
def auth_revoke(
    token: str = typer.Argument(..., help="要撤销的明文令牌"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录（撤销黑名单落盘位置）"),
):
    """撤销令牌并落盘黑名单（v0.8.0）。

    注意：运行中的服务进程在启动时读取黑名单文件，CLI 落盘的撤销**重启后生效**；
    需要即时生效请用服务端 `POST /api/auth/revoke`。
    """
    from .af_auth import TokenRegistry

    registry = TokenRegistry(Path(root) / ".auth" / "revoked.json")
    revoked = registry.revoke(token)
    if revoked:
        typer.echo(f"· 已撤销并落盘 → {Path(root) / '.auth' / 'revoked.json'}")
    else:
        typer.echo("· 该令牌已在黑名单中（无变更）")


spec_app = typer.Typer(no_args_is_help=True, help="G7 AF-Spec（文本语法 ⇄ JSON IR）")
app.add_typer(spec_app, name="spec")


@spec_app.command("compile")
def spec_compile(
    path: Path = typer.Argument(..., help="AF-Spec 文本文件"),
    out: str = typer.Option("", "--out", "-o", help="输出 IR JSON 路径（缺省打印到 stdout）"),
    nl: bool = typer.Option(False, "--nl", help="同时渲染自然语言"),
):
    """AF-Spec → JSON IR（经 Schema 校验，唯一真相仍是 Graph）。"""
    text = Path(path).read_text(encoding="utf-8")
    try:
        graph = compile_spec(text)
    except SpecError as exc:
        typer.echo(f"[AF-Spec 语法错误] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)
    except IRValidationError as exc:
        typer.echo(f"[IR 校验失败] {exc}", err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)

    raws = graph_to_raw(graph)
    payload: Any = raws[0] if len(raws) == 1 else {"automations": raws}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(rendered, encoding="utf-8")
        typer.echo(f"· 已写入 {out}")
    else:
        typer.echo(rendered)
    if nl:
        typer.echo("\n── 自然语言（确定性渲染）──")
        typer.echo(render_graph(graph).text)


@spec_app.command("render")
def spec_render(ir: Path = typer.Argument(..., help="IR JSON 文件（.json）")):
    """JSON IR → AF-Spec 文本。"""
    graph = _load(ir)
    typer.echo(render_spec(graph), nl=False)


metrics_app = typer.Typer(no_args_is_help=True, help="v0.5.0 运行指标聚合与回灌 MA（生态闭环）")
app.add_typer(metrics_app, name="metrics")


def _metrics_snapshot(ir_path: Path, events: Path | None, seed: Path | None) -> dict[str, Any]:
    """本地 build runtime + 可选回放 events，聚合运行指标快照。"""
    graph = _load(ir_path)
    runtime = build_runtime(graph)
    seed_data = json.loads(Path(seed).read_text(encoding="utf-8")) if seed else {}
    states = seed_from_graph(graph, seed_data, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states  # canary 漂移检测读同一份状态源
    runtime.adapters.register(FakeHAAdapter(states))
    if events:
        for item in json.loads(Path(events).read_text(encoding="utf-8")):
            advance = item.get("advance_s")
            if advance:
                runtime.advance(float(advance))
            entity_id = item.get("entity_id")
            if entity_id:
                state = str(item.get("state", ""))
                states.set(str(entity_id), state)
                runtime.emit(str(entity_id), state, last_changed=item.get("last_changed"))
            elif advance is None:
                runtime.tick()
    return MetricsAggregator(runtime).snapshot()


@metrics_app.command("show")
def metrics_show(
    ir: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    events: Path = typer.Option(None, "--events", help="事件回放 JSON（同 forge sim 的 events 格式）"),
    seed: Path = typer.Option(None, "--seed", help="初始实体状态 JSON"),
):
    """聚合并打印本地运行指标（不推送）。"""
    snapshot = _metrics_snapshot(ir, events, seed)
    typer.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))


@metrics_app.command("push")
def metrics_push(
    ir: Path = typer.Argument(..., help="IR 文件路径（.json）"),
    events: Path = typer.Option(None, "--events", help="事件回放 JSON"),
    seed: Path = typer.Option(None, "--seed", help="初始实体状态 JSON"),
    ma_url: str = typer.Option("", "--ma-url", help="MA 接收端点基址（如 http://192.168.2.200:8086），默认读 MA_METRICS_URL"),
    token: str = typer.Option("", "--token", help="butler 令牌，默认读 BUTLER_TOKEN"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只聚合不推送（验证用）"),
    buffer_dir: Path = typer.Option(DEFAULT_BUFFER_DIR, "--buffer-dir", help="离线缓冲目录"),
):
    """聚合运行指标并回灌 memory-agent（幂等 + 退避 + 离线缓冲）。"""
    snapshot = _metrics_snapshot(ir, events, seed)
    url = ma_url or os.getenv("MA_METRICS_URL", "")
    tok = token or os.getenv("BUTLER_TOKEN", "")
    if not dry_run and not url:
        typer.echo("[错误] 未提供 --ma-url 且环境无 MA_METRICS_URL", err=True)
        raise typer.Exit(code=1)
    ingester = Ingester(buffer_dir=buffer_dir)
    target = url or "http://localhost:8086"
    result = ingester.push(snapshot, ma_url=target, token=tok, dry_run=dry_run)
    if not dry_run:
        result["flush"] = ingester.flush_buffer(ma_url=target, token=tok)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


# ── v1.5.0 经验闭环：实体共现 + 遥测 ──────────────────────────────────
experience_app = typer.Typer(no_args_is_help=True, help="v1.5.0 经验：实体共现（只在成功落盘后采集）")
app.add_typer(experience_app, name="experience")

telemetry_app = typer.Typer(no_args_is_help=True, help="v1.5.0 经验：token/结果遥测 + 错误类别分布")
app.add_typer(telemetry_app, name="telemetry")


@experience_app.command("show")
def experience_show(
    limit: int = typer.Option(10, "--limit", help="各榜条数"),
    root: Path = typer.Option(DEFAULT_STORE_ROOT, "--root", help="store 根（默认 .forge）"),
):
    """打印实体共现经验摘要（top 共现对 / top 实体 / IR 模式）。"""
    data = ExperienceStore(root).summary(limit=limit)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@experience_app.command("export")
def experience_export(
    limit: int = typer.Option(200, "--limit", help="导出条数上限"),
    out: Path = typer.Option(None, "--out", help="写文件路径（缺省打印到 stdout）"),
    root: Path = typer.Option(DEFAULT_STORE_ROOT, "--root", help="store 根（默认 .forge）"),
):
    """结构化导出实体共现经验（喂 MA）。"""
    data = ExperienceStore(root).export(limit=limit)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        typer.echo(f"已导出到 {out}（{len(data['pairs'])} 对 / {len(data['entities'])} 实体）")
    else:
        typer.echo(text)


@telemetry_app.command("show")
def telemetry_show(
    days: int = typer.Option(30, "--days", help="统计窗口天数"),
    root: Path = typer.Option(DEFAULT_STORE_ROOT, "--root", help="store 根（默认 .forge）"),
):
    """打印 token/结果遥测（四维）+ 错误知识库类别分布。"""
    data = TelemetryStore(root).usage(days=days)
    data["knowledge"] = ErrorKnowledge(root).counts()
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# ── v1.1.0：设备目录（自然语言设备名 → entity_id）──────────────────────
entities_app = typer.Typer(
    no_args_is_help=True,
    help="v1.1.0 设备目录：拉取 HA 设备清单 + 自然语言设备名 → entity_id（切断对 MA 的硬依赖）",
)
app.add_typer(entities_app, name="entities")


@entities_app.command("refresh")
def entities_refresh(
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录（目录缓存落 `{root}/.catalog/`）"),
    ha_url: str = typer.Option("", "--ha-url", help="HA 地址（缺省读 AUTOFORGE_HA_URL）"),
    ha_token: str = typer.Option("", "--ha-token", help="HA 令牌（缺省读 AUTOFORGE_HA_TOKEN）"),
    full: bool = typer.Option(True, "--full/--incremental", help="全量替换（默认）｜增量合并"),
    domain: str = typer.Option("", "--domain", help="可选：只刷新某域"),
    area: str = typer.Option("", "--area", help="可选：只刷新某房间"),
):
    """拉取 HA 全屋设备目录进本地缓存。首次连上 HA 后、设备大幅增减后调一次即可。"""
    from .af_catalog import DeviceCatalog

    catalog = DeviceCatalog(
        root,
        ha_url=ha_url or None,
        ha_token=ha_token if ha_token else None,
    )
    result = catalog.refresh(full=full, domain=domain, area=area)
    if not result.get("ok"):
        typer.echo(f"[刷新失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    typer.echo(
        f"· 目录已刷新：total={result['total']} added={result['added']} "
        f"changed={result['changed']} removed={result['removed']} @ {result['freshness']}"
    )


@entities_app.command("summary")
def entities_summary(
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """目录摘要：按域统计 + 区域列表 + 新鲜度（不 dump 全量实体）。"""
    from .af_catalog import DeviceCatalog

    snap = DeviceCatalog(root).snapshot()
    typer.echo(f"· 设备总数：{snap['total_entities']}　freshness：{snap['freshness'] or '（未刷新）'}")
    if snap["by_domain"]:
        typer.echo("· 按域统计：")
        for dom, cnt in snap["by_domain"].items():
            typer.echo(f"    {dom}: {cnt}")
    if snap["areas"]:
        typer.echo(f"· 已知区域：{snap['areas']}")


@entities_app.command("list")
def entities_list(
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
    domain: str = typer.Option("", "--domain", help="按域过滤"),
    area: str = typer.Option("", "--area", help="按房间硬过滤（中文，只返回该房间实体）"),
    keyword: str = typer.Option("", "--keyword", "-k", help="模糊匹配 entity_id / 中文名"),
    limit: int = typer.Option(50, "--limit", help="每页条数（上限 200）"),
    offset: int = typer.Option(0, "--offset", help="分页偏移"),
):
    """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。"""
    from .af_catalog import DeviceCatalog

    result = DeviceCatalog(root).list_entities(
        domain=domain, area=area, keyword=keyword, limit=limit, offset=offset
    )
    if not result.get("ok"):
        typer.echo(f"[读取失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    for item in result["entities"]:
        typer.echo(f"· {item['entity_id']}　[{item['state']}]　{item['friendly_name']}　({item['domain']})")
    more = f"　下一页 offset={result['next_offset']}" if result["truncated"] else ""
    typer.echo(
        f"—— matched={result['matched_count']} returned={result['returned']} "
        f"total={result['total']}{more}"
    )
    if result.get("area_warning"):
        typer.echo(f"⚠️ {result['area_warning']}")


@entities_app.command("resolve")
def entities_resolve(
    name: str = typer.Argument(..., help="自然语言设备名，如「书房吊灯」"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
    area: str = typer.Option("", "--area", help="房间（中文，硬过滤：只返回该房间实体）"),
    domain: str = typer.Option("", "--domain", help="限定域（一般不要传，让它多返回候选）"),
    top_n: int = typer.Option(8, "--top-n", help="最多返回几个候选"),
):
    """自然语言设备名 → 候选 entity_id（写 IR 前必调）。"""
    from .af_catalog import DeviceCatalog

    result = DeviceCatalog(root).resolve(name, area=area, domain=domain, top_n=top_n)
    if not result.get("ok"):
        typer.echo(f"[解析失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    if not result["candidates"]:
        typer.echo(f"（无候选）{result.get('note', '')}")
        return
    for item in result["candidates"]:
        risk = " ⚠️高危" if item.get("high_risk") else ""
        typer.echo(
            f"· {item['entity_id']}　{item['friendly_name']}　"
            f"state={item['state']}　[{item['confidence']}/{item['matched_by']}]{risk}"
        )
        typer.echo(f"    可切换状态：{item['possible_states']}")
        typer.echo(f"    可调服务：{item['services']}")
    if result.get("area_warning"):
        typer.echo(f"⚠️ {result['area_warning']}")


# ── v1.6.0 P0：别名沉淀（设备名 → entity_id 精确映射，下次直中）──────
@entities_app.command("remember")
def entities_remember(
    name: str = typer.Argument(..., help="自然语言设备名（用户原话），如「书房电脑」"),
    entity_id: str = typer.Argument(..., help="真实 entity_id（先用 forge entities resolve 取）"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """把「设备名 → entity_id」的选择沉淀为别名（下次 resolve 直中）。"""
    from .af_catalog import DeviceCatalog

    result = DeviceCatalog(root).set_alias(name, entity_id)
    if not result.get("ok"):
        typer.echo(f"[失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    typer.echo(f"· 已沉淀 {result['alias']!r} → {result['entity_id']}（共 {result['total']} 条别名）")


@entities_app.command("aliases")
def entities_aliases(
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """列出已沉淀的别名映射。"""
    from .af_catalog import DeviceCatalog

    data = DeviceCatalog(root).list_aliases()
    if not data["aliases"]:
        typer.echo("（无别名）")
        return
    for name, eid in sorted(data["aliases"].items()):
        typer.echo(f"· {name} → {eid}")


@entities_app.command("forget")
def entities_forget(
    name: str = typer.Argument(..., help="要删除的别名（设备名）"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """删除一条别名映射。"""
    from .af_catalog import DeviceCatalog

    result = DeviceCatalog(root).remove_alias(name)
    if not result.get("ok"):
        typer.echo(f"[失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    typer.echo(f"· 已删除别名 {result['removed']!r}（剩 {result['total']} 条）")


@entities_app.command("state")
def entities_state(
    entity_id: str = typer.Argument(..., help="真实 entity_id，如 light.study_main"),
    root: str = typer.Option(DEFAULT_STORE_ROOT, "--root", help="存储根目录"),
):
    """查实体当前状态（实时优先，失败回退目录缓存并标注 source）。"""
    from .af_catalog import DeviceCatalog

    result = DeviceCatalog(root).get_state(entity_id)
    if not result.get("ok"):
        typer.echo(f"[读取失败] {result.get('error')}", err=True)
        raise typer.Exit(code=EXIT_SCAN_ERROR)
    typer.echo(f"· {result['entity_id']} = {result['state']}　（source={result['source']}）")
    if result.get("friendly_name"):
        typer.echo(f"· 名称：{result['friendly_name']}")
    typer.echo(f"· 可切换状态：{result['possible_states']}")
    if result.get("note"):
        typer.echo(f"⚠️ {result['note']}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（容器内用 0.0.0.0）"),
    port: int = typer.Option(8787, "--port", help="监听端口"),
    store_root: str = typer.Option(DEFAULT_STORE_ROOT, "--store-root", help="G6 归档目录"),
    examples: str = typer.Option("", "--examples", help="启动时把样例 IR 幂等灌入归档的目录（缺省 examples/ir）"),
    ui_dir: str = typer.Option("", "--ui-dir", help="前端构建产物 dist 目录；提供后一并托管 UI（SPA fallback，同源 /api）"),
):
    """启动**只读** HTTP 服务层（FastAPI，对齐 UI 开工令附录 A 契约）。

    端点见 `GET /docs`（Swagger UI）或 `GET /openapi.json`。
    传 --ui-dir 可把前端构建产物（dist）一并托管，单进程同时提供 API 与 UI。
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo('缺少服务依赖，请先安装：pip install -e ".[api]"', err=True)
        raise typer.Exit(code=EXIT_IR_ERROR)

    from .af_api import build_app
    from .af_flock import FileLock, owner_id

    # 单写者租约（DCD 裁定一 A）：抢不到锁 → 降级只读，写操作由 API 层拒绝。
    _lock = FileLock(Path(store_root) / ".serve.lock")
    readonly = not _lock.try_acquire()
    if readonly:
        typer.echo(
            f"⚠️ 单写者锁被占，降级只读（持有者: {_lock.holder().get('owner')}）；写操作将被拒绝",
            err=True,
        )
    else:
        typer.echo(f"· 取得单写者锁（owner={owner_id()}）")

    examples_path = examples or ("examples/ir" if Path("examples/ir").is_dir() else "")
    app_ = build_app(store_root, examples_path or None, ui_dir or None, readonly=readonly)
    typer.echo(f"· AutoForge 只读服务层：http://{host}:{port}（文档 /docs，store={store_root}）")
    if ui_dir and Path(ui_dir).is_dir():
        typer.echo(f"· 前端静态托管：http://{host}:{port}/（dist={ui_dir}）")
    uvicorn.run(app_, host=host, port=port, log_level="info")


def _print_stats(runtime: Runtime) -> None:
    stats = runtime.stats()
    typer.echo(f"· 自动化：{stats['automations']}")
    typer.echo(f"· 活跃实例：{stats['active_instances']}　挂起询问：{stats['pending_asks']}")
    typer.echo(f"· 总线：{stats['bus']['counts']}")
    if stats["audit"]:
        typer.echo("· 审计事件：")
        for event in stats["audit"]:
            typer.echo(f"    - [{event['type']}] {event['message']}")


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

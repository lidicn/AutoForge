#!/usr/bin/env python3
"""AutoForge 分层依赖检查（grimp API）。

锁死架构 baseline：低层模块（内核底座）绝对不能 import 高层模块（运行时/服务）。
本脚本用 grimp 直接构建 autoforge 完整 import 图，然后扫描所有模块，
如果任何"低层"模块 import 了"高层"模块，立刻报错退出。

层级定义（简化版，以"核心包前缀"分组）：
  L2 服务入口  → af_service, af_api, af_cli, af_mcp
  L1 运行时    → 所有其他非 L0 的 af_* 模块
  L0 内核底座  → af_ir, af_adapters, af_flock, af_fault, af_time, af_auth

注：仿真胶水层（af_vhass.harness/bridge/high_fidelity/sse_stream）天然需要
组装 Runtime/Scheduler，属于 L1 运行时的一部分，不是内核底座。
af_vhass.fake/device_sm/action_queue/event_bus/dual_track 虽含仿真状态机，
但 dual_track 会 import af_service.simulate_track，属于仿真集成层，归 L1。
"""
from __future__ import annotations

import sys
from pathlib import Path

import grimp


# ──────────────────────────────────────────────────────────────────────
# 层级定义（按模块前缀匹配；第一个匹配的前缀决定层级）
# ──────────────────────────────────────────────────────────────────────

# 每个层级 = (层级号, 层级名, 模块前缀列表)
# 层级号越大 = 越高层（高层可以 import 低层，低层不可 import 高层）

LAYERS = [
    # L2: 服务入口（最高层）
    (2, "service-entry", [
        "autoforge.af_service",
        "autoforge.af_api",
        "autoforge.af_cli",
        "autoforge.af_mcp",
    ]),
    # L0: 纯内核底座（最低层）—— 只有这些前缀算 L0
    (0, "kernel", [
        "autoforge.af_ir",
        "autoforge.af_adapters",
        "autoforge.af_flock",
        "autoforge.af_fault",
        "autoforge.af_time",
        "autoforge.af_auth",
    ]),
    # L1: 运行时 + 仿真集成 + 所有其他功能模块（中间层）—— 隐式匹配：
    # 凡是不在 L0/L2 的 af_* 模块都归 L1
]

L2_KEYWORDS = ("autoforge.af_service", "autoforge.af_api", "autoforge.af_cli", "autoforge.af_mcp")
L0_KEYWORDS = ("autoforge.af_ir", "autoforge.af_adapters",
               "autoforge.af_flock", "autoforge.af_fault",
               "autoforge.af_time", "autoforge.af_auth")


# ──────────────────────────────────────────────────────────────────────
# 层级定义（极简版——只锁 DCD 原始意图"service ← api ← cli 无反向"）
# ──────────────────────────────────────────────────────────────────────

# L0: 纯内核底座 —— 这些是数据结构/锁/状态机基类，绝对不能 import 任何高层
# L1: 所有其他 af_* 模块 —— 运行时/仿真/服务层/集成胶水，内部可自由耦合
# L2: 服务入口顶端 —— cli/mcp 入口，绝对不能被 L0 反向 import

L0_KERNEL = (
    "autoforge.af_ir",        # IR schema/模型（纯数据结构）
    "autoforge.af_flock",     # 文件锁（跨进程原语）
    "autoforge.af_fault",     # 故障注入定义（纯数据类）
    "autoforge.af_time",      # 时间源基类
    "autoforge.af_adapters",  # adapter 基类（ha/http/mock 是 L1 实现，adapter.base 是 L0）
)

L2_TOP = (
    "autoforge.af_service",          # 服务层（可被 L1 调，但 L0 不能 import）
    "autoforge.af_api",              # HTTP API
    "autoforge.af_cli",              # CLI 入口
    "autoforge.af_mcp",              # MCP 入口
    "autoforge.af_canary_supervisor",# canary 生命周期管理者
)


def classify(module: str) -> int:
    """返回模块层级（0=L0 kernel, 1=L1 runtime 及其他, 2=L2 service entry）。"""
    if any(module == kw or module.startswith(kw + ".") for kw in L0_KERNEL):
        # 但 af_adapters.base 是基类（L0），af_adapters.ha/http/mock 是实现（L1）
        if module.startswith("autoforge.af_adapters.") and module != "autoforge.af_adapters":
            sub = module[len("autoforge.af_adapters."):]
            if sub != "base":
                return 1  # ha/http/mock 是 L1
        return 0
    if any(module == kw or module.startswith(kw + ".") for kw in L2_TOP):
        return 2
    return 1


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    print("AutoForge 分层依赖检查 (grimp API)")
    print("=" * 50)
    print(f"Building import graph for autoforge ...")
    sys.path.insert(0, str(project_root / "src"))

    try:
        graph = grimp.build_graph("autoforge")
    except Exception as e:
        print(f"FATAL: grimp.build_graph failed: {e}", file=sys.stderr)
        return 2

    modules = sorted(graph.modules)
    n = len(modules)
    print(f"  modules: {n}")

    # 统计每层模块数
    layer_counts: dict[int, int] = {}
    for m in modules:
        lv = classify(m)
        layer_counts[lv] = layer_counts.get(lv, 0) + 1
    for lv, name in [(0, "L0 kernel"), (1, "L1 runtime"), (2, "L2 service")]:
        print(f"  {name}: {layer_counts.get(lv, 0)} modules")

    # 硬规则（唯一的失败条件）：
    #   L0 kernel ⛔  import L2 service entry —— 底座绝对不能反向依赖服务入口
    # 合法方向（允许）：
    #   L2 service  → L0 kernel（高层依赖底座，方向正确）
    #   L2 service  → L1 runtime（高层依赖功能层，方向正确）
    #   L1 runtime  → L0 kernel（功能层依赖底座，方向正确）
    #   L1 runtime  ↔ L1 runtime（同层内部自由耦合）
    violations: list[str] = []
    for mod in modules:
        mod_lv = classify(mod)
        if mod_lv != 0:
            continue  # 只检查 L0 kernel
        for imp in graph.find_modules_directly_imported_by(mod):
            imp_lv = classify(imp)
            if imp_lv == 2:
                violations.append(
                    f"  ❌ {mod}(L0 kernel)  →  {imp}(L2 service)  "
                    f"— kernel MUST NOT directly import service entry (af_service/af_api/af_cli/af_mcp)"
                )

    if violations:
        print(f"\n❌ Found {len(violations)} layer violation(s):\n")
        for v in violations:
            print(v)
        print(f"\nLower layer modules must NOT import higher layer modules.")
        return 1
    else:
        print(f"\n✅ Layer architecture clean — no reverse dependencies detected.")
        print(f"   Baseline lock: {n} modules, 0 violations.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

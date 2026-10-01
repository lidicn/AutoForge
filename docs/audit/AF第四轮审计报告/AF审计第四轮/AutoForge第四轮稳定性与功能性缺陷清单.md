# AutoForge 第四轮审计 · 稳定性与功能性缺陷清单

> 运行 ID：`af-audit-run-4`　工具：cloudflare/security-audit-skill（六阶段）
> 校验器：`coverage-ledger` PASS（17 单元）｜`findings` PASS（2 记录）
> 测试基线：1277 passed / 51 skipped / 0 failed
> **本轮换面**：静态闸漏检探测 + 静默异常吞噬 + 持久化/租约 + 未审面首查

---

## 一句话结论

**找到 2 条，但都不是崩溃级**——第四轮的产出明显弱于前三轮。这本身是个有价值的信号（见文末）。

1. **设备目录静默截断到 120 送 LLM**（中）—— 多轮未修复
2. **undo 各域恢复策略不一致**（低-中）

外加一条**重要的正面结论**：26 类静态闸**全部有效**，无一漏检。

---

## 缺陷 1（中）· 设备目录静默截断到 120 送 LLM，无 truncated 回报

**位置**：`src/autoforge/af_orchestrator.py:916`

```python
list(catalog)[:120]
```

### 为什么是缺陷

项目自己有两套矛盾的做法：

| 模块 | 做法 |
|---|---|
| `af_catalog.py` | **规范**：`MAX_LIST_LIMIT=200`、返回 `truncated` 标志与 `next_offset` |
| `af_orchestrator.py:916` | **静默**：按原始字典序取前 120，无回报、无排序 |

### 后果

- 典型家庭 300~1500 实体，LLM 只看得到 **8%~40%**
- 你要自动化的设备若不在前 120 位，LLM **只能猜 entity_id**
- 解析失败后外层 `try/except: pass` 静默降级到 fallback，**用户完全无感知**
- 而且你**已经为看不到的部分付了 token**

### 修复

按 `af_catalog.py` 已有的纪律做本地检索：

```python
# 本地 top-k 检索（房间/域/别名打分）替代全量截断
hits = catalog.search(query, limit=15)
# 并回报 truncated
meta["truncated"] = len(hits) < total
```

---

## 缺陷 2（低-中）· undo 各域恢复策略不一致

**位置**：`src/autoforge/af_undo.py`（`_light` L63-79 / `_climate` L114-125 / `_cover` L106-110）

同一种情况（快照属性值是 HA 常见非法形态），三个域给出三种处理：

| 域 | 策略 | 实测结果 |
|---|---|---|
| **灯** `_light` | **fail-soft** | `brightness=unknown` → **丢字段**，仍返回 `light.turn_on` |
| 空调 `_climate` | **fail-closed** | `temperature=unknown` → `return None`（整个放弃） |
| 窗帘 `_cover` | **fail-closed** | `position=unknown` → `return None` |

```python
# _light: 静默吞掉转换失败
if (b := attrs.get("brightness")) is not None:
    try:
        params["brightness"] = int(b)
    except (TypeError, ValueError):
        pass          # ← 丢了，但继续 turn_on
```

**后果**：灯 undo 后亮了，但亮度/色温 ≠ 原值。

严重度有限——状态方向是对的（亮 vs 灭），不会留下危险状态。但**同为"快照非法"，三个域三种策略**，这是维护隐患。

建议：统一为 fail-closed，或至少在丢弃字段时记 warn 日志。

---

## 重要正面结论：26 类静态闸全部有效

我对声明的 26 类检查逐项做了漏检探测，构造 7 个违规 IR：

| 检查项 | 结果 |
|---|---|
| `MISSING_TIMEOUT_OR_DEFAULT` | ✅ 正确触发 |
| `STATIC_LOOP` | ✅ |
| `ENTITY_NOT_FOUND` | ✅ |
| `NESTED_SUSPEND_IN_CANCEL` | ✅ |
| `SNAPSHOT_FALSE_MULTI_AND` | ✅ |
| `LOW_CONF_WRITES_DEVICE` | ✅（conf 0.3/0.5 → LOW_CONF） |
| `SHADOW_WRITES_DEVICE` | ✅（conf 0.7 → SHADOW；0.92 → 放行） |

**其中 2 个候选我一开始以为漏检，核实后是我自己构造错了**：

- `lock.unlock` → 我期望 `L3_ACTION`，实际报 `L2_NEEDS_CONFIRM`。**闸是对的**：`lock` 域属 L2（门锁/窗帘/空调），L3 是 `shell_command`/`python_script`/`hassio` 等外网/非幂等域
- `conf=0.3` 未报 → 我的 do 节点把目标写成了顶层 `target` 字段。**正确写法是 `params.entity_id`**（`target_entities()` 从 `params.entity_id` 与 `params.target.{entity_id,device_id,area_id}` 解析）

顺带核实 `target_entities()` 对 `params.target.device_id`/`area_id` 形式会标记成 `device_id:xxx`/`area_id:xxx` 前缀 —— **P0-5 绕过已修**。

**结论：G2 静态闸是本项目中实现质量最高的部分。**

---

## 本轮证伪

| 候选 | 证伪依据 |
|---|---|
| `af_store.py` 静默吞噬 | 3 处均为 fsync/rmdir 等 best-effort 容错（跨平台 fsync 差异、目录非空），失败不影响数据正确性 |
| `af_persist.py` 租约 | 仲裁完整；`_parse_iso` 有 naive→UTC 归一化；`_safe()` 对 `uuid4().hex[:12]` 无碰撞 |
| `af_scanner.py:978` 迭代改容器 | 核实为更新已有 key 的值，不触发 `RuntimeError` |
| `af_catalog.py` 分页 | 规范（`MAX_LIST_LIMIT=200` + `truncated` + `next_offset`） |

全仓 311 处 `except` 中 90 处静默吞噬，我已逐处核实重点模块——**大部分是合理的 best-effort 容错**，不是缺陷。

---

## 四轮累积视图

| 轮次 | 面 | 核心发现 | 强度 |
|---|---|---|---|
| 一 | 全量 | `homesdk` 未声明（装不上）、`instance.id`、canary 序列化 | **P0 ×2** |
| 二 | 生命周期/资源 | SAFE HALT `logger` 未定义 → tick 线程静默死亡 | **高** |
| 三 | 表达式求值 | `min`/`max` 混类型 `TypeError` 绕过 soft-fail | **中高** |
| 四 | 静态闸/异常吞噬/持久化 | 目录截断、undo 策略不一致 | **中 / 低** |

### 三个跨轮未修复项

1. **`instance.id`（`af_scheduler.py:343`）** —— 连续三轮确认未修复，P0
2. **设备目录截断（`af_orchestrator.py:916`）** —— 第一轮架构评估提出，本轮回归确认仍在
3. **门禁缺位** —— 第一轮的 `homesdk` 未声明、第二轮的 `logger` 未定义，都是一行 linter 能拦住的

---

## 未覆盖声明

17 个单元中 **12 个 `out_of_scope` 未审**：HTTP 鉴权、MCP 授权码、`af_bus` 去重熔断、`af_instance` 终态回收、`af_executor` canary、`af_evo`、`af_service` 会话、CLI 输入面、`af_vhass` 仿真、`af_adapters` 出向、`af_predict`、`af_preference`。

**四轮合计：未覆盖面仍明显多于已覆盖面。**

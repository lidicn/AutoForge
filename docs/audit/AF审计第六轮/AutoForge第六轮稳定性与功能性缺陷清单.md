# AutoForge 第六轮审计 · 稳定性与功能性缺陷清单

> 运行 ID：`af-audit-run-6`
> **本轮 skill**：`anthropics/knowledge-work-plugins` → `engineering/skills/code-review`
> （四维度：Security / Performance / Correctness / Maintainability；重点取 Performance + Correctness）
> 校验器（沿用 security-audit 的机器可读契约）：`coverage-ledger` PASS（12）｜`findings` PASS（2）
> 测试基线：1277 passed / 51 skipped / 0 failed
> **本轮方向（自主选定）**：第五轮指出的处女地 —— `af_evo.py`(1474) + `af_service.py`(2102)，合计 3576 行，五轮来从未审计

---

## 一句话结论

**`af_service` 的会话 TTL 清理只挂在读取路径，`create_session` 不触发 → 被放弃的会话永久残留。**

外加 `af_evo` 一处语义 hardening（空集 Jaccard = 1.0）。**`af_evo` 整体防御性极强，无实质缺陷。**

---

## 缺陷 1（中）· 会话 TTL 清理只在读取路径触发

**位置**：`src/autoforge/af_service.py:1347`（`create_session`）vs `:1332`（`_purge_sessions`）

### 实测

```
创建 50 个会话（TTL 缩短为 0.5s 便于观测）
  → _SESSIONS = 50
等待 1.2s（已超 TTL），不做任何读取
  → _SESSIONS = 50        ❌ 过期会话全部残留
调用一次 list_sessions()
  → _SESSIONS = 0         ✅ 读取路径才触发清理
```

### 根因

`_purge_sessions()` 只在三处被调用：

| 函数 | 调用 purge |
|---|---|
| `list_sessions` | ✅ |
| `_get_session` | ✅ |
| **`create_session`** | **❌ 没有** |

**写入路径不回收，读取路径才回收。**

### 为什么严重

每个会话持有的不是轻量条目，而是三个重对象：

```
sess = {"runtime": Runtime,   ← 含 bus/audit/adapters/clock 的完整运行时对象图
        "states":  FakeHA,    ← 仿真状态
        "graph":   Graph,     ← 完整 IR
        ...}
```

### 触发场景

- 创建会话后**用户放弃**（不 answer、不 get、不 list）→ 永久残留
- **批处理脚本**循环 `create_session` 拿视图即走 → 内存单调增长
- 默认 TTL = 3600s，即使有清理也是 1 小时窗口

### 修复

```python
def create_session(ir, seed=None, events=None):
    _purge_sessions()          # ← 加这一行
    ...
```

或更彻底：后台定时任务定期回收，不依赖任何调用路径。

---

## 缺陷 2（语义 hardening）· `_jaccard(∅, ∅) = 1.0`

**位置**：`src/autoforge/af_evo.py`

### 实测对照

| 比较对象 | 相似度 |
|---|---|
| **两个都缺 trigger 的自动化** | **1.000** ← 被判为完全相同 |
| 不同房间的人体传感器 | 0.500 |
| 人体传感器 vs 日落 | 0.000 |

### 问题

`_trigger_tokens(None)` 与 `_trigger_tokens({})` 都返回空集，`_jaccard(∅,∅)` 返回 **1.0**。

数学上是 `0/0`，实现取了 1.0，**把「共同缺失」等同于「完全相同」**。

在进化/去重/合并决策里，若下游用相似度阈值判定，两条**都缺 trigger** 的自动化会被误判为"trigger 相同"从而错误合并——而两条真实不同的自动化反而得分更低。

建议：空集返回 `0.0`，或引入「不可比较」标记。

---

## 本轮证伪

### `af_evo` —— 防御性极强，几无实质缺陷

| 检查项 | 结果 |
|---|---|
| 外部可插拔依赖调用 | 几乎全部包 `try/except Exception` + `_warn` |
| 除法 `ov / total` | ✅ 有 `total > 0` 守卫 |
| `_to_int(inf)` → OverflowError | 未捕获，但**可达性受限**（输入均为计数类字段，且外层已包 try/except） |
| `_clamp01(nan)` → 1.0 | NaN 静默变上界，同上，可达性受限 |
| `_max_jaccard` O(n²) | 实测 n=800→125ms，但**典型规模数十条**（n=100 仅 1.86ms），非瓶颈 |

**关于 O(n²) 的对比判断**：第五轮 `af_preference` 的 O(N²) 是真 P1（**每次写入都全量重写**，4001 条耗时 119.51s）；本轮 `_max_jaccard` 的 O(n²) 是**一次性计算且规模受限**，不计为缺陷。**同为 O(n²)，严重度天差地别——关键在触发频率与规模上界。**

### `af_service._ENTITY_HEALTH_CACHE` —— 无缺陷

以文件路径为键，键数量受 store 根目录数量约束（通常 1 个），条目按 `(st_mtime, st_size)` 校验失效，会被覆盖而非累积。

---

## 六轮累积视图

| 轮次 | 面 | 核心发现 | 强度 |
|---|---|---|---|
| 一 | 全量 | `homesdk` 未声明、`instance.id`、canary 序列化 | **P0 ×2** |
| 二 | 生命周期 | SAFE HALT `logger` 未定义 → tick 线程死亡 | **高** |
| 三 | 表达式求值 | `min`/`max` 混类型绕过 soft-fail | **中高** |
| 四 | 静态闸/异常吞噬 | 目录截断、undo 策略不一致 | **中 / 低** |
| 五 | **处女地** 偏好/预测/总线 | `PreferenceModel` O(N²) | **P1** |
| 六 | **处女地** evo/service | 会话清理只在读路径 | **中** |

### 方法论验证：第五轮的修正在本轮再次成立

第五轮我推翻了"边际收益衰减"的判断，提出**"取决于还剩多少处女地"**。

本轮**再次验证**：`af_evo`+`af_service` 是五轮未碰的 3576 行，本轮即命中一个中等缺陷。**已审面衰减、未审面仍有产出**——这个规律两轮连续成立。

### 跨六轮仍未修复（依然是主要矛盾）

1. **`instance.id`（`af_scheduler.py:343`）** —— 连续五轮确认未修复，唯一确定的 P0
2. **设备目录截断（`af_orchestrator.py:916`）** —— 连续五轮
3. **门禁缺位** —— 前两轮那两个 bug 都是一行 linter 能拦住的
4. **"只增不减"一族持续扩大** —— 第一轮 `AuditLog`、第五轮 `PreferenceModel._records` / `bus._last_state`、第六轮 `_SESSIONS`

**第 4 条值得单独说：六轮下来，"缓存/记录只增不减"已在同一项目的 5 个不同模块中复现。这不是偶发 bug，是缺一个统一的生命周期约定。**

---

## 未覆盖声明

12 个单元中 **7 个 `out_of_scope` 未审**：HTTP 鉴权、MCP 授权码、`af_instance` 终态回收、`af_executor` canary、CLI 输入面、`af_vhass` 仿真、`af_adapters` 出向。

按"处女地有回报"的规律，剩余未审面中 `af_executor`(699) 与 `af_instance`(446) 最值得下一轮投入。

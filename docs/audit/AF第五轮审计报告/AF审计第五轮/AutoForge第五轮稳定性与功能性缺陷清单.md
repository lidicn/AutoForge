# AutoForge 第五轮审计 · 稳定性与功能性缺陷清单

> 运行 ID：`af-audit-run-5`　工具：cloudflare/security-audit-skill（六阶段）
> 校验器：`coverage-ledger` PASS（14 单元）｜`findings` PASS（2 记录）
> 测试基线：1277 passed / 51 skipped / 0 failed
> **本轮换面**：前四轮 `out_of_scope` 未审面 —— `af_preference`(492)、`af_predict`(915)、`af_bus`(307)

---

## 一句话结论

**在从未审计过的 `af_preference` 里找到一个实打实的 P1：`O(N²)` 写放大 + 无记录上限。**

这轮说明了一件事：**换面是有回报的**——前四轮没碰过的模块里确实藏着东西。

---

## 缺陷 1（P1）· `PreferenceModel` 每次 `record()` 全量重写整个偏好库

**位置**：`src/autoforge/af_preference.py:168`（`record`）→ `:467`（`_save`）

### 实测数据（本轮最有价值的一张表）

| 记录数 | 单次 `record` 耗时 | 累计耗时 | 落盘大小 |
|---|---|---|---|
| 501 | 7.58 ms | 1.88 s | 158 KB |
| 1001 | 14.73 ms | 7.54 s | 317 KB |
| 2001 | 29.25 ms | 29.90 s | 634 KB |
| 4001 | **58.73 ms** | **119.51 s** | **1268 KB** |

单次耗时**严格线性**增长 = 每次 O(N) = 累计 **O(N²)**。仅插入 4001 条就花了 **119.51 秒**。

### 根因

```python
# record() 末尾
self._records.append(rec)
self._update_stats(...)
self._save()          # ← 每插一条就全量重写一次

# _save()
data = {"records": [r.to_json() for r in self._records], ...}
json.dump(data, f, ensure_ascii=False, indent=2)   # ← 整个文件重写
```

而且 `_records` **没有任何上限** —— 全模块 grep `maxlen` / `MAX_` / `prune` / `evict` / `expire` **命中 0 次**。

`_load()` 启动时也是全量读入内存。约 **326 字节/条**。

### 为什么这在真实场景会恶化

用户每接受/拒绝一次自动化建议就写一条偏好记录。长期常驻下：
- 写放大持续恶化（1 万条时单次约 150ms）
- `preferences.json` 无界膨胀（1 万条约 3.2MB，且**每条记录都触发一次 3.2MB 的全量写**）
- 启动加载成本同步线性上升

### 修复（按优先级）

1. **加记录上限**（最简单）：保留最近 N 条（如 5000），或按时间窗口裁剪
2. **改增量写**：追加式 JSONL，或只在 shutdown/定时批量落盘
3. **分离聚合态**：实际决策只用 `_stats` 聚合值，`_records` 明细可只保留抽样

---

## 缺陷 2（回归类）· `af_bus` 状态缓存键永不摘除

**位置**：`src/autoforge/af_bus.py` 的 `_last_state` / `_last_accepted` / `_changes`

### 实测

```
旧设备 200 个发布后                    _last_state = 200
旧设备全部下线 + 新设备 200 个接入后    _last_state = 400
```

**旧键全部残留、单调递增。** `_last_state` 与 `_last_accepted` 的 `pop`/`clear`/`del` 在源码中出现 **0 次**；`_changes` 的键同样永不删除（deque 内只按时间窗口裁剪元素）。

实体漂移（设备更换/重命名/固件变更导致 entity_id 变化）在家庭环境是常态。

### 定位说明

计为**回归类而非新发现**——与第一轮 P1（`AuditLog` / `bus.emitted` / `_time_fired` 无上限）是**同一族问题：只增不减**。

且增长受**实体拓扑数量**约束（非随时间无界），严重度弱于缺陷 1。

顺带确认：**去重与熔断逻辑本身是正确的**——`_seen` 有 LRU 上限 4096（实测 5000 事件后 `_seen=4096`）；熔断默认 `threshold=12/window=10s/cooldown=15s`，实测 30 次高频变更后正确开路。自定义事件跳过熔断是源码注释明确的**设计决策**（避免自动化联动风暴自伤），非缺陷。

---

## 本轮证伪（诚实记录）

### `af_predict` —— 全项目参数校验最严格的模块，无缺陷

| 测试 | 结果 |
|---|---|
| 跨午夜窗口 `22:00 + 9h` | ✅ `[(1320,1440),(0,420)]`，03:00 正确命中 |
| 负 `start`（-60） | ✅ 取模为 1380 |
| `_require_window` 对 0/负/inf/nan | ✅ 全部拒绝 |
| `_require_threshold` 越界 | ✅ 拒绝 |
| `predict` 空历史 | ✅ 返回 0.0（冷启动） |
| `now` 为 naive datetime | ✅ 抛 ValueError（提示需 tz-aware） |
| `window_minutes=0` | ✅ 抛 ValueError |
| `window_minutes=1e9` | ✅ 返回 0.0 且在 [0,1] |
| `automation_id` 空/None/非字符串 | ✅ 全部抛 ValueError |

**我原以为会在这里找到时区/越界 bug，结果全部正确处理。**

### `af_bus` 去重与熔断 —— 正确

`_seen` 有界、熔断参数合理、自定义事件跳过熔断是设计意图。

---

## 五轮累积视图

| 轮次 | 面 | 核心发现 | 强度 |
|---|---|---|---|
| 一 | 全量 | `homesdk` 未声明、`instance.id`、canary 序列化 | **P0 ×2** |
| 二 | 生命周期/资源 | SAFE HALT `logger` 未定义 → tick 线程静默死亡 | **高** |
| 三 | 表达式求值 | `min`/`max` 混类型绕过 soft-fail | **中高** |
| 四 | 静态闸/异常吞噬 | 目录截断、undo 策略不一致 | **中 / 低** |
| 五 | 未审面（偏好/预测/总线） | **`PreferenceModel` O(N²)** | **P1** |

### 关键观察：衰减曲线被打断了

第四轮我说"边际收益会持续下降"——**第五轮推翻了这个判断**。

原因是第四轮审的是**已审面的邻近区域**，而第五轮转向了**前四轮完全没碰过的模块**（`af_preference` / `af_predict` / `af_bus`）。**未审面里仍有实打实的东西。**

**"还能找到 bug 吗"的正确答案不是"强度衰减"，而是"取决于还剩多少处女地"。**

### 跨五轮仍未修复

1. **`instance.id`（`af_scheduler.py:343`）** —— 连续四轮确认未修复，唯一确定的 P0
2. **设备目录截断（`af_orchestrator.py:916`）** —— 第一轮提出，第四轮回归确认仍在
3. **门禁缺位** —— 前两轮那两个 bug 都是一行 linter 能拦住的
4. **"只增不减"一族** —— 第一轮 `AuditLog`、本轮 `PreferenceModel._records` / `bus._last_state`

---

## 未覆盖声明

14 个单元中 **9 个 `out_of_scope` 未审**：HTTP 鉴权、MCP 授权码、`af_evo`、`af_service` 会话、`af_instance` 终态回收、`af_executor` canary、CLI 输入面、`af_vhass` 仿真、`af_adapters` 出向。

**五轮合计：仍未审的面集中在 `af_evo`(1474) / `af_service`(2102) / `af_instance` / `af_executor` / `af_adapters`。**

按第五轮的经验（处女地有回报），**下一轮建议直接打 `af_evo` + `af_service`**——这两个合计 3576 行，从未审计过。

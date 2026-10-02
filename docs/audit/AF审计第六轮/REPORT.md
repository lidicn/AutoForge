# AutoForge 第六轮审计报告（稳定性 / 功能性）

> 本轮 skill：`anthropics/knowledge-work-plugins` → `engineering/skills/code-review`
> 校验器（沿用 security-audit 机器可读契约）：`coverage-ledger` PASS（12）｜`findings` PASS（2）｜状态 `complete`

## 本轮方向（自主选定）

第五轮结尾我判断"下一轮应直打处女地"。本轮据此选定 **`af_evo.py`(1474) + `af_service.py`(2102)** —— 合计 3576 行，前五轮从未审计。

审计口径沿用你要求的「稳定性 + 功能性」，采用新装的 `code-review` skill，重点取 **Performance**（O(n²)、资源泄漏、无界缓存）与 **Correctness**（边界、类型安全、语义缺陷）。

## 核心发现

**`af_service.create_session` 不触发 `_purge_sessions`，会话 TTL 清理只挂在读取路径。**

实测（TTL 缩短为 0.5s 便于观测）：创建 50 个会话 → 等待 1.2s 超 TTL 且不读取 → `_SESSIONS` **仍为 50** → 调用 `list_sessions()` → 立即清零。

严重度来自条目权重：每个会话持有 `Runtime`（完整运行时对象图）+ `FakeHA` + `Graph`，非轻量条目。触发场景：创建后用户放弃，或批处理脚本循环创建不查询。

**修复只需一行**：在 `create_session` 开头加 `_purge_sessions()`。

## 第二项（语义 hardening）

`af_evo._jaccard(∅,∅) = 1.0` —— 两条**都缺 trigger** 的自动化被判为 100% 相似，而两条真实不同的自动化（不同房间传感器）仅 0.500。把「共同缺失」等同于「完全相同」，在阈值化去重决策中可能误合并。

## 一个有价值的对比判断

同为 O(n²)，两轮结论相反：

| | 第五轮 `af_preference` | 第六轮 `_max_jaccard` |
|---|---|---|
| 触发频率 | **每次写入**全量重写 | 一次性计算 |
| 规模上界 | 无上限 | 典型数十条 |
| 实测 | 4001 条 → 119.51s | n=100 → 1.86ms |
| 结论 | **P1 缺陷** | 非缺陷 |

**判断 O(n²) 是否构成缺陷，关键不在复杂度本身，而在触发频率与规模上界。**

## 方法论：第五轮的修正在本轮再次成立

第五轮我推翻"边际收益衰减"、提出"取决于还剩多少处女地"。本轮在五轮未碰的 3576 行里即命中缺陷 —— **已审面衰减、未审面仍有产出，两轮连续成立。**

## 跨六轮最值得说的一条

**"缓存/记录只增不减"已在 5 个不同模块复现**：`AuditLog`（一轮）、`bus._last_state`（五轮）、`PreferenceModel._records`（五轮）、`_SESSIONS`（六轮）。

这不是偶发 bug，是**缺一个统一的生命周期约定**。建议引入统一的 `BoundedCache` 基类 + 强制 TTL/上限声明，比逐个修补更根本。

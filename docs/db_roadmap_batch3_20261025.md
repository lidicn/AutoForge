# 豆包（db）执行路线图 · 第三批（2026-10-25，第二批已交付）

> **PM：Claude（本会话）· 开发：豆包（db）**
> 前置：第二批（DB-05/DB-06）已交付，PM 复核通过；可观测性 pin 由 PM 补（`7dffcf0`）。
> **动本批前必读第一批的 §1.3.1（台账同批）、§1.7（判据腿顺序污染）、§1.8（gates.sh 绿 ≠ 单测绿）、§1.9（台账 regen ≠ 测试 pin）**——那四条是两批实测换来的。

---

## 0. 本批背景与切活

第二批两项（G10 实施 + G3 仿真建模）已交付。剩余缺口：**G3 生产核验、G6、G7**。
本批只派 **DB-07 = G6 主体透传**。G3 生产核验要连生产容器（不是代码活），G7 会在
`af_draft.py` 首次引入写盘代码（撞原子写门 + 有界缓存门 + 卫生清单）——两项都 PM 自留。

### 0.1 PM 对 G6 的现读修正（第二批 §3 的理由不精确，以此为准）

第二批我把 G6 写成"断点 `af_mcp.py:411` `_t_apply(store, args)` 不接 `current`"，
并据此判"撞 `_TOOLS_WITH_CONTEXT` 语义 ⇒ PM 接"。**现读后发现这个判断过于粗糙**，
真实结构是**三层**，且能力层早已就绪：

| 层 | 位置 | 现状（现读确认） |
|---|---|---|
| 1 · MCP 面 | `af_mcp.py:411` `_t_apply(store, args)` | 无 `current` 参数，且不在 `_TOOLS_WITH_CONTEXT`（`af_mcp.py:546` 只列 5 个） |
| 2 · 编排层 | `af_apply.py:200` / `af_apply.py:394` | `submitted_by="af_apply"` 硬编码；`authenticated_subject` 未传 |
| 3 · 服务层 | `af_service.py:677` | **`authenticated_subject` 参数已存在**（P0-13 防伪造，`:705` `effective_submitter = authenticated_subject or submitted_by`） |

⇒ G6 不是"发明机制"，是**把第三层已经设计好的防伪造接口接到第一/二层**。
`af_service.py` 不用动。

### 0.2 为什么本项可派（隔离度现读核过）

| 文件 | 可观测性 | 进程模型 | 信任边界 | 发布线 | 卫生 | 结论 |
|---|---|---|---|---|---|---|
| `src/autoforge/af_apply.py` | 0 | 0 | 0 | 0 | 0 | **零锚点**，主体 |
| `src/autoforge/af_mcp.py` | 1 | 0 | 0 | 0 | 0 | 仅 1 个真锚点，见 0.3 |
| `src/autoforge/af_service.py` | 45 | 13 | 0 | 0 | 0 | **本项不改它**（参数已存在） |

### 0.3 ⚠️ af_mcp.py 那唯一锚点在哪（决定台账要不要同批）

`docs/可观测性清单.md:149` 与 `:277` 各一行：
```
| DRAFT_FAILURE_RECORD_SKIPPED | DEBUG | src/autoforge/af_mcp.py:407 | _t_draft |
```
（`:81` 是汇总行，不是独立锚点。）

**本项的改动区在 `_t_apply`（:411 起）与 `_TOOLS_WITH_CONTEXT`（:546），两者都在 407 的下方。**
⇒ 若你在 `_t_apply` 上方加行，这个锚点会漂移 ⇒ **必须同批带上台账**（§1.3.1）。
**在它下方加行则不会漂。** 但这不由你赌——按 §1.3.1，**改完先跑一次门看它报不报漂移**，
报了再 `--write` 同批补。⛔ 不要预先猜。

### 0.4 现成先例（照抄形状，不要发明）

`af_mcp.py:203-204` 的 `_t_import` 就是同一形状的既有实现：
```python
def _t_import(store, args, current=None):
    owner = (current or {}).get("subject", "") or ""   # R20-01: 导入操作者归属
    return svc.import_store(store, ..., ...)
```
G6 就是把 `_t_apply` 改成同款，并把 subject 一路传到 `submit_pending`。

---

## 1. DB-07 · G6：af_apply 链路补认证主体透传

### 1.1 目标

MCP 面调 `af_apply` 入队的待批条目，其提交人必须是**认证主体**（token subject），
而不是硬编码的 `"af_apply"`。`submit_pending` 的 P0-13 防伪造口径
（`authenticated_subject` 覆盖 `submitted_by`）必须在这条路上生效。

### 1.2 改法（三层，逐个来；⛔ 不改 `af_service.py`）

**第一层 · `af_mcp.py`**
1. `_t_apply` 加 `current` 参数，形状照 `_t_import`：
   ```python
   def _t_apply(store, args, current=None):
       subject = (current or {}).get("subject", "") or ""
       ...
       return apply(ref, stage=stage, store=store, authenticated_subject=subject or None)
   ```
2. 把 `_t_apply` 加进 `_TOOLS_WITH_CONTEXT`（`af_mcp.py:546`）。
   ⚠️ 这是 `frozenset({...})` 字面，**加一个名字即可，不要改它的类型**。

**第二层 · `af_apply.py`（主体，零锚点）**
1. `apply()` 加参数 `authenticated_subject: str | None = None`。
   - ⚠️ **位置有讲究**：`tests/unit/test_af_premiere.py:226` 有一条签名钉腿
     断言 `sig.parameters["premiere_code"].default is None`。它只钉 premiere_code，
     你加参数不会破坏它——**但你要知道这条腿存在，别顺手改它。**
   - **必须给默认值 `None`**（向后兼容 `af_test.py:200` 等既有调用）。
2. 传给 `af_service.submit_pending(...)`：`authenticated_subject=authenticated_subject`。
3. `apply_group()`（`af_apply.py:394` 附近）**是否也要传**：先想清楚再动。
   group 路径的提交人语义与单 automation 不同（有 `group_ref`）。
   **PM 默认裁决：本项只改单 automation 路径（`apply()`），`apply_group` 不动。**
   若你认为 group 也该透传，写 blockers 说明理由，PM 裁。⛔ 不要两种都做。

### 1.3 ⛔ 不许碰

- `af_service.py`（58 个锚点，且本项不需要改它——参数已存在）
- `af_canary.py` / `af_executor.py`（第二批 §1.6 延续）
- `tests/unit/test_af_premiere.py:226` 那条签名钉腿
- 一切第一批 §1.3 禁区

### 1.4 判据腿（先写，必须红）

新建 `tests/unit/test_af_apply_subject_passthrough.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | MCP 层：持令牌调 af_apply | 入队条目的提交人 = 令牌 subject，**不是** `"af_apply"` |
| 2 | `apply()` 直接调，不传 `authenticated_subject` | 行为不变（向后兼容；`submitted_by` 仍为 `"af_apply"`） |
| 3 | `apply()` 传 `authenticated_subject="alice"` | `submit_pending` 收到 `authenticated_subject="alice"`（可 mock 断言） |
| 4 | 无令牌身份（`current=None`） | 不崩；落到既有默认路径（诚实降级，不许假绿） |
| 5 | `_t_apply` 在 `_TOOLS_WITH_CONTEXT` 里 | 真调用一次 `_t_apply(store, args, current)` 确认三参形态可用 |
| 6 | 伪造尝试 | 调用方传的 `submitted_by` 与认证 subject 不一致时，**认证方赢**（P0-13 口径） |

腿 1/3 是本项的正面证据；腿 2/4 是向后兼容护栏；腿 5 是接线腿（最容易漏）；
腿 6 是安全腿——**P0-13 的防伪造语义不能因为这次接线被削弱**。

### 1.5 ⚠️ 本项最大的风险：`_TOOLS_WITH_CONTEXT` 改动的连带面

`af_mcp.py:1085` 的调用点是：
```python
if fn in _TOOLS_WITH_CONTEXT:
    result = fn(store, args, current)
else:
    result = fn(store, args)
```
把 `_t_apply` 加进去 ⇒ **所有走 MCP 面的 af_apply 调用都会多收一个 `current`**。
必须自检既有 af_apply 相关测试无回归：
```bash
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "apply or premiere or mcp or save or pending"
```
红了就停下来写 blockers，**不要靠改断言收场**。

### 1.6 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_af_apply_subject_passthrough.py -q
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "apply or premiere or mcp or save or pending"   # §1.5 风险自检
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "draft or canary or watch"                      # 顺序污染自检（§1.7）
GATES_PYTHON=python bash gates.sh                                                                    # 贴完整输出；若报 af_mcp.py:407 漂移 ⇒ 按 §0.3 + §1.3.1 同批补台账（含 CRLF 修复）
```

---

## 2. 交付格式（同第二批，另加一条）

`docs/db_task_receipt.md` 追加：

```markdown
## DB-07 <标题>
- 判据腿：<文件>（N 条；修前红→修后绿）
- 改了哪些文件：<逐个列，标注是否在禁区清单里>
- `apply()` 新参数的位置与默认值：<贴在签名哪一行>
- `_TOOLS_WITH_CONTEXT` 前后形状：<加名字前后各贴一次>
- af_mcp.py:407 锚点是否漂移：<漂了/没漂；漂了贴 --write 后的台账 diff>
- gates.sh 退出码 + 完整输出
- 新增 except-pass：0（若 >0 说明为什么）
- 顺序污染自检 + §1.5 风险自检的输出
- 我没做的事：<明确列出，特别是 apply_group 动没动>
```

**新增硬要求**：`apply_group` 动没动、为什么，必须单独写一段。PM 默认裁决是不动，
你要推翻就得给理由。

---

## 3. 时间盒

单项，预估中。**卡住超 30 分钟**写 `docs/db_roadmap_blockers.md`
（文件路径 + 卡在哪一行 + 试过什么）。

**PM 自留项（不派，本批继续）**：
| 项 | 原因 |
|---|---|
| G3 生产核验 | 要连生产容器核 HA 是否真有 `set_state` 服务 |
| G7 staging 持久化 | `af_draft.py` 首次引入写盘 ⇒ 原子写门 + 有界缓存门 + 卫生清单 |

# AutoForge 第十二轮审计报告：候选分诊（1224 → 2）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 输入：`ADM-auditkit` 第十一轮产出的 1224 条候选
> 本轮主题：**候选分诊**——把工具产出按第十一轮归纳的三条判据自动降噪，再逐条实测
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

第十一轮用 ADM-auditkit 跑出 1224 条候选，同时发现规则级精确召回只有 5/9。第十二轮做两件事：**用第十一轮归纳的"方向不对称"认识反过来做降噪**，然后**对 P0 逐条实测**。

**结果：162 条（AFS-01 79 + AFS-04 83）→ 自动分诊出 P0 22 条 → 人工实测确认 2 个缺陷。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R12-01** | 🔴 High | **`json.loads(line)` 在 try 之外——一行损坏永久废掉整个指标缓冲区** | `af_metrics.py:218` |
| **R12-02** | 🟡 Medium | **损坏的待批文件成为永久僵尸**：列表不可见、load 与"不存在"无法区分、清扫也清不掉 | `af_pending.py:154/168` |

### 最值得说的一件事

R12-01 的对照实现就在同一个仓库里，而且**带着说明理由的注释**：

```python
# af_telemetry.py:190-191  ✅ 正确
    except ValueError:
        kept.append(line)  # 坏行保留（不静默丢数据）

# af_metrics.py:218  ❌ 缺陷
    metric = json.loads(line)      # ← 无 try，坏行直接炸穿整个 flush
```

**同一形状（JSONL 逐行解析）、两个文件、相反实现，正确的那个还写了注释。**

这不需要论证"这算不算缺陷"——对照本身就是证据。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 八 | 全门禁 + 测试套件 | 3 |
| 九 | 配置面 | 2 |
| 十 | 持久化层 | 2 |
| 十一 | 工具切换 + 召回率验证 | 2（auditkit 自身） |
| **十二** | **候选分诊（1224 → 2）** | **2** |

### 新增资产

**`scripts/triage_round12.py`** —— 候选自动分诊器。三档判据：

| 档 | 判定 | 作用 |
|---|---|---|
| **T1 留痕档** | 失败分支有没有 log / raise / 计数 | SILENT（真静默）vs TRACED（观察项） |
| **T2 放大档** | 读取结果是否进入 write 路径 | AMPLIFIED（RMW，永久丢失）vs READONLY |
| **T3 作用域档** | 模块顶层（导入即失败=可见）vs 运行期 | 区分 fail-closed 与真静默 |

按 `(T2放大 + T1静默 + T3运行期)` 排序，P0 通常只剩 20 条左右。

### 四个新范式（已记入 lessons 第 39–42 条）

**39** 拿到大批量候选先做三档自动分诊，人工只看 P0
**40** **"留痕"的形式不止 log——毒化标志比 log 更强**（见第三节的误判复盘）
**41** 同一 shape 在同一仓内的对照实现，是确证缺陷最快的证据
**42** "坏行跳过"的两种错法：静默丢 vs **炸穿整批**（后者更隐蔽）

---

## 三、分诊结果

```
分诊 162 条（AFS-01-silent-failure + AFS-04-unguarded-load）

    22  P0 静默 + 写入放大 + 运行期     ← 人工实测范围
   125  P2 静默 + 运行期
    15  P9 有痕（观察项）
```

### 一次误判复盘（值得单独记）

分诊器把 `af_auth.py:240 _load_revoked_file` 判为 **P0 SILENT**——因为它 except 分支里没有 log。

**这是误判。** 实际代码：

```python
try:
    data = json.loads(self._revoked_path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    # 撤销黑名单读不出来 ≠ 名单为空。此处若按"空名单"继续跑，被撤销过的令牌
    # 会全部复活（fail-open）。置位后 `authenticate()` 对任何令牌都返回 None
    self._revoked_poisoned = True
    return
```

它置了毒化标志，随后 `authenticate()` 对**所有令牌**返回 None——**比打日志强得多**（日志可能没人看，毒化标志一定生效）。

**教训**：判"有没有留痕"必须同时认 **log / raise / 计数 / 状态标志置位** 四类。只认 log 会把最健壮的实现误报成缺陷。

这处也是 AutoForge 里处理得**最好**的一处留痕——注释还明确写出了"读不出来 ≠ 名单为空"的推理。

---

## 四、确认缺陷

### 🔴 R12-01　`json.loads(line)` 在 try 之外——一行损坏永久废掉指标缓冲区

**位置**：`src/autoforge/af_metrics.py:205-231`（`Ingester.flush_buffer`）

```python
lines = [ln.strip() for ln in buf.read_text(encoding="utf-8").splitlines() if ln.strip()]
remaining: list[str] = []
flushed = 0
for line in lines:
    metric = json.loads(line)           # ← 218 行：在 try **之外**
    try:
        self._post_with_retry(metric, ma_url, token, http_post=http_post)
        flushed += 1
    except Exception:                    # 只兜 POST 失败
        remaining.append(line)
```

**实测**（构造 3 条正常 + 1 条截断的缓冲区）：

```
缓冲内容（4 行，最后 1 行截断）:
  1: {"i": 0, "metric": "x"}
  2: {"i": 1, "metric": "x"}
  3: {"i": 2, "metric": "x"}
  4: {"i": 4, "metric": "half_wr

❌ flush_buffer 抛异常: JSONDecodeError: Unterminated string starting at: line 1 column 20

缓冲文件还在: True | 行数: 4
⇒ 3 条正常指标因 1 行损坏而**永远发不出去**
```

**为什么是"永久"**：`remaining` 只收集"POST 失败"的行。坏行在 `json.loads` 就抛出，**永远不会进入 `remaining`**，也就永远不会被跳过或移除 ⇒ **每次 flush 都在同一行崩溃，缓冲区永久失效**。

**可达性**：`_buffer()` 用 `f.write(json.dumps(metric) + "\n")` **追加写、无 fsync**。进程被 SIGKILL（OOM killer / 容器驱逐 / 断电）中断在写入中途，就会留下截断的最后一行。而**缓冲机制存在的理由恰恰是"推送失败/环境不健康"**——最需要它的时刻，也正是最容易产生半行的时刻。

**影响面**：唯一调用点是 `af_cli.py:1088`，**没有 try 包裹**：

```python
result["flush"] = ingester.flush_buffer(ma_url=target, token=tok)
```

→ CLI 命令整体崩溃退出。前面 `push()` 已经完成的本次快照推送结果也一并丢失（命令非 0 退出，调用方只看到失败）。

**对照实现**（`af_telemetry.py:188-192`，同一形状的正确写法）：

```python
for line in lines:
    try:
        item = json.loads(line)
    except ValueError:
        kept.append(line)  # 坏行保留（不静默丢数据）
        continue
```

**修复建议**（与 telemetry 对齐）：

```python
for line in lines:
    try:
        metric = json.loads(line)
    except ValueError:
        logger.warning("指标缓冲行解析失败，隔离到 .bad：%.80s", line)
        bad.append(line)
        continue
    try:
        self._post_with_retry(...)
        flushed += 1
    except Exception:
        remaining.append(line)
```

（把坏行隔离而非原地保留，否则下次仍会在同一行崩——telemetry 的"保留"是配合其"不删即留"语义，metrics 这里需要主动隔离。）

---

### 🟡 R12-02　损坏的待批文件成为永久僵尸

**位置**：`src/autoforge/af_pending.py:154-158`（`list`）、`168`（`load`）、`186-193`（`_sweep_locked`）

三处都是 `except (OSError, ValueError)` 后 `continue` / `return None`，无日志。

**实测**：

```
── op-1 损坏（截断）──
  list(): ['a476d68957d54a1b', 'a5e41fa99cd14234'] ← op-1 从列表消失
  load('op-1'): None   ← 与『文件不存在』完全无法区分
  _sweep_locked(0) 删除 2 条
  剩余: ['op-1.json']
  ⇒ 损坏文件既不可见、也不可清扫 ⇒ 永久僵尸
```

**三重死角**：
1. `list()` 跳过 → **审批界面看不到这条待批**
2. `load()` 返回 None → 报 404「未找到待批操作」而非"文件损坏"，**误导排查方向**
3. `_sweep_locked` 解析不出 `submitted_at` → 跳过 → **TTL 清扫永远清不掉它**

**严重度为什么是 Medium 不是 High**：方向是 **fail-closed**——损坏的待批无法被批准（payload 读不出来），不会绕过审批。危害是"可见集合小于实际集合，且差值不可知"（第二轮那个静默漏项族），不是安全闸绕过。

**修复建议**：读失败时向 `.corrupt` 隔离（`af_predict.py:910` 已有此范式）并计一条 warning；`load()` 区分"不存在"（返回 None）与"损坏"（抛明确异常）。

---

## 五、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **`af_auth._load_revoked_file`** | 读实现 | ✅ **处理得最好**——置 `_revoked_poisoned` 毒化标志，`authenticate()` 对全部令牌拒绝，注释写明"读不出来 ≠ 名单为空"（fail-closed 教科书写法） |
| **`af_telemetry.py:190` 坏行保留** | 读实现 | ✅ 正确范式，且带说明理由的注释 |
| **`af_flock.held_by_other`** | 读实现 | ✅ `except (BlockingIOError, OSError): return True` —— 拿不到锁**保守认定为"被别人持有"**，方向 fail-closed，正确 |
| **`af_catalog._record_bucket`** | 读实现 | ✅ `except Exception: return` 外层注释"遥测失败绝不影响解析本身"，且外层有 `data = {}` 兜底；遥测计数丢失不影响主流程，属有意设计 |
| **`af_service.approve_pending` 自批检查** | 读实现 | ✅ `reviewer == submitter` 检查在位（第一轮 HIGH-2 已确认其"不同 subject 令牌可绕过"的局限，此处不再重复） |

---

## 六、已排除（rejected）

| 候选 | 数量 | 理由 |
|---|---|---|
| **P9 有痕类** | 15 | 失败分支有 log/raise，属正常降级 |
| **P2 中绝大多数在 `scripts/`** | 约 40 | 这些是**审计门禁自身**的解析兜底。门禁脚本读失败返回空，多数配合 `exit 2`（射程失败）使用——第八轮已确认这套 rc 约定是对的。逐个实测性价比低，留作后续 |
| **P0 中 `scripts/` 下的 5 条** | 5 | 同上：门禁的"静默"多数由上游 `exit 2` 兜住，与 src 运行期静默不同档 |
| **`af_catalog._record_bucket`** | 1 | 见第五节，有意设计 |

---

## 七、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R12-01 坏行隔离 | 约 6 行 | 一行损坏永久废掉缓冲区，且 CLI 会崩 |
| **P2** | R12-02 待批损坏留痕 + 隔离 | 约 8 行 | 永久僵尸 + 误导性 404 |

**回归验证清单**：
1. 缓冲区含 1 行截断 → `flush_buffer` 应**正常返回**（其余行发出），坏行被隔离且不再阻断
2. 连续跑两次 flush → 第二次不应再崩
3. 损坏一个 pending 文件 → `list()` 应有 warning，`_sweep_locked` 应能清掉它
4. 修好后：`af_auth` 的毒化标志测试仍应通过（防止改坏最好的那处）

---

## 八、十二轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十一 | 生命周期 / 门禁 / 测试 / 配置 / 持久化 / 工具验证 | 11 |
| 十二 | 候选分诊 | 2 |

**"已修一处、未铺开"这个模式，十二轮里出现了七次：**

| 轮次 | 修对的那一处 | 没铺开的地方 |
|---|---|---|
| 三 | 表达式有 `MAX_EXPR_DEPTH` | 图遍历无深度上限 |
| 四 | `af_store`/`af_catalog` 用 FileLock | `af_pending` 只用进程内锁 |
| 六 | 漂移检测分 `unmodeled` 档 | 回滚结果无条件说"已自动回滚" |
| 八 | 2 个门禁遵守 rc=2 | 2 个崩溃报 rc=1 |
| 九 | `af_bus._env_number`（P1-7） | 其余 6 处裸转换 |
| 十 | `af_persist` 校验和档有 warning | 同函数 JSON 解析档没有 |
| **十二** | **`af_telemetry` 坏行保留（带注释）** | **`af_metrics` 坏行炸穿整批** |

第十二轮这条有个新特点：**正确的实现连注释都写好了**——"坏行保留（不静默丢数据）"。这说明团队不仅知道，还能说清为什么。但知识停在写它的那个人和那个文件里。

**一个可以立刻验证的猜测**：既然 telemetry 写对了、metrics 没写，那全仓**其他 JSONL 逐行解析的地方**呢？本轮只分诊了 AFS-01/AFS-04 两族（162/1224）。建议下一轮专门做一次"同形状横向扫描"——**把已确认的正确实现当模板，反查同形状的所有调用点**。这比写新规则快，也准。

**给工程团队的一句话**：你们不缺正确的实现，缺的是把"正确的实现"变成唯一入口。telemetry 那行注释如果当初被抽成 `iter_jsonl(path, on_bad="skip"|"keep"|"quarantine")` 放在公共模块，metrics 这里就不可能有第二种写法。**第三次、第四次遇到同一形状时，就该抽 helper 了**——现在已经有七次了。

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **候选自动分诊器（T1/T2/T3）** | **`audit-env/scripts/triage_round12.py`** |
| 分诊结果明细 | `audit-env/reports/round12-triage.json` |
| 误报修正手册（38 条 + 本轮 4 条 = **42 条**） | `audit-env/scripts/lessons-round2.md` |
| ADM-auditkit（含自建 CLI） | `/data/workspace/adm-auditkit/ADM-auditkit-main/auditkit` |
| 第十一轮产物（1224 条候选） | `.../projects/AutoForge/round-011/registry/` |
| 持久化 / 配置面扫描器 | `scan_round10.py` · `scan_round9.py` |
| 门禁变异测试台 · 侵入式变异 | `mutation_gates.py` · `mutation_invasive.py` |

### 本轮新增 lessons（39–42）

- **39** 大批量候选先做三档自动分诊（留痕/放大/作用域），人工只看 P0
- **40** **"留痕"的形式不止 log——毒化标志比 log 更强**；只认 log 会把最健壮的实现误报成缺陷
- **41** 同一 shape 在同仓内的对照实现 = 确证缺陷最快的证据
- **42** JSONL 逐行解析两种错法：静默丢（数据消失）vs **炸穿整批（永久自持，更隐蔽）**

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 3 组（指标缓冲永久失效 / 待批永久僵尸 / 分诊器 162 条） |
| 分诊器口径 | `state_defects` 的 AFS-01（79）+ AFS-04（83）；其余 1062 条候选尚未分诊 |
| AutoForge 仓库状态 | 干净（探针与变异均已还原） |

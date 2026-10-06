# AutoForge 第四轮审计报告：并发与状态一致性

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 77,949 行）
> 本轮主题：**并发与状态一致性（TOCTOU / 跨进程 / fd 生命周期）**
> 判定标准：严格档 —— **实测复现**（多线程 barrier / 多进程 workers PoC），疑似项单列排除
> 报告日期：2026-10-06

---

## 一、执行摘要

前三轮分别是：安全/架构 → 异常吞咽/静默降级 → 递归/资源上限。第四轮换到**并发**这个此前完全没碰的维度。

**结论：找到 3 个缺陷，其中 1 个 Critical —— 一个在单线程下完全正常、只在并发时炸的 fd 双重关闭 bug。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R4-01** | 🔴 Critical | **`_atomic_write` 二次关闭 fd → 并发下误关别的线程刚分配的 fd** | `af_pending.py:94`（fdopen 在 `:84`） |
| **R4-02** | 🟠 High | per-agent 熔断可并发绕过：上限 5 实测落盘 18–25 条 | `af_pending.py:121-137` |
| **R4-03** | 🟡 Medium | 并发重复批准同一条待批：8 并发 → 6 次落盘 | `af_service.py:585-616` |

### R4-01 为什么值得单独说

它是这四轮里**唯一一个"单线程完全正常、并发才炸"的缺陷**，而且代码里的注释恰好记录了作者的误判：

```python
# af_pending.py:95-96
try:
    os.close(fd)  # 兜底关闭描述符：with 已关则吞 EBADF
except OSError:
    pass
```

注释认为"with 已关则吞 EBADF"是安全的兜底。**但 fd 号是会被复用的**——`os.fdopen(fd)` 把所有权交给文件对象，`with` 结束时就关了这个 fd；`finally` 里再 `os.close(fd)`，此时 fd 号可能已被同进程的另一个线程分配给完全不同的文件（甚至目录）。关掉的不是自己的，是**别人的**。

实测在 40 并发下观察到的错误：

```
NotADirectoryError   × 5   [Errno 20] Not a directory: '.../pending'
OSError              × 5   [Errno 9]  Bad file descriptor
IsADirectoryError    × 1   [Errno 21] Is a directory: 7     ← fd 7 指向目录
```

`IsADirectoryError: fd=7 是目录` 是最直白的证据——fd 7 被别处复用成目录描述符，这里又去关它。

### 一个重要的正面发现

`af_pending.py` 的问题**不是项目整体的并发纪律差**。横向对比后恰恰相反：

| 模块 | 持久化 | 并发保护 |
|---|---|---|
| `af_store.py` | 版本化文件 | ✅ `FileLock(directory/".lock", timeout=10.0)`（`:171`） |
| `af_catalog.py` | 设备目录 | ✅ `FileLock` |
| `af_persist.py` | 崩溃恢复 | ✅ `atomic_write_text` + SHA256 校验 + 租约仲裁 `claims()` |
| **`af_pending.py`** | **磁盘队列** | ❌ **仅 `threading.Lock()`** |

**`af_pending.py` 是 28 个含写操作文件里唯一"磁盘队列 + 仅进程内锁"的组合**。它不是"大家都没做"，是"大家都做了，就这一个漏了"——这正是横向对比能找到它的原因。

---

## 二、工作流迭代：这一轮改了什么

### 主题轮换

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 第一轮 | 安全 / 架构 / Agent 威胁模型 | 5 |
| 第二轮 | 稳定性 / 功能性（异常吞咽） | 7 |
| 第三轮 | 递归 / 资源上限 / 降级 | 3 |
| **第四轮** | **并发 / 状态一致性 / fd 生命周期** | **3** |

前三轮的静态分析能力已经很强（D01–D12、E01–E13），但**都是单线程视角**。第四轮补上并发维度。

### 三个新范式（可复用到后续轮次）

**1. 契约对比法——不看单点代码，看"代码的假设"与"运行环境"是否匹配**

`af_pending.py:67` 的类注释写着：`待批队列存储（JSON 文件，单目录锁串行，pending 规模小足够）`。

"单目录锁串行"这个假设在**多进程**下不成立。而实际运行环境是：

- `uvicorn.run` 未指定 workers，但 **FastAPI 的同步 `def` 路由跑在线程池**（`af_api.py:414/421` 的 `/api/pending/*` 都是同步 `def`）
- CLI（`af_cli.py:721`）、watch 进程、MCP 服务可**多进程**并发访问同一个 store root

**代码假设单进程，环境是多进程多线程** → 对比即发现。

**2. 横向对比找异常值**

不逐文件审，而是**先建立同类项的基线，再找偏离基线的那个**。本轮：28 个含写操作的文件里，24 个全文无 `FileLock`，但绝大多数是纯内存结构（无需跨进程锁）。用"是否有磁盘持久化"二次筛选后，`af_pending.py` 成为唯一异常值。

这比"逐个看 28 个文件"快得多，也更不容易漏。

**3. 实测驱动 + 异常分类统计**

静态扫描只能说"这里可能有问题"，实测才能说"这里确实坏了，坏成这样"。

本轮的两次关键突破都来自**对异常的细致分类**：初看并发测试报了 22 个异常，很容易当成"测试脚本自己的问题"而忽略。分类统计后发现是 **3 种不同的真实错误**（`NotADirectoryError` / `Bad file descriptor` / `IsADirectoryError`），才顺藤摸瓜挖出 fd 二次关闭。

### 新增扫描器与一个自身的 bug 修正

`scripts/scan_round4.py`（F01–F05）：

```
F01  fd 二次关闭（fdopen 后再 close）      ← 直接命中 R4-01
F02  check-then-act（TOCTOU）
F03  磁盘持久化 + 仅进程内锁
F04  每调用新建 Store 实例 → 实例锁失效
F05  被写入的模块级可变全局无锁
```

**踩到的坑（已修正并记入 lessons）**：F01 初版检出 **0 条**，但 `af_pending.py` 明明有这个模式。原因是 `own_walk` 用 LIFO 栈遍历，`os.close(fd)`（94 行）**早于** `os.fdopen(fd)`（84 行）被访问，检查时 `fdopened` 还是空的。

**修正**：改为两遍扫描——先收集所有 `fdopen` 的 fd 名，再匹配 `close`。修正后精确命中 `af_pending.py:94`，且**全仓仅此一处**。

> 这类"遍历顺序依赖"是 AST 分析里的隐蔽陷阱：LIFO 栈会打乱源码顺序，任何依赖先后关系的检测都必须两遍扫描。

---

## 三、确认缺陷

### 🔴 R4-01　`_atomic_write` 二次关闭 fd → 并发下误关他人 fd

**位置**：`src/autoforge/af_pending.py:94`（fdopen 在 `:84`）

```python
def _atomic_write(self, path: Path, data: Mapping[str, Any]) -> None:
    # R-19：随机 tmp 名（避免并发写同名 .tmp）；mkstemp 的 fd 必须显式关闭（AF-9 半修）
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:      # ← :84 所有权交给 f
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os_replace(Path(tmp_path), path)
    finally:
        try:
            os.close(fd)   # ← :94 二次关闭！with 结束时 f.close() 已关过
        except OSError:
            pass
```

**失效链条**（三步）：

1. `os.fdopen(fd)` 把 fd 所有权交给文件对象 → `with` 块结束时 `f.close()` 关闭该 fd
2. fd 号回到内核的空闲池 → **同进程另一个线程的 `mkstemp()`/`os.open()` 复用了这个号**
3. `finally` 里 `os.close(fd)` 关闭的是**别人正在用的 fd**

**实测复现**（真实 `PendingStore`，40 线程 barrier 齐发，上限 5）：

```
成功 25（上限 5）  ← 同时暴露 R4-02
异常分类:
    5  NotADirectoryError    [Errno 20] Not a directory: '.../pending'
    5  OSError               [Errno 9]  Bad file descriptor
    4  PendingLimitExceeded  （正常拒绝）
    1  IsADirectoryError     [Errno 21] Is a directory: 7
```

**异常落点**（完整堆栈归属）：

| 落点 | 次数 |
|---|---|
| `af_pending.py:84` `os.fdopen(fd, ...)` → `Bad file descriptor` | 3 |
| `af_pending.py:124` 的 `glob` → `NotADirectoryError` / `Bad file descriptor` | 8 |
| `os.py:1030 fdopen` → `Bad file descriptor` | 1 |

**为什么后果比"抛异常"更严重**：它关掉的可能是**同进程内任何代码刚分配的 fd**——`af_store` 的 FileLock、`af_catalog` 的读取、HTTP 连接、日志文件。这类故障的表象是"某个无关模块间歇性报 `Bad file descriptor`"，**极难定位**，因为破坏者和受害者不在同一处代码。

**单线程 vs 多线程**：单线程下第二次 close 只抛 EBADF 被吞，**永远无症状**——这也是它能长期存活的原因。

**修复**：删掉 `finally` 里的 `os.close(fd)`。`os.fdopen` 已接管所有权，`with` 会正确关闭。若担心异常路径泄漏，用 `try/except BaseException` 包住 `os.close(fd)` **在 fdopen 之前**的路径（本例中 mkstemp 成功后立即进入 try，无此风险）。

```python
fd, tmp_path = tempfile.mkstemp(...)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:   # fd 由 f 全权管理
        ...
    os_replace(Path(tmp_path), path)
finally:
    # 不再 os.close(fd) —— f.close() 已处理
    if os.path.exists(tmp_path):
        try: os.unlink(tmp_path)
        except OSError: pass
```

---

### 🟠 R4-02　per-agent 熔断可并发绕过

**位置**：`src/autoforge/af_pending.py:121-137`

```python
with self._lock:                                    # :121
    self._sweep_locked(DEFAULT_TTL_S)
    if self.max_per_agent > 0 and self.pending_dir.is_dir():
        count = sum(                                 # :124  读：glob 数一遍
            1 for p in self.pending_dir.glob("*.json") if self._agent_of(p) == agent
        )
        if count >= self.max_per_agent:
            raise PendingLimitExceeded(...)
    op = PendingOp(...)
    self.pending_dir.mkdir(parents=True, exist_ok=True)
    self._atomic_write(self._path(op.op_id), _op_to_dict(op))   # :137  写
    return op
```

**两重失效**：

1. **实例锁无效**：`PendingStore` 在 `af_service.py:496/590/626`、`af_api.py:1107`、`af_cli.py:721` 都是**每次调用新建实例** → `threading.Lock()` 每次都是新锁，等于无锁。
2. **跨进程无效**：即使锁生效，`threading.Lock` 也管不了多进程。而 `{root}/pending/*.json` 是**跨进程可见的磁盘队列**。

**实测复现**（`max_per_agent=5`，同一 `agentA`）：

| 场景 | 返回成功 | 磁盘实际 | 绕过倍数 |
|---|---|---|---|
| 40 线程 barrier | 18 | 18 | **3.6×** |
| 40 线程（重复） | 25 | 25 | **5×** |
| 12 进程 × 60 次 | 7 | 7 | **1.4×** |

跨进程倍数较低是因为进程启动成本高、真实并发窗口窄，但**绕过成立**。

**后果**：per-agent 熔断（`DEFAULT_AGENT_LIMIT=20`，`af_pending.py:31`）意在防止单个 agent 灌爆待批队列。被绕过意味着这个保护在并发下失效——而 FastAPI 同步 `def` 路由走线程池，并发是**默认状态**而非异常状态。

**修复**：改用 `FileLock`，与 `af_store.py:171`、`af_catalog.py` 保持一致：

```python
with FileLock(self.pending_dir / ".lock", timeout=10.0):
    ...  # 计数 + 写入
```

---

### 🟡 R4-03　并发重复批准同一条待批

**位置**：`src/autoforge/af_service.py:585-616`（`approve_pending`）

```python
ps = PendingStore(store.root)
op = ps.load(op_id)                    # :591  读
...
submitter = str(op.get("submitted_by") or "")
if reviewer == submitter: raise ...
payload = op["payload"]
result = save_graph(...)               # :605  落盘（真机写入前的归档）
ps.delete(op_id)                       # :616  删除待批
```

`load`（591）与 `delete`（616）之间**无排他**，中间还夹着 `save_graph`。

**实测复现**（8 线程并发审批同一条 `op_id`）：

```
8 个并发审批同一条 op_id → approved 6 次，miss 2 次
```

**触发场景**：用户双击"批准"按钮、UI 重试、网络超时重试、多个审批人同时操作（项目是多令牌主体模型）。

**缓解因素（重要，避免夸大）**：`af_store.save` **有 `FileLock` 保护**（`af_store.py:171`），版本号自增是原子的，因此**不会损坏数据**——不会产生版本冲突或覆盖。后果是：

- 未传 `expect_version` → 多写 N−1 个**内容相同的冗余版本**（v1、v2…）
- 传了 `expect_version` → 第二个因版本不匹配抛 `WriteConflictError`，并记入 `write_conflicts.jsonl` 审计

**所以这是 Medium 而非 High**：有冗余数据，但无数据损坏，且有审计痕迹。

**修复**：把 `load → save → delete` 纳入同一个 `FileLock` 临界区；或在 `delete` 前做一次"带版本/状态条件的删除"（CAS 语义），失败即视为已被他人批准。

---

## 四、已排除（rejected）

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **F05 模块级可变全局无锁** | 106 | 判定过粗：把 `__all__` 列表等纯常量也算成"被写入的全局"。人工核查后仅 `_PRUNE_COUNTER`（`af_telemetry.py:46`）成立，**已在第二轮 BUG-05 报过**，不重复计入 |
| **`_cache`（`af_runtime_plugins.py` 等）** | — | 仅缓存 `importlib` 模块对象，良性竞态（重复 import 结果一致），非缺陷 |
| **`_CONFIGS` / `_ENTITY_HEALTH_CACHE` / `_SESSIONS`** | — | 均配了对应 `Lock`（`_CONFIGS_LOCK` / `_ENTITY_HEALTH_LOCK` / `_SESSIONS_LOCK`），非缺陷 |
| **`_check_blast` 爆炸半径检查** | — | 纯函数（无读改写），非 TOCTOU |
| **`af_scheduler.Quota` 配额** | — | 单进程内存计数，设计即如此；跨进程场景由 `af_flock` 单写者租约约束 |
| **`af_persist.py` 崩溃恢复** | — | 有 SHA256 校验、`mkstemp` 随机 tmp、租约仲裁 `claims()`、坏文件跳过且有 warning 日志。**本轮最扎实的模块** |
| **`WatchAggregator()` 每次新建**（F04） | 1 | 信息级提示；`af_watch.py:168` 在单进程内使用，无跨进程需求 |

---

## 五、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P0** | R4-01 删除 `os.close(fd)` | **1 行删除** | 收益最大、风险最低。改完 R4-01 后并发测试中的 `NotADirectoryError`/`IsADirectoryError` 应消失 |
| **P1** | R4-02 `af_pending` 改用 `FileLock` | 约 5 行 | 与 `af_store`/`af_catalog` 对齐；同时覆盖 R4-03 的临界区 |
| **P2** | R4-03 `approve_pending` 加 CAS 删除 | 约 10 行 | 消除冗余版本；若 P1 的 FileLock 覆盖了批准路径，可一并解决 |

**建议一次做完 P0+P1**：`af_pending.py` 的写入路径统一套 `FileLock`，顺带修掉 fd 双重关闭。这两处改完，整个待批队列的并发纪律就与项目其他模块持平了。

**回归验证清单**：
1. 40 线程 barrier 并发提交 → 成功数应 ≤ `max_per_agent`
2. 同上场景 → 不应出现 `NotADirectoryError` / `IsADirectoryError` / `Bad file descriptor`
3. 12 进程并发提交 → 成功数应 ≤ 上限
4. 8 线程并发批准同一 `op_id` → 只应有 1 次成功

---

## 六、四轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 | 主因 |
|---|---|---|---|
| 一 | 安全 / 架构 | 5 | 审批链路身份语义不严密 |
| 二 | 稳定性 / 功能性 | 7 | 异常被吞后不留痕 |
| 三 | 递归 / 资源上限 | 3 | 声明能力与实际能力脱节 |
| 四 | 并发 / 状态一致性 | 3 | 单进程假设 vs 多进程现实 |

四轮下来，**AutoForge 的工程质量是高的**——它自己就带着 7 轮人工审计的历史、写下了"门永远不可能红"的教训、`af_store`/`af_catalog`/`af_persist` 的持久化做得相当扎实。

但有一个反复出现的形状：**防护做得好，但覆盖不均。**

- 表达式求值有 `MAX_EXPR_DEPTH`/`MAX_EXPR_NODES`，图遍历没有（第三轮）
- `af_store`/`af_catalog` 用 `FileLock`，`af_pending` 只用 `threading.Lock`（本轮）
- CI 门禁杜绝了假绿，运行时代码的静默降级没有同标准（第二轮）

**建议**：把这几条从各自子系统里**提升为全局约定**并做成机器可校验的门禁——你们已经有 `check_imports.py` 和 `.gates.toml` 这套机制了，只差把"资源上限""失败必须留痕""磁盘状态必须 FileLock"写进去。

---

## 七、已沉淀的复用资产

| 资产 | 位置 | 内容 |
|---|---|---|
| 误报修正手册 | `audit-env/scripts/lessons-round2.md` | 6 条（作用域感知、宿主限定、必崩 vs 条件崩、`.get()` 非默认参数、递归防护分类、二分定位阈值） |
| 深度缺陷扫描器 | `audit-env/scripts/scan_round3.py` | E01–E13 |
| **并发缺陷扫描器** | **`audit-env/scripts/scan_round4.py`** | **F01–F05（F01 精确命中 R4-01，全仓唯一）** |
| 稳定性审计流程 | `audit-env/scripts/40-stability-audit.sh` | 支持 `STEP=` 分段执行 |
| 环境自检 | `audit-env/scripts/99-verify.sh` | 20 项全通过 |

### 本轮新增的两条 lessons（建议补入 lessons 文件）

**7. AST 遍历顺序依赖**：`own_walk` 用 LIFO 栈，源码顺序被打乱。任何"先 A 后 B"的检测（如 fdopen→close）必须**两遍扫描**，否则漏检。本轮 F01 初版检出 0 条即因此。

**8. 横向对比优于逐项审查**：先建立同类项基线（28 个写文件 → 24 个无 FileLock），再用业务属性二次筛选（是否有磁盘持久化），异常值自然浮现。比逐个审 28 个文件快且不易漏。

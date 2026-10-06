# AutoForge 第十七轮审计报告：契约声明验证（claim vs implementation）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**从"代码自己声明的不变式"出发反查实现——提取 557 条规范性声明，验证安全相关度最高的那些**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

前十六轮的方法都是"我设计判据 → 扫形状 → 找匹配"。第十七轮换方向：

> **先提取代码自己声称的不变式，再逐条验证它是否成立。**

理由是十六轮观察得来的：AutoForge 的 docstring 里有大量规范性声明（「at-most-once」「原子防重放」「一次性消耗」「永不抛」「只读、无副作用」）。**声明是作者意图的最强信号，也是最容易与实现漂移的地方**——声明写在 A 处，实现散落在 B/C/D 处；后来改了 B，A 的声明没人更新。

**扫描 98 文件，提取规范性声明 557 条：**

```
  ATOMICITY    51 条       NEVER       102 条
  ONCE         38 条       BOUNDED      98 条
  UNIQUE       60 条       READONLY    112 条
  FAILDIR     128 条
```

按安全相关性排序后逐条验证，**确认 1 个 High 缺陷——它同时违反三条声明**（一次性消耗、可撤销、必须 consume 才生效）。

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R17-01** | 🔴 High | **授权码的「一次性消耗 / 可撤销」在跨实例下不成立**——`AuthCodeStore` 只在构造时加载一次，`validate`/`consume` 从不重载盘；且 `consume()` 返回值被丢弃 | `af_auth.py:645/672` · `af_mcp.py:206` |

### 最关键的一组对照（同仓两个一次性码，一个对一个错）

| | `af_premiere.consume` | `AuthCodeStore` |
|---|---|---|
| 声明 | 「原子防重放」 | 「一次性消耗」「必须 consume 才生效」 |
| 实现 | `with self._lock:` **内**一次性完成四道校验 + 置位 + 落盘 | `validate()` 与 `consume()` 是**两个各自持锁的独立方法**，中间隔着 `submit_pending` / `approve_pending` |
| 结论 | ✅ **成立** | ❌ **不成立** |

**判据**：看到"一次性消耗""原子防重放"，先确认**"检查"和"置位"是不是同一把锁内的连续代码**。拆成两个公开方法，就把保证原子性的义务转嫁给了调用方——而调用方通常做不到。

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十四 | 调用图展开 + 跨进程边界 | 3 |
| 十五 | 反向可达性 + 闸唯一性 | 1 |
| 十六 | 失败方向全量枚举 + RMW 模板反查 | 2 |
| **十七** | **契约声明提取 + 声称/实现对照验证** | **1** |

### 新增资产

**`scripts/scan_round17.py`**（154 行）—— 契约声明提取器：

- 从 **docstring**（模块/类/函数）+ **行注释**中提取含规范性关键词的句子
- 归类七类：ATOMICITY / ONCE / UNIQUE / FAILDIR / NEVER / BOUNDED / READONLY
- 按**安全相关性**排序（ATOMICITY ≈ ONCE > UNIQUE > FAILDIR > 其余）输出验证清单

**降噪经验**：「幂等」在很多处指"可重复调用无害"（`install()` 幂等、样例灌入幂等），**不是**"只执行一次"——看到"幂等"先判语义，否则近半是噪音。

### 四个新范式（已记入 lessons 第 59–62 条）

**59** 从"代码声明"出发反查实现（claim extractor）；声明是最强意图信号，也最容易漂移
**60** "一次性/原子"成立的充要条件：**校验与置位在同一个临界区内**
**61** 进程内单例 + 只在构造时 `_load()` ⇒ 撤销/消耗等外部变更永远不可见
**62** 消费型 API（consume / claim / acquire）的**返回值被丢弃 = fail-open**

---

## 三、确认缺陷

### 🔴 R17-01　授权码「一次性消耗 / 可撤销」在跨实例下不成立

**位置**：`src/autoforge/af_auth.py:645`（`validate`）、`672`（`consume`）、`548`（`_load`）；调用方 `af_mcp.py:206`

#### 事实 1：`_load()` 只在构造时调用一次

```python
def __init__(self, path):
    ...
    self._load()          # ← 类内唯一一次调用
```
`validate()` 与 `consume()` **都不重载盘**，操作的是构造时的内存快照。

#### 事实 2：两个实例共享同一文件时，一枚码可被消耗两次（实测）

```
══ 场景1：两个 store 实例（模拟 MCP 进程 + 另一实例共享同一文件）══
  码: 36159863
  s1.validate: True
  s1.consume : True  → 落盘
  新实例 s3.validate（重新加载）: False  ← 正确：已消耗
  **旧实例 s2.validate（内存中陈旧）: True   ← 仍为 True
  **旧实例 s2.consume : True                 ← 又消耗成功一次
  ⇒ 一枚码被两个实例各消耗一次 → 两次直部署
```

#### 事实 3：管理员撤销后，长驻进程里仍然有效（实测）

```
── 管理员撤销这枚码（写入文件）──
  admin.revoke: True
  新实例 validate（重读盘）: False           ← 正确
  **MCP 进程（陈旧内存）validate: True       ← 仍为 True ⇒ 撤销不生效
  **MCP consume: True                        ⇒ 已撤销的码仍可完成一次直部署
```

**这条最严重**：撤销是一个安全动作，而它在运行中的进程里**完全没有效果**，直到进程重启。

#### 事实 4：`consume()` 的返回值被丢弃 ⇒ fail-open

```python
# af_mcp.py:206-215  _t_save 路径 A
auth_code = (args.get("auth_code") or "").strip()
if auth_code:
    acs = _MCP_AUTH_STORE or AuthCodeStore(Path(store.root) / ".auth" / "auth_codes.json")
    if acs.validate(auth_code):
        acs.consume(auth_code)          # ← bool 返回值被丢弃
        res = svc.submit_pending(...)   # ← 无论 consume 成败，照常继续
        applied = svc.approve_pending(...)   # ← 直部署
```

`consume()` 明确返回"是否消耗成功（码存在且未消耗过）"。**丢弃它 = 把"已被别人抢走"当成"我拿到了"**。

#### 为什么是 High

授权码是**人审替代路径**（路径 A：`validate → consume → submit → approve` 一步直部署，跳过审批门）。它声称的三条防线里：

| 防线 | 状态 |
|---|---|
| 高熵（8 位码空间） | ✅ 有效 |
| **一次性消耗** | ❌ **本缺陷削弱** |
| **可撤销** | ❌ **本缺陷削弱** |

而 `record_failure` 的 docstring 自己写着：「主防线仍是**高熵 + 一次性消耗**」——**本缺陷削弱的正是这半**。

#### 必须诚实说明的缓解因素

1. **进程内 TOCTOU 窗口极小**：`validate` / `consume` 各自持 `threading.Lock`，实测 20 并发只有 1 次通过（GIL 下相邻语句很少被切走）。**但这不是保证**，只是概率。
2. **主要风险在跨实例/跨进程**：`af_api.py:263` 每请求新建实例（因此它总能看到最新状态），`af_mcp._MCP_AUTH_STORE` 是**长驻单例**（因此它永远看不到外部变更）。二者对同一份文件的视图不一致。
3. **新码不可见是安全方向的**：MCP 看不到 API 新建的码 → `validate` False → 落入待批队列（fail-closed）。有害的是**反方向**：已消耗/已撤销的码仍被视为有效。

#### 修复建议

```python
# 1) validate/consume 前重载盘（与 PairCodeStore.consume 一致，它已有 self._load()）
def validate(self, code):
    self._load()          # ← 跨进程同步
    with self._lock: ...

def consume(self, code):
    self._load()          # ← 跨进程同步
    with self._lock: ...

# 2) 合并为单一原子操作，杜绝 TOCTOU（af_premiere.consume 范式）
def consume_if_valid(self, code) -> bool:
    """校验与置位在同一临界区内完成；返回 True 才代表"这次是我消耗掉的"。"""
    self._load()
    with self._lock:
        ...全部校验...
        rec["consumed"] = True
        self._persist()
        return True

# 3) 调用方必须检查返回值
if acs.consume_if_valid(auth_code):
    ...approve...
else:
    ...落入待批队列（路径 B）...
```

**回归验证清单**：
1. 两个实例共享文件 → 第二个 `consume` 应返回 **False**
2. 管理员 `revoke` 后 → 长驻实例 `validate` 应返回 **False**
3. 并发 20 次 → 成功消耗应**恰好 1 次**
4. `af_premiere` 的既有测试不受影响

---

## 四、本轮验证通过（声明成立）

这一节与缺陷同样重要——**它证明"声称/实现对照"这个方法双向有效**。

| 声明 | 位置 | 验证 | 结论 |
|---|---|---|---|
| **「原子防重放」** | `af_premiere.consume:155` | 读实现：`with self._lock:` 内一次性完成 `unknown_code` / `already_consumed` / `expired` / `sha_mismatch` 四道校验 + 置 `consumed_at` + `save()` | ✅ **成立**——校验与置位在同一临界区 |
| **「原子落盘（随机 tmp 名 + fsync），失败不静默」** | `af_catalog._save:292` | 读实现：走 `af_atomic.atomic_write_text`；docstring 还记录了修复过的"两进程写同一 tmp 名"历史故障 | ✅ 成立 |
| **「授权面的三个落盘文件都必须整份换」** | `af_auth._atomic_write_text:77` | 读实现 + 分层门禁 | ✅ 成立（写侧） |
| **「validate 现在仅查询，不修改状态」** | `af_auth.validate:645` | 读实现：无写操作 | ✅ 成立（**但也正因如此才产生 TOCTOU**——声明本身没错，错在调用方把它和 consume 拼起来用） |
| **「原子性只覆盖到入待批队列」** | `af_apply._group_caveats:309` | 这是一条**诚实的自我限定**声明 | ✅ 声明与实现一致（值得表扬） |
| **「实体 id 仍是唯一键」** | `af_catalog._merge_devices` | 归并只影响展示 | ✅ 成立 |

**特别值得说的**：`af_apply._group_caveats` 主动声明"组合的原子性目前只覆盖到入待批队列这一段；真正写 HA 发生在 approve 之后，那时每条子自动化是独立的 save_graph，不再有 group 级回滚把手……这句话必须让签核的人看得到"。

**这条声明是"诚实"的典范**——它主动划出能力边界，防止 `ok=True` 被误读。R17-01 的问题恰恰相反：声明了能力，但能力不成立。

---

## 五、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`install()` 幂等 / 样例灌入幂等** 等 20+ 条 | 「幂等」此处语义是"可重复调用无害"，非"只执行一次"；不改安全语义 |
| **`af_canary_supervisor.begin` 幂等** | 需构造 `ConfidenceStore`/`FeedbackRecorder`/`clock` 三个依赖，沙箱验证成本高；**未验证**，不作结论 |
| **「永不抛」「只读、无副作用」共 214 条** | 数量大、多为局部契约；未在沙箱实测的不下结论。建议下一轮以「只读」类为专项（尤其 `check_store_cross_conflicts` 已接线后） |
| **`af_predict` at-most-once** | 声明为"先落盘再触发"，属时序契约；需真机时序才能证伪，静态阅读未见违反 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R17-01 `validate`/`consume` 加载盘 + 合并为原子操作 + 调用方检查返回值 | 约 15 行 | 削弱"一次性消耗 + 可撤销"两条主防线 |

---

## 七、十七轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十六 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程/闸/失败方向 | 21 |
| 十七 | 契约声明验证 | 1 |

### 本轮最值得记住的一件事

**同仓两个"一次性码"，一个成立一个不成立，差别只在临界区边界怎么划。**

```
af_premiere.consume       [ 校验 + 置位 + 落盘 ]     ← 一把锁包住全部
AuthCodeStore             [ 校验 ] [ 置位 ]           ← 两把锁，中间还隔着 I/O
```

这不是能力问题。`af_auth.py` 的 docstring 写得比 `af_premiere.py` 更详细——它清楚知道"必须 consume 才生效""无限重试已被堵死"，还解释了 10⁸ 码空间为何不足以单靠高熵。**想清楚了，只是把实现拆成了两个方法**，把原子性的责任推给了调用方。

而调用方 `af_mcp._t_save` 确实"照着声明写"了：`if validate(): consume()` ——**看起来完全符合语义，实际上不成立**。这是最隐蔽的一类缺陷：**调用方代码读起来是对的**。

### 与前十轮的连接

| 轮次 | 缺陷 | 共同形状 |
|---|---|---|
| 四 R4-01 | fd 二次关闭 | 跨线程/进程的资源状态 |
| 九 R9-02 | 爆破半径负数 → 护栏静默失效 | 配置即行为，越界无校验 |
| 十三 R13-01 | `device_acl` 损坏 → 保护消失 | 安全状态依赖一个可被破坏的载体 |
| 十六 R16-01/02 | 读侧静默空 → RMW 抹数据 | 读不出来当作"没有" |
| **十七 R17-01** | **授权码不重载盘 → 撤销无效** | **读一次常驻内存 → 外部变更不可见** |

**"状态看不见"是这个项目反复出现的主题。** 第十六轮是"文件读不出来当作空"，第十七轮是"文件改了但看不见"。

### 给工程团队的一句话

你们在 `af_premiere` 里已经写出了正确的范式（`consume` 一个方法、一把锁、四道校验、返回结构化结果）。**同样的东西在 `af_auth` 里被拆成了两个方法**——而两者的声明几乎一模一样。

建议：**把"一次性凭证"抽成一个公共的 `OneTimeTokenStore`**，暴露唯一的 `consume_if_valid(code, bind_hash) -> ConsumeResult`（照抄 `af_premiere.consume` 的形状，它是对的）。`af_premiere` 与 `AuthCodeStore` 都用它，并强制加载盘。**这会让"一次性"这个语义在全仓只有一份实现，也就只有一种出错方式。**

十七轮下来，"正确范式孤岛"清单又多了一格——现在是七个。**每一次孤岛没能成为公共入口，下一轮就会在孤岛之外再发现一次同类缺陷。**

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **契约声明提取器（七类 / 557 条）** | **`audit-env/scripts/scan_round17.py`** |
| 声明清单明细 | `audit-env/reports/round17-claims.json` |
| 失败方向审计扫描器（D1/D2/D3） | `audit-env/scripts/scan_round16.py` |
| 反向可达性 + 闸绕过分析器 | `audit-env/scripts/scan_round15.py` |
| 调用图展开 + 污点传播 | `audit-env/scripts/scan_round14.py` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（58 条 + 本轮 4 条 = **62 条**） | `audit-env/scripts/lessons-round2.md` |

### 本轮新增 lessons（59–62）

- **59** 从"代码声明"出发反查实现（claim extractor）；「幂等」需先判语义（可重复调用无害 vs 只执行一次）
- **60** "一次性/原子"成立的充要条件：**校验与置位在同一个临界区内**
- **61** 进程内单例 + 只在构造时 `_load()` ⇒ 撤销/消耗等外部变更永远不可见
- **62** 消费型 API（consume / claim / acquire）**返回值被丢弃 = fail-open**

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 3 组（跨实例重复消耗 / 撤销不生效 / 20 并发 TOCTOU） |
| 声明提取 | 98 文件 / 557 条（ATOMICITY 51 · ONCE 38 · UNIQUE 60 · FAILDIR 128 · NEVER 102 · BOUNDED 98 · READONLY 112） |
| 未验证声明 | 「永不抛」「只读、无副作用」共 214 条 —— 未实测不下结论，建议下一轮专项 |
| 仓库状态 | 探针与变异均已还原 |

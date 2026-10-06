# AutoForge 第七轮审计报告：资源生命周期与门禁有效性

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件）+ `scripts/` 门禁脚本
> 本轮主题：**资源生命周期与长驻进程无界增长**，以及**"门禁本身是否真的能判红"**
> 判定标准：严格档 —— 门禁有效性须**变异测试**（注入已知违规看是否判红）
> 报告日期：2026-10-06

---

## 一、执行摘要

AutoForge 有一套自建的**有界缓存门禁**（`scripts/check_bounded_caches.py`，546 行），用来防止长驻进程里的容器无界增长。前六轮审的是运行时代码，**从未审过"守门的人"**。第七轮审门禁本身。

**结论：门禁是有效的（变异测试证明它会判红），但有一个盲区——`defaultdict(...)` 这类带参构造的容器完全不在扫描范围内。另发现一处注释与实现不符的软上限。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R7-01** | 🟠 High | **有界缓存门禁看不见 `defaultdict(...)`——最常见的无界累加器写法** | `scripts/check_bounded_caches.py` |
| **R7-02** | 🟡 Medium | `RateLimiter._MAX_KEYS` 是**软上限**（只触发清理），实测可涨至 3× | `af_auth.py:729-752` |

### 先说好消息：门禁是有效的

我用变异测试直接验证了这一点——在 `src/autoforge/` 下临时放一个探针文件，造一个已知违规的容器：

```
探针 A：self._cache = {}  +  self._cache[k] = 1     →  门禁判红，退出码 1  ✓
探针 B：self._cache2 = defaultdict(list) + .append  →  门禁判绿，退出码 0  ❌
```

**探针 A 被抓到了**，说明门禁的机制（注册表 + 固定键表 + 基线名单 + 就地豁免四重判定）是真实生效的，不是摆设。这正是团队在 CI 注释里痛斥过的"门永远不可能红"的反面——**这里的门确实会红**。

但也正因为探针 B 没被抓到，才暴露出盲区。

### 一个必须先讲清楚的环境事实

本沙箱 Python **3.10.12**，低于项目 `pyproject.toml` 声明的 **`>=3.11`**，导致 `homesdk` 无法用 pip 安装（`Requires-Python >=3.11`）。门禁的判据 B（收集 pytest 测试 id）因此失败，退出码为 **2（射程失败）**，而非 1（判红）。

**这不是项目缺陷**——是审计环境版本低于项目声明下限。绕过方式：把 wheel 解压到 `audit-env/homesdk-pkg/` 并用 `PYTHONPATH` 指向它，门禁即可正常跑通（退出码 0/1 正常）。

> 这点本身值得记一笔：门禁把"环境不具备条件"设计成**退出码 2 显式报射程失败**，而不是退化成通过——**这个设计是对的**，它避免了"环境坏了就假装绿"。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一 | 安全 / 架构 | 5 |
| 二 | 稳定性 / 功能性（异常吞咽） | 7 |
| 三 | 递归 / 资源上限 | 3 |
| 四 | 并发 / 状态一致性 | 3 |
| 五 | 契约 / 语义一致性 | 2 |
| 六 | 可恢复性链 | 2 |
| **七** | **资源生命周期 / 门禁有效性** | **2** |

### 本轮最大的方法升级：变异测试（mutation testing）

前六轮都是"读代码找问题"，第七轮换了个方向——**不审被测对象，审检查者**。

具体做法：写一个**已知违反规则**的探针，放进被测目录，跑门禁看它是否报警。这比读 546 行门禁源码可靠得多：源码看起来很严谨，但盲区只有实际注入才能暴露。

**结果**：探针 A 判红（机制有效）、探针 B 判绿（盲区暴露）。读源码绝对发现不了 B——因为源码逻辑本身是自洽的，问题出在**识别条件**上。

### 四个新范式（已记入 lessons 第 20–23 条）

**20. 门禁必须做变异测试**：读源码看不出的盲区，注入一次就暴露。这是验证"门能否红"的唯一可靠方式。

**21. 静态扫描的构造形态白名单会漏掉带参构造**：`_is_empty_container` 要求"无参构造"才视为空容器，于是 `defaultdict(list)` 被排除。**凡"先识别目标再扫描"的检查，都要查识别条件是否把常见变体排除在外。**

**22. 分清"判红(1)"与"射程失败(2)"**：门禁失败时先看退出码——后者是环境问题不是项目问题。

**23. `_MAX_*` 要区分软上限与硬上限**：注释写"最大保留"，实现是"触发清理的阈值"，即为注释与实现不符。

---

## 三、确认缺陷

### 🟠 R7-01　有界缓存门禁看不见 `defaultdict(...)`

**位置**：`scripts/check_bounded_caches.py` 的 `_is_empty_container()`

门禁扫描增长容器的流程是：**先识别"空容器"**（`_is_empty_container`），**再**看它是否被增长性方法（`append`/`update`/`setdefault`/`add`/`[]=` 等）操作。

而"空容器"的判定条件是——**构造函数无参数**：

```python
if isinstance(node, ast.Call) and _call_name(node) in CONTAINER_FUNCS:
    return not node.args          # ← 有参数就"不是空容器" → 不扫描
```

于是：

| 写法 | 是否空容器 | 是否被门禁扫描 |
|---|---|---|
| `self._x = {}` | ✅ 是 | ✅ 扫描 |
| `self._x = dict()` | ✅ 是 | ✅ 扫描 |
| `self._x = defaultdict(list)` | ❌ **否**（有 1 个参数） | ❌ **不扫描** |
| `self._x = defaultdict(int)` | ❌ **否** | ❌ **不扫描** |

**`defaultdict(list)` 恰恰是 Python 里最常见的无界累加器写法**——它的存在意义就是"键不存在时自动创建"，天然适合累积。门禁把它排除了。

**变异测试实证**：

```python
# 探针 A（被抓到）
class ProbeA:
    def __init__(self): self._cache: dict[str, int] = {}
    def add(self, k): self._cache[k] = 1
# → [有界缓存] 新增增长容器 _audit_probe.py::ProbeA._cache ... 共 1 处判红   退出码 1

# 探针 B（漏网）
class ProbeB:
    def __init__(self): self._cache2: dict[str, list] = defaultdict(list)
    def add(self, k): self._cache2[k].append(1)
# → [有界缓存] 注册表 2 项双腿齐全 ... 扫到增长容器 76 个    退出码 0（绿）
```

**当前实际影响：0**（诚实说明）

我统计了全仓 `defaultdict` 的使用：

```
含 defaultdict 的文件: 3
defaultdict 出现总数: 5
  ├ 赋给局部变量（函数内，短生命周期）: 5
  └ 赋给 self.* 属性（长生命周期）     : 0
```

**全部 5 处都是函数内局部变量**，函数返回即回收，**当前没有任何一处真实的无界增长**。

所以这是**潜在门禁缺陷**而非现存内存泄漏——但它意味着：**将来任何人写 `self._x = defaultdict(list)`，门禁都不会报警**。对一个靠门禁守内存纪律的项目来说，这是"门上有洞"。

**修复建议**（改识别条件，两行）：

```python
if isinstance(node, ast.Call) and _call_name(node) in CONTAINER_FUNCS:
    # defaultdict(list)/defaultdict(int) 带工厂参数，但**构造后仍是空容器**，
    # 且是最常见的无界累加器写法 —— 不能因为带参就排除
    if _call_name(node) == "defaultdict":
        return True
    return not node.args
```

改完后探针 B 应被判红，可回归验证。

---

### 🟡 R7-02　`RateLimiter._MAX_KEYS` 是软上限，实测可涨至 3×

**位置**：`src/autoforge/af_auth.py:729-752`

```python
#: 最大保留的 key 数量（P0-10：防止 _hits 无界增长导致内存泄漏）
_MAX_KEYS = 10000

def check(self, key: str) -> None:
    ...
    with self._lock:
        # P0-10：超过最大 key 数时触发全量清理
        if len(self._hits) >= self._MAX_KEYS:
            self._cleanup_expired(now)          # ← 只清理，不阻止新增
        hits = self._hits.setdefault(key, [])   # ← 无论如何都会新增
```

**注释说"最大保留的 key 数量"，实现是"达到该数量时触发一次清理"。** 若所有 key 都在窗口内活跃，`_cleanup_expired` 一个都删不掉，字典继续增长。

**实测**：

```
场景A：30000 个 key，每个只打 1 次（全部在窗口内活跃）
  _MAX_KEYS = 10000
  实际 _hits 长度 = 30000
  判定: ❌ 超出 _MAX_KEYS（软上限，非硬上限）  超出倍数: 3.0×

场景B：key 过期后能否回收（对照）
  睡过窗口后 _hits 长度 = 5  （旧 key 已回收 ⇒ 机制本身有效）
```

**机制本身是有效的**（场景 B 证明过期 key 能回收），只是**上限不是硬的**。

**严重度说明（避免过度渲染）**：

- 默认 `_client_ip` 用 `request.client.host`（TCP 连接地址，**不可伪造**）
- 仅当 `AUTOFORGE_TRUST_PROXY=true` 时才信任 `X-Forwarded-For`，此时攻击者才能用伪造头刷大量不同 IP
- 每个 key 只存一个 `list[float]`，单 key 内存很小

所以实际可利用性**低**。标 Medium 是因为：注释与实现不符（团队自己定的 P0-10 目标没完全达成），且在高 QPS + 代理模式下有内存增长风险。

**修复建议**：清理后再判一次，超限则拒绝新 key 或走 LRU 淘汰：

```python
if len(self._hits) >= self._MAX_KEYS:
    self._cleanup_expired(now)
    # 清理后仍超限 → 硬闸：不再新增（或淘汰最旧的 N 个）
    if len(self._hits) >= self._MAX_KEYS and key not in self._hits:
        raise RateLimitExceeded("限速表已满（防护性拒绝）")
```

---

## 四、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **门禁判红能力** | 变异测试（探针 A） | ✅ **退出码 1，确实判红**——机制生效，非摆设 |
| **5 个 StateProvider 的 fail-closed 一致性** | 实测未知实体行为 | ✅ HAStateProvider / InMemoryStateProvider / HassStateProvider / FakeHA / HighFidelityHA **全部抛 `UnknownEntity`**，P1-12 口径统一（项目注释中"第七轮审计"已完成项，本轮复核确认） |
| **基线容器回收机制**（抽查） | 逐个读实现 | ✅ `WatchAggregator._by_auto` 有 `MAX_AUTOMATIONS`；`InstanceManager._instances` 终态即 `pop`；`Scheduler._time_fired` 按日期清理（P1-1 已修）；`af_mqtt_bridge._BRIDGES` 有 `remove` 去重；`ConflictArbiter._release_log` 值按 `flicker_window` 裁剪 |
| **门禁退出码设计** | 读脚本 | ✅ 环境不具备条件时报**退出码 2（射程失败）**而非通过——**避免了"环境坏就假装绿"** |
| **客户端 IP 来源** | 读实现 | ✅ 默认 `request.client.host` 不可伪造；仅 `AUTOFORGE_TRUST_PROXY=true` 才信任 `X-Forwarded-For` |

---

## 五、已排除（rejected）

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **112 处带参构造容器中的绝大多数** | ~109 | **局部变量**（`dict(v)`、`list(x)` 等），函数返回即回收，非增长容器 |
| **实例属性上的带参容器** | 13 | 逐个核查：`AdapterRegistry._adapters`（适配器注册表，条目数固定）、`DeviceSM.attributes`（在 `FIXED_KEY_CACHES` 豁免表内）、`PreferenceModel._records = deque(过滤生成器)`（**这是裁剪不是增长**）、其余均为构造后不再增长的只读集合 |
| **项目需 Python >=3.11 但沙箱是 3.10** | — | 环境版本低于项目声明下限，`pyproject.toml` 与 CI 一致，**非项目缺陷** |
| **门禁退出码 2** | — | 射程失败（homesdk 装不上），非判红；用 `PYTHONPATH` 绕过即正常 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R7-01 门禁识别 `defaultdict` | **约 2 行** | 改 `_is_empty_container`；改完用探针 B 回归验证应判红 |
| **P2** | R7-02 限速器加硬闸 | 约 4 行 | 清理后再判一次；或改注释为"触发清理的阈值" |

**回归验证清单**：
1. 探针 B（`defaultdict(list)` + `.append`）→ 门禁应判红（退出码 1）
2. 探针 A 仍应判红（不能改坏）
3. 全仓 5 处 `defaultdict` 均为局部变量 → 修复后不应产生新的存量告警
4. `RateLimiter` 注入 30000 活跃 key → `_hits` 长度应 ≤ `_MAX_KEYS`

---

## 七、七轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 | 主因 |
|---|---|---|---|
| 一 | 安全 / 架构 | 5 | 审批链路身份语义不严密 |
| 二 | 稳定性 / 功能性 | 7 | 异常被吞后不留痕 |
| 三 | 递归 / 资源上限 | 3 | 声明能力与实际能力脱节 |
| 四 | 并发 / 状态一致性 | 3 | 单进程假设 vs 多进程现实 |
| 五 | 契约 / 语义一致性 | 2 | 同一算子两套类型转换策略 |
| 六 | 可恢复性链 | 2 | 恢复结果被记成恢复成功 |
| 七 | 资源生命周期 / 门禁 | 2 | **守卫本身的识别盲区** |

**七轮下来最有价值的一次转向，是第七轮从"审代码"变成"审守卫"。**

前六轮一直在找"代码哪里写错了"，第七轮问的是：**"你们用来防止写错的那套机制，本身靠得住吗？"** 答案是"大部分靠得住，但有一个洞"。而找到这个洞的方法（变异测试）比读源码高效得多——546 行源码读下来会觉得它很严密，注入两个探针就真相大白。

**这个项目最值得称道的一点**：它把"防止自欺"做成了工程机制——

- CI 注释写"门永远不可能红"的教训
- `af_canary.py` 写"读不到实际态不等于验过了"
- 门禁在环境不具备时报**退出码 2 显式射程失败**，而不是退化成通过

最后一条尤其难得。大多数项目的门禁在依赖缺失时会静默跳过或返回 0，**这里是显式报"我没法验"**——这正是铁律 #5（EXEMPT ≠ VERIFIED）在门禁层的贯彻。

**给工程团队的一句话**：把变异测试加进 CI。不用复杂——**在 `src/` 下放一个带已知违规的探针文件，跑一遍门禁断言它必须判红，然后删掉**。这一条测试能防住未来所有"门禁悄悄失效"的情况，比任何代码审查都可靠。你们已经把"不编造证据"刻进了运行时代码，现在该把它刻进门禁的验证里。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（19 条 + 本轮 4 条 = **23 条**） | `audit-env/scripts/lessons-round2.md` |
| 深度缺陷扫描器（E01–E13） | `audit-env/scripts/scan_round3.py` |
| 并发缺陷扫描器（F01–F05） | `audit-env/scripts/scan_round4.py` |
| 契约扫描器（G01–G03） | `audit-env/scripts/scan_round5.py` |
| 稳定性审计主流程（`STEP=` 分段） | `audit-env/scripts/40-stability-audit.sh` |
| 环境自检（**25 项**全通过） | `audit-env/scripts/99-verify.sh` |
| homesdk 本地包（供仿真/对拍/门禁验证） | `audit-env/homesdk-pkg/` |

### 本轮新增 lessons（20–23）

- **20** 门禁必须做变异测试：注入已知违规看它判不判红
- **21** 静态扫描的构造形态白名单会漏掉带参构造（`defaultdict(list)`）
- **22** 分清"判红(1)"与"射程失败(2)"，后者是环境问题
- **23** `_MAX_*` 要区分软上限（触发清理）与硬上限（拒绝新增）

---

## 附：审计环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12 |
| 项目声明 | `requires-python = ">=3.11"`（CI 用 3.11） |
| 影响 | homesdk 无法 pip 安装；已完成解压绕过（`PYTHONPATH`） |
| 门禁可跑性 | 绕过后可正常判 0/1；判据 B（pytest 收集）在绕过后可跑通 |

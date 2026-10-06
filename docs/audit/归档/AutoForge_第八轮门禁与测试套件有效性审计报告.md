# AutoForge 第八轮审计报告：门禁与测试套件的整体有效性

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`scripts/` 全部 15 个门禁 + `tests/` 测试套件（2812 个用例）
> 本轮主题：**审计审计者之二——对所有门禁做变异测试，并对测试套件做有效性测量**
> 判定标准：严格档 —— 门禁须**注入已知违规**验证其判红；测试须**注入变异**验证其杀死
> 报告日期：2026-10-06

---

## 一、执行摘要

第七轮证明了"有界缓存门禁会判红"，但只测了 1 个门禁。第八轮把范围扩到**全部 15 个门禁 + 测试套件**。

**结论：12 个可跑门禁中 11 个抓到了注入的违规（92%），但存在 1 个已知漏网洞 + 2 个门禁在依赖缺失时报错码 + 测试套件有一个"沉默区"。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R8-01** | 🟠 High | **两个门禁在依赖缺失时崩溃并报 rc=1（判红），违背项目自己的 rc=2=射程失败约定** | `check_imports.py` · `check_mqtt_runtime_dep.py` |
| **R8-02** | 🟠 High | **`af_expect` 数值比较语义无任何测试钉住——2 个变异全部存活** | `af_expect.py:85-106` |
| **R8-03** | 🟡 Medium | 有界缓存门禁的 `defaultdict` 盲区（第七轮 R7-01）**回归确认，未修复** | `check_bounded_caches.py` |

### 总体成绩单：门禁是硬的

12 个可跑门禁 × 13 个变异探针：

```
check_undefined_names.py      1  判红 ✓    引用未定义名字
check_atomic_write_sites.py   1  判红 ✓    固定名 .tmp + 无 mkstemp
check_topic_whitelist.py      1  判红 ✓    未登记 topic 字面量
check_mqtt_writers.py         1  判红 ✓    桥外 _mqtt.publish
check_mqtt_subscriptions.py   1  判红 ✓    桥外订阅
check_snapshot_policy.py      1  判红 ✓    snapshot() 不抛 UnknownEntity
check_tool_names.py           1  判红 ✓    调用未注册工具名
check_states_fanout.py        1  判红 ✓    漏换 runtime.states 外另三处
check_param_injection.py      1  判红 ✓    漏递带默认值的关键参数
check_ui_api_paths.py         1  判红 ✓    UI 调了不存在的 /api 路径
check_mcp_arg_schemas.py      1  判红 ✓    函数读了 schema 未声明的键
check_bounded_caches.py       1  判红 ✓    空 dict + 下标写入
                              0  漏网 ✗    defaultdict(list) + append
```

**11/13 抓到，漏网的 1 个是第七轮已经报过的已知洞。** 剩下 3 个门禁因沙箱环境（git 不可用、grimp/tomllib 缺失）跑不了。

对一个靠 15 道门禁守纪律的项目来说，**这个成绩是扎实的**——绝大多数门是真的会红的。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码（安全/稳定性/递归/并发/契约/可恢复性） | 22 |
| 七 | 资源生命周期 + **单个门禁有效性** | 2 |
| **八** | **全部门禁有效性 + 测试套件有效性** | **3** |

### 两个新资产

1. **`scripts/mutation_gates.py`**——门禁变异测试台，10 组探针，约定 `0=绿/1=红/2=射程`
2. **`scripts/mutation_invasive.py`**——侵入式变异（改完必还原），覆盖不便用外挂探针的两个门禁

### 四个新范式（已记入 lessons 第 24–27 条）

**24. 变异探针必须用目标门禁真实识别的调用形态**（否则造出假 MISS）

第一轮 `ui_api_paths` 探针写成 `export const P = "/api/x"`（裸字符串常量）→ 门禁 rc=0，我一度判定"漏网"。**这是我的假 MISS**：该门禁只认 `request('GET', …)` / `req(…)` 两种 helper 调用形态，裸常量根本不是调用点，忽略它是对的。改成真实调用形态后 rc=1。

> 造出 MISS 时先自问：这个违规是不是门禁射程内的东西？

**25. 测试套件有效性要变异，并区分"杀死"与"沉默"**

不看通过率，看**注入变异后测试是否失败**。特别要用**反向变异**：把已确认 bug 的**修复**注入进去——若测试仍绿，说明没有任何测试钉住这个语义。

**26. 对照变异不可省**（证明沉默是区域特性，不是套件整体失效）

只报"某区域变异存活"不够——可能整个套件都无效。必须做同批对照。

**27. 门禁失败先分 rc：`1=判红 / 2=射程失败 / 崩溃=约定违背`**

---

## 三、确认缺陷

### 🟠 R8-01　两个门禁依赖缺失时崩溃并报 rc=1（判红），违背 rc=2 约定

项目自己在 `gates.sh` 里建立了明确约定：**`rc=2 = 射程塌了（环境不具备条件）`，与"判红"严格区分**。这个设计是对的——它避免了"环境坏了就假装绿"。

但 15 个门禁里，**4 个会遇到依赖缺失，只有 2 个遵守了约定**：

| 门禁 | 缺失依赖 | 实际 rc | 是否符合约定 |
|---|---|---|---|
| `check_pkg_markers.py` | git 不可用 | **2** | ✅ 符合 |
| `check_bounded_caches.py` | pytest 缺失 | **2** | ✅ 符合 |
| `check_imports.py` | `grimp` 缺失 | **1** | ❌ **违背** |
| `check_mqtt_runtime_dep.py` | `tomllib` 缺失（Python 3.10） | **1** | ❌ **违背** |

**根因**：这两个脚本在**模块顶层直接 `import grimp` / `import tomllib`**，没有 try/except 兜底，解释器直接抛 `ModuleNotFoundError` → 退出码 1。

```python
# check_imports.py:23（模块顶层，无兜底）
import grimp

# check_mqtt_runtime_dep.py:33（模块顶层，无兜底）
import tomllib
```

**后果**：运维看到 `gates.sh` 报"门禁红"，会去源码里找违规——**方向完全错了**，实际问题只是没装 grimp 或 Python 版本不对。这与项目反复强调的"不编造证据、不说没验过的话"是同一类问题的门禁版。

**严重度说明（避免过度渲染）**：

- 项目 `requires-python = ">=3.11"`，CI 用 3.11，`tomllib` **在 CI 一定存在**
- `grimp` 是 dev 依赖，在 CI 的 `architecture` 作业里**一定已装**
- 所以**在 CI 上永远不会触发**；只在本地低版本 Python 或没装 dev 依赖时出现

标 High 是因为：**同一约定在 4 个同类门禁里只贯彻了 2 个**，而这正是七轮以来反复出现的"防护覆盖不均"模式在门禁层的再现。

**修复建议**（各 3 行）：

```python
try:
    import grimp
except ImportError:
    print("FATAL: 缺 grimp（dev 依赖）⇒ 本门射程已塌，无法判定", file=sys.stderr)
    sys.exit(2)      # 射程失败，不是判红
```

---

### 🟠 R8-02　`af_expect` 数值比较语义无任何测试钉住

**位置**：`src/autoforge/af_expect.py:85-106`

第五轮确认的 **R5-01**（`eq`/`ne` 走字符串比较，`gt`/`lt` 走数值比较）是个真实缺陷。第八轮追问：**为什么测试套件没抓到它？**

用变异测试实测（注入变异 → 看测试是否失败）：

| 变异 | 内容 | 结果 |
|---|---|---|
| **M1** | 注入 **R5-01 的修复版**（eq 改为数值感知） | **37 passed，全绿** ❌ 存活 |
| **M4** | `gt` 改为字符串比较（**故意破坏**数值语义） | **27 passed，全绿** ❌ 存活 |
| **M5** | `_state_matches` 恒 `True`（**对照**：实体断言失效） | **4 failed** ✅ 被杀死 |

**M1 存活**最关键：把正确的修复写进去，测试一个都没红——**说明没有任何测试断言过 `eq` 的比较语义**。第五轮那个 bug 之所以能活到今天，原因就在这里。

**M4 存活**进一步证明：不只 `eq`，**整个数值比较语义（gt/lt/lte/gte）都不在测试射程内**。把 `gt` 改成字符串比较（`"9" > "10"` 为 True 这种典型错误）测试毫无反应。

**M5 是对照组**——`_state_matches` 恒 True 时立刻 4 个测试失败，证明**这个文件的测试整体是有效的**，沉默只发生在数值比较这一小块。

**另一组对照**（`af_canary` 区）：注入 `has_drift` 恒 False、`expected_state` 恒 None → **2/2 全部被杀死** ✅

**所以结论是精确的**：不是测试套件整体无效，而是 **`af_expect` 的数值比较分支是明确的沉默区**。

**建议**：补两组测试即可封住——

```python
# 1) 钉住数值相等（R5-01 回归）
assert _apply_op("eq", "22.0", 22) is True
assert _apply_op("eq", " 22", "22") is True

# 2) 钉住 gt/lt 的数值语义（防 M4 类退化）
assert _apply_op("gt", "9", 10) is False      # 字符串比较会得 True
assert _apply_op("lt", "9", 10) is True
```

---

### 🟡 R8-03　有界缓存门禁 `defaultdict` 盲区——回归确认，未修复

第七轮 R7-01 的回归验证：

```
探针A  空 dict + 下标写入        rc=1  判红 ✓
探针B  defaultdict(list)+append  rc=0  漏网 ✗
```

`_is_empty_container` 要求"构造函数无参"才视为空容器，`defaultdict(list)` 带一个工厂参数 → 被排除在扫描范围外。

**当前实际影响仍为 0**：全仓 5 处 `defaultdict` 全是函数内局部变量，短生命周期。**但它意味着将来任何人写 `self._x = defaultdict(list)`，门禁都不会报警。**

修法仍是第七轮给的两行，本轮不再重复展开。

---

## 四、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **11 个门禁的判红能力** | 变异测试注入已知违规 | ✅ **全部 rc=1 判红**，门禁机制真实生效，非摆设 |
| **`af_canary` 区测试有效性** | 2 个变异（has_drift 恒 False / expected_state 恒 None） | ✅ **2/2 被杀死** |
| **`af_expect._state_matches` 测试有效性** | 变异为恒 True | ✅ **4 个测试失败**，被杀死 |
| **测试套件规模** | `pytest --collect-only` | ✅ **2812 个用例**（22 个收集错误，均为沙箱缺 fastapi 等依赖所致，非项目问题） |
| **3 个门禁的射程失败处理** | `check_pkg_markers`（git）/ `check_bounded_caches`（pytest） | ✅ 正确返回 rc=2 |
| **门禁的 rc=2 设计** | 读 `gates.sh` | ✅ 环境不具备时显式报射程失败，**不退化成通过**——这是对的 |

---

## 五、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`ui_api_paths` 漏网（首轮判定）** | **我方假 MISS**。探针写成裸字符串常量，不是门禁识别的调用形态；改为 `request('GET', '/…')` 后 rc=1 判红 |
| **`check_imports.py` 不在 `gates.sh`** | 非缺陷——它在 `.github/workflows/ci.yml:90` 的 `architecture` 作业里跑，CI 覆盖完整 |
| **22 个 pytest 收集错误** | 沙箱缺 fastapi 等依赖，非项目问题 |
| **`check_imports` / `check_mqtt_runtime_dep` 崩溃** | 已计入 R8-01，但不视为"CI 会失败"——CI 环境依赖齐全，永不会触发，属约定一致性问题 |
| **local `defaultdict` 5 处** | 全为函数内局部变量，短生命周期，非增长容器 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R8-02 补 expect 数值比较测试 | 约 8 行测试 | 顺带把 R5-01 的修复钉死，防回归 |
| **P1** | R8-01 两门禁加 import 兜底 | 各 3 行 | 让"依赖缺失"报 rc=2 而非 rc=1 |
| **P2** | R8-03 门禁识别 `defaultdict` | 约 2 行 | 第七轮遗留；改完用探针 B 回归验证 |

**回归验证清单**：
1. 注入 R5-01 修复版 → 新增的 eq 数值测试应**通过**（修复后语义正确）
2. 把 `gt` 改成字符串比较 → 新增的 gt 测试应**失败**（证明钉住了）
3. 卸载 grimp（或改名模拟）→ `check_imports.py` 应返回 **rc=2**
4. 探针 B（`defaultdict(list)`）→ 门禁应判红

---

## 七、八轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七 | 资源生命周期 + 单门禁有效性 | 2 |
| 八 | 全门禁 + 测试套件有效性 | 3 |

**八轮最值得记的一个数字：11/13 门禁变异被抓到。**

这意味着 AutoForge 的 15 道门禁**绝大多数是真的会红的**。对一个有"门永远不可能红"这条惨痛教训的项目来说，这是实打实的进步——教训被转化成了机制，而且机制是活的。

**但三次审计都指向同一个模式：防护做得好，覆盖不均。**

| 轮次 | 不均之处 |
|---|---|
| 三 | 表达式有 `MAX_EXPR_DEPTH`，图遍历没有 |
| 四 | `af_store`/`af_catalog` 用 FileLock，`af_pending` 只用进程内锁 |
| 六 | 漂移检测 fail-closed，回滚结果却无条件说"已自动回滚" |
| **八** | **4 个门禁遇依赖缺失，只有 2 个遵守 rc=2 约定；`af_expect` 实体状态有测试，数值比较没有** |

这个模式说明：**团队有能力把一件事做对，缺的是把"做对了"这件事横向铺开**。而铺开的最好方式，恰恰是你们已经会的那招——**把它变成能判红的门**。

**给工程团队的一句话**：把 `mutation_gates.py` 和 `mutation_invasive.py` 收进 CI。不用复杂——**每次跑一遍，断言每个门禁都必须对自己负责的那类违规判红**。这两条测试防的是"门禁悄悄失效"，比任何代码审查都可靠。你们已经证明了门会红，现在该证明**门一直会红**。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（23 条 + 本轮 4 条 = **27 条**） | `audit-env/scripts/lessons-round2.md` |
| **门禁变异测试台（10 组探针）** | **`audit-env/scripts/mutation_gates.py`** |
| **侵入式门禁变异（改完必还原）** | **`audit-env/scripts/mutation_invasive.py`** |
| 深度缺陷扫描器（E01–E13） | `audit-env/scripts/scan_round3.py` |
| 并发缺陷扫描器（F01–F05） | `audit-env/scripts/scan_round4.py` |
| 契约扫描器（G01–G03） | `audit-env/scripts/scan_round5.py` |
| homesdk 本地包 | `audit-env/homesdk-pkg/` |
| 持久 pytest（避免沙箱重置） | `audit-env/pylib/` |

### 本轮新增 lessons（24–27）

- **24** 变异探针必须用目标门禁真实识别的调用形态（否则造假 MISS）
- **25** 测试套件有效性要变异：区分"杀死"与"沉默"，并用**修复方向反向变异**
- **26** 对照变异不可省：证明沉默是区域特性而非套件整体失效
- **27** 门禁失败先分 rc：1=判红 / 2=射程失败 / 崩溃=约定违背

---

## 附：审计环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`，CI 用 3.11） |
| 影响 | `tomllib` 不存在 → `check_mqtt_runtime_dep.py` 崩溃；`homesdk` 需 3.11 无法 pip 装 |
| 绕过 | wheel 解压到 `audit-env/homesdk-pkg/`，`PYTHONPATH` 指向 |
| 门禁可跑性 | 15 个中 **12 个**可跑（3 个受环境限制：git / grimp / tomllib） |
| 测试套件 | 2812 个用例可收集；22 个收集错误均因缺 fastapi 等沙箱依赖 |

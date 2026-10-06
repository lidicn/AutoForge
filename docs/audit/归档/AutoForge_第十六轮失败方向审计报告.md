# AutoForge 第十六轮审计报告：失败方向审计（fail-open / fail-closed）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**全量枚举"失败后改变系统行为"的分支，逐条判方向（fail-open / fail-closed）**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

AutoForge 源码里反复出现关于"失败语义"的自觉认知：

> - 「读不到实际态不等于验过了」
> - 「撤销黑名单读不出来 ≠ 名单为空…会全部复活（fail-open）」
> - 「把『没验到』当『验过了』就是自欺」
> - 铁律 #5：EXEMPT ≠ VERIFIED

但前十五轮的发现说明这些认知是**逐点正确、全局不一致**的。第十六轮把逐点判断变成**全量枚举**——扫描所有"失败后改变系统行为"的分支，逐条判方向。

**找到 2 个缺陷，两个都是同一个复发形状的第 3、4 次出现。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R16-01** | 🔴 High | **`aliases.json` 损坏 → 下一次正常的 `set_alias` 抹掉全部已有别名** | `af_catalog.py:547 / 561 / 580` |
| **R16-02** | 🟠 Medium | **`catalog.json` 损坏 → 收窄刷新丢弃所有未刷新的实体**，且"保留旧条目"保护整个失效 | `af_catalog.py:269 / 336` |

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十三 | 模板反查（只认直接调用） | 2 |
| 十四 | 调用图展开 + 跨进程边界 | 3 |
| 十五 | 反向可达性 + 闸唯一性 | 1 |
| **十六** | **失败方向全量枚举 + RMW-on-silent-empty 模板反查** | **2** |

### 新增资产

**`scripts/scan_round16.py`**（211 行）—— 三档判据：

| 档 | 判定 | 作用 |
|---|---|---|
| **D1 方向档** | 失败后系统更宽松（放行/跳过检查/返回空集合）还是更严格（拒绝/抛错/毒化） | FAIL_OPEN / FAIL_CLOSED / NEUTRAL |
| **D2 留痕档** | **四叉**：logger / raise / 计数 / 错误列表 append（lesson 47） | 真静默 vs 有痕降级 |
| **D3 语义档** | 所在函数是否判定类（check/valid/verify/guard/acl/auth/load_…） | 排除遥测/展示类噪音 |

**实测分布（98 文件，判定类函数中的 except 分支 43 条）**：

```
  FAIL_OPEN      23 条   其中无痕   18 条
  NEUTRAL        14 条   其中无痕    7 条
  FAIL_CLOSED     6 条   其中无痕    4 条
```

### 关键的第二项迭代：以 R10-02 为模板反查 RMW

本轮第二个方法改进是**把第十轮 R10-02（`af_store._read_tags` 静默空 + 读改写 → 永久丢失）固化成模板**，反查全仓同形状：

1. 先找"失败时静默返回空容器"的加载器 → **46 个**
2. 再找调用它之后又写盘的函数 → **10 处**
3. 人工分诊 → 确认 R10-02 之外**还有 2 处真缺陷**

**这个形状现在是第 3、4 次出现**——模板反查把它从"偶然发现"变成了"可复现检出"。

### 四个新范式（已记入 lessons 第 55–58 条）

**55** 失败方向要全量枚举（D1 方向 / D2 留痕 / D3 语义），不能只审安全模块
**56** 方向扫描器的假阳性来自**降级被显式暴露**的实现——判方向前先看"降级有没有被暴露出去"
**57** RMW-on-silent-empty 是复发形状，必须做模板反查（已确认 4 处）
**58** 任何"保留旧值"的保护，都要问"旧值读不出来时会怎样"

---

## 三、确认缺陷

### 🔴 R16-01　`aliases.json` 损坏 → 下一次正常的 `set_alias` 抹掉全部已有别名

**位置**：`src/autoforge/af_catalog.py:547`（加载）、`561`（`set_alias`）、`580`（`remove_alias`）

```python
def _load_aliases(self) -> dict[str, str]:
    try:
        data = json.loads(self.alias_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}                      # ← 静默返回空，无日志

def set_alias(self, name, entity_id):
    ...
    with FileLock(str(self.alias_path) + ".lock", timeout=10.0):
        aliases = self._load_aliases()   # ← 损坏时是 {}
        aliases[q] = eid
        atomic_write_text(self.alias_path, json.dumps(aliases, ...))
```

**实测**：

```
set 1: {'ok': True, 'total': 1}
set 2: {'ok': True, 'total': 2}
当前别名: {'厨房灯': 'light.kitchen', '书房灯': 'light.study'}

── aliases.json 损坏（截断）──
  list_aliases(): {'ok': True, 'total': 0, 'aliases': {}}   ← 显示 0 条（实际 2 条）

── 用户做一个完全正常的 set_alias('门厅灯') ──
  set 3: {'ok': True, 'total': 1}
  落盘内容: { "门厅灯": "light.hall" }
  ⇒ 原来 2 条别名全部消失，被新的一条覆盖；全程无 warning
```

**附带问题**：`remove_alias` 在损坏时 `_load_aliases()` 返回 `{}` → `name not in aliases` → 返回**「未找到别名 X」**——用户的别名实际存在，只是文件坏了。这是**误导性错误**，会把排查引向错误方向（与第十二轮 R12-02 的"404 未找到待批"同形）。

**为什么是 High**：与第十轮 R10-02 完全同形且后果相同（用户一次正常操作导致数据永久丢失、无痕）。别名是"自然语言名 → entity_id"的精确映射，是解析直中的高置信来源——丢失后解析降级为模糊匹配，**可能匹配到错误实体**。

**注**：`set_alias` 的写路径本身是**正确的**（FileLock + `atomic_write_text`）——问题只在读侧静默空。修一行即可。

**修复建议**（与 R10-02 共用一个 helper）：

```python
def _load_aliases(self) -> dict[str, str]:
    try:
        data = json.loads(self.alias_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("ALIASES_LOAD_FAILED path=%s err=%s —— 别名不可用，"
                     "写入前须隔离坏文件，否则会抹掉全部已有别名", self.alias_path, exc)
        self._aliases_broken = True     # 毒化标志，af_auth 范式
        return {}
```

再加上：`set_alias` / `remove_alias` 在 `_aliases_broken` 时**拒绝写入**（fail-closed），或先把坏文件隔离为 `.corrupt`（`af_predict.py:910` 范式）再重建。

---

### 🟠 R16-02　`catalog.json` 损坏 → 收窄刷新丢弃所有未刷新的实体

**位置**：`src/autoforge/af_catalog.py:269`（`_load`）、`336`（`refresh` 的合并基准）

```python
def _load(self) -> dict[str, Any]:
    """读目录（带缓存）。文件不存在/损坏 → 空目录（不抛）。"""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": CATALOG_VERSION, "freshness": "", "entities": {}}

def refresh(self, *, full=True, domain="", area=""):
    old = self._load().get("entities", {})          # ← 损坏时是 {}
    narrow = bool(domain or area)
    entities = {} if (full and not narrow) else dict(old)
    ...
    _preserve_known(meta, old.get(entity_id))       # ← 也依赖 old
```

**实测**：

```
全量刷新: 4 新增，total = 4
  目录实体: ['light.kitchen', 'light.study', 'lock.front', 'sensor.temp']

── catalog.json 损坏（截断）──
  _load() 实体数: 0  ← 静默变空（有 4 条）

── 用户只刷书房（收窄刷新 domain=light，这是正常操作）──
  refresh → {'ok': True, 'added': 2, 'total': 2}
  落盘实体: ['light.kitchen', 'light.study']
  ⇒ 未刷新的 lock.front / sensor.temp 已不在（本次刷新本应保留旧条目）
  ⇒ _preserve_known 失去基准（old 为空），注册表富化的已知值无法沿用
```

**讽刺点**：`refresh` 的注释明确写着：

> 收窄过滤（domain/area 非空）时**保留未匹配的旧条目**，避免「只刷书房」把全屋清空

这条保护**恰恰在最需要它的场景（目录损坏）下失效**——因为它依赖 `_load()` 成功。（lesson 58）

**为什么是 Medium 不是 High**：
- 全量刷新（`full=True` 且不收窄，默认值）**不受影响**——会重新从 HA 拉全量
- 只有收窄/增量刷新（`full=False` 或带 domain/area）才触发
- 设备目录可从 HA 重新拉取，不是唯一副本

**但值得修**，因为 `_save` 的 docstring 说明**损坏是已知的历史故障模式**：

> 这一站原来既没有锁也没有随机 tmp 名：两个进程同时刷新目录会写同一个 `catalog.json.tmp`，交错内容被最后一次 `os.replace` 装上，下次 `_load()` **把整份设备目录按损坏读空**。

团队修了"产生损坏"的原因（加随机 tmp 名 + fsync），但**没修"损坏后 `_load()` 静默变空"这一环**。前者降低了概率，后者决定了后果——而现在后果是"静默丢失 + 保护失效"。

**修复建议**：`_load()` 解析失败时记 `logger.error` 并置 `_catalog_broken` 标志；`refresh` 在标志置位时**强制走全量**（不信任空 old），并先隔离坏文件为 `.corrupt`。

---

## 四、本轮验证通过（这些被我的扫描器误判了，实际是正确的）

这一节比缺陷本身更重要——**它说明 AutoForge 在核心路径上的失败语义是过硬的**。

| 项目 | 我的扫描器判定 | 实际 | 结论 |
|---|---|---|---|
| **`af_expect._read_attribute`** | FAIL_OPEN / 无痕 | 读不到返回 `MISSING` → 下游标 `unverified`（**不是 pass**），reason 写明"本次仿真无法验证该断言" | ✅ **正确** |
| **`af_expect.evaluate_expects` 聚合** | — | `ok = failed == 0`，另有 `fully_verified = 无 fail 且无 unverified`；注释："把『没验到』当『验过了』就是自欺" | ✅ **教科书级**——`ok` 与"全验过"分开表达 |
| **`af_time.load_tz`** | FAIL_OPEN / 无痕 | 解析失败退化为 +08:00，注释写明"这件事必须能被外部看到，所以配套 `house_tz_status()` 供 /api/health 暴露" | ✅ **降级可见 = 不是静默** |
| **`af_service.load_entity_health`** | FAIL_OPEN / 无痕 | 外层有 `logger.debug`；docstring 声明"读取异常 → 空 dict（离线编写 IR 场景零误报）" | ✅ 有意设计 |
| **`af_secrets.load_secret`** | FAIL_OPEN / 无痕 | docstring 声明"文件不存在/无权限/为空时返回 default"，注释"交给 env 回退" | ✅ 有意设计（与 R13-02 同族，已报） |
| **`af_adapters/ha.get_state`** | FAIL_OPEN / 无痕 | 返回 None，docstring 声明"失败返回 None（不抛）" | ✅ 契约明确 |
| **`af_catalog._save`** | — | 原子写 + fsync + 随机 tmp 名，docstring 记录了修复过的并发交错故障 | ✅ 写侧正确（问题在读侧） |

**lesson 56 的来源**：我误判的这几处有一个共同点——**降级被显式暴露或有明确契约**。判"方向"不能只看 handler 里有没有 `raise`/`logger`，要先看**降级有没有被暴露出去**（`house_tz_status`、`unverified` 状态、docstring 契约都算）。

---

## 五、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`af_apply.check_store_cross_conflicts` 静默跳过** | 该函数**只被测试引用**，未接入生产。虽然"失败项跳过 → 冲突漏检"形状成立，但当前无实际影响；建议接线时一并补日志 |
| **`af_auth._rewrite_issued`** | 解析失败时 `return`，**不写盘**，不构成 RMW ⚠️ 我当时差点点名它，核实后排除 |
| **`af_closedloop/deepfix.repair`** | `extract_json` 是解析 LLM 输出，失败有专门降级路径；非持久化 RMW |
| **`af_service.get_conf` / `get_metrics` 跳过失败归档** | 属"静默漏项族"（第二轮已归纳），严重度低；且 `get_conf` 的 `ok` 由 items 表达，不是 fake-ok |
| **`af_store.read_jsonl_bounded` 坏行跳过** | 第十轮 R10-01 同族，已报 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R16-01 `_load_aliases` 毒化 + 写前拒写 | 约 6 行 | 一次正常操作永久抹掉别名，无痕 |
| **P2** | R16-02 `_load` 毒化 + 强制全量刷新 | 约 8 行 | 收窄刷新静默丢实体，且"保留旧条目"保护失效 |

**两者共用一个修复范式**（这也是 lessons 57 的落点）：抽一个公共的 `load_json_or_fail(path)`，把"解析失败 → 记日志 + 置毒化标志 + 隔离 `.corrupt`"做成唯一入口。R10-02、R16-01、R16-02 三处一起收口。

---

## 七、十六轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十五 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程/闸绕过 | 19 |
| 十六 | 失败方向 | 2 |

### 本轮最重要的数字：同一个形状，第 4 次

**RMW-on-silent-empty**（读侧静默空 + 读改写 → 一次正常操作永久抹掉数据）：

| # | 轮次 | 文件 | 数据 |
|---|---|---|---|
| 1 | 十 | `af_store._read_tags` → `set_tags` | 标签 |
| 2 | 十 | `af_store._read_tags` → `_delete_archive` | 标签 |
| 3 | **十六** | **`af_catalog._load_aliases` → `set_alias`/`remove_alias`** | **别名** |
| 4 | **十六** | **`af_catalog._load` → `refresh(收窄)`** | **设备目录** |

第十轮第一次发现时，它看起来是"某个文件没写对"。第十六轮用模板反查证明：**它是一个系统性形状**，仓里至少 4 处。

而这 4 处的**写侧全都正确**（FileLock + `atomic_write_text`）。问题 100% 出在读侧——**读不出来说"没有"，然后写回去**。

### 与"六个孤岛"的关系

前几轮归纳的正确范式孤岛（`af_predict._quarantine`、`af_auth` 毒化标志、`af_telemetry` 坏行保留、`af_insight_queue` 记账…）本轮**又多了一个可加进清单的**：`af_expect` 的 `unverified` 语义。

它其实是**最好的一个**——把"没验到"和"验过了"编码成两个不同的字段，并写明"把『没验到』当『验过了』就是自欺"。**如果这套语义被抽成公共的 `LoadResult`（ok / empty / broken / unverified），本轮这两条缺陷都不会存在**——因为 `set_alias` 拿到 `broken` 时就不会继续写。

**给工程团队的一句话**：你们已经把"读不出来不等于没有"这个道理写进了 `af_expect`，而且写得比大多数项目都清楚。现在缺的只是把它**从断言模块推广到所有加载点**。这也是第十六轮唯一真正值得做的事——不是修两个文件，是让第 5、第 6 处不再出现。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **失败方向审计扫描器（D1/D2/D3）** | **`audit-env/scripts/scan_round16.py`** |
| 方向审计明细 | `audit-env/reports/round16-direction.json` |
| 反向可达性 + 闸绕过分析器 | `audit-env/scripts/scan_round15.py` |
| 调用图展开 + 污点传播（P2/P5/P6/P7） | `audit-env/scripts/scan_round14.py` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（54 条 + 本轮 4 条 = **58 条**） | `audit-env/scripts/lessons-round2.md` |

### 本轮新增 lessons（55–58）

- **55** 审计"失败方向"要全量枚举三档（方向 / 留痕 / 语义）
- **56** 方向扫描器的假阳性来自**降级被显式暴露**的实现——判前先看"降级有没有被暴露出去"
- **57** RMW-on-silent-empty 是复发形状（已确认 4 处），必须做模板反查
- **58** 任何"保留旧值"的保护，都要问"旧值读不出来时会怎样"

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 2 组（别名 RMW 永久丢失 / 收窄刷新丢弃实体）+ 方向扫描 43 条 + RMW 模板反查 10 处 |
| 扫描口径 | 98 文件；判定类函数中的 except 分支 43 条；静默返回空的加载器 46 个；RMW 调用点 10 处 |
| 仓库状态 | 探针与变异均已还原 |

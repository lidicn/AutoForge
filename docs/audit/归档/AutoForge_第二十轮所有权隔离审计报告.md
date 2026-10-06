# AutoForge 第二十轮审计报告：所有权隔离覆盖面（最终轮）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**B-01 owner 所有权隔离覆盖面**（主）+ **B-02 序列化往返保真**（辅）
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 前置：执行工作流 v2 门 1（扫描器自检）后开工
> 报告日期：2026-10-06

---

## 一、执行摘要

本轮执行第十九轮之后制定的最终轮计划：攻两个**完全未审计**的盲区。

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R20-01** | 🔴 High | **导出/导入通道整体绕过所有权隔离**——`import overwrite` 可覆盖他人归档，且 `save_version_raw` 写的记录**没有 owner 字段**，备份恢复后整库归属清零、隔离永久失效 | `af_store.py:356/427` · `af_service.py:1928` |

**B-02（序列化往返）结论：确认无问题**——二次往返完全一致，int/float/bool/None/大整数/嵌套全部保真。详见第五节（这是本轮同样重要的产出）。

### 主控实验（两条通道对照）

```
alice 建归档 → owner: alice

══ 对照：bob 用 save_graph 覆盖（服务层）══
   ✅ 拒绝: ServiceError: 归档 'shared' 归属 'alice'，当前主体 'bob' 无权覆盖（所有权隔离）

══ 实验：bob 用 import overwrite 覆盖（导入通道）══
   import 报告: {'ok': True, 'imported': ['shared'], 'errors': []}
   覆盖后 _existing_owner: ''   ← alice 的归属没了
   落盘 record 里有没有 owner 字段: False

══ 后果：此后任何人都能覆盖（prev_owner 为空 = 视为公共）══
   ❌ carol 覆盖成功 —— 所有权隔离已对该归档**永久**失效
```

---

## 二、工作流 v2 的执行情况

| 门 | 检查 | 结果 |
|---|---|---|
| **门 1** | 跑 `scanner_selfcheck.py`，修函数级 SC-01 | ⚠️ **部分通过**——本轮新脚本 `scan_round20.py` 已全部改用 `auditlib` 原语（`full_unparse`/`own_walk`/`call_name`），但旧脚本的函数级 SC-01 **未修**（见第八节遗留） |
| **门 2** | 命中异常少时手工 grep 间接形态 | ✅ 执行——扫描器只出 5 条，手工 grep 追出 `save_version_raw` 这条**无 owner 形参**的间接路径，正是关键 |
| **门 3** | 每条缺陷有实测 PoC；验证通过也写方式 | ✅ 执行——主控实验 + 迁移实验 + 往返实验；B-02 的"无问题"也附了实测 |

### 门 2 为什么是决定性的

扫描器按"函数名含 save/import 且体含 owner"筛出 5 条，其中 3 条无校验。但**真正的问题不在那 3 条里**——`save_version_raw` 被扫到是因为名字含 `save`，而它的实质问题是**连 owner 参数都没有**（`af_store.py:356` 的签名里没有 `owner`）。

这是 lesson 44（只认直接调用漏间接形态）的又一次兑现：**扫描器能列出"谁没做校验"，但列不出"谁根本没这个字段"**。手工 grep 才追出来。

---

## 三、确认缺陷

### 🔴 R20-01　导出/导入通道整体绕过所有权隔离

**位置**：`af_store.py:356` `save_version_raw`、`af_store.py:427` `import_bundle`、`af_service.py:1928` `import_store`

#### 事实 1：服务层的隔离本身是正确的（对照组）

```python
# af_service.py:413-418  save_graph
prev_owner = _existing_owner(store, str(name).strip())
if prev_owner and owner and prev_owner != owner:
    raise ServiceError(
        f"归档 {name!r} 归属 {prev_owner!r}，当前主体 {owner!r} 无权覆盖（所有权隔离）",
        status=403)
```

实测：bob 覆盖 alice → **403 正确拒绝**。**闸本身没问题。**

#### 事实 2：`import_bundle(overwrite)` 既不校验也不写入 owner

```python
# af_store.py:478-486
if self.versions(name) and strategy == "overwrite" and not entry_has_error:
    self._delete_archive(name)              # ← 无归属校验，直接删
for ver, graph_dict in validated:
    self.save_version_raw(target, graph_dict, int(ver.get("version", 1)),
                          ver.get("saved_at", ""), ver.get("note", ""))
```

```python
# af_store.py:356  save_version_raw —— 签名里根本没有 owner
def save_version_raw(self, name, graph_dict, version, saved_at, note) -> int:
    record = {"name":..., "version":..., "saved_at":..., "note":...,
              "writer": owner_id(), "graph": dict(graph_dict)}
    #                                    ↑ 没有 owner 字段
```

#### 事实 3：导出端也不带 owner

```python
# af_store.py:322-334  export_bundle
versions.append({"version":..., "saved_at":..., "note":..., "graph":...})
#                                                        ↑ 没有 owner
```

**实测（迁移场景）**：

```
导出前归属: a1= alice | b1= bob
bundle 内 entries 条目里是否含 owner: False
导入: {'ok': True, 'imported': ['a1', 'b1']}
导入后归属: a1= '' | b1= ''

══ 迁移后：alice 覆盖 bob 的 b1 ══
   ❌ 覆盖成功（隔离在迁移后失效）
```

#### 为什么是 High 而不是 Medium

1. **触发是正常运维操作**——不是构造攻击，是"备份恢复 / 迁移 / 导入别人给的 bundle"。
2. **后果不可逆**——owner 信息**根本不在 bundle 里**，一旦导入就无法还原；不是"暂时失效"，是**永久丢失**。
3. **失效是静默的**——导入报告 `ok: True`，无 warning，无人告知"归属已清零"。
4. **`prev_owner` 为空 = 视为公共**（`save_graph:415` 的 `if prev_owner and owner`）⇒ 归属清空后**任何人**都能覆盖，隔离对该归档彻底关闭。

#### 与第十五、十八轮是同一根因的第三次出现

| 轮次 | 缺陷 | 缺失的护栏 |
|---|---|---|
| 十五 R15-01 | CLI `store save` | 静态扫描闸 |
| 十八 R18-01 | CLI `store enable` | 爆炸半径 |
| **二十 R20-01** | **`import_store`** | **所有权隔离** |

**三条都是：服务层有护栏，另一条写入通道绕过它。** 区别在于前两条是 CLI（本地），本条是**导入通道**（可能处理外部 bundle，且是运维常规动作）。

#### 修复建议

```python
# 1) bundle 携带 owner（导出端）
versions.append({..., "owner": rec.get("owner", "")})

# 2) save_version_raw 接收并写入 owner
def save_version_raw(self, name, graph_dict, version, saved_at, note, owner="") -> int:
    record = {..., "owner": owner, ...}

# 3) import_bundle 在 overwrite 前做归属校验（对齐 save_graph:414）
if self.versions(name) and strategy == "overwrite" and not entry_has_error:
    if owner:
        prev = _existing_owner(self, name)
        if prev and prev != owner:
            report["errors"].append({"name": name, "error": f"归属 {prev!r} 无权覆盖"})
            continue
    self._delete_archive(name)
```

**回归验证清单**：
1. alice 建归档 → bob `import_store(overwrite)` → 应**拒绝**（当前：成功）
2. 导出 → 导入到新 store → owner 应**保留**（当前：清空）
3. `import_store(skip)` 正常路径不受影响

---

## 四、扫描结果：owner 数据流全景

| 角色 | 位置 | 是否处理 owner |
|---|---|---|
| **写（服务层）** | `af_service.py:421` `save_graph` → `store.save(owner=)` | ✅ 写 |
| **写（store 层）** | `af_store.py:154` `save`（`owner` 形参落盘） | ✅ 写 |
| **校验（唯一）** | `af_service.py:414` `_existing_owner` 比对 | ✅ 判 |
| **读（展示）** | `af_service.py:263/2250`、`af_store.py:256`、`af_api.py:1090` | 只读 |
| **绕（导入）** | **`af_store.py:356` `save_version_raw`** | ❌ **无 owner 形参，不写** |
| **绕（导出）** | **`af_store.py:322` `export_bundle`** | ❌ **不带 owner** |
| **另：实例归属** | `af_persist.py:161/194` `owner` + `claims()` 租约仲裁 | ✅ 独立且正确（见第五节） |

**关键点**：整份代码里 **只有 `af_service.py:414` 一处**把 owner 用作判据。而能改变归档内容的通道至少有三条（`save_graph` / `store.save` / `import_bundle`）⇒ 2/3 不受约束。

---

## 五、B-02 序列化往返：确认无问题（本轮同样重要的产出）

按第二轮遗留了 17 轮的 TODO（lesson 3 点名"专项查序列化往返类型丢失"）做了实测，结论是**clean**。

### 实测 1：二次往返完全稳定

```
一次读回 == 二次读回: True | 长度: 1203 == 1203
```

### 实测 2：类型保真（我注入了 int/float/bool/None/大整数/嵌套）

| 字段 | 一次 | 二次 | 结论 |
|---|---|---|---|
| `num_int` | 42 | 42 | ✅ |
| `num_float` | 3.14 | 3.14 | ✅ |
| `flag` | True | True | ✅ |
| `nothing` | None | None | ✅ |
| `big`（10²⁰） | 100000000000000000000 | 100000000000000000000 | ✅ |
| `nested.deep.n` | 7 | 7 | ✅ |

### 实测 3：`default=str` 只在**不可序列化**对象上触发

| 输入类型 | `json.dumps(default=str)` 后读回类型 |
|---|---|
| int / float / bool / None / list / dict | **原类型**（不触发） |
| `datetime` | **str**（触发，且不可还原） |
| 自定义对象 | **str**（触发） |

**结论**：P1-4 那类"静默字符串化"发生在**写入时**（对象本就不可序列化），**不是往返时**。往返本身是保真的。

### 一个差点误报的陷阱（lesson 74）

首次对比出现 **111 处类型差异 + 109 处值差异**，一度像是大规模数据丢失。核查后确认：**不是丢失，是结构归一化**——顶层 `nodes`/`edges` 被提升为 `automations[0].nodes`/`edges`（图模型把单自动化归一为列表）。用"二次往返"对比后差异归零。

**这提醒：做结构对比前必须先归一化，否则会制造 100+ 条假阳性。**

### 顺带确认：`af_persist` 的实例归属与租约仲裁是正确的

```python
# af_persist.py:194-200  claims()
owner = str(record.get("owner", ""))
if not owner or owner == self.owner: return True      # 无主 / 本进程
lease = _parse_iso(record.get("lease_until_wall"))
if lease is None: return True                          # 无租约信息 → 可接管
return clock.now() >= lease                            # 仅租约过期才可接管
```
写侧用 `atomic_write_text` + SHA256 校验和，注释还记录了 P1-18 的修法由来。**这块是本轮看到的实现质量最高的一处**，与 `af_store` 的归档归属形成对照——**同一项目里，实例归属做对了，归档归属在导入通道上没做对。**

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R20-01 bundle 携带 owner + `save_version_raw` 写 owner + overwrite 前校验 | 约 15 行 | 备份恢复后隔离永久失效 |

---

## 七、二十轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十九 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程/闸/失败方向/契约/入口对等/新鲜度 | 25 |
| **二十** | **所有权隔离覆盖面 + 序列化往返** | **1** |

### "服务层有护栏、旁路没有"——第四次

| # | 轮次 | 通道 | 缺失护栏 |
|---|---|---|---|
| 1 | 十五 | CLI `store save` | 静态扫描闸 |
| 2 | 十八 | CLI `store enable` | 爆炸半径 |
| 3 | 十八 | （同上，合并） | — |
| **4** | **二十** | **`import_store`** | **所有权隔离** |

第十九轮我给的建议是"不变量应该是**所有写操作必须经由服务层**，而不是逐条列举护栏"。本轮为这条建议提供了**第三条证据**——而且这条最有力，因为：

> **导入通道不是开发者图省事走的近路，它是产品功能。** 备份恢复、迁移、导入他人 bundle 都是正常运维。也就是说，**护栏缺失发生在一个用户被鼓励去用的路径上**。

### 轮次之间的呼应

- **第十五轮** 提出"闸是不是唯一入口" → 本轮证明同类问题在**导出/导入**上也成立
- **第十七轮** `AuthCodeStore` 拆成两个方法导致原子性失效 → 本轮 owner 的"写"和"判"分散在不同层（store 写、service 判），同样是**责任分散**
- **第十九轮** `refresh()` 用空值覆盖好状态 → 本轮 `save_version_raw` 用**无 owner 的记录**覆盖有 owner 的记录，形状一致

### 给工程团队的一句话

`save_graph` 里的三道护栏（StaticScanner / `_check_blast` / owner 隔离）**全部写在同一个函数里**，这本身是好事——但前提是**所有写操作都经过它**。目前能改归档内容的通道至少三条，其中 `import_bundle` 完全不走它。

具体建议仍是第十九轮那条，但范围要扩：**`check_archive_gate.py` 不只枚举 `store.save`，还要枚举 `save_version_raw` / `import_bundle` / `_delete_archive`**——断言每一个"能改变归档内容或归属"的函数，其上游都在服务层且过护栏。

> 顺带一提，`af_persist` 的实例归属（租约仲裁 + SHA256 + 原子写 + 注释记录 P1-18 由来）是很好的样板。**归档归属可以照它抄一份。**

---

## 八、遗留（工作流本身，非项目缺陷）

| 项 | 状态 | 说明 |
|---|---|---|
| 旧脚本函数级 SC-01 | ⚠️ **未修** | `scan_round10/14/15/16/18` 等 10 个脚本仍用截断 unparse 做判定。**本轮新脚本已全部改用 `auditlib`**；旧脚本的结论需按 SC-01 分级表复核（函数级 79% 漏检、语句级 0%） |
| 第十六轮结论 | ✅ 已复验有效 | 用完整 unparse 重跑，0 处改判 |
| B-03~B-08 盲区 | ⏸ 未审 | prune/GC、数据流值保真、状态机时序、鉴权覆盖面矩阵、规模边界、可观测性 |

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **owner 隔离盘点扫描器** | **`audit-env/scripts/scan_round20.py`** |
| 归属数据流明细 | `audit-env/reports/round20-owner.json` |
| **审计扫描器公共库（正确实现集中处）** | `audit-env/scripts/auditlib.py` |
| **扫描器自检器（10 项 + 严重度分级）** | `audit-env/scripts/scanner_selfcheck.py` |
| 工作流 v2 | `AutoForge_审计工作流v2_盲区覆盖与自检体系.md` |
| 入口对等性 / 汇聚点比对 | `audit-env/scripts/scan_round18.py` |
| 契约声明提取器 | `audit-env/scripts/scan_round17.py` |
| 失败方向审计（D1/D2/D3） | `audit-env/scripts/scan_round16.py` |
| 反向可达性 + 闸绕过 | `audit-env/scripts/scan_round15.py` |
| 误报修正手册（70 + 本轮 4 = **74 条**） | `audit-env/scripts/lessons-round2.md` |

### 本轮新增 lessons（71–74）

- **71** 盘点安全特性覆盖面要循**数据流三问**（谁写 / 谁读 / 谁能改而不判）
- **72** **导出/导入天然是绕过服务层护栏的第二写入通道**，优先查它是否同步归属/校验字段
- **73** 往返保真要测两件事（二次往返稳定性 / 写入时是否丢类型），只测一件会误判
- **74** 结构重排 ≠ 数据丢失；对比前先归一化（否则制造 100+ 假阳性）

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 3 组（import overwrite 绕过归属 / 迁移后归属清零 / 序列化往返三测） |
| 门 1 | ⚠️ 部分通过（新脚本合规，旧脚本未修） |
| 门 2 | ✅ 执行，且是决定性的一步（追出无 owner 形参的间接路径） |
| 门 3 | ✅ 缺陷有 PoC；B-02 的"无问题"也附实测 |
| 仓库状态 | 探针与变异均已还原 |

# AutoForge 第十轮审计报告：持久化层（读侧容错与数据完整性）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 全部持久化读侧（28 个 `json.loads` 调用点）
> 本轮主题：**持久化层——磁盘数据回到内存的那一跳是否健壮**
> 判定标准：严格档 —— 损坏行为须**构造真实损坏文件实测**；语义空白须**反向变异**证明
> 报告日期：2026-10-06

---

## 一、执行摘要

前九轮审了运行时代码、资源生命周期、门禁、测试套件、配置面。第十轮换到**持久化层**——磁盘数据是"上一进程留下的不可信输入"，与 HTTP 请求同属边界，但几乎所有项目只对 HTTP 做校验。

AutoForge 的核心资产是 IR（图）与各类 Store 落盘文件。读侧宽容若不留痕，一条损坏就变成"那条数据从来没存在过"——**这正是项目自己反复痛斥的铁律 #5（EXEMPT ≠ VERIFIED）在存储层的形态**。

**结论：找到 2 个 High 缺陷，以及一处"整条语义无测试守护"的空白。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R10-01** | 🔴 High | **`af_persist` 落盘记录 JSON 解析失败无日志**——与同函数"校验和不匹配"档不对称，损坏记录静默消失 | `af_persist.py:215-237` |
| **R10-02** | 🔴 High | **`af_store._read_tags` 静默返回空 + read-modify-write ⇒ 一次正常操作永久抹掉全部标签** | `af_store.py:265-269` |

### 最值得说的一件事

`af_persist.save()` 的注释里，团队自己写下了这段警示：

> 两边写同一个 tmp、交错内容被最后一次 replace 装上，而读侧对校验和失败的记录是**跳过** ⇒ 这条活着的实例记录静默消失，**不报错也不告警**。

**团队已经识别了这个形状，并补了 warning。但只补了一半。**

实测两种损坏形态：

| 损坏形态 | `records()` | 日志 |
|---|---|---|
| 内容篡改（合法 JSON，校验和不匹配） | 0 条 | ⚠️ **WARNING** ✓ |
| 文件截断（非法 JSON） | 0 条 | **无** ❌ |

而 mkstemp 修复前那种"两进程写同一个固定 tmp 名"产生的交错内容，**恰恰更可能是截断的非法 JSON**。修复覆盖了"内容被改"的形态，遗漏了"文件被截断"的形态——**后者正是它要防的那个场景的主要产物**。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码（安全/稳定性/递归/并发/契约/可恢复性） | 22 |
| 七 | 资源生命周期 + 单门禁有效性 | 2 |
| 八 | 全门禁 + 测试套件有效性 | 3 |
| 九 | 配置面（环境变量） | 2 |
| **十** | **持久化层（读侧容错 / 数据完整性）** | **2** |

### 新增资产

- **`scripts/scan_round10.py`**（I01–I04）：`json.loads` 保护检测 · 静默跳过检测 · 过宽 except · 计数出口

### 四个新范式（已记入 lessons 第 32–35 条）

**32. 磁盘数据是"上一进程留下的不可信输入"**

判据：凡是"读了但没用上"的分支（`continue` / `return {}` / `return None`），都要问**调用方能区分"没有这个文件"和"这个文件坏了"吗**。区分不了即为可疑。

**33. 找"同函数里的两档错误处理"做不对称对比**

`af_persist.records()` 同函数内：`except (ValueError, OSError): continue`（无日志）vs `if not _verify_record: logger.warning(...)`（有日志）。**同一件事的两种失败，一档留痕一档不留 ⇒ 不对称处即靶点。** 比逐个审计 28 个加载点快得多。

**34. 读侧静默 + read-modify-write ⇒ 永久丢失（比"跳过"严重得多）**

**35. 反向变异可证明"整条语义无测试"**（见第三节 R10-02 后的实测）

---

## 三、确认缺陷

### 🔴 R10-01　`af_persist` JSON 解析失败无日志——记录静默消失

**位置**：`src/autoforge/af_persist.py:215-237`（`load()` 与 `records()` 两处同形状）

```python
def records(self) -> list[dict[str, Any]]:
    """读取全部落盘记录；坏文件/校验和不匹配跳过，不让一条损坏拖垮整轮恢复。"""
    for path in sorted(self.directory.glob(f"*{_SUFFIX}")):
        if path.name.endswith(".tmp"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue                                    # ← 无日志 ❌
        if not _verify_record(record):
            _logger.warning("af_persist: 落盘记录校验和不匹配，跳过（%s）", path)   # ← 有日志 ✓
            continue
        out.append(record)
    return out
```

**实测**（构造真实损坏文件，`logging.basicConfig` 捕获全部输出）：

```
════════ 场景：truncated（截断，非法 JSON）════════
RESULT count=0
（无任何日志）

════════ 场景：tampered（合法 JSON，校验和不匹配）════════
    LOG[WARNING] af_persist: 落盘记录校验和不匹配，跳过（/tmp/.../inst-1.json）
RESULT count=0
```

`load()` 同形状：读损坏文件返回 `None`，**与"文件不存在"完全无法区分**，且无日志。

**后果**：`PersistStore` 的整个存在意义是"崩溃后能恢复活着的实例"。一条记录损坏 → 那条活着的实例记录静默消失，**不报错也不告警**——正是注释里自己写的那句话。

**为什么这一档反而没修**：团队修的是"校验和"这一档（SHA256 是后来"B 增强"补的），而 `except (ValueError, OSError)` 是最初就有的兜底。补新档时没有回头对齐旧档。

**修复建议**（2 行 × 2 处）：

```python
except (ValueError, OSError) as exc:
    _logger.warning("af_persist: 落盘记录解析失败（%s）：%s", path, exc)
    continue
```

---

### 🔴 R10-02　`_read_tags` 静默返回空 + read-modify-write ⇒ 数据永久丢失

**位置**：`src/autoforge/af_store.py:265-269`

```python
def _read_tags(self) -> dict[str, list[str]]:
    try:
        return json.loads(self._tags_path().read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}          # ← 无日志；且这是 read-modify-write 的读半段
```

**这是第十轮危害最高的一条**，因为它不是"跳过"，是**永久丢失**：

```
读损坏 → 静默变空 → 下一次正常的 set_tags() → 用空集覆盖写回 → 原数据永久抹掉
```

**实测**：

```
基线 tags: {'a': ['x', 'y'], 'b': ['z'], 'c': ['w']}

── tags.json 被截断（模拟崩溃落盘 / 磁盘故障）──
  _read_tags() → {}   （无日志 ⇒ 静默变空）

── 用户执行 set_tags('d', ['new'])（一次完全正常的操作）──
  结果 tags: {'d': ['new']}
  ❌ 原标签 a/b/c 全部丢失，丢失 3 项：['a', 'b', 'c']
```

**一次完全正常的用户操作，就把损坏前的所有标签永久抹掉，且全程无任何提示。**

**调用点确认**：`set_tags`（287 行）、`_write_tags` 前的读（411 行）都是 read-modify-write 形状；`tags_of`（297）、`all_tags`（300）是纯读。

**补充事实**：`af_store.py` 全文 **`logger.` 出现 0 次**——这个模块**没有任何日志能力**。所以修复不是加一行 warning，而是要先引入 logger。

**修复建议**：

```python
_LOG = logging.getLogger(__name__)          # 模块顶层新增

def _read_tags(self) -> dict[str, list[str]]:
    try:
        return json.loads(self._tags_path().read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        _LOG.warning(
            "tags.json 解析失败，按空处理——**下次写入会用空集覆盖，原标签将永久丢失**：%s", exc)
        return {}
```

**更强的选项**：读侧解析失败时**拒绝写入**（fail-closed），或把坏文件 `rename` 成 `.corrupt` 保留现场（`af_predict.py:910` 已有这个做法：`os.replace(path, path + ".corrupt")`——**又一处已存在的正确范式**）。

---

### 附：测试套件的语义空白（反向变异实测）

沿用第八轮方法，注入变异看测试是否察觉：

| 变异 | 内容 | 结果 |
|---|---|---|
| 基线 | — | 34 passed, 3 skipped |
| **M1** | 给 JSON 解析失败**加** warning（R10-01 修复） | 34 passed ❌ 存活 |
| **M2** | **删掉**已有的校验和不匹配 warning（**反向对照**） | 34 passed ❌ **存活** |
| **M3** | 给 `_read_tags` 解析失败加 warning（R10-02 修复） | 34 passed ❌ 存活 |

**M2 是关键**：把**已存在的正确行为**删掉，测试也没红。

这说明的不是"这两个 bug 没被覆盖"，而是——**"落盘损坏必须留痕"这条语义在测试套件里整体没有任何守护**。R10-01/R10-02 之所以能存活，根因在此。

（沙箱补装 typer 后，`test_af_persist.py` / `test_af_store.py` / `test_v0_6_tags.py` 共 34 个用例可跑，数据可靠。）

---

## 四、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **IR 版本枚举同源** | 对比 `SUPPORTED_IR_VERSIONS` 与 schema `ir_version.enum` | ✅ 两者均为 `["0.2.1", "0.3.0"]`，同源一致 |
| **`af_persist` 校验和档** | 实测篡改 | ✅ 有 WARNING，且坏文件不拖垮整轮恢复（设计正确） |
| **`af_persist.save` 原子写** | 读实现 + 第七轮门禁 | ✅ `mkstemp` 随机 tmp + fsync + `os.replace`；`check_atomic_write_sites` 门在守 |
| **`af_store` 写入侧** | 读实现 | ✅ `FileLock` + `atomic_write_text` + `expect_version` 乐观并发检测，是项目里最完整的写入纪律 |
| **`.tmp` 文件跳过** | 读 `records()` 227-229 行 | ✅ 显式跳过 `.tmp`，不留半截文件干扰 |
| **`af_persist` 坏文件不扩散** | 实测 | ✅ 一条损坏不影响其余记录加载（"不让一条损坏拖垮整轮恢复"目标达成） |

---

## 五、已排除（rejected）

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **扫描器 I02 报出的 28 处"静默跳过"** | 28 | 绝大多数是**协议/消息解析**（`af_mcp.py:981` 读 MCP 请求行、`af_mqtt_bridge.py:434` 读 MQTT 载荷、`af_registry.py:323` 读 HTTP 响应）——这些地方失败即丢弃一条消息，有上层重试/错误响应，**不是持久化读侧**，不适用铁律 #5 那档 |
| **`af_store.history()` 静默跳过** | 1 | 纯只读列举，不影响写入；危害远低于 `_read_tags`，降级为观察项 |
| **`af_undo._load` 重置为空** | — | 有 `logger.warning("undo_log 读取失败，重置为空", exc_info=True)`，**留痕正确** ✅ |
| **`af_persist` 校验和档** | — | 有 warning，正确；缺陷只在解析档（R10-01） |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R10-02 `_read_tags` 留痕 | 约 4 行 + 引入 logger | 危害最高：一次正常操作即永久丢失数据 |
| **P1** | R10-01 `af_persist` 两个解析档补 warning | 各 1 行 | 让"文件截断"与"内容篡改"两档留痕对齐 |
| **P2** | 补"损坏留痕"测试 | 约 20 行 | 断言坏文件必须产生 WARNING——封住整条语义空白（M2 实测无守护） |

**回归验证清单**：
1. `tags.json` 截断 → `_read_tags()` 应产生 WARNING 且不静默变空导致覆盖
2. `af_persist` 目录下放一个截断的 `.json` → `records()` 应产生 WARNING
3. 删掉 `af_persist` 已有的校验和 warning → 新增测试应**失败**（证明钉住了这条语义）
4. 破坏 `tags.json` 后跑 `set_tags` → 原标签不应无声消失

---

## 七、十轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七 | 资源生命周期 + 单门禁 | 2 |
| 八 | 全门禁 + 测试套件 | 3 |
| 九 | 配置面 | 2 |
| 十 | 持久化层 | 2 |

**"已修一处 / 修了一半"这个模式，十轮里出现了六次：**

| 轮次 | 修对的那一处 | 没铺开 / 只修一半 |
|---|---|---|
| 三 | 表达式有 `MAX_EXPR_DEPTH` | 图遍历无深度上限 |
| 四 | `af_store`/`af_catalog` 用 FileLock | `af_pending` 只用进程内锁 |
| 六 | 漂移检测 fail-closed 分 `unmodeled` 档 | 回滚结果无条件说"已自动回滚" |
| 八 | 2 个门禁遵守 rc=2 | 2 个崩溃报 rc=1 |
| 九 | `af_bus._env_number`（P1-7） | 其余 6 处裸转换 |
| **十** | **`af_persist` 校验和不匹配有 warning** | **同一函数里 JSON 解析失败没有** |

第十轮这一条尤其意味深长——它甚至不是"这里修了、那里没修"，而是**在同一个函数的相邻 6 行里，修了一档、漏了另一档**。团队对"静默消失"这个问题的认识是准确的（注释写得比很多项目都清楚），**执行时却只覆盖了当时想到的那一半损坏形态**。

**给工程团队的一句话**：`af_predict.py:910` 已经有 `os.replace(path, path + ".corrupt")` 的保留现场做法，`af_persist` 有校验和 warning，`af_undo._load` 有 `exc_info=True` 的留痕——**三个正确的留痕范式散在三个文件里**。建议把它们收成一个公共 helper（比如 `load_json_or_warn(path, *, on_bad="skip"|"raise"|"quarantine")`），全仓 28 个加载点统一走它。

这样"留痕"就从"每个作者记得写"变成"不写就用不了"。你们已经证明自己知道该怎么做——现在该让正确的做法成为**阻力最小的那条路**。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（31 条 + 本轮 4 条 = **35 条**） | `audit-env/scripts/lessons-round2.md` |
| **持久化容错扫描器（I01–I04）** | **`audit-env/scripts/scan_round10.py`** |
| 配置面扫描器（H01–H04） | `audit-env/scripts/scan_round9.py` |
| 门禁变异测试台（10 组探针） | `audit-env/scripts/mutation_gates.py` |
| 侵入式门禁变异 | `audit-env/scripts/mutation_invasive.py` |
| 深度/并发/契约扫描器 | `scan_round3.py` · `scan_round4.py` · `scan_round5.py` |
| homesdk 本地包 / 持久依赖 | `audit-env/homesdk-pkg/` · `audit-env/pylib/`（含 pytest、typer） |
| 环境自检（25 项全通过） | `audit-env/scripts/99-verify.sh` |

### 本轮新增 lessons（32–35）

- **32** 磁盘数据是"上一进程留下的不可信输入"，读侧静默 = 数据消失无痕
- **33** 找"同函数里的两档错误处理"做不对称对比（一档有日志一档没有 ⇒ 靶点）
- **34** 读侧静默 + read-modify-write ⇒ **永久丢失**，比单纯跳过严重得多
- **35** 反向变异（删掉**已有**正确行为）可证明整条语义无测试守护

---

## 附：审计环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮新增依赖 | `typer` 装入 `audit-env/pylib`（使 `test_af_store` 等可收集） |
| 绕过方式 | homesdk wheel 解压 + `PYTHONPATH` |
| 本轮实测次数 | 损坏文件构造实测 4 组（截断/篡改 × records/load）+ 标签永久丢失实测 1 组 + 变异测试 4 组 |
| 仓库状态 | 探针与变异均已还原，`git status` 干净 |

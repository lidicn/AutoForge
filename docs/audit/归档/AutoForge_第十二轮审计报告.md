# AutoForge 审计报告 · 第十二轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-012`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-012/`
- **报告日期**：2026-10-06

---

## 一句话结论

**补丁副本上 8/8 已拦住（blocked 7 + quiet 1），但更重要的是发现：我们自己的 F11 修复补丁一直在静默失效——用了一个没导入的函数名，异常被原函数已有的 `except Exception: return` 吞掉。**

数据其实"保住了"，所以 PoC 判不出问题；但**既无隔离也无日志，运维完全无从察觉。修了等于没修。**

---

## 一、最重要的发现：修复补丁自己踩了正在审计的那个坑

第十轮给 `DeviceCatalog._record_bucket`（F11，第 3 档遥测）打的补丁里调用了 `quarantine(...)`，但 import 行只写了：

```python
from .af_stateguard import StateCorrupt, barrier, mark_poisoned   # ← 少了 quarantine
```

后果链：

```
quarantine(...)  →  NameError
                →  被本函数原有的 `except Exception: return` 吞掉
                →  不报错、不隔离、不留日志
                →  外部看调用正常返回
```

**这正是审计了十二轮的同一模式（CWE-390：检测到错误却不处理），这次发生在我们自己写的修复里。**

失效方向尤其阴险：**文件确实没被覆盖**（异常在落盘前抛出，写入被跳过），所以损坏字节还在、PoC 判不出异常；但目标中的"隔离现场 + 留痕"两项完全没执行。从运维视角看，和没修一模一样。

手工验证（`resolve_metrics.json` 损坏后调 `_record_bucket`）：

| | 修复前 | 修复后 |
|---|---|---|
| 原文件 | 保留（内容仍是损坏字节） | 被隔离成 `.corrupt-<时间戳>` |
| 日志 | 无 | `状态文件已隔离：... → ...（原因：...）` |

### 两处修正

1. 补上 `quarantine` 的 import
2. **把隔离动作移出那个宽 `try`**——只要它还待在 `except Exception: return` 的覆盖范围里，任何失败都会被吞。这是结构性修正，比补 import 更重要。

---

## 二、工作流迭代

### W33：补丁自检（`scripts/patch_lint.py`）+ 纳入门禁

新增静态检查，专查护栏补丁自身的静默失效：

- **names-missing**：用到 `StateCorrupt` / `quarantine` / `mark_poisoned` / `barrier` 但没从 `af_stateguard` 导入
- **swallowed-by-bare-except**：护栏调用落在静默的 `except Exception` 内，失败会被吞掉不留痕

首跑即命中 `af_catalog.py:515` 两处（同一根因的两个面）。修完后自检 OK，已作为第 26 项门禁固定下来。

**为什么必须做成门禁**：第八轮的教训是"从备份恢复会静默丢掉本地修复"。这类"补丁看起来在、实际没生效"的问题，只能靠自动检查兜住。

### W34：结论判据修订（第 3 档不该要求抛异常）

旧判据把"跳过写入 + 隔离现场"归成 `half_fixed`（"只隔离没拦写"），属于误判——**坏字节还在盘上就说明写入确实没发生**。

第 3 档（遥测）的正确修法就是静默跳过，不该抛异常打断解析。修订后：

| 判据 | 结论 |
|---|---|
| 坏字节被盖掉 | `data_lost` |
| 坏字节还在 + 写被异常拒绝 | `guarded`（`mode=blocked`，第 1/2 档） |
| 坏字节还在 + 已隔离 | `guarded`（`mode=quiet`，第 3 档） |
| 坏字节还在，但什么都没发生 | `no_write`（**没测到**） |

---

## 三、回归对照

| 目标 | 原仓库 | 补丁副本 |
|---|---|---|
| `TokenRegistry._persist_issued` | data_lost | guarded / blocked |
| `PairCodeStore._persist` | data_lost | guarded / blocked |
| `AuthCodeStore._persist` | data_lost | guarded / blocked |
| `DeviceCatalog.set_alias` | data_lost | guarded / blocked |
| `Config.update_credentials` | data_lost | guarded / blocked |
| `ExperienceStore.observe` | data_lost | guarded / blocked |
| `PreferenceModel._rewrite_all` | data_lost | guarded / blocked |
| `GraphStore.set_tags` | data_lost | guarded / blocked |
| `DeviceCatalog._record_bucket` | data_lost | guarded / quiet（**本轮修好**） |

### 一个需要说清楚的数字

补丁副本的 PoC 只跑了 **8 个目标**（上一轮 9 个）。第 9 个 `_record_bucket` 不在列表里，是因为**重构后它不再符合 DO-* 规则的形态**——读和写不再挤在同一个 `try` 里，静态规则自然不再命中。

这不是缺陷消失，是形态变了。它已由手工验证确认（隔离文件生成 + 日志输出），并纳入上表的 quiet 一栏。

**这里有个陷阱值得记**：静态规则不命中 ≠ 已修复。它可能是因为修好了，也可能只是形态变了。本轮是靠手工补验才确认属于前者。

---

## 四、正式审计结果（原仓库，未打补丁）

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 跨轮 diff（vs round-011） | **new 0 / gone 0** |
| 台账 | **F1–F14 全部 still_open** |
| 递归 PoC | 24 目标：cyclic_crash 9 / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | **9/9 data_lost** |

门禁 **26 项全过**（新增 patch_lint），`drift=0`，clean 零假阳性，状态 PoC 门禁 dirty 4/4。

---

## 五、十二轮累计的七次同类事故

| # | 轮次 | 形态 |
|---|---|---|
| 1 | 2 | `scripts/` 漏扫，125 条"缺陷消失"实为漏扫 |
| 2 | 4 | 写盘别名缺失，规则安静地什么都不报 |
| 3 | 6 | RES-03 全文件正则冒充数据流分析 |
| 4 | 8 | 抑制锚点用行号，一漂就静默失效 |
| 5 | 9 | glob 跳过点目录，两条命中全程"测不到" |
| 6 | 11 | `_fetch_stub` 只返回一个实体，手工与机器结论打架 |
| 7 | 12 | **补丁自身少导入 + 被宽 except 吞掉，修了但没人看得见** |

前六次是审计工具的问题，第七次是**修复补丁的问题**。说明这个失效模式不挑代码——只要"错误被静默降级"，无论写在被测代码里还是审计工具里，都会以同样的方式骗过你。

---

## 六、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |

F1–F7、F9、F12、F14 **未纳入补丁**，仍为 `still_open`。补丁只覆盖 F8 / F10 / F13（+ F11、F9 的局部），且只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

递归 PoC 的 9 条 `unavailable`、4 条 `inconclusive` 仍未解决。

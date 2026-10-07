# AutoForge 审计报告 · 第十八轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-018`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-018/`
- **报告日期**：2026-10-07

---

## 一句话结论

**F12（别名撞车保护被静默绕过）在提出十三轮后，第一次被端到端自动确证——而且证实的事比第五轮人工测出的更严重：不只是守卫放行，覆盖真的发生了。**

---

## 一、F12 端到端确证：从"守卫放行"到"覆盖真的发生"

第五轮的人工实测只验到了"守卫静默放行"这一步。本轮新增的自动 PoC 把链路走完：

| 步骤 | 原仓库 | 补丁副本 |
|---|---|---|
| 播种（别名 `a.b` ⇒ 物理目录 `a_b`） | `owner=a.b` | 同 |
| 基线 `_assert_name_owns_dir("a_b")` | **ArchiveNameConflict ✓ 生效** | 同 |
| 损坏 `v1.json` 后 `_dir_owner` | **None** | `StateCorrupt` |
| 复测守卫 | **not_blocked（放行）** | **blocked:StateCorrupt** |
| 端到端 `save("a_b")` | **`happened:v2` ← 覆盖发生** | **`refused:StateCorrupt`** |

第五轮只看到"守卫放行"，本轮证实了**放行的后果是另一条归档被真覆盖**。

`_dir()` 把 `.` 映射成 `_`，所以 `a.b` 和 `a_b` 落到同一个物理目录——这是别名撞车的根源。守卫的默认值是"取不到归属即视为无约束"，而正确默认必须是"不放行"。

同时确证了第五轮记录的同形态**边缘实例** `assert_deletable`（删除路径，检查每一条记录、需全部不可解析才 fail-open），两处均已修复。

---

## 二、W44：新增 `core/poc_failopen.py`

把 FO-01（失败开放守卫）从"人工实测"升级为流水线能力，协议为 **播种 → 基线 → 损坏 → 复测 → 端到端**。

判据：`fail_open_confirmed`（基线拦住+损坏后放行）/ `guard_holds`（两步都拦住）/ `unavailable`（**不等于没问题**）。

结果：原仓库 **2/2 fail_open_confirmed**，补丁副本 **2/2 guard_holds**。

### 调试中踩的坑：修复生效被显示成"测不到"

首跑补丁副本时，写入路径被判 `unavailable`。根因是探针里这行裸调没包 try：

```python
rec["owner_after"] = store._dir_owner(plain)
```

修复版会在**这里直接抛 `StateCorrupt`**（而不是返回 None），异常冒泡到外层处理器 ⇒ 判成"测不到"。

**一个正确的修复，被工具显示成了无法测试。** 这是本项目记录的**第十次**"工具静默失效伪装成结论"，也是第二次失效源在**探针自身的覆盖缺口**（第一次是第十七轮 `_NestStandin.__iter__` 返回空迭代）。

另一个小坑：门禁里 `af_src` 在下方"出站 PoC"段才定义，本段引用直接 `UnboundLocalError`。已各自独立定义，不再依赖声明顺序。

---

## 三、工作流迭代汇总

| 项 | 内容 |
|---|---|
| **W44** | `core/poc_failopen.py` —— FO-01 失败开放守卫行为级 PoC |
| 门禁 | 28 → **32** 项（新增 `poc_failopen`：原仓库至少 1 条 confirmed） |
| auditkit | 11 → **12** 阶段（新增 `[12/12]` 失败开放 PoC） |
| 补丁 | `af_store.assert_deletable` 的 `continue` 改为 fail-closed（写入路径 `_dir_owner` 早前已修） |

---

## 四、正式审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 26/26 ok（门禁 **32** 项全过） |
| 原始命中 | **1083** |
| 去重后待办 | **1052**（抑制 4 条） |
| 严重度 | high **85** / medium 682 / low 285 |
| 跨轮 diff | **new 0 / gone 0** |
| 台账 | **F1–F16 全部 still_open** |
| 递归 PoC | cyclic_crash 14 / safe 6 / inconclusive 4 / unavailable 0 |
| 状态 PoC | 9/9 data_lost |
| 出站 PoC | bypass_confirmed_unfixed（原仓库） |
| **失败开放 PoC** | **2/2 fail_open_confirmed（原仓库）→ 2/2 guard_holds（补丁）** |
| 依赖审计 | homesdk 58 条（high 3 / medium 43 / low 12） |

门禁：`ok=True`、analyzers=32、failures=[]、drift=0；extras 全绿（patch_lint 11 文件 0 问题、ensure_tools 就绪 3 / 项目依赖 8、四类 PoC 均有确证）。

**new 0 / gone 0 合理**：本轮新增的是 PoC 脚本，没动分析器规则，静态命中面应与上轮一致（1083）。

---

## 五、四类 PoC 现状

| 类别 | 覆盖 | 状态 |
|---|---|---|
| 递归（无预算/无环检测） | RSC-02 / GOD-02 等 | 24 目标：14 崩 / 6 safe / 4 inconclusive |
| 状态损坏（破坏性覆盖） | DO-01..04 | **9/9 data_lost** |
| 出站护栏 | OUTB-01 | 1/1 确证旁路 |
| **失败开放守卫** | **FO-01** | **2/2 确证（本轮新增）** |

四类静态规则族现在都有行为级实测兜底。**F8 / F10 / F12 / F13 / F14 / F15 / F16 均已从"静态命中"升级为"自动实测确证"。**

---

## 六、仍未覆盖

| 工具 | 未覆盖面 | 状态 |
|---|---|---|
| pip-audit | 依赖 CVE / 供应链漏洞 | ❌ osv.dev、pypi.org 均 HTTP 403；且跨 bash 调用丢失 |
| semgrep | SAST 交叉验证 | ❌ 安装失败（`File too large`），无 CLI |
| — | homesdk 运行时探针 | ❌ wheel 要求 Python>=3.11，环境 3.10 |
| pytest | 测试运行时探针、变异测试 | ❌ 未接入 |

**4 条递归 `inconclusive` 未解**：`af_draft._resolve_expr`、`af_scheduler._satisfied`、`af_version.diff`、`detectors.expr_nodes`。

**依赖 CVE 面十八轮都没扫。** `homesdk` 是未上 PyPI 的私有 wheel——**风险未知，不是无风险**。

F1–F7、F9、F11、F16 未纳入补丁。F8/F10/F12/F13/F14/F15 补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

---

## 七、硬约束（延续第十六轮）

**凡涉及外部工具的步骤，必须在同一次 bash 调用内先跑 `scripts/ensure_tools.py` 再执行。** 分开调用会静默丢掉工具能力，且表现为"没有发现"而非报错。

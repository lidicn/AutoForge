# AutoForge 审计报告 · 第十轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-010`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-010/`
- **报告日期**：2026-10-06

---

## 一句话结论

**本轮把 F8 / F10 / F13 从「静态规则说会丢」推进到「补丁副本上实测已拦住」。**

在一套只读补丁副本（`/data/workspace/repos/af-patched`）上应用 22 处修复后，状态损坏 PoC 的 9 个目标从 **7 处 data_lost** 变成 **7 处 guarded**（写被拒绝 + 坏文件隔离保留）。

代价是暴露了一个更值得警惕的模式：**第一版补丁里有 3 处把护栏装在了不会被执行到的路径上**——与 F1 是同一类错误。

---

## 一、补丁与验证流水线（本轮新建）

| 组件 | 作用 |
|---|---|
| `scripts/apply_fixes_poc.py` | 22 处补丁的集中应用脚本，带 `--check` 预检 |
| `af_stateguard.py` | 护栏模块：`StateCorrupt` / `quarantine()` / `mark_poisoned()` / `barrier()` |
| `core/poc_state.py` 判据升级 | 新增 `guarded` / `half_fixed` / `no_write` 三种结论 |

**护栏语义**（与 F8 那族缺陷的失败方向正相反）：

- 读失败 ⇒ `mark_poisoned()`：置位 + **隔离坏文件**（改名 `.corrupt-<时间戳>`，保留现场）+ 日志留痕
- 写侧落盘前 ⇒ `barrier()`：置位则抛 `StateCorrupt`，**拒绝生成新权威状态**

即"读不出来 ≠ 没有"，且失败方向从**静默清空**改成**可见地拒绝**。

---

## 二、最重要的发现：护栏装在不会被执行到的路径上

第一版补丁跑回归，9 个目标里有 3 个判 `half_fixed` / 未拦住。逐一查证：

| 位置 | 护栏装在哪 | 实际执行路径 |
|---|---|---|
| `af_store.py` `set_tags` | 装在 `_write_tags()` | **`set_tags` 自己直接调 `atomic_write_text`，压根不经过 `_write_tags`** |
| `af_config.py` `update_credentials` | 只装了读侧 `mark_poisoned` | 写侧一个护栏都没有 |
| `af_preference.py` `record` | 只装在 `_rewrite_all()`（压缩时） | 日常走 `_append_record()` 追加，压缩要累积到阈值才触发 |

**这与 F1 是同一个错误的两种写法**：F1 是无预算的 `_check_vars` 排在有预算的 `_check_expr` 前面，导致护栏永远等不到；这里是护栏装在了一条调用方不走的方法上。

区别只在于：**F1 是源码原有的，这三处是我们自己写补丁时刚犯的。** 说明"知道这个坑"和"不再踩这个坑"是两件事——写补丁时最容易犯的错，是把护栏加在**看起来相关**的方法上，而不是**实际会被调到**的那条路径上。

三处补齐后回归：9 个目标 **7 guarded / 2 no_write**。

---

## 三、回归对照

| 目标 | 修复前 | 修复后 |
|---|---|---|
| `TokenRegistry._persist_issued` | data_lost | **guarded** |
| `PairCodeStore._persist` | data_lost | **guarded** |
| `AuthCodeStore._persist` | data_lost | **guarded** |
| `DeviceCatalog._record_bucket` | data_lost | no_write（探针种不进第二次） |
| `DeviceCatalog.set_alias` | data_lost | no_write（目录损坏后校验拒绝） |
| `Config.update_credentials` | data_lost | **guarded** |
| `ExperienceStore.observe` | data_lost | **guarded** |
| `PreferenceModel._rewrite_all` | data_lost | **guarded** |
| `GraphStore.set_tags` | data_lost | **guarded** |

实测样例（`PairCodeStore`）：损坏后新建实例 ⇒ 读侧置位 + 隔离出 `state.json.corrupt-20261006T145917Z`；再 `create()` ⇒ 抛 `StateCorrupt`，错误信息直接指明现场保留位置；盘上不再出现"写成功但内容是空"的情况。

---

## 四、判据本身的修正（本轮第三个迭代点）

第一版回归把 9 个目标全判成 `data_lost`，看起来"补丁完全没用"。原因是判据错了：

> 用"播种标记还在不在"来判断，而标记是**探针自己**在损坏步骤抹掉的（往原位置写了垃圾），不是被测代码抹的。

正确的问法是：**损坏后的那堆坏字节还在不在盘上**。

- 未修复：写侧照常全量覆盖 ⇒ 坏字节被新内容盖掉 ⇒ 现场没了 ⇒ `data_lost`
- 修好之后：写侧拒绝 + 坏文件隔离 ⇒ 坏字节仍在 ⇒ `guarded`

同时新增 `no_write` 一档：**损坏内容还在但根本没发生写入**（新实例种不进数据，如目录损坏后 `set_alias` 校验失败），这时判"保住了"是自欺欺人，必须算"这一轮没测到"。

这是本项目**第六次**遇到"工具静默失效伪装成结论"——前五次是 `scripts/` 漏扫、写盘别名缺失、RES-03 全文件正则、抑制锚点行号漂移、glob 跳过点目录。

---

## 五、正式审计结果（原仓库，未打补丁）

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 跨轮 diff（vs round-009） | **new 0 / gone 0** |
| 台账 | **F1–F14 全部 still_open** |
| 递归 PoC | 24 目标：cyclic_crash 9 / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC（原仓库） | data_lost 7 / no_write 2 |

命中面与第九轮完全一致（本轮只改了探针与补丁，未动分析器规则）。

---

## 六、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |

F1–F6、F7、F9、F11、F12、F14 均**未纳入本轮补丁**，仍为 `still_open`。本轮补丁只覆盖 F8 / F10 / F13 三族（读失败降级 → 破坏性覆盖）。补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**未改动原仓库**。

递归 PoC 的 9 条 `unavailable`、4 条 `inconclusive` 仍未解决。

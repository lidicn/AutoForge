# AutoForge 审计报告 · 第十一轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-011`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-011/`
- **报告日期**：2026-10-06

---

## 一句话结论

**两侧的可测量性同时提高了**：原仓库状态 PoC 从 7/9 变成 **9/9 data_lost**；补丁副本从 7 guarded 变成 **8 guarded**。剩下 1 个目标（遥测计数）探针仍到不了，已用人工补验确认补丁行为正确。

---

## 一、本轮工作流迭代（3 项）

### W30：护栏必须紧邻落盘调用，不能放方法入口

第十轮给 `DeviceCatalog.set_alias` 装的 `barrier()` 放在方法入口，而 `mark_poisoned()` 是在几行之后的 `_load_aliases()` 里才置位——**守卫在状态被判定之前就跑完了，永远拦不住**。

这与 F1 是同一类时序缺陷（无预算的校验排在有预算的前面），只是发生在我们自己写的补丁里。已把 `barrier()` 移到 `_load_aliases()` 之后、`atomic_write_text()` 之前；并给同形态的 `remove_alias` 补上护栏。

### W31：`no_write` 升级重试

探针此前一次播种失败就判"没测到"。现在最多试 6 轮（换播种口 → 换变体），尝试记录写进 `rewrite_attempts`。

### W32：整档损坏拿不到结论时逐文件单独损坏

一次损坏所有状态文件会让 `set_alias` 的前置校验（entity_id 必须在目录里）连带失败——因为 `catalog.json` 也坏了。现在改成逐文件单独损坏，并修正决定性判据：**只认「写被拒绝」或「坏字节被盖掉」，不把只读隔离算作决定性结论**。

---

## 二、`_fetch_stub` 只返回一个实体 —— 卡了两轮的假阴性

这是本轮找到 set_alias 一直 `half_fixed` 的真正原因。

探针注入给 `fetch_all` 的桩只返回 `light.seedalpha`，而重跑阶段用 `seedgamma` 调 `set_alias("light.seedgamma", ...)`——**目录里没有这个实体，方法在写入前就 return 了**。

手工验证时我用的是目录里已有的 `light.seedalpha`，所以手工明明抛了 `StateCorrupt`，探针却始终说"没拦住"。

改成桩同时返回三个实体后，`set_alias` 立刻从 half_fixed 变成 **guarded**（隔离出 `aliases.json.corrupt-...`）。

**教训**：手工用例和自动探针用了不同的输入，导致"手工说修好了、机器说没修好"。两边结论不一致时，先怀疑是不是**喂进去的东西不一样**，而不是急着改结论判据。

---

## 三、回归对照

| 目标 | 原仓库 | 补丁副本 |
|---|---|---|
| `TokenRegistry._persist_issued` | data_lost | **guarded** |
| `PairCodeStore._persist` | data_lost | **guarded** |
| `AuthCodeStore._persist` | data_lost | **guarded** |
| `DeviceCatalog._record_bucket` | data_lost | no_write（探针到不了） |
| `DeviceCatalog.set_alias` | data_lost | **guarded**（本轮修好） |
| `Config.update_credentials` | data_lost | **guarded** |
| `ExperienceStore.observe` | data_lost | **guarded** |
| `PreferenceModel._rewrite_all` | data_lost | **guarded** |
| `GraphStore.set_tags` | data_lost | **guarded** |

`guarded` 的实测语义：写侧抛 `StateCorrupt` 拒绝落盘，坏文件被隔离成 `.corrupt-<时间戳>` 保留在现场，错误信息直接指明隔离文件位置。

### 剩下那个探针到不了的目标

`DeviceCatalog._record_bucket`（第 3 档遥测）在补丁副本上始终 `no_write`。已人工补验：损坏后调用 `_record_bucket`，**原文件完整保留，内容仍是损坏字节，没有被覆盖成空表**——F11 的修复目标（遥测不再把坏文件抹平）达成。探针到不了是因为它是纯内部遥测路径，探针的公开播种口碰不到它。

---

## 四、正式审计结果（原仓库，未打补丁）

| 指标 | 数值 |
|---|---|
| 分析器 | 24/24 ok，原始命中 **1076** |
| 去重后待办 | **1045**（抑制 4 条） |
| 严重度 | high **85** / medium 676 / low 284 |
| 跨轮 diff（vs round-010） | **new 0 / gone 0** |
| 台账 | **F1–F14 全部 still_open** |
| 递归 PoC | 24 目标：cyclic_crash 9 / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | **9/9 data_lost**（上一轮 7/9） |

门禁：25 项全过，`drift=0`，clean 零假阳性，状态 PoC 门禁 dirty 4/4 data_lost。

**9/9 这个数字上升不是缺陷变多了**，是 W32 让此前测不到的两个 DeviceCatalog 目标变得可测——测量覆盖变全了，被测对象没变。

---

## 五、十轮累计的六次同类事故

| # | 轮次 | 形态 |
|---|---|---|
| 1 | 2 | `scripts/` 漏扫，125 条"缺陷消失"实为漏扫 |
| 2 | 4 | 写盘别名缺失，规则安静地什么都不报 |
| 3 | 6 | RES-03 全文件正则冒充数据流分析 |
| 4 | 8 | 抑制锚点用行号，一漂就静默失效 |
| 5 | 9 | glob 跳过点目录，两条命中全程"测不到" |
| 6 | 11 | `_fetch_stub` 只返回一个实体，手工与机器结论打架 |

**共同点：全部表现为"结论变好/命中变少"，而不是报错。** 任何一次"看起来修好了"或"看起来没了"，都先怀疑工具，再相信结果。

---

## 六、仍未覆盖

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |

F1–F7、F9、F11、F12、F14 均**未纳入补丁**，仍为 `still_open`。补丁只覆盖 F8 / F10 / F13 三族，且只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

递归 PoC 的 9 条 `unavailable`、4 条 `inconclusive` 仍未解决。`_record_bucket` 的自动验证仍缺。

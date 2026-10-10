# AutoForge 审计报告索引（docs/audit/）

> 整理日期：2026-10-06 ｜ 整理人：开发 Agent
> 目标：干净的 audit 目录，顶层仅本索引文件，所有审计报告归入子目录。

## 整理动作记录
- **删除 7 个解压过的 zip**：`AF第二轮审计报告.zip` … `AF第七轮审计报告.zip`、`AutoForge安全审计报告.zip`（均已解压，内容由顶层 `AutoForge_第X轮审计报告.md` + `审计报告_第X轮_核实与修复.md` 覆盖）。
- **删除 5 个重复解压子目录**：`AF第三轮审计报告/`、`AF第四轮审计报告/`、`AF第五轮审计报告/`、`AF审计第六轮/`、`AF审计第七轮/`（与顶层报告重复）。
- **已完成核实修复的报告统一移入 `归档/`**（当时 39 份；现 72 份，见 §一）。
- **非审计资料移入 `参考/`**（调研 / 评审 / 测试集，4 份）。
- **元宝新增的 20 份审计报告保留在 `元宝/`**，待逐轮核实（见下文）——此为 2026-10-06 整理当日口径；后续已逐轮核实收口，2026-10-08 现读 `元宝/` **0 份**、`归档/` **72 份**（与下方"目录结构"一致，更正标记依据执行记录 §二之七十 §五）。

## 目录结构
```
docs/audit/
├── index.md        # 本文件（唯一顶层索引）
├── 归档/           # 已完成核实与修复的审计报告（72 份）
├── 元宝/           # 已清空（原 20 份全部核实收口并转入 归档/）
└── 参考/           # 调研 / 评审 / 测试集（4 份，非审计）
```
（顶层另有一个隐藏 `.gitkeep` 占位文件，无害，不影响整洁。）

---

## 一、归档/（已完成核实与修复，72 份）

> 份数可对账口径：**72 = A 19 + B 9 + C 24 + §二转入的元宝 20**。C 组 24 份含 ADM-auditkit 体系
> 第八~二十轮（第八~十三轮那六份此前从未被列举，索引从第七轮直接跳到第十四轮——2026-10-08 补入，
> 逐份定性与 HEAD 复测见 `docs/ADM联动执行记录-AF.md` §二之七十一）。
> 现读核对：`ls docs/audit/归档 | wc -l` = 72、`元宝/` = 0、`参考/` = 4（2026-10-08）。

### A. 多 Agent 主题审计报告（第一轮体系，19 份，已由《审计回执》核实收口）
- 审计报告-并发与异步正确性.md
- 审计报告-错误处理与降级路径.md
- 审计报告-控制流完整性与异常契约.md
- 审计报告-可观测性与遥测正确性.md
- 审计报告-外部输入与信任边界.md
- 审计报告-资源上限与复杂度.md
- 审计报告-序列化往返与跨层契约.md
- 审计报告-死代码与缓存契约.md
- 审计报告-稳定性与功能性缺陷.md
- 审计报告-配置传播与版本兼容性.md
- 审计报告-时间与数值边界.md
- 审计报告-数据一致性与事务边界.md
- 审计报告-状态一致性与崩溃恢复.md
- 审计报告-读写一致性与单例生命周期.md
- 审计报告-鉴权与权限边界.md
- 审计报告-测试与验证缺口.md
- 审计报告-模式泛化与语义正确性.md
- 审计报告-资源生命周期与清理.md
- 审计报告-最终轮.md

### B. 轮次核实与修复记录（9 份）
- 审计报告_第二轮_核实与修复.md
- 审计报告_第三轮_核实与修复.md
- 审计报告_第四轮_核实与修复.md
- 审计报告_第五轮_核实与修复.md
- 审计报告_第六轮_核实与修复.md
- 审计报告_第七轮_核实与修复.md
- 审计报告_安全审计_核实与修复.md
- 审计报告_增量模块_20260930.md
- 审计回执_十三轮BUG核实与修复_20261006.md（BUG-01…21 共 21 项收口总账）

### C. 原始审计底稿 / 独立审计（24 份）
- AutoForge_第二轮审计报告.md
- AutoForge_第三轮审计报告.md
- AutoForge_第四轮审计报告.md
- AutoForge_第五轮审计报告.md
- AutoForge_第六轮审计报告.md
- AutoForge_第七轮审计报告.md
- AutoForge_第八轮审计报告.md（ADM-auditkit 体系，round-008 工具链重建 + 两份 PoC 首次同轮产出：**本轮未新增缺陷**（该轮 §五 自记），"F1–F14 全部 still_open" 是审计方台账口径；对 AF 的新事实 = `GraphStore.set_tags` 的 F8 **第一次被端到端自动确证**（前七轮为人工实测）。收口见执行记录 §二之七十一）
- AutoForge_第九轮审计报告.md（ADM-auditkit 体系，round-009 状态损坏 PoC **首次 9/9 data_lost**：F8/F10/F13 三族此前全靠人工实测支撑，本轮由机器端到端复现，九站清单在该轮 §二。AF 侧落码 `3d49595`（四站）+ `f315112`（三站），另两站（`af_store` 写侧、`set_alias`）按 HEAD 现读早已是拒写口径）
- AutoForge_第十轮审计报告.md（ADM-auditkit 体系，round-010 补丁副本 7 guarded / 2 no_write：**原仓库未改动**（该轮 §六 自记），"已修"只存在于 `/data/workspace/repos/af-patched`；给 AF 的真产品结论是 §二 那条**护栏装在不会被执行到的路径上**——`set_tags` 不走 `_write_tags`、`update_credentials` 只装读侧、`record` 只装压缩腿。AF 落码时按此把护栏装进真正落盘的方法体并写成结构判据）
- AutoForge_第十一轮审计报告.md（ADM-auditkit 体系，round-011 两侧可测量性同时提高（原仓库 9/9、补丁副本 8 guarded）：W30「护栏必须紧邻落盘调用，不能放方法入口」+ `_fetch_stub` 只返回一个实体造成的假阴性；`_record_bucket` 探针到不了改人工补验。AF 侧该站落在 `f315112`，按第 3 档改为静默跳过）
- AutoForge_第十二轮审计报告.md（ADM-auditkit 体系，round-012：**F11 补丁自身静默失效**——少 import `quarantine`，NameError 被本函数既有的 `except Exception: return` 吞掉，数据保住了但既无隔离也无日志，"修了等于没修"；W33 patch_lint 上门禁、W34 第 3 档判据（遥测不该要求抛异常）。AF 侧 `f315112` 的"拒写与静默跳过按档位分开"与"留痕移到宽 `except` 外面"直接采用该口径）
- AutoForge_第十三轮审计报告.md（ADM-auditkit 体系，round-013 F14 补丁副本 0 崩溃：实测证明**环检测与深度预算正交**——只装 `visited` 后 9 个 cyclic_crash 变成 8 个 depth_crash，Python 栈上限先于业务预算触发。AF 侧口径不同：预算装在**每个递归站点入口**，环每绕一圈深度 +1 必然撞上限（`af_ir/models.py:166` 明写"自引用不需要 visited"），九站现读全部带 `check_*_depth`；**该轮 §三 的 `_leaf_key` 第二条失败腿成立且预算挡不住**（叶子是终端，不经遍历），本批收成具名 `LeafUnserializable`）
- AutoForge_第十四轮审计报告.md（ADM-auditkit 体系，F15 出站盲跟 3xx：核实成立，已修 + 上门禁，见执行记录 §二之五十六）
- AutoForge_第十五轮审计报告.md（ADM-auditkit 体系：F15 同批提出，AF 侧半边已修 `79d1c3e`；F16 判在 homesdk 库侧，裁定 §六 Q3=A）
- AutoForge_第十六轮审计报告.md（ADM-auditkit 体系：**无新增 AF 缺陷**，动作项在 auditkit 自己仓里；"F1–F16 全部 still_open" 是 zip 快照口径）
- AutoForge_第十七轮审计报告.md（ADM-auditkit 体系，W43 递归 PoC：F6 **首次自动实测确证**，AF 侧已修，见执行记录 §二之五十九）
- AutoForge_第十八轮审计报告.md（ADM-auditkit 体系，W44 `poc_failopen`：F12 **端到端确证覆盖真的发生**，AF 侧已修，见执行记录 §二之五十九）
- AutoForge_第十九轮审计报告.md（ADM-auditkit 体系，W45 递归 PoC 26 目标：F6 **运行期路径 `_satisfied` 首次确证**，AF 侧第十七轮已修；`via_stub=True` 一档强度低于自动实测，已按"核实成立但已修"登记）
- AutoForge_第二十轮审计报告_最终轮.md（ADM-auditkit 体系收官：台账 F2 成立、AF 侧本批已修并补闸门侧 `assert_param_budget`；F1 站序倒置同批收口；W46c 探针卡死宿主根因未定位，`safe_manual` ≠ 没问题——见执行记录 §二之六十一）
- 代码审计报告_20260918.md
- AutoForge代码审计报告hy4.md
- AutoForge代码审计与建设性建议byhy3.md
- AutoForge_稳定性与功能性审计报告.md
- AutoForge架构评估与Token优化.md

---

## 二、元宝/（已清空：20 份逐轮核实完毕，报告与本仓核实修复记录一并移入 `归档/`）

> 2026-10-06/07 两批收口：原列 20 份（第二轮…第二十轮 + 内核安全深度审计）已全部核实——
> 成立项落码并补判据、已被历史修复覆盖项登记"核实成立但已修"、不成立项写明理由。
> 收口总账见 `归档/审计回执_十三轮BUG核实与修复_20261006.md`（BUG-01…21）与
> `docs/ADM联动执行记录-AF.md` §二之五十二～五十八。
> 归档总数因此从 39 份涨到 66 份；`元宝/` 目录现为空，保留占位不再回装。

- ~~AutoForge_内核安全与架构深度审计报告.md~~ → 已归档
- ~~AutoForge_第二轮稳定性与功能性审计报告.md~~ → 已归档
- ~~AutoForge_第三轮深度缺陷审计报告.md~~ → 已归档
- ~~AutoForge_第四轮并发与状态一致性审计报告.md~~ → 已归档
- ~~AutoForge_第五轮契约与语义一致性审计报告.md~~ → 已归档
- ~~AutoForge_第六轮可恢复性审计报告.md~~ → 已归档
- ~~AutoForge_第七轮资源生命周期与门禁审计报告.md~~ → 已归档
- ~~AutoForge_第八轮门禁与测试套件有效性审计报告.md~~ → 已归档
- ~~AutoForge_第九轮配置面健壮性审计报告.md~~ → 已归档
- ~~AutoForge_第十轮持久化层审计报告.md~~ → 已归档
- ~~AutoForge_第十一轮_ADM-auditkit部署与召回率验证报告.md~~ → 已归档
- ~~AutoForge_第十二轮候选分诊审计报告.md~~ → 已归档
- ~~AutoForge_第十三轮同形状横向传播审计报告.md~~ → 已归档
- ~~AutoForge_第十四轮调用图展开与跨进程边界审计报告.md~~ → 已归档
- ~~AutoForge_第十五轮安全闸绕过审计报告.md~~ → 已归档
- ~~AutoForge_第十六轮失败方向审计报告.md~~ → 已归档
- ~~AutoForge_第十七轮契约声明验证审计报告.md~~ → 已归档
- ~~AutoForge_第十八轮入口对等性审计报告.md~~ → 已归档
- ~~AutoForge_第十九轮新鲜度契约审计报告.md~~ → 已归档
- ~~AutoForge_第二十轮所有权隔离审计报告.md~~ → 已归档

---

## 三、参考/（非审计报告，背景资料，4 份）
- 调研_autoflow对照_全模块.md
- 调研_autoflow对照_实体链路.md
- 评审_AutoForge进度架构与建议_2026-09-15.md
- FFL-200题测试提示词.md

---

## 四、状态与待办
- ✅ audit 目录已清理：7 个 zip 删除、5 个重复解压子目录删除、已完成报告归档、非审计资料独立。
- ✅ 顶层仅本索引文件（另有隐藏 `.gitkeep` 占位，无害）。
- ✅ **元宝 20 轮审计报告已全部核实收口**（见 §二）：成立项落码 + 补判据，历史已覆盖项登记"核实成立但已修"，
  不成立项写明理由；总账在 `归档/审计回执_十三轮BUG核实与修复_20261006.md` 与 `docs/ADM联动执行记录-AF.md`。
- ✅ **ADM-auditkit 体系的第十五轮（F15 出站盲跟 3xx）**：本仓已修 + 上门禁（commit `79d1c3e`，执行记录 §二之五十六）。
- ⏳ **同批第十五轮的 F16（`homesdk/gates/scan.py` 三处自递归无深度预算）**：裁定
  `20261007-MA五件与AF一件-裁定.md` §六 Q3 = **A（库侧修，排 0.3.3）**，AF 不做绕行；
  AF 侧只把"依赖门禁崩掉时不许读成违规、也不许读成干净"分三档记账（commit `608cdf1`，执行记录 §二之五十七）。
  受害面含 MA/DB 的 CI——生态 bug，不止本仓台账。
- ⏳ **顶层 `AutoForge_第十六轮审计报告.md`（ADM-auditkit 体系，2026-10-07）**：核实结论 = **本轮无新增 AF 缺陷**。
  该轮动作项（W41/W42/`ensure_tools.py` 与"跨 bash 调用工具丢失"）全在 **ADM-auditkit 自己那份仓**里，不在 AF；
  其"台账 F1–F16 全部 still_open / 原仓库未改动"是 **zip 快照口径**，不是 GitHub HEAD——
  F8~F16 中 AF 侧的修复已在 `9aa6499`/`79d1c3e`/`608cdf1` 落地（本仓口径见项目记忆"外部审计跑的是快照不是 HEAD"）。
  该轮 §六 的"依赖 CVE 面十六轮都没扫 + homesdk 私有 wheel 无法装运行时探针"是**审计工具链的射程缺口**，
  AF 侧唯一能自决的是"不许把这格读成无风险"——已在执行记录里挂名为待窗项，不谎报。
- 🔗 关联：F12 联动（MA→AF 指标回灌）已于本会话闭环，见 `docs/handoff/_af_exec_append.md` §6.3（✅ 已闭环）。
  ⚠️ 同号不同事：本行的"F12"是**计划 §六**的 F12；下面两条 ✅ 里的 F6/F12 是**审计台账**（ADM-auditkit F1–F16）编号。
- ✅ **ADM-auditkit 体系的第十七轮（台账 F6：trigger group 递归无深度预算）**：核实成立且 HEAD 确有洞
  （第二轮起提出、十五轮只是"静态命中＋人工推理"，本轮探针第一次自动实测确证 4 处崩溃）。
  AF 侧已修：`af_ir.models.MAX_TRIGGER_DEPTH = 32` + `check_trigger_depth` + `TriggerDepthError` 收成单一真源，
  七个递归点共用（含运行期每事件都走的 `af_scheduler._satisfied`），两处手抄 `> 32` 字面量清除；
  新增 13 条判据（含 CONTROL 浅树、恰好到界、自引用、反空洞 no-op 腿）。执行记录 §二之五十九。
- ✅ **ADM-auditkit 体系的第十八轮（台账 F12：别名共享目录覆盖）**：核实成立，且比第五轮人工读数更严重——
  `poc_failopen` 证明**覆盖真的发生**，不只是守卫放行。AF 侧已修：新增 `ArchiveOwnerUnknown`，
  写路径（`_dir_owner` 与 `resave_raw` 锁内复用支）与删路径（`assert_deletable` 的 `continue`）全部 fail-closed，
  HTTP 面 409 而非 500；"归属未知"既不编造主人也不假设无主。执行记录 §二之五十九。
  ⚠️ 报告 §四 那句"写入路径 `_dir_owner` **早前已修**"是**补丁副本/zip 快照口径**，现读 HEAD 两处都还 fail-open；
  按项目记忆"外部审计跑的是快照不是 HEAD"复测后才落码。
- ✅ **ADM-auditkit 体系的第十九轮（F6 运行期路径首次确证）**：核实成立。上一轮只有保存时的 scanner 路径被实测，
  `af_scheduler._satisfied` 一直是 `inconclusive`（`ModuleNotFoundError: homesdk`）；本轮用桩把它跑成**确证崩溃**，
  且它是**每个事件都走一次**的运行期路径，暴露面比保存时校验更宽。AF 侧的守卫在第十七轮已装在这一层
  （执行记录 §二之五十九 明写"运行期每事件都走的 `af_scheduler._satisfied` 共用同一份预算"），本轮登记为
  **核实成立但已修**；同时把报告自记的两条工具链结论收下：`via_stub=True` 的证据强度低于自动实测（不能混算成确证），
  `var=or` 才崩这一形状差异记入台账——深度阶梯不换 op 也照样崩，**形态差异只在环上体现**。
- ✅ **ADM-auditkit 体系的第二十轮（最终轮，台账 F2 + F1）**：两项均核实成立，AF 侧本批已修（执行记录 §二之六十一）。
  - **F2**（medium/P1，条件与参数遍历无深度预算）：预算常量早就存在，**缺的不是预算，是遍历路径没接上**——
    报告的正反对照（`expr.py:270 _walk_operand` 真用了预算 ⇒ 实测 safe；族内 `_walk`/`_nnf`/`_canon` ⇒ 实测崩）与此一致。
    本轮实测 18 处崩溃站点落在 **496–997 层**，而 `json.loads` 稳定送达 ≥1000 层 ⇒ 中间那段是真实可达面。
    修法：全部引同一份预算（`check_expr_depth` / `check_trigger_depth` / `check_param_depth` / `MAX_CNF_CLAUSES`）；
    并补上**闸门侧缺的那一档** `assert_param_budget`（原先预算只装在遍历腿上，校验闸门不查 ⇒ "build 放行、运行期内省必失败"的 IR 能进库）。
    实测闸门与遍历腿**同档**拒绝（容器 64 层放行 / 65 层拒），不拒合法存量 IR（`examples/ir` 最深 9 层）。
  - **F1**（high/P0，扫描器校验顺序倒置、护栏排在遍历之后，站点 `af_scanner.py:348`）：现读 HEAD 坐实
    `_check_vars` 排在 `_check_expr` 之前；本批把站序改为 expr → trigger → params → vars，并给 `_check_vars` 加 `except ExprError`。
  - **扫描器契约补齐（本轮真产品发现，非台账项）**：`scan()` 原本会把超预算但可载入的 IR **抛穿**给调用方
    （修完 `reads()` 那根腿又换 NL 那根、再换触发源那根）。对外契约改成"返回诊断"：新增
    `TRIGGER_INVALID` / `PARAMS_TOO_DEEP` 两格诊断，实体依赖腿经容错单点走，写侧不受读侧被拒影响。
    判据：`test_ir_expr_depth_budget_walkers.py` 41 条（含 20 站点腿清单集合相等、CONTROL、边界、反空洞）+
    `test_ir_trigger_depth_budget.py` 16 条（F6 那两条"断言扫描器内部会抛"的腿按分层契约上移为"`scan()` 落 `TRIGGER_INVALID`"）。
  - ⚠️ **报告 §四 的"F1–F16 全部 still_open"仍是 zip 快照口径**（项目记忆：外部审计跑的是快照不是 GitHub HEAD）；
    AF 侧现况：F1/F2/F6/F12/F15 已修，F16 = 裁定库侧修（排 homesdk 0.3.3，AF 不绕行）。
  - 📌 **审计工具链自身的射程缺口（不是"AF 无风险"）**：`_build` 那一格是 **`safe_manual`（手工结论，强度低于自动实测）**，
    不等于没问题；探针 W46c 加 `RLIMIT_AS/RLIMIT_CPU` 后**仍复现卡死、根因未定位**——依赖 CVE 面（pip-audit）、
    semgrep 规则面、homesdk 运行时探针、变异测试仍未纳入。这几格在 AF 台账里一律挂名为待窗项，不读成干净。
- ⏳ **第十九轮之后仍会持续进件**：新报告到达即按"先复测 HEAD、成立项落码并补判据、已覆盖项登记'核实成立但已修'、
  不成立项写明理由"四档收口，不在核实前登记状态。

---

## 三、第二期（AF1…AF21）2026-10-11 现读收口

- 对账全文：`第二期审计核实与修复对账_20261011.md`（逐条 file:line、PoC 读数、归档判据）。
- **已移入 `归档/`**：`AutoForge_第二期第一轮审计报告.md`（AF1）、`…第二轮…`（AF2/AF3/AF4）、
  `…第三轮…`（AF5 属"核实成立·已修（非本批）"，现读 `af_store.py:291-313` 已 `raise ArchiveOwnerUnknown`）、
  `…_第04轮…`（AF7 属"核实成立·已修（非本批）"，现读 `af_ir/expr.py:268,284` 已带 `_depth` 接同一份预算）。
- **留在原地**（按判据"还剩一条成立·未修就不归档"）：
  - `AutoForge_第二期第五轮审计报告.md`——AF8（`pydantic` 未声明依赖，现读 `grep -n pydantic pyproject.toml` 零命中）
    属交付口径问题，**不自决**，已进 DCD 攒批（与 ARCH-02/03/04/05/06/07 同批）。
  - `AutoForge_第二期审计报告_第六至二十轮合并.md`——AF18／AF21 影子档半边本批已修；
    AF13/AF14 落在登录线在途文件（`af_api.py`／`af_auth.py`）本批不碰；AF15 封顶口径已被
    `scripts/check_bounded_caches.py:505,576` 收进基线、待裁；AF21 的 ask 档半边要动
    `resume`／`pending_confirm` 生命周期语义，递 DCD。
  - 三份 HTML（`AutoForge审计报告.html`／`AutoForge安全审计报告.html`／`AutoForge运行时审计报告.html`）
    各自收口台账见执行记录 §二之九十八~一百零一。
- 现读计数（2026-10-11）：`ls docs/audit/归档 | wc -l` = **76**（§一 写的 72 是 2026-10-08 口径，本批 +4）；
  `ls docs/audit | wc -l` = **10**，其中报告与对账件 6 份（`*.md`＋`*.html` 现读 7 枚含本节对账件）＋`index.md`＋三个目录。

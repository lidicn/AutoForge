# AutoForge 开发者日志（流水）

> 维护：AF-PM｜UTF-8 / LF｜纯落盘文档，未 commit
> **写法**：本文件是 append-only 流水。顶部「当前进度」是快照（每次更新覆盖），下方流水**只追加、时间正序**，每笔一行：`时间 | 谁 | 做了什么 | 结果/关键读数 | sha`。
> 配套：架构权威件见 `docs/AF_开发者手册.md` §0；治理流水见统一台账 `E:\NAS\QA\AF_Tester\ledger\AF_unified_ledger.md`；PM 协同状态见 `docs/AF_PM状态日志.md`。
> ⛔ 令牌值/授权码值不进本文件（一律 `auth_code:xx***` 掩码）。

---

## 当前进度快照（2026-11-05 09:5x）

- **线上**：`v1.25.0`（`origin/master` = `68739fd`，tag `v1.25.0` peel 同 sha），六作业 CI 全绿、四道坐标全中、容器内 homesdk 0.3.3 + areas 可用、watch alive。
- **本批**：**v1.26 修复批（D1–D9）派卡在途**，各席位独立分支落码，**⛔ 均未 push / 未部署 / 未 tag**；中午出资人回来测试。
- **已交卷**：
  - AF-UI 分支 `ui/v126-d6-freshness`（基 `68739fd`）：`b862c9a`（D8 触发读数：visibilitychange/focus 补拉）、`292c840`（D6 真机下发 UI：短期码输入框＋开关置灰＋unmodeled 终态）、`ac649be`（D1 后半：Agent 列表按 scopes 打身份族徽标）。读数：mimo `npm test` 94 passed、`vite build` EXIT=0、ui `vue-tsc`/build EXIT=0、`gates.sh` RC=0、行尾逐文件保持。
  - AF-Core 分支 `pm/v125-room-from-entity`：6 笔 `3706cc9`（room 实体反查，TTS 静默根因）/ `7d62f12`（D8 canary 三处）/ `8222b7e`（D2 归档写闸）/ `dd77845`（D9 emit 挂点）/ `2937f69`（D3 consume 时序）/ `0775793`（D4 禁用后 watch 重装）。每笔 `gates.sh` RC=0，D3/D4 判据有变异自证；判据族复跑在途。
- **AF-Core 剩余**：D5（单发三门：只放 developer/owner 主体＋治理面运行期开关默认关＋单发只认短期码，⛔ 不影响 watch 常驻执行）、D1 后半（`live_run` 单发同步推进观察窗）。
- **AF-Verify 已落票**：v1.25.0 部署面票 `AF_verify_v1250_deploy_68739fd_20261105.md`（sha8 `30EC6214`，四道坐标＋9 文件逐字节＋变异自证，通过）；触发抖动真机票 `AF_verify_trigger_jitter_20261105.md`（sha8 `64C30BAD`：空调被命令 0 次、HA logbook 48h 无 set_temperature、四个归档 d2 参数全 26、无「20 度」痕迹、live 是 v3.json 观察模式）；凭据落点清单 `AF_verify_cred_landing_20261105.md`（sha8 `1758BCCB`，已脱敏）。MIPS 门槛 3 轮连续 Δ=0 已满足。
- **待办/挂账**：v1.26 双闸＋CI→tag→部署；dsh 令牌轮换＋ACL 收紧＋清落点（Chief 裁定：重测一结束立即做，⛔ 不滑下批）；pair-redeem subject 自选保留名校验（QA-Code 跟踪）；`trial.outcome`/`bands` 映射/三步条文案三件（AF-UI 报告 §七）；asks 归属者 404 裁决；instance trace 覆盖洞（AF-Verify 提出，待 AF-Core 确认落点）。
- **设备状态**：空调 `off`（09:0x 由 PM 经 `af_live_run` 关闭，出资人在场）；⛔ 中午前不再动任何设备。
- **纪律**：真机调试标准通道＝`af_live_run`＋短期码；双窗口协同＝行动前读 `docs/AF_PM状态日志.md`（见该文件协议）。

---

## 流水（时间正序，追加到文末）

### 2026-11-03
- 07:5x | 出资人 | 7 条裁定落地 | sync 维持稳定版（后升级为全面退役）、磁盘清理、AF-717 不起真机窗、AF-402 6~8 位真拦＋AF-507 按①收、不做多用户（非 owner 看不到＋禁导出）、AF-733/734 权限矩阵由 PM 定 | 台账 §1.16x
- 14:1x | 出资人 | 拍板弃用 sync 容器 | watch 由 api 容器从 store 驱动，接受重启约 30s 空窗；`docker-compose.sync.yml` 仅留作回退备件 | 已执行
- 11-03 全天 | 团队 | 发版 v1.17.0 → v1.18.0 → v1.19.0 → v1.20.0 → v1.21.0 | v1.20 守卫真机首验（SKIP 0→1）；v1.21 治理面五件（AF-733/734/732/402/507），CI 红→AF-Core 实测纠偏→收口 | `94a57c7` 等

### 2026-11-04
- 凌晨 | PM | v1.22.0 发布部署 | history 跳过点目录防御＋UI 渲染缺口可见性；守卫第二次真机验证通过；MQTT TTS 链路真机验通（`butler/inbox/speak`，DB inbox_events accepted，小爱念出来了） | `b11af70`
- 白天 | 团队 | v1.23.0 / v1.24.0 发布部署 | EOL 真树差分判据（`b5cd10d`）；authcode approve 双通道修复（`adb5ea5`）＋mimo 归档按钮（`c6c4549`）；CI run 282 六作业全绿 | `3b8c91d` / `8a63291`
- 13:2x | DCD | 裁定 1：主动问询 MQTT 跨仓契约 | 阶段1（乙，单向播报，零新 DB 代码）→ 阶段2（甲，双向，前置 AF 先出 MQTT 版 ask 面）；C7 配额撤销 | `decisions/20261104-AF-主动问询MQTT跨仓契约-裁定.md`
- 18:4x | DCD | 裁定 2：房间定义统一到 HA 区域 | 房间 canonical 键 = `area_id`；homesdk 0.3.3 交付 `areas` 模块（583 passed，wheel 55,345 B，分发三仓）；硬判据：resolve None ⇒ AF 拒投递＋`AREA_UNRESOLVED` | `decisions/20261104-DB-房间定义统一到HA区域三仓契约-裁定.md`
- 夜间 | AF-Core | 五笔+一笔落码（分支 `pm/v123-homesdk-areas-batch`） | ask 播报 / T1 do 播报 / T3 运行期事实 / T2 do trace / homesdk 0.3.3 / AREA_WORDS 废弃；46 枚判据族 856 passed、gates RC=0 | `13f903c`
- 22:0x | PM | v1.25 开窗（9 笔形状） | core 6＋UI 3＋bump；与群窗口并发撞车（详见 `AF_PM状态日志.md`） | 台账 §1.163
- 23:1x | PM | CI run 287 红（`af_orchestrator.py` 站点漂移、AF-718 基线未钉）→ 重钉 `68739fd` → run 288 六作业全绿 | 族覆盖洞教训：不带 `gate` 的判据本地族扫不到 | `68739fd`
- 23:4x | PM | **v1.25.0 发布收官** | tag peel `68739fd`；部署四道坐标全中、homesdk=0.3.3、watch alive、守卫第六次真机验证通过（SKIP 5→6） | 台账 §1.164

### 2026-11-05
- 08:1x | PM | v4 部署完成（archive v2→v3，`d2→p1 on_error` 边） | 新短期码（掩码 `21***`）一发命中；破案：`dsh` 令牌在 harness MCP 连接器配置（签发表指纹实锤） | 台账 §1.165
- 08:2x | 出资人 | 重测发现归档件在开发者 UI 可见、mimo 不可见 | 拍板「重新新建自动化，不要修改归档的自动化」→ 新建 `study_ac_smart_v5test` 部署（长期码只计 use_count） | 台账 §1.166
- 08:2x | Chief/AF-Verify | 凭据面事件 | 连接器配置文件 ACL 含沙箱组可读 ⇒ 裁定：重测后立即轮换 dsh 令牌＋ACL 收紧＋清落点；AF-Verify 出脱敏落点清单 | sha8 `1758BCCB`
- 08:3x | PM | 三症状取证：旧档双跑（watch 咬旧快照）、禁用不重装、TTS 静默 | 重启 api 让 watch 从 store 重建 ⇒ 只剩 v5test 一条活跃；取证链：AREA_UNRESOLVED 2 条、MQTT_PUBLISH_FAILED、canary observing 不下发 | 台账 §1.166/§1.167
- 08:5x | PM | **T2 根因坐实**：canary 挂起 L2 动作永不执行 | `af_live_run` 单发调试：实例 `suspended`、动作停 `pending_canary`，温度写从未到空调；`scheduler.tick()` 无 canary_observe 分派、单发模式无 tick 循环 | 台账 §1.166（第二处）
- 08:5x | PM | `af_live_run` 定为真机单动作调试标准通道 | 实测三重闸诊断质量合格；出资人拍板「操控设备须我在场」⇒ 三门收口方案（只放 developer/owner＋治理开关默认关＋单发只认短期码） | D5
- 09:0x | 出资人 | 拍板 v1.26 修复批（D1–D9）＋三份协作文档 | 中午回来测试；要求行动前先读 PM 日志 | 本月批次
- 09:1x~09:4x | AF-UI / AF-Core / AF-Verify | v1.26 交卷（见「当前进度快照」） | AF-UI 3 笔、AF-Core 6 笔、AF-Verify 3 票；D5 与 D1 后半仍在办 | `b862c9a`…`0775793`
- 09:5x | PM | 落盘三份文档＋把「行动前先读 PM 状态日志」写进 `AGENTS.md` | 手册 / 开发者日志 / PM 状态日志（带抢锁协议） | 本文件

### 2026-11-05（下午～晚，v1.26 发版与真机验证）
- 13:1x | 私聊窗 | v1.26 发版：建树 `7e31636`＋UI 4 笔＋bump `226db97`＋双闸＋族 3 红判据翻新 ⇒ push `9fe7a5e` | 族口径第三处盲区（CI 红 1 条） | `9fe7a5e`
- 19:2x | 群窗口 | **强制回收锁**（私聊窗租约过期）→ 接管收尾：CI 红判据翻新 `bba375c` → CI 290 全绿 → **tag `v1.26.0`** | 协议首次真实交接成功 | `bba375c8`
- 21:0x | 三方 | 装载面三层定性关账（装载=archived／触发=scheduler 三路守 enabled／执行=哑执行） Chief 签认 | 判例入 DCD 库 §八 | 台账 §1.173
- 21:4x | 出资人 | ask_user 亲选「部署 v1.26.0」 | 群窗口按拍板单执行 | 台账 §1.172
- 21:59 | 群窗口 | **v1.26.0 部署完成**（`to=bba375c8`，四坐标＋守卫第七次） | 线上=1.26.0、homesdk 0.3.3 | 台账 §1.172
- 22:3x | 私聊窗＋出资人 | **真机温度控制验证通过 ✅** | `af_live_run` 单发（短期码 `58***`）：实例 `done` 到 p1、无 canary 挂起（D1/D8 生效）；空调 `state=cool target=26.0` 三连读稳定；**出资人亲眼确认 26 度** | T2 关账
- 22:4x | PM | TTS 静默定性：非静音时段（DB 惯例 23:00 起）——DB 今晚零推送、watch.log 零 speak 行 | 根因＝单发模式不接 MQTT 播报桥（桥只在 watch 常驻注入）＋自动化未武装 | —
- 22:43 | 私聊窗 | 重启 api 重装载 watch ⇒ **v5test 武装**（IR 只含 v5test enabled=True） | `alive=True` drifted=False | —
- 22:43-45 | 自动化 | **全链路真机跑通 ✅** | 触发→条件命中→d1 制冷＋d2 设 26 下发→T1 播报→DB `accepted`（22:43:47，room 反查生效无 AREA_UNRESOLVED）→**出资人听到小爱**→空调 26 度；runtime facts live（today=329 触发计数、trial=observing）；329 次触发仅 1 次全条件命中＝条件闸有效 | 全绿
- 待办 | PM | dsh 令牌轮换＋ACL 收紧（Chief 硬时限：重测结束即做）；v1.27 候选（装载层 enabled 过滤、harness 连接器修复、pair-redeem 保留名、族口径修正）；DB speak 预算读数变化（`budget=1/unlimited`，11-04 是 `2/3`——移交 DB 席位核） | —
- 22:5x | 出资人 | 空调变 27 度＝**米家安睡模式夜间自动上调**（出资人判定），与自动化无关 | AF 侧佐证：spec 只写 26、无 27 写入路径；AF-Verify 26 度真机验证已关账 | —
- 22:5x | PM | **D10/D11 派卡（v1.27）**：D10＝派发冷却期（同自动化派发后默认 10 分钟静默，可配；治温度在 28.5 附近徘徊时小爱反复念叨）；D11＝播报文案模板化（按出资人原话「当前书房温度是 {实时温度} 度，已经帮你设置空调为制冷模式26摄氏度」，需 IR 支持传感器实时值插值） | v1.27 批次 | —
- 23:0x | PM | **D10 语义升级（出资人提问触发）**：派发改**边沿触发**——只在条件「不满→满足」跳变沿派发一次，持续满足不重派，先断后立＝新回合；冷却期降级为兜底 | 判据四腿已钉 | —
- 23:1x | PM | **阶段2（甲）双向主动问询派卡**＋DB 交接卡落盘（`交接卡_AF-MQTT主动问询阶段2开工_20261105.md`）：AF-Core 落 af/ask/pending 发布＋af/ask/answer 订阅腿（明日开工），DB 侧按裁定并行，联调窗口等交卷 | AF 侧距离≈一天落码＋联调 | —
- 23:3x | PM＋出资人 | **试演机制边界盘点（出资人追问触发）**：四条短板入池——①属性级动作盲判（canary 只看 state，set_temperature 判 unmodeled＝Bug-1 类问题本可自动抓）②expect 后置条件没人写（半强制提案）③sim 不在部署必经工作流④多自动化并发盲。QA via MCP 战果澄清：静态闸层已抓过 AF-723/AF-716；运行期两层价值未兑现的三因＝工作流缺失＋expect 缺位＋unmodeled 盲区 | v1.27/v1.28 池：属性级漂移判定／expect 半强制／sim 必经／并发快照 | —
- 23:2x~23:4x | PM＋出资人 | **dsh 令牌轮换完成（Chief 硬时限兑现）** | ①出资人删旧 dsh agent＋出配对链接 ②私聊窗 redeem（agent_name=dsh 保归属连续）→ 新令牌 subject=dsh live/read/write 实锤（af_whoami）③连接器配置换新令牌 ④**ACL 收紧 4 文件**（连接器配置＋凭据 yaml＋2 备份：CodexSandboxUsers 移除，SYSTEM/Administrators/lidicn owner-only——对齐 NAS 600 尺子）⑤落点全清核验（af_token/af730_tokens 6 枚全 gone）⑥一次性配对脚本已删 ⑦post-ACL whoami 通过。遗留：只读 scope 令牌签发（等 harness 连接器修复时做） | 轮换窗关账 | 台账 §1.166
- 16:4x~16:58 | PM | **v1.28.0 发版闭合（部署段）**：AF-739 根因修复（真根因＝条件成立≠已派发记账错位，unmodeled 假设代码实证排除）＋ask 双向桥＋单发回执＋undo；部署 16:57:49 四坐标全绿（容器 7 文件 blob 逐字节同、`:prev` 回滚网完好、watch 自动还原 v6 零漂移、health 版本坐标失真消除） | ✅ 线上 1.28.0；余：出资人关门终验＋AF-Verify 部署面票 | e87e646
- 19:0x~19:38 | PM | **部署面票接管**：AF-Verify 两度回合中断（确认卡停机＋中途回话停机），出资人三次催办 ⇒ PM 令其停手并接管采集落票（19:38 新采五读数全绿，接管票如实标注非独立第三眼）；红判据名考证＝check-run 注释无名字、2 候选判据均行尾容忍 ⇒ 维持推理不坐实 | ✅ 票落盘 sha8 `37a212f0`；Verify 独立票挂账 | e87e646
- 09:4x~09:52 | PM | **v6 v4 落档（出资人亲令）**：触发温度 28.5→27.6（i1 唯一实质改动＋meta.expect 同步，其余五节点逐字同）；af_build 干跑 ok=true 仅两条已知旧警告 → af_save ok=true store v3→v4（approved_by=auth_code:23***，通道②）→ watch 自动重装载零漂移（三处 sha=`ad04f8f7…`）＋i1 现读 27.6 ✓ | ✅ 全绿；终验预期：室温>27.6 ⇒ cool 26＋一句播报＋lifecycle 0→正 | 667e9ce
- 09:56~10:1x | PM | **首真实派发复盘＋AF-742 开号＋Verify 席位重建**：09:56:42 门关派发 spawn（lifecycle 首行＝AF-740 腿②实证）→ d1 canary 5m → d2 失败零留痕（行 708→709 相邻）→ IR 短路跳过 e1 ⇒ 无 TTS（butler 零收件无辜）⇒ 台账 §1.181 开号 AF-742 派 Core；v1.28.1 接管票 sha8 `23435f6a`；AF-Verify 原席停用（危险 worktree 重挂未遂）＋新席重建 | ✅ 全部落盘 | 667e9ce
- 14:53~15:01 | PM | **v1.28.3 部署（出资人私聊窗亲批选 3）**：AF-742/743/744 三修上线——POST 14:53 → build 7 分钟 → 15:00:37 health 翻 1.28.3；四坐标全绿（容器 af_scheduler/af_flock/af_cli 三 blob 逐字节同）、`:prev`=v1.28.1 镜像完好、watch 自动还原 v6 零漂移；AF-742 取证面就绪（action_failed/HA_TRANSIENT_RETRY 零样本起计） | ✅ 全绿；余：出资人终验＋AF-745（v1.28.4） | ec9627a
- 15:12~19:20 | PM | **终验全链成功实证＋重复触发循环破案＋v6 v5 落档**：15:12 轮全链 100%（喇叭拉取播放 `played ok=True` 铁证）；18:29 轮 d2 失败＝手动关机打断（`dev=state=off` 留痕）；19:05/19:06/19:11 三连重触发＝27.6 阈值抖动循环（TH2 28.4→27.7 悬线）⇒ 出资人指出缺空调状态条件 ⇒ **v6 v5**（i1 追加 ne cool）干跑一次过 → af_save store v5（auth_code=71***）→ watch 零漂移重装载 | ✅ 循环结构性死亡；余：AF-745（v1.28.4） | ec9627a

### 追加模板
`YYYY-MM-DD HH:MM | 谁 | 做了什么 | 结果/关键读数 | sha（无则写 —）`
- 2026-11-13 23:3x | PM | **v1.30.4 发版＋部署（AF-771 返工 521a888）＋五轮 QA 战役终审（§1.216）**：N1-N7 独立复验 9 passed＋变异自证（from→False 恰 N6/N7/N5c 三红）；gates 核心树/合并面双 RC=0 28✓；push 3 笔→CI 43450136085 六作业全绿→tag v1.30.4；NAS checkout -f 058d6c7＋compose 重建，health version=1.30.4 ok=true watch 三 sha 同值；R5 信号已发 QA-MCP、delta 守夜令已发 QA-Code | ✅ 上线；AF-771 闭 | 058d6c7
- 2026-11-14 01:0x | PM | **v1.30.5 发版＋部署（五件：AF-776/773/具名码/emit 钉/AF-781）**：独立复验 22 passed＋变异有牙＋双面 gates 28✓；push 6 笔→CI 43465247644 六作业全绿→tag→NAS checkout -f 292eb97→health 1.30.5 ok=true watch 三 sha 同值；主卧问句自此只播一遍 | ✅ 上线 | 292eb97
- 2026-11-14 02:3x | PM | **v1.30.6 发版＋部署（四件：MCP 读面单源闸/部署码 consume/simulate cancelled 剥离/cancel 审计）**：复验 18 passed＋四变异有牙（M3 为 PM 补跑）；CLI 无闸开 AF-785；push 5 笔→CI 43480528728 六绿→tag→NAS→health 1.30.6 ok=true | ✅ 上线 | 42fd9e4
- 2026-11-14 04:4x | PM | **v1.30.7 修复闭环＋发版部署（P3 六件＋CI 红 fix）**：AF-Core c763abb 单笔（两 pin 随批＋W1 显式 bash＋gates.sh +x）；PM 复验 102 passed＋gates 28✓ → push → CI 43499785201 六绿 → tag → NAS → health 1.30.7 ok=true；「三枚 pin」第三次重演的窄面纪律永久化（必含 observability/process_model 两 pin 文件） | ✅ 上线 | c763abb
- 2026-11-14 06:3x | PM | **v1.30.8 发版＋部署（门禁增强批三件：AF-786 时长闸/F 条函数归属/pin 对账门）**：PM 复验＝对账门 RC=0＋M1/M3 变异有牙＋还原干净＋窄面 195 passed＋合并面 gates 29✓；push 2 笔（docs 121984e 读数 52→54＋release 33b5274）→ CI 43517185896 六绿 → tag v1.30.8 → NAS → health 1.30.8 ok=true watch 三 sha 244c097f 同值；批 4（进程模型对账腿）已派 | ✅ 上线 | 33b5274
- 2026-11-14 06:5x | PM | **v1.30.9 发版＋部署（批 4：进程模型进 pin 对账门）**：复验＝双账 RC=0＋P1b exit 2＋M1 有牙＋还原干净＋窄面 130 passed＋gates 30/0；push 2 笔（9b2b954＋adcefb0）→ CI 43521299766 六绿 → tag v1.30.9 → NAS → health 1.30.9 ok=true watch 三 sha 244c097f 同值 | ✅ 上线 | adcefb0
- 2026-11-14 16:3x | PM | **元宝 MCP 三条 P1 开号＋配对暂停缓解**：AF-787/788/789 入台账 §0；`/api/user/pair/accepting` accepting=false 回读验证；批一派 AF-Core | ✅ 缓解生效 | adcefb0
- 2026-11-20 03:0x | PM | **v1.30.13 发版＋部署（批T 真接线收口）**：AF-Core pin 修复 7efee3b 复核（diff 3+/2-，窄面 F15 25 passed）→ merge 7618f5e（主窗 02:28 所推，核净）→ CI run 44588253505 六绿 → tag v1.30.13（peel=7618f5e 同值）→ NAS checkout -f 7618f5e＋compose 真重建 → health 1.30.13 ok=true、watch 三 sha f72ece89 同值、tv.py blob 三方 22f45be9 逐字节同、:prev=8ecb54e8≠latest=0a179402 | ✅ 上线 | 7618f5e
- 2026-11-20 16:2x | PM | **v1.30.14 发版＋部署（墨鉴批①九件：815/816/817/819/820/821/822/823/828）**：AF-Core 分支 afc607c 亲核（8 笔线性，首版交卷 77657b4 假 sha ⑫记档）→ bump 1.30.14＋契约重生成 unmatched=0 → chore(release) 97818c6 → 双闸（九件 47+pin 64/25/25+gates RC=0）→ push → CI run 44696473552 六绿 → tag v1.30.14（peel 同值）→ NAS checkout+重建 → health 1.30.14、watch 三 sha f72ece89、blob 三方 b6b92039/c8091f4b/cef23e47 | ✅ 上线 | 97818c6

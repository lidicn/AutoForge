# AutoForge 完整架构与运行时说明

> **更新时间**：2026-10-09　**鲜度基准**：`master` HEAD `e5b3fd5`（本文每一条读数都在这一版代码上当场重取，不引用旧版结论）
> **本文回答什么**：AF **怎么搭的、怎么跑的、每道闸在哪个文件哪一行**。用法、清单、命令速查在《AF完整知识文档.md》。
> **两文档分工**（避免同一事实长两处、改一处漏一处）：
> - 本文 = 机制与不变量（档位语义、闸门位置、失败面、出向唯一生产者）。
> - 知识文档 = 面与清单（MCP 31 工具表、HTTP 路由表、CLI 18 命令、IR 语言参考、安全闸 43 项）。
> **取证口径**：所有 `file:line` 均可用 `git show HEAD:<file>` 复核；行数是当场用注册表/枚举读出来的（`len(TOOLS)=31`、`len(CHECKS)=43`、`len(app.routes 含 methods)=90`、`forge --help` 命令表 18 条），不是手抄。

---

## 〇、这一版重写了什么：旧文失效点逐条

旧版基于 2026-09-24，此后三周内下列各条**已被代码推翻**，逐条给现读：

| 旧文断言 | 现状（当场读数） | 证据 |
|---|---|---|
| 「MCP 工具 13 个」 | **31 个**（`len(af_mcp.TOOLS)=31`） | `src/autoforge/af_mcp.py:463` |
| 「`af_adapters.py` 单文件」 | 已是包 `af_adapters/`（base/ha/http/mock） | `ls src/autoforge/af_adapters` |
| 「`af_self_repair.py`」 | **该文件不存在**（自修正闭环改由 `af_intervention`/`af_insight_queue`/`af_proposal` 承载） | 文件读不到 |
| 「IR 节点 7 种：on/if/do/ask/wait/pass/**emit**」 | 8 种：on/if/do/ask/wait/**set**/pass/**group**；`emit` 是 v0.3.0 已实现的**节点字段**，不是 kind | `src/autoforge/af_ir/models.py:75`、`:103-104` |
| 「边 6 种：then/**true**/**false**/on_error/on_timeout/yes/no」 | 7 种：then/yes/no/**default**/on_timeout/**on_cancel**/on_error，且有强制优先级 | `af_ir/models.py:85`、`:88-95` |
| 「没有 CI（.github/workflows 不存在）」 | `.github/workflows/ci.yml` 存在并在跑 | `ls .github/workflows` |
| 「镜像 `autoforge-api:nonroot`；源码挂卷 `/app/src`」 | 镜像 `autoforge-api:latest`，`src` **已烘进镜像**、运行时不挂源码卷 | `git show HEAD:docker/docker-compose.api.yml`（image 行 + 被注释掉的 src 卷） |
| 「`AUTOFORGE_LIVE_ENABLED=1`」 | 仓里 compose 缺省 **`=0`**（真机下发默认关），**NAS 现场注入成了 1** —— 两边读数会相反：`GET /api/live/status` 在 NAS 读 `enabled=true`，仓内缺省档读 `false`。以宿主机 env／`.env` 为准，别拿仓内默认值当部署事实 | `docker/docker-compose.api.yml:45`（`${AUTOFORGE_LIVE_ENABLED:-0}`）× NAS `/api/live/status`（§六） |
| 「`AF_REQUIRE_AUTH=1` 强制鉴权」 | 该键**已废弃**；缺省即 fail-closed，逃生阀换名为 `AF_ALLOW_NOAUTH` | `src/autoforge/af_api.py:360-383` |
| 「协调锁在 `/app/.forge/watch.lock`」 | 两把不同的锁：单写者租约 `{store_root}/.serve.lock`，watcher 协调锁 `{persist_dir}/watch.lock`（+ `watch.lock.info` sidecar） | `af_flock.py:29`、`af_cli.py:600`、`af_service.py:2569-2571`（2026-10-10 因本批在 `af_service.py` 2400+ 区插了五枚助手而重钉；`:2463-2464` 现在量到的是探测退避表 `_WATCH_PROBE_DELAYS_S`／`_WATCH_PROBE_TOTAL_S`，不是锁路径） |
| 「webui 前端未构建，根路径 404」 | 两棵 dist 已挂载同源：控制台 `/ui`、用户视角 `/mimo` | `docker/docker-compose.api.yml` volumes、`af_api.py:265` |
| 「全量回归 1107 passed」 | HEAD 整树跑批 **3479 passed / 5 skipped* 53 / 5 failed**（5 条红见 §十八，全部是"钉住的读数漂移未重钉"这一族） | 本文 §十八 原样读数 |

旧文里那份 **MCP 令牌明文**（此处连前缀都不复述，记作 `af****`）已从文档删除：口令类字面量不入库、不出境，运行侧真值只在 `AUTOFORGE_TOKENS`（`af_secrets.py:20-26` 的读取顺序）。**旧值已在 git 历史里，按已泄漏处理，该换。** 同一值在 `docs/audit/参考/FFL-200题测试提示词.md` 也写过明文，同批改成 `$AF_MCP_TOKEN`。

---

## 一、三面、两态、两个常驻进程

### 1.1 三个调用面（同一份服务层真相）

| 面 | 入口 | 传输 | 谁在用 |
|---|---|---|---|
| **MCP** | `forge mcp`（stdio，协议 `2024-11-05`）／`POST /mcp`（HTTP JSON-RPC） | stdio 或 HTTP | Agent（黄金路径 `af_draft`→`af_apply`） |
| **HTTP REST** | `forge serve` → FastAPI（90 条参与匹配的路由） | REST + SSE | 两棵 WebUI、外部监控、配对流程 |
| **CLI** | `forge <18 个子命令>` | 进程内 | 运维、判据脚本、CI |

三面共用 `af_service.py` 的服务语义（`af_service.py:52` 起的 `__all__` 就是这份契约面）。**MCP 面与 HTTP 面不是两套实现**：`af_mcp.py` 的工具实现直接调 `svc.*`（例：`_t_apply` → `af_apply.apply`，`af_mcp.py:387-392`）。这条"一面一实现"是历史上多起"只修了一脸"缺陷的根因（记录 §二之二十六、§5.3 第 7 行），现在由门禁盯。

### 1.2 两态：设计态 / 运行态

- **设计态**（`forge serve`、`forge mcp`、`forge build/sim/spec/diff`）：不碰真机。写面只落**待批队列**与**归档**。
- **运行态**（`forge watch`、`forge run --live`）：订阅 HA 事件流、真下发。生产 compose **只起 `forge serve`**，watch 由人/`POST /api/watch/start` 显式拉起。

### 1.3 进程拓扑（NAS 现状）

```
┌ autoforge-api 容器（compose 起，restart: unless-stopped）
│  ├ forge serve --host 0.0.0.0 --port 8787 --store-root /data
│  │    --ui-dir /ui --ui-user-dir /mimo --examples /app/examples/ir
│  ├ （同进程内）联动桥：AUTOFORGE_MQTT=1 才起，且排在 uvicorn.run 之前、不吞异常
│  └ （同进程内）watch 子进程：由 /api/watch/start 或 forge watch 拉起，靠 watch.lock 单实例
└ 卷：/data=autoforge-store、/ui=ui/dist、/mimo=ui-user-mimo/dist（源码不挂卷，已烘进镜像）
```

起桥位置：`af_cli.py:1335` 定义 `_start_linkage_bridge`，serve 那一路在 `af_cli.py:1446` 调它、`af_cli.py:1449` 才 `uvicorn.run`（watch/dry-live 那一路在 `af_cli.py:322` 也调，未开启即 no-op）——桥配不好就是**整个 AF 起不来**，含只读面。

---

## 二、模块地图（按层；顶层 `.py` 现读 71 个（不含 `__init__.py`）+ 4 个包目录，含包内文件共 102 个 `.py`，import-linter 的基线锁读的就是这 102）

| 层 | 模块 | 责任 |
|---|---|---|
| **IR 与编译** | `af_ir/`（models/schema/condition_norm/…）、`af_spec.py`、`af_nl.py`、`af_nl_parse.py`、`af_nl_build.py`、`af_fidelity.py` | 图模型、JSON Schema、AF-Spec 文本⇄IR、NL 渲染与解析、往返保真 |
| **第一道闸** | `af_scanner.py`（`CHECKS` 43 项、`live_preflight`、`DeviceGuardRegistry`）、`af_irreversible.py` | 编译期静态安全闸、L2/L3 不可逆字段标注 |
| **第二道闸** | `af_vhass/`（`fake.py` 唯一效果真值表、`high_fidelity.py`）、`af_expect.py` | 双轨仿真 + 后置条件断言 |
| **设计链** | `af_draft.py`（staging，进程内 TTL，不落盘）、`af_apply.py`、`af_premiere.py`、`af_pending.py`、`af_service.py` | 意图→IR→校验→仿真→（预演）→入队→试演期 |
| **运行态** | `af_runtime.py`、`af_executor.py`、`af_scheduler.py`、`af_bus.py`、`af_state.py`、`af_registry.py`、`af_time.py` | 实例生命周期、事件总线、调度、状态源、时钟 |
| **真机接线** | `af_live.py`、`af_tick_supervisor.py`、`af_adapters/`（base/ha/http/mock/inbox）、`af_watch.py` | SSE 订阅、tick 心跳读数（"自愈"那函数今天未接线，见 §四 末）、HA 适配、收件箱投递、生产态证据聚合 |
| **治理** | `af_conf.py`、`af_shadow.py`、`af_canary.py`+`af_canary_supervisor.py`、`af_conflict.py`+`af_conflict_runtime.py`+`af_conflict_audit.py`、`af_intervention.py`、`af_undo.py`、`af_health.py`、`af_closedloop/` | 置信度 band、影子比对、金丝雀、冲突仲裁、人工干预、回滚、降级、自改进闭环 |
| **插件装配** | `af_runtime_ext.py`、`af_runtime_plugins.py` | 把治理件挂到 executor/runtime 上（不改内核） |
| **经验与观测** | `af_telemetry.py`、`af_experience.py`、`af_error_knowledge.py`、`af_preference.py`、`af_predict.py`、`af_pretrigger.py`、`af_insight_queue.py`、`af_proposal.py`、`af_feedback.py`、`af_metrics.py`、`af_evo.py` | 遥测、共现经验、错误知识库、预测触发、洞察队列、提案、反馈 |
| **存储与凭据** | `af_store.py`、`af_persist.py`、`af_atomic.py`、`af_flock.py`、`af_secrets.py`、`af_auth.py`、`af_audit.py`、`af_fire_recorder.py`、`af_bounded_caches.py`、`af_config.py`、`af_env.py`、`af_conf.py` | 版本化归档、实例持久化、原子写、租约、密钥、令牌、审计、封顶注册表 |
| **联动：出向与入向** | `af_mqtt_bridge.py`、`af_linkage_feed.py` | 出向：ADM 联动桥是唯一出向生产者（`fired`/`failed`/retained status 与收件箱 `butler/inbox/*` 都从这一个模块出去）。入向：`ma/insights` 落提案队列，`ma/presence`/`ma/device-health` 落 `af_linkage_feed.LinkageFeed` 这条独立持久队列（卡3；只落盘，注总线由 `af_live.pump_linkage` 在 tick 线程做） |
| **面** | `af_api.py`、`af_mcp.py`、`af_cli.py`、`af_catalog.py`、`af_scene.py`、`af_fault.py`、`af_version.py`、`af_instance.py`、`af_vhass/` | 对外接口与设备目录 |

**依赖方向是门禁**：`pyproject.toml` 的 import-linter 契约（例：`name = "Service boundary never imported by kernel"`）在 CI 里跑，跨层反向 import 直接判红。

---

## 三、设计链：意图 → 归档

```
自然语言 / 意图 JSON
   ↓ af_draft(intent)                    进程内 staging（TTL，不落盘）af_draft.py:51
   ↓ 返回 ref（IR 永不回传，防上下文炸）
   ↓ af_apply(ref, stage=?)              af_apply.py:72
   ├── build   → StaticScanner 43 项 CHECKS
   ├── simulate→ af_service.simulate / simulate_track（双轨）
   ├── [dry_run] → 到此为止，零写入            af_apply.py:189-193
   └── save    → submit_pending 入待批队列      af_apply.py:196-204
   ↓ 人工 approve（CLI pending approve / POST /api/pending/approve）
   ↓ GraphStore 版本化归档（{root}/…，save af_store.py:296）
   ↓ 导出 IR → forge watch 常驻运行
```

设计链上的三条硬不变量：

1. **未知 stage 必须拒收**（`af_apply.py:92-98`）。旧实现里打错一个字母会一路落到 `save` = **打错字就部署**。现在只有 `APPLY_STAGES`（`:30`）+ 别名表 `STAGE_ALIASES={"apply":"save"}`（`:34`）能进。
2. **`af_save` 也先过第一道闸**：静态扫描未过就拒绝归档（MCP 工具描述里明写，`af_mcp.py:816`）。
3. **批量启停要显式二次确认**：`af_set_tags`/`af_enable_by_tag`/`af_import_store`/`af_save` 有 `allow_bulk` 爆炸半径护栏（`af_mcp.py:770`、`:796`、`:816`）。

---

## 四、运行链：事件 → 实例 → 动作 → 证据

```
HA 状态变化
  ↓ SSE /api/stream（`af_live.py:110` 路径常量，订阅在 `:219`）           或 /api/events（事件注入）
  ↓ af_bus.EventBus：去重 dedup_key :121 / 节流 _pass_throttle :215（窗口 200ms，:137-147）/ 熔断结果 breaker_open :191
  ↓ Runtime 匹配 trigger → 起实例（af_runtime.py / af_scheduler.py）
  ↓ NodeExecutor 逐节点走（on/if/do/ask/wait/set/pass/group）
  ↓ 【插件拦截链】冲突守卫 dispatch → shadow/ask band 路由 → canary 观察期 → adapter.call
  ↓ HAAdapter / HTTPAdapter（dry_run 或真实下发）｜InboxAdapter（收件箱投递，dry_run 时零上线）
  ↓ 终态 → af_mqtt_bridge.observe_terminal() 唯一出向事件写者（:673/:676）
  ↓ af_watch 聚合生产态证据 verified_in_prod
  ↺ tick：af_tick_supervisor（TRANSIENT/DEGRADED/FATAL 三-class :28-30，退避，连续 20 次 SAFE HALT）
```

`af_tick_supervisor` 是给常驻 watcher 兜底的"心跳**读数**"：tick 线程死了会在 `/api/health` 的 `ticker_alive` / `tick_exit_reason` 上读到（`af_service.py:272-288`、`:299-301`）。**读不到桥就报 `unwired`，不许读成健康**（`af_service.py:256-260` 的 docstring 把这条写成判据）。

⇒ 2026-10-10 现读收窄一句（此前这里写的"心跳自愈"三字把两件事混成了一件事）：这一格今天**读得出原因、读不出补救**。`af_live.tick_watchdog_pass`（`af_live.py:83`，`__all__` 在 `:51`）在 `src/` 内**没有任何生产调用者**，跑它的只有 `tests/unit/test_af_live.py` ⇒ "tick 线程意外终止会被自动重启"是一条未接线的承诺，不是运行期事实（两份独立审计各读到同一处）。因此"SAFE HALT 绝不自动重启"那条安全红线（D1 方案 C，DCD 20261001）今天也还没有运行期消费者。`af_tick_supervisor.py:222-226` 的 docstring 已按此改口。**接线还是降档不在本文自决**——按 `decisions/20261010-AF与DB与DPP十件-裁定.md` §六 Q3，"死码／无调用方"的定性归 DCD 结案，AF 不得把它写成"按设计"。声明与控制流的一致性钉成两条腿（`tests/unit/test_watch_lifecycle_r6_audit.py`）：点到这名字的 docstring 连同定义处自己必须写着"没有调用者"，而 `src/` 里一旦出现 `tick_watchdog_pass(...)` 调用点就判红——接线那天必须**同时**改声明、改腿、引裁定号。

---

## 五、写侧档位：`check / simulate / dry_run / save`（预演就在这一格）

`APPLY_STAGES = ("check", "simulate", "dry_run", "save")`（`af_apply.py:30`）。四个档不是"同一件事的四种说法"，**每一档的写入面不同**：

| stage | 过 build | 过 simulate | 消费首演码 | 进待批队列 | 进试演期 | 落盘 | 返回值特征 |
|---|---|---|---|---|---|---|---|
| `check` | ✅ | ❌ | ❌ | ❌ | ❌ | 无 | 在 `af_apply.py:175-176` 直接返回 |
| `simulate` | ✅ | ✅ | ❌ | ❌ | ❌ | 无（仿真只喂遥测） | 在 `:186-187` 返回 |
| **`dry_run`** | ✅ | ✅ | **❌** | **❌** | **❌** | **零写入** | `:189-193`：`{stage:"dry_run", dry_run:True, would_enqueue:True, pending_ref:None}` |
| `save` | ✅ | ✅ | ✅（若带码） | ✅ | ✅（默认 24h） | pending 文件 | `:196-217` |

三条容易读错的地方，逐条给代码位置：

- **`dry_run` 一律不带首演码**：一次性码消费掉就没了，"先看看"不该有代价（`af_apply.py:134-135`）。DB 侧「拟→验→批→部署」里的**"验"**就是这一档——它必须能在零写入前提下跑，所以 MCP 工具描述里明写了这句（`af_mcp.py:486`）。
- **正面证据写在返回值里，不靠调用方"记得自己没入队"**：`would_enqueue=True` = 真跑 `save` 就会入队；`pending_ref=None` = 此刻队列里确实什么都没有（`af_apply.py:190-192` 的注释就是这条的理由）。
- **`save` 成功后自动进"试演期" 24 小时**：`af_premiere.enter_trial(store_diff_sha, hours=24)`（`af_apply.py:206-208`）+ `PREMIERE_TRIAL_STARTED` 审计（`:209-217`）。试演期的语义是**只统计不封禁**（冲突守卫那侧见 §八）。

首演码（部署仪式）绑定的是 **store diff 的规范化 SHA256**（`_store_diff_sha`，`af_apply.py:39-51`，`sort_keys`+紧凑分隔符，issue 与 consume 两侧算同一哈希）——**验码后掉包即拒**。签发走 `issue_premiere`（`:54-69`）。

---

## 六、真机侧档位：`live` / `dry-live` / 服务端总开关

| 档 | 开关 | 时钟 | HA 状态源 | do 动作 | 备注 |
|---|---|---|---|---|---|
| 纯仿真 | `forge sim` / `af_apply(stage=simulate)` | 虚拟 | FakeHA / HiFi | 不触 HA | §五 |
| **dry-live** | `forge run --dry-live`（`af_cli.py:471`）；`POST /api/watch/start` **缺省 True**（`af_api.py` 的 `api_watch_start`、`af_service.py:2651` 的 `start_watch(..., dry_live: bool = True)`） | **真墙钟** | **真 HA** | **只记意图，不下发** | 抓 live 代码路径 bug，不动设备 |
| live（一次性） | `forge run --live --confirm --live-allow …`（`af_cli.py:470`）；HTTP 侧 `POST /api/live/run`（要 `confirm=true` + 非空 `live_allow`，`af_service.py:1889/:1897`） | 真 | 真 | 真下发，**回放完就退出** | 三重闸：服务端开关 + confirm + 白名单 |
| live（常驻） | **只有 CLI**：`forge watch <ir> --confirm --live-allow …` | 真 | 真（SSE 订阅） | 真下发 | `forge watch` **没有 `--live` 这枚旗子**：真机档就是"不带 `--dry-live`"（`live = not dry_live`，`af_cli.py:585`），缺 `--confirm` 直接拒启动（`af_cli.py:565-566`） |

- **HTTP 面／用户视角界面到今天没有常驻真机通道**（2026-10-09 现场结论，之前文档把它写成"可真机"是错的）。`svc.start_watch` 只会拼两种命令：带 `--dry-live`，或什么真机旗子都不带；它**从不传** `--confirm`／`--live-allow`／`--entities`。前者只记意图，后者会被子进程自己拒启动并立即退出。所以返回值如实给两格：`tier`（`dry_live` / `live_unconfirmed`）与 `real_device`（**两档都是 `false`**）。要不要给 UI 开这条常驻通道归 DCD 裁（申请 `20261009-AF-用户视角到真机的常驻通道`）。判据钉在 `tests/unit/test_start_watch_identity.py`，其中一档是真跑 CLI 的：`forge watch` 不带 `--confirm` ⇒ `exit_code != 0` 且输出里出现 `--confirm`。
- `ok=true` 现在**必须由 sidecar 证明是本次这份**（`af_service.py:2745-2800`）。旧形状只要 `watch.lock.info` 存在就回成功，而那是目录里唯一的一个文件——上一条 watch 的残留会被读成"本次启动成功"（现场实测：1.18 秒回 `ok=true`，带的是 9-29 另一条 IR 的路径）。现在要求 `sidecar.graph == 本次写出的 IR 路径`，认不上就分三种如实失败：`child_exited` / `coord_lock_held_by_other` / `not_registered`，成功时一并回 `acquired_at`。

**watch 生命周期的五条规矩**（2026-10-10 收第六轮审计 BUG-04／05／06／07／14，授权来自 `decisions/20261010-AF与DB与DPP十件-裁定.md` §五 Q4 甲"落在 AF 可动文件的条目由 AF 逐条复测后自行收口"；判据 `tests/unit/test_watch_lifecycle_r6_audit.py`（26 条腿）＋ `tests/unit/test_af_mqtt_bridge.py`（本批 +4 def），十六案变异 14 案被杀／控制与同义改写全绿）：

1. **协调锁的文件本体不删**（`_cleanup_watch_files`，`af_service.py:2512`）。锁挂在打开的文件描述符上（`af_flock.FileLock`：POSIX `flock`／Windows `msvcrt.locking`），`unlink` 既不释放锁，还会让下一个 watcher 在同一路径 `create` 出**新 inode** 并"拿锁成功"⇒ 同一目录两个 watcher 同时监听，且日志里一个字都没有。修前那次 unlink 之所以"看起来没事"，只因持锁期它本来就删不掉（Windows 实测 `PermissionError [WinError 32]`），而旧代码 `except OSError: pass` 把它吞了 ⇒ "清干净"与"什么都没清"对外逐字相同。现在只回收 sidecar 与 PID 文件，读数面给出 `sidecar_removed`／`pid_file_removed`／`lock_file_kept: True`／`errors: ["label:异常类型"]`。
2. **发信号之前必须核验身份**（`_owner_pid_from_sidecar` `:2471` ＋ `_verified_pid` `:2486`，七种读数里只有 `verified` 允许 `os.kill`）。PID 会复用，`os.kill(读来的数字, 15)` 命中复用后的无关进程不可撤销；Windows 上 `os.kill(...,15)` 等价 `TerminateProcess`，目标连忽略的机会都没有。核验用的是**系统自己的信号**：sidecar 的 `owner` = `{hostname}-{pid}-{uuid8}`（`af_flock.owner_id()`），三样对不上就不杀，让新建的 watcher 自己去抢锁，抢不到如实回 `coord_lock_held_by_other` 并带 `previous_watch={pid, identity}`（`:2670-2684`）。发完信号按 `_WATCH_EXIT_WAIT_S`（`:2467`，合计 4.9 秒）轮询 `_lock_is_free`（`:2532`），等不到回 `exit_unconfirmed` 且诊断文件一律留；**刻意不升级成 SIGKILL**——身份虽已核验，对方可能正在写持久化，强杀会把在途写入丢在半截。对侧一半脸在 CLI：`af_cli.py:618-630` 把 SIGTERM 接回已有的 `finally`（`stop.set()` 后 `raise KeyboardInterrupt`），Windows 上 `os.kill` 只能强杀、没有优雅收尾可言，这条链路的真机语义在 POSIX（docker 面）才完整——不在文档里假装两边一样。
3. **探测预算只有一个真源**：`_WATCH_PROBE_DELAYS_S`（`:2463`）＝`(0.1, 0.2, 0.3, 0.5, 0.8) + (1.0,) * 10`，合计 `_WATCH_PROBE_TOTAL_S`＝11.9 秒。旧形状是固定 `15 × 1.0` 秒——成功路径至少白等 1 秒，失败路径把一个 Starlette 同步线程池工位占满 15 秒。错误文案里那个秒数用 f-string 从这枚常量读回来，散文里不再抄第二份。
4. **父进程不替子进程握着日志句柄**：`af_service.py:2713-2721` 用 `with (root / "watch.log").open("ab") as logf:` 包住 `Popen`。`Popen` 已经把 fd 复制给子进程，父进程再持一份只会让 Windows 上的 `watch.log` 无法轮转/删除，而 `Popen` 抛错那条路径原来连引用计数回收都走不到。
5. **MQTT 桥 started 就得 stopped**（`af_mqtt_bridge.py:749-770`）：先播 offline、再摘 `client.on_message`、再 `loop_stop()`／`disconnect()`，拆环失败**抛穿**（不加新的宽 `except` 静默）；`start()`（`:724`）已在位则抛 `BridgeUnavailable`，不再静默覆盖订阅表。这一格**不等 homesdk 0.3.3**：0.3.2 的 `mqtt.get_client()` 交出来的是**未连接的裸 paho client**，docstring 把 `connect()`／`loop_start()` 明确写成调用方的活 ⇒ 对称的收尾也是调用方的活。等 0.3.3 的是另一格（`#93`／`#80`：`advertise(degraded=…, reasons=…)` 与 `ADM_ERR_PUBLISH_REFUSED` 的消费半边），那格卡的是发版动作不是判定。

机制上只有**一个分岔点**：`build_runtime(..., dry_run=…)`（`af_runtime.py:289-294` 把同一个旗子交给 `HAAdapter`/`HTTPAdapter`/`InboxAdapter`，所以不存在"某一面接不到真机、某一面接不到 DB"的分裂）。

- `HAAdapter.__init__` 的 **缺省是 `dry_run: bool = True`**（`af_adapters/ha.py:227`）——默认构造就是安全档，要真下发必须显式给 False（`af_cli.py:304`、`af_service.py:1942/:2054`）。
- `call()` 的顺序是**故障注入 → dry_run → transport**（`af_adapters/ha.py:253-268`）：注入的失败优先于 dry_run（`:241-251` 四件 `fail_next/timeout_next/drop_next/unavailable_next`），dry_run 只 append 意图并返回 `{"dry_run": True, …}`（`:258-266`），没注入 transport 又想真下发 = `AdapterError`（`:267-268`）。
- `intents` 环形封顶 `INTENTS_MAX=200`（`af_adapters/ha.py:46`、`:259-261`）：常驻服务里 dry_run 也每次都记一条，不裁就只增不减。
- **收件箱那一侧同形**（`af_adapters/inbox.py:47`）：`dry_run=True` 只经记录代理 `_RecordingInboxClient`（`af_mqtt_bridge.py:371`）留一条意图、**一个字节都不上线**，而记下来的 `payload` 是从"库侧真要发出去的那份 `body`"反解回来的，AF 不重拼第二遍——所以"预演说音箱播这句"与"音箱该收到这句"结构上不可能不一致。真发档缺桥＝缺通道，按 fail-closed 报 `ADM_ERR_AUTH_REQUIRED`（不是静默跳过）。意图环同封顶 200（`af_adapters/inbox.py:34`）。
- **动作前快照（撤销）只在真实下发路径生效**：`undo_recorder` 在 `dry_run` 分支之后（`af_adapters/ha.py:266` 之后那段），且 `--dry-live` 不触发 recorder（`af_cli.py:305-308`）。
- `--dry-live` 会自动放过 confirm 预检、并跳过写白名单检查（`af_scanner.py:1299/1326/1336-1337`，上一批因新增检查方法 +24，本批因两本目录各插行 + 第 43 项的检查方法再 **+27**）：**它不下发，所以不该被下发护栏拦**。

服务端总开关 `AUTOFORGE_LIVE_ENABLED`（`af_service.py:1768`）：没开 ⇒ `live_run` 抛 403（`:1889`）、undo 抛（`:2045`）。仓内 compose 缺省 **0**（`docker/docker-compose.api.yml:45`），**NAS 那侧被宿主 env 注入成了 1**——两档读数相反，判部署事实要看 `/api/live/status` 而不是仓内默认值。

---

## 七、自主档位：band = `auto / shadow / ask`（单一真值源）

band 是**运行时置信度**（`ConfidenceStore`）映射出来的自主级别，不是 IR 静态字段。数值与集合的唯一出处：

- 阈值：`AUTO_MIN = 0.85`、`SHADOW_LOW = 0.60`（`af_conf.py:23-24`）⇒ `auto ≥0.85`、`shadow 0.60–0.85`、`ask <0.60`。
- 优先级：`BAND_PRIORITY = {"ask":0, "shadow":1, "auto":2}`（`af_conf.py:37`）——**取最严**时按它比。
- 只读比对集合：`PASSIVE_BANDS = {"shadow"}`（`af_conf.py:39`）。
- 必须人工确认集合：`CONFIRM_REQUIRED_BANDS = {"ask"}`（`af_conf.py:41-42`）。

**shadow 档的运行语义**（`af_shadow.py:1-17` 的模块 docstring 就是这条的权威表述）：`ShadowRunner` 用 Python 实例属性优先级**就地装饰 `NodeExecutor._do`**，不改 `af_executor.py`：

- `auto` → 透传原 `_do`（真实执行），并向 `InterventionDetector` 报到；
- `shadow` → **绝不调用 `adapter.call`**，只写 `shadow_log`（动作+参数+期望态），延迟 `compare_after` 秒后**只读比对**目标实体是否真变成期望态（`af_shadow.py:367-381` 路由、`:399-413` `run_do` 不落设备）；
- `ask` → 不执行，生成 ask 提案挂起等人确认（`open_ask`）。

达标转正：连续 `streak_to_promote` 次命中（或 conf 自己爬到 `AUTO_MIN`）→ `conf.promote()`，并回调 `on_promote` 重挂 canary + 启动 `CanarySupervisor` 观察期。`shadow_log.json` 独立存放，与正常运行日志隔离；重启时会回放日志，**日志损坏会在 `replay_load_error` 上留痕**，不会把"历史判定丢了"读成"回放一切正常"（`af_shadow.py:360-366`）。

band 与执行闸的联动在两处：编译期 `SHADOW_WRITES_DEVICE`/`LOW_CONF_WRITES_DEVICE`（`af_scanner.CHECKS`），运行期 `af_conflict_runtime.py:317-321`（shadow 不参与抢锁、ask 一律拒自动下发）。**另有一枚与 band 正交的运行期闸**：节点级 `requires_confirm`（裁定 20261009 §三 硬前置）在 `_do` 入口 `af_executor.py:648-659` 判旗，未授权就挂成人工确认会话（`_suspend_for_confirm` `:864-891`，走 `pending_asks` → `/api/asks`（`af_api.py:653`，读进程内那本）/ `/api/asks/answer`（`:907`）/ sidecar / inbox 这张已有脸），唤醒在 `resume` `:211-241`（yes ⇒ 一次性把手 + 重进同一节点执行一次；no/超时 ⇒ 零下发 + `confirm_denied` 审计，终态照既有边纪律选路：无 `no`/`default` 边就在 `:333-338` 落 `done`，与 `ask` 被拒同一条路，不为确认单开特例；**这一格现在编译期也喊得出来**——受确认节点缺 `no`/`on_timeout`/`default` 任一出口时出 `CONFIRM_WITHOUT_DENY_PATH` **告警**，`af_scanner.py:509-527`，43 项之一，裁定 20261009（第二份）§二 2.2：不拦发布，只把"被拒后走向"从运行期静默挪到图里可读）。它不看 conf，只看这枚旗——但 `af_shadow.py:367-381` 是按 band 决定要不要真进 `_do` 的：shadow 档只写 `shadow_log`、ask 档先开提案，所以**这枚闸实际只在 `auto` 带上真拦**；`af_conflict_runtime.py:319-321` 那句 `ask_band_requires_confirmation` 是一条**拒发**（REJECT），与这枚"停下来问人"的闸不是一回事，两枚都在。

`af_canary.py` / `af_canary_supervisor.py` **不是仿真档**：`auto_rollback`（`af_canary.py:145`）与 `demote_below`（`af_canary_supervisor.py:68`）是真机路径上的护栏策略；适配器是 dry 时执行器会跳过 canary 接线（`af_executor.py:646` 取 `dry_run`、`:663-670` 的 `use_canary` 里带 `not dry_run`）。`requires_confirm` 对 dry 适配器走同一口径（不挂起、留 `confirm_skipped_dry_run` 痕，`:657-659`）——**这一口径已由裁定 20261009（第二份）§一 追认为甲**：确认闸防的是"不可逆真写要人点头"，预演档零字节上线就没有可确认的实体；裁的是"跳过 + 记痕"而不是"静默跳过"，所以那枚痕是这条裁定的关键半边，撤掉它＝预演不再诚实。历史上 `suspend→resume` 接缝丢过 `auto_rollback=false` 旗子（`af2ee56`）。

---

## 八、冲突守卫档位：`off / observe / enforce`

唯一开关是环境变量 `AUTOFORGE_CONFLICT_ARBITER`（`af_conflict_runtime.py:47`），取值解析在 `:106-133`：

| 值 | mode | 行为 |
|---|---|---|
| 空/其他 | `off`（**缺省**，`:87`） | 完全不装拦截器，`dispatch` 直通（`:281-282`） |
| `observe` / `audit` / `log` / `watch`（`:71`） | `observe` | 照常内省、照常仲裁，但**不改行为**：异常/拒绝一律回落到"照常执行" |
| `1` / `true` / `on` / `yes` / `enforce`（`:70`） | `enforce` | 判红就真拦：REJECT/CIRCUIT_OPEN → 走 `on_error` 边；WAIT → 停车 `_park` |

**enforce 档的 fail-closed 站点表**（DCD 20261008 §二 落地，判例 1：守卫自己的输入来源失败时 fail-closed）：

| 站点 | 触发条件 | 处置 | 通知半边 | 位置（现读，本批 §十八 B.4 收口后） |
|---|---|---|---|---|
| 内省 | `_automation_id`/`extract_entity_ids` 等**抛异常**（超预算或任何代码 bug） | REJECT + `_audit_degraded(fail_open=False)` | `blocked=not observe`（两档都落指示，只有真拦下才发对端快照） | `af_conflict_runtime.py:294-304` |
| 内省成功但挖不出实体 | 只读/无实体节点的**正常形状** | **放行**（与上一档不同路） | 不通知（不是失明） | `:306-309` |
| 读 band | `conf.band()` 返回 `None` | REJECT + 通知（**不看模式**，裁定 §四 没给试演档开口子 ⇒ 永远 `blocked=True`） | `blocked=True` | `:310-321` |
| 仲裁请求 | `arbiter.request(...)` **抛异常** | REJECT（同族推广，逐站理由已投 DCD 求追认） | `blocked=not observe` | `:331-343` |
| 仲裁器内层 | 内层实现异常 | REJECT（**旧实现这里是改写 ALLOW，已修**） | — | `af_conflict.py:212-223` |
| 用户覆盖登记 | cooldown 登记失败 | 进 `_cooldown_pending` 并 fail-closed 挡住 | — | `af_conflict.py:266`、`:350-353` |

observe 档"照常执行"这一半是**设计**而不是漏网：`:301-302`、`:339-340` 处注释都写着"试演期只观测：不改行为，否则判据没法对比"。但**"照常执行"不等于"没人知道守卫瞎了"**（本批 §十八 B.4 收口）：`_notify_guard_blind`（`:474-523`）的两半边按**这一跑拦没拦**分栏，`blocked` 由调用点现给——被调方**不自己读模式**（那是第二份真值，而且会把 band 那一站读反）。仓内常驻指示（`af_watch` 的 `conflict` 列，detail 带 `phase`/`error`/`entity_ids`/`blocked`）两档都落；对端 retained `af/status` 只在真拒发时发，试演档那一次改落一条 WARNING（`:496-501`）。为什么不反过来：判据腿 `test_every_guard_blind_call_point_declares_whether_it_blocked` 钉"三枚调用点各带 `blocked`、被调方签名里保留它且不含 `settings.mode`"，`test_notification_precedes_the_observe_passthrough_at_both_blind_sites` 钉"通知必须站在 `if observe:` 回落**之前**"——修前那一版正是站在之后，所以试演期一次都没被调用过。

其余可调环境变量：`_ENV_FLOATS`/`_ENV_INTS`/`_ENV_BOOLS` 三张表（`af_conflict_runtime.py:49-69`），审计落盘目录 `AUTOFORGE_CONFLICT_AUDIT_DIR`（`:69`，默认 `.forge/conflict_audit.json`，`af_conflict_audit.py:21`）。

---

## 九、证据档位：仿真"跑对了吗"怎么读

这一层解决的是**假绿**：`ok=True` 只回答"跑完了、没抓到反例"，回答不了"验过了"。

### 9.1 双轨仿真 `simulate_track`

`af_service.py:1037-1063`：

- `track="fake"`：降级内存底座（`FakeHAAdapter`），动作立即生效；`sun` 固定 18:00/06:00。
- `track="hifi"`：`HighFidelityHA + HighFidelityAdapter`，动作**入队后 flush 才落位**，`sun` 用真实太阳几何。
- 两轨共享 `af_vhass/fake.py` 的**唯一效果真值表**（`SERVICE_STATE` / `service_effect` / `is_modeled`），对建模动作的结果必须一致；**唯一已知分歧点是 `sun`**，对拍由 `compare_dual_track` 白名单豁免。
- `track` 不是 `fake`/`hifi` 就抛 `ValueError`（`:1062-1063`），仿真底座切换另有一枚 `AUTOFORGE_VHASS_HIFI`（`af_vhass/high_fidelity.py:276-283`），它只换底座、不是产品档。
- 返回里 `fully_verified = bool(items) and not non_simulable and expect.failed == 0`（`:1210`）——**比 `ok` 严**：声明过断言且全部验过才算。

### 9.2 诚实报告五档 `honest_report`

`af_service.py:1156-1211`，绝不把"没验到"当"验过了"：

| 档 | 含义 | 来源 |
|---|---|---|
| `verified` | 断言**实际跑过**（status=pass/fail） | `expect.items` |
| `inferred` | 仿真产出但**没有断言覆盖**的实体（final_states 里未被任何 expect target 覆盖） | 按实体前缀匹配，注意实体 id 本身含点号，不能 `split(".")[0]`（`:1192-1201`） |
| `non_simulable` | 断言**无法验证**（status=unverified）→ 显式标黄；含底座**未建模的动作** | `:1175-1185` |
| `exempted` | 副作用不可观测、被**显式豁免**（决策 B 真豁免通道，如 notify）——单列一档，绝不冒充 verified，也不算 non_simulable | `:1177` 注释、`:1207` |
| `verified_in_prod` | **生产态真实证据**（shadow/canary/conflict 落点，由 `af_watch` 聚合回灌） | `:1209` |

强度排序（读结果时按这个次序判"证据有多硬"）：`verified_in_prod`（真跑过且对）> `fully_verified` > `verified` > `inferred` > `non_simulable` > `exempted`（后者是"不打算验"，不是"验过了"）。

编译期还有三条配套检查防"假断言"：`EXPECT_MISSING`（没声明后置条件）、`EXPECT_UNREACHABLE`（断言的实体图里既不读也不写 ⇒ 永远验不到）、`EXPECT_STATE_INVALID`（断言值不在该实体域内）——都在 `af_scanner.CHECKS`。

### 9.3 不可逆字段标注

`af_irreversible.py:42-66` 给 L2/L3 动作标不可逆；`RUNTIME_ONLY_FIELDS`（`:31-33`）把 `stage`/`diff_sha`/`simulate_track`/`honest_report` 这些**仿真产物**从 NL 往返里剥掉。现读：它的产品调用方只有 NL 渲染侧（`af_nl.py`、`af_nl_parse.py`），**执行面没有调用方**——列在 §十八 残余里。

---

## 十、测试通道（`af_test`）：批量跑题、自动放行、与正式区隔离

产品化的"测试模式"只有一个，就是 `af_test.TestChannel`（`src/autoforge/af_test.py`）。设计文档在 `docs/reference/AF测试通道设计方案.md`，实现与它是同名的同物。

| 维度 | 现读 |
|---|---|
| 隔离根 | `TestChannel(test_root="/data/test")`（`af_test.py:109`），正式区是 `/data` | 
| 目录 | `pending/`、`graphs/`、`reports/`（`:111-117`） |
| 批次上限 | `MAX_BATCH_SIZE = 500`（`:33`），超限 `TestError`（`:139-141`）——**这正是旧文那条"跑 200 题必须用 simulate"的根因**：走正式 `save` 会撞待批队列熔断，测试通道把熔断换成自己的批次闸 |
| 单条流程 | `draft → apply(stage="simulate") → save_graph(tags=["test"])`（`:155-230`）：**不碰 pending 队列**，直接落测试区归档（`:156` 的 docstring 就是这句） |
| 是否碰真机 | **不碰**。中间只跑 build + simulate（`:186-189`） |
| 报告 | `batch_id/total/pass/fail/pass_rate/fail_reasons/details/created_at`（`:253-261`）；失败原因按 `stage`/`code` 归类计数（`:240-251`） |
| 落盘 | `reports/{batch_id}.json` 走 `atomic_write_text`（`:264-269`）——报告是 WebUI 轮询读的，崩在半截会被读成"没有这份报告"（判据 E，审计 BUG-05） |
| 读侧 | `get_report`（`:271-277`，不存在就抛）、`list_reports`（`:279-296`，坏 JSON 跳过不炸） |
| 清理 | `clear(*, protected_root=...)`（`:298-328`）：**删之前过形状守卫** `assert_test_area_deletable`（`:71-97`），**删之后按盘上实测数残留**（`residual`，在 `_ensure_dirs()` 之前量）；`ignore_errors=True` 已撤，删除失败现场按 `onerror` 收进 `errors`（上限 `DELETE_ERRORS_MAX = 20`，`:35`，另带如实的 `errors_total`）。残留 > 0 或有任何失败现场 ⇒ `ok: False`/`cleared: False` |
| 调用脸 | **只有 MCP 脸**：`af_test_submit`（`af_mcp.py:507`，scope `write`）、`af_test_report`（`:521`，无 scope）、`af_test_clear`（`:534`，scope `write`，`:538`）；实现在 `:395-430`。**这一格已由裁定 20261010 §三 裁甲维持**（scope `write`、不加面板），见下面第 2 条 |

两条必须写进架构的边界（现读，不是推测）：

1. **`/data/test` 这个根是硬编码缺省**（`af_test.py:109`、`:335`；MCP 侧 `get_test_channel()` 不传参 ⇒ 用缺省，`af_mcp.py:401/:412/:426`）。隔离靠"路径不同"，**不靠写闸**：测试区里的 `save_graph` 是真写盘（写进 `GraphStore(root=test_root)`）。
2. **`clear()` 的删除面已接守卫（§二之八十六落地）**：`assert_test_area_deletable(test_root, protected_root)`（`af_test.py:71-97`）只拦"删下去必然出格"的两档形状——`TEST_ROOT_IS_FILESYSTEM_ROOT`（目标就是盘根）与 `TEST_ROOT_COVERS_PRODUCTION`（目标等于正式存储根**或为其祖先**）。对照量由调用方现递 `store.root`（同一个进程真正在用的那一份，`af_mcp.py:428`），两侧都 `resolve()` 再比，因为正式根常写成相对路径（`DEFAULT_STORE_ROOT = ".forge"`）。`protected_root` 是**必填的关键字参数**：少递就是 `TypeError`，宁可当场炸也不让一次不可逆删除在"没人知道正式区在哪"的状态下发生。MCP 工具的 schema 保持**零参数**，所以 Agent 递不进删除目标；守卫拒判回工具语义 `ok: False` + 具名 `code`（`af_mcp.py:418-430`），不塌成"清完了"。判据 17 条腿在 `tests/unit/test_test_area_delete_guard.py`（含"部分删除失败不许报成清空成功"）。
   - **政策半边已由 DCD 裁掉**（`20261010-AF测试区删除面守卫与政策口-裁定` §二，2026-10-10，L2）：裁**甲＝维持形状守卫**，不新增"只准删 `{root}/test` 前缀"的白名单闸。理由记清两点：① 本批要根治的是"必然出格"与"没删掉却报成功"两格，都已落地且有判据钉住；② **乙**（前缀白名单）的正确取值取决于 NAS 部署里正式根与测试区的真实位置关系，那台机器 AF 读不到——硬写前缀就是 AF 自己抄的第二份部署事实。**乙仍列候选**：将来若改判，需要 DB 先给一句权威树的现读路径。**丙**（接进 F12 归属守卫那套 `_dir_owner`/`assert_deletable`）要先给测试区落一套归属标记，跨 `af_test`/`af_store` 两模块，本批不动。⇒ 由此定型一条**已知射程边界（不是遗漏）**：`test_root` 被指到正式根的**同级**目录（如 `/data/other`、`.forge-backup`）仍照删。
   - 权限与面板可见性（同一份裁定 §三）裁**甲＝维持现状**：scope `write`（`af_mcp.py:538`）、无 HTTP 路由、无面板格。裁定采用的依据与 AF 侧现读一致——两棵第一方 UI 树对 `af_test` **0 命中**、`build_app` 路由表里 `api/test` **0 条**；收 owner 档令牌要动 #79 在途的鉴权文件，加只读面板格要重钉路由计数棘轮，两条都与登录线同源冲突。
   - **判例（同一份裁定 §四，已入册为常规）**：**不可逆操作的守卫，对照量必须由调用方现递，被守卫的模块不许自带第二份真值。** 这一条钉的威胁面不是"Agent 递路径"（schema 零参数递不进），而是**服务端自己那份 `test_root` 配置指错了地方**；所以对照量取"这个进程真正在用的正式根"，不取配置文件里的第二份读数，也不拿 `/data/test` 那种字面默认值当判据。

---

## 十一、前端：三棵第一方树，生产用的是两棵

| 树 | 面向 | 技术栈 | base / 挂载 | 状态 |
|---|---|---|---|---|
| `ui/` | 开发者/工程师控制台（naive-ui） | Vue 3.5 + vue-router 4.5 + naive-ui 2.45 + pinia 3 + vite | 挂 `/ui`（compose volume + `--ui-dir`） | **在用**，20 条路由（`ui/src/router/index.ts:5-30`） |
| `ui-user-mimo/` | **用户视角 WebUI（ForgeSight 线）** | Vue 3.5 + naive-ui 2.40 + pinia 2.2 + vite-plugin-pwa | 挂 `/mimo`，vite `base:'/mimo/'`（`vite.config.ts:9`） | **在用**（生产），路由带 `beforeEach` 登录守卫（`src/router.ts:4-30`） |
| `ui-user/` | 早期原型（Tailwind + lucide） | Vue 3.5 + tailwind 3.4 + pinia 2.2 | 无 Python/Docker 引用 | **已冻结/归档**（`ui-user/README.md:1-3` 明写） |

> 注意名字坑：**"用户视角 WebUI" = `ui-user-mimo/`，不是 `ui-user/`**。`ui-user/` 里那份登录是坏的（`stores/auth.ts` 写 `'mock-token'`，`api/client.ts` 根本没有 `login` 方法；token key 两处还分叉 `forgesight_token` vs `fs_token`）。任何"改用户视角 UI"的任务如果落到 `ui-user/` 上，改的是废树。

**`/mimo` 这条同源链是白屏事故的来源，已被判据钉住**：dist 里资源绝对路径 `/mimo/assets/*` 必须与后端挂载前缀一致，不一致就"200 但白屏"。三处必须同值：

1. `af_api.py:265` `UI_USER_PREFIX = "mimo"`（声明为唯一真源）；
2. `ui-user-mimo/vite.config.ts:9,24-25`（`base` + PWA `start_url/scope`）；
3. `docker/docker-compose.api.yml:23,27`（`--ui-user-dir /mimo` + volume）。

外加 router 的 history base 用 `import.meta.env.BASE_URL`（构建期同源，不硬编码），这条由 `c06d8ea` 收；跨三处的一致性由 `tests/unit/test_ui_user_mount.py:71-155` 当场核对，**不靠人记得**。

SPA fallback：`af_api.py:1389-1448`（catch-all，`ui_dir`/`ui_user_dir` 由 `build_app(store_root, examples_dir, ui_dir, ui_user_dir, readonly)` 注入）。MCP 与 HTTP **同一个 FastAPI app**（`POST /mcp` 在 `af_api.py:1057`），没有第二个静态服务。

UI↔路由门禁把三棵树全扫（`9c32ea0`），读数现在长这样：调用点 94 处（ui 53、ui-user 19、ui-user-mimo 22）、字面量 55、模板拼接 38、条件分支 1、传输层包装 3、SSE 建流 2、参与匹配路由 90、兜底/MCP 排除 2、反向读数未被调用 15（只计数不判红）。

### 卡片取数正源：`svc.automation_card`（2026-10-09 重钉）

`GET /api/automations` 与 `GET /api/automations/{name}` 的卡片字段**只有一个写者**：`af_service.automation_card`（`af_service.py:461-518`），端点只做参数搬运。这条接缝此前在 `af_api.py` 里直接读 `rec["graph"]` 那一层的 `nodes` / `enabled` / `nl` —— **那一层没有这些键**：`_graph_raw` 写出的容器层只有 `{"automations": [...]}`（`af_store.py:56-58`），`nodes`/`enabled`/`nl` 全在 automation 级。于是 12/12 条归档一律渲染成「无设备 / 空预演 / 恒已启用」，而"停用"点在容器层落旗子、没有代码读它——**运行期真正读这枚旗子的是调度器**（`af_scheduler.py:87-89` 不为禁用项注册触发、`:241-243` 不排空其队列），写在容器层等于按钮白按：现场表现为"卡片是假的、点了没反应"，而这条自动化到底在不在跑，跟卡片那几个读数无关。

- 设备：`sorted(auto.reads() | auto.writes())` 跨 automation 去重，人类可读名走 `DeviceCatalog.display_names()`（`af_catalog.py`，**只读缓存、不发网络**，缓存没有的实体回 `entity_id` 本身）。
- 启停：`enabled = bool(autos) and all(auto.enabled …)`；空归档按"未启用"呈现（真值不是缺省）。
- 预演：`render_graph(graph).text`；归档解析失败**不再把整个列表打成 500**，坏记录只在自己那一格写 `⚠ 归档无法解析（IR 校验失败）：…`。
- `trial` / `last_triggered` / `trigger_7d`：**没有可读取的落盘正源**，一律 `null` / `0`。首演-试演台账按 `store_diff_sha256` 记账（`af_apply.py:208` → `af_premiere.enter_trial(store_diff_sha, hours=24)`），不是按自动化名；触发记录只在 watch 进程内存里。旧形状在这里硬写 `state:"auto"` 等于替每条自动化宣布"已走到全自动档"——读不出就给 `null`。
- 启停写侧走 `svc.set_automation_enabled`（`af_service.py:521-543`）→ `store.resave_raw`：锁、版本号、归属核对留在 store 那一处，端点不碰盘；形状不认识（容器层不是 `{"automations":[…]}` 或含非对象条目）**抛 409 且不落新版本**，不写在容器层装成功。
- 判据：`tests/unit/test_user_ui_card_and_toggle.py`（含一条源级腿：`af_api.py` 里不得再出现 `g.__setitem__("enabled"` 这种手抄落盘）。

---

## 十二、鉴权：登录正规化、令牌、配对

### 12.1 首次进入设定管理员（`e5b3fd5`）

- `AdminUserStore`（`af_auth.py:1053`）：**PBKDF2-HMAC-SHA256，`PBKDF2_ITERATIONS = 100000`（`:1064`），`SALT_BYTES = 16`（`:1065`，`secrets.token_hex`）**；落盘 `{store_root}/.auth/admin.json`，权限 0600（`:1061`）。
- 校验走常数时间比较，且**用户名不存在时也照算一次哈希**（`verify` `:1139-1152`，缺盐时用 `"00"*SALT_BYTES`）——防时序侧信道"哪个用户名存在"。
- 端点：`GET /api/auth/has-admin`、`POST /api/auth/register`（已存在则 409，注册即自动发令牌）、`POST /api/auth/login`、`/logout`、`/me`。
- 前端怎么知道要首设：`ui-user-mimo/src/views/LoginView.vue` 挂起来拉 `/api/auth/has-admin`，`isRegister = !has_admin`。**没有第四个信号源**。
- 兼容档：管理员尚未注册时 `login` 接受任意非空凭据并回 `warning`；注册后转严格校验。**这一档是"未登录也能用"的窗口，只在首设前存在。**

### 12.2 令牌面

- `TokenRegistry` 持久化 `{store_root}/.auth/issued_tokens.json`（`af_api.py:294`、`af_mcp.py:1060` 读写同一份，一个注册表两个脸）。
- 缺省 TTL `ISSUED_TTL_S = 86400.0`（`af_auth.py:144`，签发在 `:329`）；`expires_at` 缺失/naive ⇒ fail-closed（`:212-233`）。
- 多主体令牌注入键名 **`AUTOFORGE_TOKENS`（单数）**，真源是 `af_auth.py` 的 `load_secret(...)`；这条键名同源由 `tests/unit/test_compose_env_key_source.py` 钉住（旧文写成复数，NAS 现场因此手补过）。
- 读取优先级：**credentials.json > secret 文件（`/run/secrets/<NAME>`，`AUTOFORGE_SECRET_DIR` 可覆盖）> 环境变量**（`af_secrets.py:6`、`:20-26`）。

### 12.3 三个逃生阀（都是"只放权限、不改执行"）

| 键 | 位置 | 语义 |
|---|---|---|
| `AF_ALLOW_NOAUTH=1/true/yes` | `af_api.py:360-383` | 放开匿名读；**当前有一枚在飞的扩展改动（工作区未提交）**，本文按 HEAD 描述 |
| `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` | `af_mcp.py:94-95` | 只认字面量 `1`；原型全放行档 |
| `AF_REQUIRE_AUTH` | `af_api.py:374-377` | **已废弃**，fail-closed 是缺省 |

`conftest.py:21-23` 规定这些只能 per-test `monkeypatch.setenv`，**不许全局置位**（否则会出现"测试绿是因为把鉴权关了"）。

### 12.4 配对（Agent 拿令牌，不拿码）

`af_request_pair`（`af_mcp.py:540`）→ 后端生成 **8 位单次短时效码**，经 SSE 推到用户 ForgeSight 弹窗 → 用户**口述**给 Agent → `af_pair`（`:554`）兑换 Bearer 令牌。码只显示给用户，Agent 侧不落地。HTTP 侧对应 `/api/mcp/pair/request`、`/api/mcp/pair/redeem`、`/api/mcp/pair-request`（SSE）、`/api/user/pair/{code}/confirm`、`/api/user/pair/accepting`。

---

## 十三、写闸与单写者租约

两把锁，别混：

| 锁 | 名字真源 | 路径 | 作用 |
|---|---|---|---|
| **单写者租约** | `SERVE_LOCK_NAME = ".serve.lock"`（`af_flock.py:29`） | `{store_root}/.serve.lock`（`:32-33`） | 决定这个实例**收不收写** |
| watcher 协调锁 | 字面量 `watch.lock`（`af_cli.py:600`、`af_service.py:2569-2571`） | `{persist_dir}/watch.lock`（+ `watch.lock.info` sidecar） | 同一时刻只允许一个 watcher |

- 启动时抢：`af_cli.py:1416-1430` 用 `FileLock(serve_lock_path(store_root)).try_acquire()`；抢不到 ⇒ `readonly=True` 传进 `build_app`，写端点 `_readonly_guard` 回 503（`af_api.py:430-441`）。
- 运行期探：`_single_writer_check` 用 **`held_by_other()` 只探测、不 acquire**（`af_flock.py:114-135`；`_LOCAL_HELD` 认出本进程持有的锁，`:121`，闸门不反装），判据只认内核 flock/`msvcrt.locking`，陈旧 sidecar 忽略。
- 拒收文本前缀唯一出处：`READONLY_DEGRADED_PREFIX = "READONLY_DEGRADED:"`（`af_service.py:1840`），MCP 真机下发被拒时**原样回传、不套壳**（`af_mcp.py:988-993`），HTTP 面 503（`:1751-1753`）。这枚前缀在契约表里的登记半边**归 DCD／homesdk**，现读两文档各零命中（执行记录 §二之七十一）。
- `/api/health` 两格分开：`readonly` 是**身份声明**（v1.x "这是一个可被只读部署的服务"，硬编码字面量，语义不动，`af_service.py:294`）；`write_gate` 才是**运行期真值**（裁定 20261008 §一 B，`:217-242`、`:295`）。三态：`open` / `blocked` / `no_lease`——**探不到租约状态时读 `no_lease`，绝不塌回 `open`**。旧现场出现过"health 报 readonly=true 但写面 200"的矛盾读数，这就是新增那一格的理由。

---

## 十四、出向通道：只有一条生产者

| topic | 方向 | 唯一生产者 | 位置 |
|---|---|---|---|
| `af/automation/fired` | 出 | `observe_terminal()` → `publish_fired()` | `af_mqtt_bridge.py:71`（常量）、`:647`（发布者）、`:673`（唯一调用点） |
| `af/automation/failed` | 出 | `observe_terminal()`（同处声明 sole writer） | `:72`、`:650`、`:676` |
| `adm/autoforge/status`（retained，presence） | 出 | `advertise()` / `publish_degraded()` | `:77`、`:540`、`:555` |
| `butler/inbox/speak\|notify\|tv` | 出（AF 只**投递**，不订阅、不决定播不播） | `inbox_publish()`（由 `InboxAdapter` 调） | `:82`（前缀真源）、`:328`（名单从 `_presence.INBOX_TOPICS` 派生）、`:405`（三道 fail-closed 都在这一个口）；适配器 `af_adapters/inbox.py:47` |
| `ma/insights` | **入向 only** | 订阅白名单守卫拒任何出向尝试 | `:73`、`:684`/`:692`（`start()` 里唯一一次 subscribe）、`:700`/`:709`（`subscribe_topic`/`handle_message` 的禁订守卫）、`FORBIDDEN_SUBSCRIPTIONS :85` |
| `ma/presence` | **入向 only**（卡3） | AF 只消费：`ingest_linkage()` 落盘，**不转发、不出向** | `PRESENCE_TOPIC :86`、`start()` 的订阅环 `:737`、`ingest_linkage :866`（`LINKAGE_KIND_BY_TOPIC.get :881`） |
| `ma/device-health` | **入向 only**（卡3） | 同上，同一张嘴 | `DEVICE_HEALTH_TOPIC :87`、`:737`、`:866` |

- **入向两条主题名只有一枚真源**：`LINKAGE_TOPICS`（`:90`）+ `LINKAGE_KIND_BY_TOPIC`（`:91`），`start()` 的订阅环、`handle_message` 的分派、`linkage_status` 的 `inbound.subscribed` 三处都从它拼。`homesdk.presence` 里**没有** `ma/*` 主题符号，所以这两条常量归 AF 持有，同源由 `scripts/check_topic_whitelist.py` 对着契约表核（现读 7 处 topic 字面量全部在册）。
- **收与消费在两个线程上，队列就是那条接缝**：paho 回调线程只做"判形状 + 原子写一条文件"（`ingest_linkage`），常驻 tick 线程经 `poll_linkage()`（`:931`）→ `af_live.pump_linkage()`（`af_live.py:364`）把新条目注进内部总线（`af_cli.py:623`，接在 `_tick()` 里 `runtime.tick()` 之后）。`EventBus` 没有锁，回调里直接 publish 等于把跨线程改状态这件事藏进 broker 的重试路径里。`serve` 那条进程**没有 ticker**（`af_api.py` 里 `start_ticker`/`.tick(` 现读 0 处），所以它只入队不触发。
- **落盘形状与载荷裁剪分两半**：形状由 `af_linkage_feed.LinkageFeed`（`:153`）定——`{store}/linkage_events/{kind}/{13位毫秒}-{event_id}.json`，`append()`（`:177`）之后就地 `_trim()`（`:267`：先 TTL 删超龄、再按硬上限删最旧，**只由写触发**，所以"纯写不读"也被回收）；载荷键由桥按契约 §1.2 逐键白名单取（成员子键**不含 `via_raw`**、设备侧 `from` 另存 `from_state`），对端多发的私有字段不会经 AF 的归档扩散。
- **水位线键把毫秒放在最前**（`_key`：`{ms}|{kind}|{后缀}`）。按 `{kind}/{文件名}` 排会得出一个静默失效的形状：`device_health` 整个目录永远排在 `presence` 前面，于是"先收一条在场、再收一条更晚的设备健康"时后者字典序更小、被当成已消费——那条掉线事件不触发，而盘上明明有。
- **拒收只按契约，不按心情**：`trace_id` 非空、`members` 必须是数组（空数组合法）、`stable_id` 必须非空；`total` 只有真 `int` 才留（`"2"`/`True`/`2.5` 都丢）。没给队列（`linkage_sink=None`）⇒ `no_linkage_sink_wired` + `ADM_ERR_INTERNAL`，每条都进 `linkage_rejected` 计数——这比"看着健康其实全丢了"好读。入向计数**独立**（`presence_in`/`device_health_in`/`linkage_rejected`），不与洞察那套 `insights_rejected` 混用。

- 出向事件载荷必经 `_envelope()`（`:622`）；错误码/状态名从 `homesdk.adm.errors` import，不手抄。
- **收件箱三条不走 `_envelope()`**：载荷由库侧 `homesdk.presence.<kind>` 生成（必填键、长度上限 `INBOX_MAX_TEXT`/`INBOX_MAX_TITLE`/`INBOX_MAX_BODY`、`ts` 的 epoch 口径都在库侧，0.3.2 规格 §三.1"谁定 schema 谁把校验"——**AF 的文档与代码里都不再复写那三个数字**，复写就是第二份会漂移的手抄面）。AF 只做三件事：把 IR 作者填的字段按库侧签名铺成位置参数（`_inbox_fields` `:350`）、**堵掉机制层入参**（`_INBOX_INTERNAL_ARGS :324`：`client`/`trace_id`/`qos` 不许 IR 手写——开放出去等于允许伪造事件号或降 QoS）、以及把同一批上限**在编译期读一次**去预拒超长载荷（`af_adapters/inbox.py:74` `inbox_overlong_fields` → 扫描器第 43 项 `INBOX_PARAM_TOO_LONG`，见 §十八 第 9 条）。
- **前缀只有一枚**：`INBOX_PREFIX`（`:82`）同时供投递名单派生与禁订守卫（`:700`/`:709`）。两处各写字面量的话，改一处会得到"AF 往新前缀发、却按旧前缀拒订"——护栏看着还在，实际管不到自己发出去的那一族。这条由 `tests/unit/test_inbox_contract_keys.py` 钉住（含"桥源码里该字面量只许出现一次"）。
- **发布不许静默**：`homesdk.mqtt.publish` 把 paho 的 `rc` 原样交回，而库侧各家投递函数丢弃它；paho 在**没连上**时不抛异常、只回 `rc=MQTT_ERR_NO_CONN`。所以 `_raise_if_refused`（`:344`）把非零 `rc` 升成 `PublishRefused`（`:333`），失败统一进 `_account_publish_error`（`:578`）：计 `publish_errors` + `mark_degraded(ADM_ERR_BROKER_UNREACHABLE)` + retained status 转 degraded。**这一格的拆码已有裁定**（`decisions/20261010-AF拆码与MA自更新四问-裁定.md` §一：Q1 甲新增一枚 `ADM_ERR_PUBLISH_REFUSED`／Q2 甲 rc→语义的翻译归**库侧**做／Q3 甲 ACL 被拒仍算 degraded 但 `reasons` 必须指向真因码）。库侧那一半已落地（homesdk `37fc7aa`：`PublishResult`/`classify_publish_rc`/`PublishRefused`，`ADM_ERRORS` 现读 7 枚，契约 §7.2 同批加行）；**上面这段描述的仍是 AF 盘上的 0.3.2 消费面**——AF 半边（删掉本仓那份 `PublishRefused` 与 rc 检查映射、`_account_publish_error` 改取 `exc.error_code`）硬前置在"新 wheel 装到本机 + 四处 pin"，发版执行人已由 AF 递交申请（见 §十八 第 10 条）。
- 这条"唯一生产者 + 必经信封"已升成静态门禁：`scripts/check_mqtt_writers.py`（`8b629b8`），现为四条判据（A 写者点位只在桥内；B `fired`/`failed` 只在 `observe_terminal` 内；C `_publish` 载荷来自 `_envelope`；D 机制层 `_presence` 入口——含 `getattr(_presence, …)` 动态派发——只在桥内）。
- 守卫失明半边仍走 retained `af/status`（`mark_degraded(ADM_ERR_INTERNAL)` + `publish_degraded()`）。
- 洞察入向落盘：`PersistentInsightSink` → `{root}/insight_proposals`（`af_mqtt_bridge.py:196`），`af_insight_queue.py:75/:181` 是**不可部署**的队列（approve/reject 才进提案面，`af_proposal.py:125/:186`，无 deployer ⇒ 不自动上线）。

---

## 十五、持久化：默认根、原子写、拒写护栏

| 面 | 现读 | 位置 |
|---|---|---|
| 归档默认根 | `.forge`（`DEFAULT_STORE_ROOT`） | `af_store.py:49` |
| 实例持久化 | `{root}/instances/{instance_id}.json`，原子 + 记录级 SHA256 校验和；**读侧坏记录/坏校验和跳过不炸** | `af_persist.py:35-37`、`:76-83`、`:223` |
| 恢复后重挂会话 | `restore_persisted()` 末尾调 `executor.reseed_sessions(restored)`，把落盘时挂起的 `ask` / 人工确认会话重挂回 `pending_asks` ⇒ `/api/asks`、`/api/asks/pending`、`pending_asks.json`、`Runtime.stats()` 四张脸都读得到。判据只用 `state==suspended` + `ctx.context["pending_confirm"]` + `node.kind=="ask"`（**不能循 `timer.kind`**：未设 `timeout` 的 ask 挂起时 `timer` 就是 `None`，重启后没有任何计时器信号）；**只重挂、不放行**，恢复过程零下发；挂起节点查不到 ⇒ 落 `instance_session_lost` 具名审计，不许静默跳过 | `af_runtime.py:203`、`af_executor.py:507-565`、`af_audit.py:35-37` |
| 两种写形 | **append**（`append_jsonl` `af_store.py:129`，用于 `af_error_knowledge.py:174`/`af_preference.py:535`/`af_telemetry.py:168`）vs **rewrite-on-save**（`save` `:296`、`resave_raw` `:349`） | — |
| 撕裂面 | 单行写入不撕裂（`fh.write(line + "\n")`），坏行的失败面是"丢一行"（`read_jsonl_bounded:138-147` 跳过坏行） | 执行记录 §二之七十一 |
| **拒写护栏（数据丢失族）** | 归档/凭据/偏好/授权码等九站在落盘前判"现档是否坏"，坏 ⇒ **拒写并留原因**，绝不用空/半截覆盖好文件 | `af_store.py:427-434`、`:457`、`:461`；九站归属见执行记录 §二之七十一（`3d49595`+`f315112`） |
| 原子写助手 | `atomic_write_text`（tmp + fsync + `os.replace`），21 个模块走它 | `af_atomic.py:27` |
| 审计 | append-only journal（`open("a")`） | `af_audit.py:132-156` |
| fire 日志 | `{store_dir}/fire_log.json`，rewrite 且原子；坏行容忍 | `af_fire_recorder.py:40-45`、`:55` |
| 洞察提案队列 | `{root}/insight_proposals`，**满则拒收新提案**（丢一条＝"MA 从没投过"） | `af_mqtt_bridge.py:196`、`af_insight_queue.py:75/:181` |
| 联动入向队列（卡3） | `{root}/linkage_events/{kind}/{13位毫秒}-{event_id}.json`，**满则裁最旧**（状态快照：旧的这条不值挡下新的这条）；每类各 500、TTL 24h、触发侧另有 120s 年龄闸；读不出的文件进 `unreadable` 环形清单（≤50）只记账不判红 | `af_linkage_feed.py:153/:177/:267`、`af_mqtt_bridge.py:222` |
| 有界容器 | `BOUNDED_CACHES`/`FIXED_KEY_CACHES` 注册表（不落盘） | `af_bounded_caches.py:30-32` |
| secrets | `/run/secrets/<NAME>`，`AUTOFORGE_SECRET_DIR` 覆盖 | `af_secrets.py:20-26` |

---

## 十六、门禁与判据：绿不是给人看的

| 门禁 | 判什么 | 位置 |
|---|---|---|
| `gates.sh` | 仓内质量门禁总入口（`GATES_PYTHON=<path>` 指定解释器） | `gates.sh` |
| `scripts/check_mqtt_writers.py` | 四条判据：A 出向写者点位只在桥内；B `fired`/`failed` 只在 `observe_terminal` 体内；C `_publish` 载荷来自 `_envelope`；D 机制层 `_presence` 入口（静态属性与 `getattr` 派发都算）只在桥内。锚点含 `INBOX_PREFIX`/`inbox_publish`，锚点读不到就 exit 2 | `scripts/` |
| `tests/unit/test_inbox_contract_keys.py` | 收件箱名单与字段表**没有第二份抄本**：全部从 `homesdk.presence` 派生，含"前缀只有一枚"与风险分级反例腿 | `tests/unit/` |
| `tests/unit/test_inbox_pipelines.py` | `adapter: inbox` 过编译（扫描无 `L2_*`）、过仿真、过 NL 渲染，且预演档零上线字节；含 `sms`/`send` 反例证明缺省档没被放松 | `tests/unit/` |
| `scripts/check_mqtt_subscriptions.py` | 入向订阅点位门禁：订阅只许出现在桥内、`INSIGHTS_TOPIC` 之外必须是登记过的动态主题且函数体内有禁订族守卫。卡3 加了两条主题后现读仍是"订阅点 2 处 / 1 个文件"（两条都走同一个 `subscribe_topic()`） | `scripts/` |
| `tests/unit/test_linkage_feed.py` | 联动入向队列本体：落盘形状 `{kind}/{13位毫秒}-{event_id}.json`、重启后回读、**每类各自封顶不共享**、TTL 裁最旧、外来非毫秒文件名不误删、水位线跨类按毫秒优先排序（这条钉的是"设备健康被静默当成已消费"那个失效形状）、超龄只留档不回放、坏 JSON 记账不阻塞、`unreadable` 环封顶、成员键白名单与 `MEMBERS_LIMIT`/`TEXT_LIMIT` 裁剪 | `tests/unit/` |
| `tests/unit/test_linkage_subscription.py` | 桥的入向半边：两主题各落各目录、契约必填项（`trace_id`/`members` 是数组/`stable_id` 非空）缺一个就拒收且每条带 `ADM_ERR_*`、无队列 ⇒ `no_linkage_sink_wired`、入向计数与洞察计数**互不共用**、`pump_linkage` 真把事件注进总线且只消费一次、`inbound` 那格读数形状 | `tests/unit/` |
| `scripts/check_bounded_caches.py` | 新增增长容器必须进注册表/固定键表/基线，或带豁免标记 | `scripts/` |
| `scripts/check_mqtt_runtime_dep.py` | 镜像/开发/测试三面依赖一致（paho 必须在） | `scripts/` |
| `scripts/verify_adm_window.py` | 停机窗当天验收入口，缺项读不成绿（PASS/FAIL/UNAVAILABLE 三态） | `scripts/` |
| `tests/unit/test_ui_api_paths_gate.py` | UI 调用点 ↔ 后端路由双向对账（含三棵树、反向读数） | `tests/unit/` |
| `tests/unit/test_ui_user_mount.py` | `/mimo` 三处同源 | `tests/unit/` |
| `tests/unit/test_compose_env_key_source.py` | compose 环境变量键名 = 代码读取键名 | `tests/unit/` |
| `tests/unit/test_serve_lease_single_writer.py` | 租约三判据（HTTP 503 / MCP 拒收不套壳 / 空闲照常） | `tests/unit/` |
| `tests/unit/test_corrupt_state_write_bar.py` | 九站拒写护栏（45 腿） | `tests/unit/` |
| `tests/unit/test_fidelity_canon_vocabulary.py` | 保真校验器的规范化词汇表面：词汇表外的值出**报告**不出崩溃（位置＋具名码）、tuple≠list／int 键≠str 键／bool≠1／NaN 拒比、合法形状照常保真且键序仍无关、深度预算取自 `MAX_PARAM_DEPTH` 那份单一真值源（本模块不许出现第二个数字）。三枚 AST 腿钉形状（`_canon` 不许再吃整容器 `dumps(sort_keys)`、`json.dumps` 只准编码单枚字符串叶子、`verify_roundtrip` 的 `except` 里不许出现 `Exception`/`BaseException`），另有两枚结构腿：`not_comparable` 必须同时在数据类字段与 `__all__` 里、`ok` 必须等于那四项的派生值而不是字面量 | `tests/unit/` |
| import-linter 契约 | 分层依赖方向（kernel 不得 import service 边界） | `pyproject.toml` |

反空洞纪律（本项目已踩出来的做法，写在账本里）：每条新判据要带 **CONTROL 腿**（什么都不改也必须绿）、**边界腿**、**原始失败存在性腿**（不套 wrapper 前先证明坏会真坏）、**AST 结构腿**（守卫装在那个真的会落盘的方法体内、且排在写调用之前）；变异腿只跑在 `%TEMP%` 的 `git archive HEAD` 副本树里，跑前 `ast.parse`，字节备份在 `finally` 还原。

---

## 十七、部署现状（NAS）

- 编排：`docker/docker-compose.api.yml`（HEAD）。`build.context=..`、`image=autoforge-api:latest`、`container_name=autoforge-api`、端口 `8787:8787`、`restart: unless-stopped`。
- 镜像：`docker/Dockerfile.api`，基础镜像 `python:3.14-slim`，**`COPY src`（源码烘进镜像）**，`pip install -e ".[api,ha,mqtt]"` + 钉死文件名装 `docker/homesdk/homesdk-0.3.2-py3-none-any.whl`，非 root `uid=1000/gid=1001` 运行。那枚私有 wheel 的来源、构建命令、引用面清单与更换流程只写在 `docker/homesdk/README.md` 这一份指路文档里（它**刻意不复制摘要**——字节真值在 `tests/unit/test_mqtt_compose_env.py:163` 的常量与库侧 `dist/VERSIONS.txt` 两处）；"目录里只许一枚、四个引用面必须同名、字节必须等于登记值"由 `scripts/check_vendored_wheel.py` 每批判（§二之九十八）。
- 卷：`/vol1/1000/docker/autoforge-store:/data`、`…/ui/dist:/ui`、`…/ui-user-mimo/dist:/mimo`。**运行期不挂 `src`**（热改后端要重烘镜像；开发期临时挂卷那行是注释掉的，勿入生产）。
- 环境：`AUTOFORGE_LIVE_ENABLED`（缺省 0）、`AUTOFORGE_HA_URL`、`AUTOFORGE_SESSION_TTL_S=3600`、`AUTOFORGE_INBOX_KEY`、`AUTOFORGE_MQTT`（缺省 0）+ `MQTT_HOST/PORT/KEEPALIVE/USER/PASSWORD`（值一律留空）、`AUTOFORGE_TOKENS`。敏感值走 secrets 或宿主 shell env，**不写进这份入库文件**。
- compose 的 `.env` 加载位置在**文件同目录**（`docker/.env`），不是仓库根；放错就是取空值。
- 变更后端 ⇒ `docker compose build` 重烘；inode 类变更 ⇒ `--force-recreate`；验收按 md5 对撞（部署通道细则在项目记忆/执行记录里）。
- git：本地 `master`、远端 `main`，只准 `git push origin master:main`；NAS 侧另有 `nas` remote。

---

## 十八、已知残余（HEAD 现读，逐条点名）

**A. HEAD 上有 5 条判据腿是红的**（当场在 `git archive HEAD` 副本树整树跑批：`3479 passed, 53 skipped, 65 subtests passed, 5 failed in 331.18s`，`PYTEST_RC=1`）：

| 红腿 | 现场读数 | 根因 | 归口 |
|---|---|---|---|
| `tests/unit/test_bounded_caches_gate.py::test_real_repo_is_green` | `af_conflict.py:183` 的 `ConflictArbiter._cooldown_pending` 既不在注册表/固定键表/基线，也没豁免标记 | DCD 20261008 §二 那批改动的登记半边没做 | **本批已收（2026-10-09）**：走就地豁免并写理由——它的唯一写入口是 `on_user_override()`（失败分支 `add`、成功分支 `discard`），成员是**实体 id** 而非事件，所以同一实体反复失败不增长。不给它硬上限（那等于把实体从 fail-closed 守卫里放出去，正是该裁定要防的"冷却没启动就改回用户刚设的状态"），也不给它 TTL（`_expire()` 不清它，出口只有成功重登）——所以它**不进** `BOUNDED_CACHES`，那条 TTL 腿是给一条不存在的腿盖章。若 DCD 认为 pending 该有硬上限＋告警，那是策略面，本批不自主决定 |
| `…::test_real_repo_measurements_are_pinned` | 扫到 126 个容器，钉的是 125（HEAD `2d92bb1` 复测已到 128） | 同一格 + 之后三批各带来新容器没重钉 | **本批已收**：钉到 129，四条增量逐条给出处（`InboxAdapter.intents`、`_RecordingInboxClient.records`、`ConflictArbiter._cooldown_pending`、`LinkageFeed.unreadable`，全部带就地豁免与封顶腿） |
| `tests/unit/test_ui_api_paths_gate.py::test_real_ui_and_src_are_clean_and_counted` | `(90-5)+2 == 85` 断言：参与匹配路由已从 85 涨到 90（新增 `/api/auth/*`） | 登录正规化 `e5b3fd5` 带来 2 条装饰器路由（`GET /api/auth/has-admin`、`POST /api/auth/register`；88→90 那条读数里含 5 条挂载表），未重钉 | **本批已收**：重钉 87 / 90，两条路由各有 `ui-user-mimo/src/views/LoginView.vue` 里的真 `fetch` 调用点（第四张调用脸），扫描范围没放宽 |
| `…::test_all_trees_of_this_repo_are_in_scope_and_green` | 期望 `ui-user-mimo 20`，现读 **22** | 同上（mimo 树多了 has-admin + register 两处调用点） | **本批已收**：钉到 22 |
| `tests/unit/test_pkg_markers_gate.py::test_real_repo_is_green_on_the_index_reading` | 仅副本树红（无 `.git` 索引） | **副本树环境所致，不是产品缺陷**；工作区里这条是绿的 | 记为测量口径，不修 |
| `gates.sh` 的 AST 计数棘轮（不是 pytest 腿） | 现读（测试区删除面批次后）：`新增/未获批 2 条（error 0 / warn 2）`、`基线内存量 96 条`、`过期基线条目 0 条`，`计数：except-pass-broad=20 \| fake-ok-const=78` ⇒ 全量 **98** 条 / 登记上限 **97** 条。差的两条仍是 `api_auth_has_admin`（HEAD `af_api.py:980`）与 `api_auth_register`（`:1001`）返回字面量 `ok=True`；对 `git archive HEAD` 副本树复测得 `99 条 / 过期 0`，带上本批三份文件得 `98 条 / 过期 1`（`af_test.py#fake-ok-const#TestChannel.clear` 已不再命中）| 上限那两条是登录正规化 `e5b3fd5` 上线后没重钉——与上面那族同一个成因，**本批仍没碰**（`af_api.py` 躺着并发会话未提交改动）。上调上限是**评审动作**，已交 DCD `20261009-AF-AST两条未获批的ok字面量归属`（甲改派生／乙上调上限／丙入基线，AF 建议甲；Q2 问纯查询端点能否不写 `ok` 键），裁定回"窗口钉在登录线提交后"排到登录线之后（任务 #79）。本批对 `.gates-baseline.txt` 只做一件事：按门禁自身语义（`Report.is_red = bool(active) or bool(stale)` ⇒ 过期条目本身就判红）**删掉那条已不再命中的过期指纹**，未新增豁免、未动上限；99→98 的差是本批把 `TestChannel.clear` 从字面量 `ok=True` 改成按盘上实测派生（fake-ok-const 79→78）。远端那格（run 127 六 job 只 `quality-gates` 红、`pytest` 已绿）本批没重跑，只作历史同形记录，不当现值 |

> 说明：这 4 真 + 1 口径**都不是本轮文档改动引入的**，是"产物已上线、钉住的读数没重钉"这一族。工作区当前另有并发会话未提交的 `af_api.py`/`af_auth.py`/`ui-user-mimo/*`/`docker/*` 改动，所以重钉前先把"这批数字有没有混进未提交态"量掉：`git archive HEAD` 副本树单跑**这六份文件**（两份计数门 + 四份鉴权）得 `4 failed, 146 passed, 1 warning in 151.84s`，那 4 条恰好是上表前四行、读数与工作区**逐位相同**（路由 90、mimo 22、容器 128；本批只再加 `LinkageFeed.unreadable` 这一条 = 129）；四份鉴权文件在那棵树上**一条 FAILED 都没有**。⇒ 钉的是"已提交态 + 本批"，不是混合态；鉴权那族红只在带未提交改动的工作区里红，不由本批重钉也不由本批修。
>
> 2026-10-09 工作区混合态现读（**未提交态**整树跑批，含并发批次的鉴权改动 + 本批 A/C 修复）：`10 failed, 3504 passed, 53 skipped, 1 warning, 65 subtests passed in 887.87s`，`PYTEST_RC=1`。逐条归属：上表 4 条真红原样还在（同因）；**新增 6 条全在鉴权线**——`test_dcd_20261004_auth_limits`（owner 明文 / 第三方 write 令牌掩码 2 条）、`test_v0_8_auth`（legacy 单令牌兼容 / 多令牌分档 / 撤销即时生效 3 条）、`test_v1_4_token_expiry`（过期令牌 HTTP 侧读到 `400` 而非 `403`，`assert 400 == 403`）。这 6 条**不在 HEAD**：对 `git archive HEAD` 副本树单跑这四份鉴权文件得 `48 passed, 1 warning in 51.38s`（`PYTEST_RC=0`），它们只在带那批未提交改动的混合态里红。本批三份判据文件（`test_user_ui_card_and_toggle` / `test_start_watch_identity` / `test_atomic_write_sites_fixes`）在这一跑里全绿、一条都没进 FAILED 名单。

**B. 结构性残余**（不是红，是射程边界）：

1. `af_irreversible.py` 只有 NL 渲染侧调用方，**执行面没有调用方**（§9.3）。⇒ **2026-10-10 复测：这半句成立，但"它 therefore 是缺陷"这个前提要裁（B.1，已递 DCD 等裁，执行记录 §二之九十二）**。把消费者按符号量开：四枚 helper 里只有 `nl_runtime_note`（`:66`）有 src 消费者（`af_nl.py:20` import、`:404` 调用），`runtime_fields_of :42`／`is_node_non_reversible :47`／`annotate_non_reversible :54` **各 0 处**（只由 `tests/f14/test_ask_irreversible.py:139-148` 跑）；两枚常量另有人用——`af_nl_parse.py:40-43` import 后在 `:1093/:1150/:1217/:1227/:1284/:1303/:1306` **写**标注，`scripts/check_ir_runtime_keys.py:67/:92/:97` 拿它做 schema 白名单硬门（写侧那模块自己正卡在 B.7 这格）。**执行面不读 `_non_reversible` 是事实**：确认闸走的是 `requires_confirm`（编译期派生 `af_orchestrator.py:683`、运行期消费 `af_executor.py:648-651`，裁定 20261009 §三 已落 ⇒ §二之八十）。更要紧的是**撤销的真值源不是它**：`af_undo.py:359 inspect()` 自己给 `undoable/expired/domain_mapped/domain_unmapped/confirm_required` 五档，而 `af_irreversible.py:1-12` 的模块 docstring 从头到尾写的是 **NL↔IR 往返保真**（运行时字段不算"可逆核心"，用 `[运行时]` 占位承认其存在），从没主张自己是撤销的输入。所以 B.1 有两条互斥的读法：按设计它是保真标注 ⇒ 这格该结案；要它参与执行面闸 ⇒ 那是新增语义，且要与 `requires_confirm` 排先后。DCD 申请 Q1 给了三档（甲＝按设计结案／乙＝接进 `requires_confirm` 闸／丙＝撤销台账加一格，但押 `af_api.py` 并发窗口），**AF 不主张乙、丙**；等裁期间不预先落码，也不在文档里把这格写成已修。
2. ~~`af_fidelity.py:29-30` `_canon` 仍是裸 `json.dumps`，与第十三轮修掉的 `_leaf_key` 同形~~ **已由执行记录 §二之八十七 收口**（编号保留、不占残余格，免得后面 3-13 全部重号）：`_canon`（现读 `af_fidelity.py:54-93`）改成**带类型标记的递归编码**，词汇表就是 JSON 那一套（null/bool/int/有限 float/str/list/dict），`bool` 排在 `int` 之前判（否则 `True` 与 `1` 塌成一格），tuple 与 list 各打 `t`/`l` 标签，非 str 键与非有限浮点当场拒，深度预算走 `check_param_depth` 那份单一真值源（本模块不出现第二个深度数字，由 AST 腿钉住）。表外形状抛具名 `FidelityNotComparable`（`:35`、`:38-51`，带 `code`/`where`/`detail`，仍继承 `ValueError` ⇒ 调用方按既有口径接得住），`verify_roundtrip`（`:273-315`）只捕三枚具名错（`FidelityNotComparable`/`ParamDepthError`/`LeafUnserializable`，宽捕由 AST 腿拒绝），把它落成 `FidelityReport.not_comparable`（`:261-270`）一档：`ok` 与 `projection_fidelity` 同时 False，`detail` 逐字写"无法比较（不是不等）：…"。`fidelity_equal` 遇同一格仍抛具名错而不返回 `False`——返回 `False` 会被读成"两条不等"，实情是"这一条没比成"。判据 `tests/unit/test_fidelity_canon_vocabulary.py` 21 条腿（四格抛穿、两格塌陷、合法形状不误伤、四枚静态形状面），副本树变异 8 枚里 7 枚各有被杀腿、**1 枚是等价变异**（M5：把 `ok` 里的 `and not not_comparable` 摘掉仍全绿——当前代码里 `not_comparable` 为真时 `projection_fidelity` 必为 False，那一项被蕴含；留着是把不变式写在可读的位置，不假称它能杀）。**消费面如实记**：`af_fidelity` 在 `src` 里没有产品调用方（`grep` 全仓 src 的唯一命中是 `af_nl_build.py:7` 的一句 docstring 提及，不 import、不调用），调用面只有 `tests/f14` 的开发/CI 验收（F14 P1 分层等价），所以这一格修的是"校验器自己会不会把假绿写进验收结论"，不改对外契约。
3. ~~`af_test.clear()` 的无守卫 `rmtree`（§十）~~ **已由执行记录 §二之八十六 收口**（编号保留、不占残余格，免得后面 4-13 全部重号）：`clear()` 删前过 `assert_test_area_deletable`（`af_test.py:71-97`，两枚具名码 `TEST_ROOT_IS_FILESYSTEM_ROOT` / `TEST_ROOT_COVERS_PRODUCTION`），对照量是调用方现递的 `store.root` 且两侧 `resolve()` 后比；`ignore_errors=True` 已撤（那一档把"没删掉"抹平成 `cleared: True`，与仓内"没做成都不许报成 done"同族），残留改成按盘上 `rglob` **数出来的** `residual`，删除失败现场按 `onerror` 收进 `errors`（`DELETE_ERRORS_MAX = 20` 封顶 + 如实 `errors_total`）。MCP 工具 schema 保持零参数 ⇒ Agent 递不进删除目标；守卫拒判回 `ok: False` + 具名 `code`。判据 `tests/unit/test_test_area_delete_guard.py` 17 条腿 + 14 枚变异自证。**政策半边已由裁定 20261010 收尾**（§二 甲＝维持形状守卫、§三 甲＝维持 scope `write` 且无面板、§四 判例入册"不可逆守卫的对照量必须调用方现递"），同级目录照删从此是**裁定的已知边界**而非待办；详见 §十 那三条子弹与执行记录 §二之八十八。
4. ~~observe 档在异常/拒绝两处回落为"照常执行"是设计（§八），但它意味着**试演期的守卫是瞎的**——这段时间的降级通知半边由 `_notify_guard_blind` 承担，判据只覆盖 enforce。~~ **已由执行记录 §二之九十 收口**（编号保留、不挪后面条目）：前半句（回落是设计）成立，**后半句量的时候发现不成立**——修前 observe 档在 introspect/request 两站**根本没调用** `_notify_guard_blind`（调用点站在 `if observe:` 回落**之后**、request 那站还整个在 `else:` 分支里），只有 band 那站会通知，所以"这段时间由它承担"是一句没被实测过的承诺（原句另说"判据只覆盖 enforce"，现读其实连 observe 的失明面都没有腿）。修法是把通知半边按**这一跑拦没拦**分栏而不是按模式：`blocked` 由三枚调用点现给（`:300`/`:318-320`/`:338`），被调方 `_notify_guard_blind`（`:474-523`）**不自己读模式**（第二份真值，且会把 band 那站读反）。仓内常驻指示（`af_watch` 的 `conflict` 列，detail 新键 `blocked`）两档都落；对端 retained `af/status` 只在真拒发时发，试演档那一次落一条 WARNING（`:496-501`）——这一跑什么都没拦，拿"守卫瞎了"去占对端的"降级"那一格是给没发生的事下结论。band 站**不看模式**（裁定 20261008 §四 裁的是"读不出来就拒发"，没给试演档开口子 ⇒ 永远 `blocked=True`），这一档由 CONTROL 腿钉住防止下一批把它"顺手统一"成跟着模式走。判据 `tests/test_af_conflict_runtime.py` 现读 32 条腿（本批 +8：observe 两站指示／enforce `blocked=True` 反向 CONTROL／对端快照两档同腿对照／band 档在试演期照拒照通知／WARNING 读数面带 `SENTINEL_B94` 自证／两枚 AST 结构腿＝"三枚站点各带 `blocked` 且被调方不含 `settings.mode`"＋"通知必须站在 `if observe:` 之前"）；副本树变异 M0 全绿＋M1–M9 全部被杀。**还剩的半边（仍属本条，不另开格子）**：试演期失明**要不要也对端可见**＝跨仓 `degraded` 词汇，AF 不自加、不改契约 §7.1 的 state 枚举，等 DCD 裁（未在本批申报，理由是这一格没有 AF 可自决的落码半边——两种答案都是"要么不动要么越权"）。
5. 语义泛化/审计工具的四个缺口（semgrep / detect-secrets / pip-audit / mutation）仍是射程缺口，未变成门禁。
6. `READONLY_DEGRADED:` 前缀在 homesdk 契约表的登记那一格在 DCD／homesdk 手里（现读两文档零命中）。
7. `af_nl_parse` 有实现无产品调用方；NL→IR 自由文本属 F14 P2。⇒ **2026-10-10 现读订正并拆成两半（执行记录 §二之九十二）**：这条格子原先只点名 `af_nl_parse`，审计 §十八 B.7 点名的是 `af_nl_build.build_ir_from_nl`（`af_nl_build.py:57`）。现读两个模块在 `src/` 里的**引用数都是 0**（`grep -rln af_nl_parse src/` ⇒ 空；`af_nl_build` 的唯一命中是一句过时 docstring 提及，不 import、不调用），所以"有实现无产品调用方"两格都成立，不是笔误。**AF 职权内的那半已落（`c99047e`）**：`af_fidelity.py` 的模块 docstring 长期写着 P2 `build_ir_from_nl`"未实现"，而解析器早已在树里、`tests/f14/test_nl_build.py` 一直在测它——写在源码里的能力声明不会随代码自己更新。本批把它改成现状（P1 用结构化投影、P2 用受限文法解析器，两条半程互不替换），并钉成判据：docstring 里凡点到这个名字的行都不许带「未实现」，且**必须至少有一行点到它**（否则这条腿会因"空集给的干净"而静默失效）；副本树变异四腿 M0 控制全绿／M1 把声明改回"未实现"被杀／M2 删掉点名被杀／M3 同义改写且属实＝必须仍全绿（证明这条腿不是 diff 探测器）。**剩下那半不是"能不能实现"的问题**：文本→IR 的对外脸已有两张（AF-Spec 文本 `compile_text`：`af_service.py:1423` → HTTP `af_api.py:648` → CLI `af_cli.py:1030`；意图 JSON：MCP `_t_draft` `af_mcp.py:367`），缺的只是**中文自由文本**这一张——MCP `TOOLS`（`af_mcp.py:471`）现读 31 枚（AST 取列表元长度），其中唯一带 `build` 字样的 `af_build` 是 `required: ["ir"]`（`:683`，输入必须是 IR dict，描述里的"自然语言"是它的**输出**而非输入），`ui-user-mimo` 里"自然语言"现读 0 命中。要不要新开一枚工具/端点属对外契约与 blast radius 决策，已递 DCD（`inbox/20261010-AF-两格已实现无调用方的定性与NL面是否开工具-决策申请.md` Q2 三档：甲 MCP 工具／乙 HTTP+mimo／丙维持库面），等裁期间 AF 不预先落码。
8. ~~`L2_NEEDS_CANARY` 没登记进 `CHECKS`~~ **已由执行记录 §二之八十三 收口**（编号保留、不再占残余格，免得后面 9-13 全部重号）：键进了 `CHECKS`（`af_scanner.py:55`）与 `CODE_HINT`（`:112`），诊断发出点现读 `:429`；按注册表枚举检查面的消费面从此不漏这枚硬门，且那条 ERROR 到 Agent 手上 `hint` 非空（`Diagnostic.__post_init__ :179-181` 的回退以前对这枚码是空串）。防复发是通用判据而非点名：`tests/unit/test_diagnostic_code_catalog.py` 拿 AST 扫全仓 `Diagnostic("X")` 字面量，要求 X 两本目录都在、hint 非空，另有一枚反向棘轮（除点名名单里那 7 枚走变量/常量传码的键之外，目录里不许有谁也发不出的幽灵键）。
9. 收件箱投递（计划 §七 卡1）的**上线可见那一半没在这台机器上验**：验收口径写的是 `mosquitto_sub` 能见，而 NAS 侧订阅+对撞属合并窗动作。仓内证到的是"上线字节由库侧生成、被记录代理原样接住并反解核对"（`tests/unit/test_inbox_adapter.py`），不是"broker 上真有这条主题"。同一条线上原先并列的第二件（长度上限只在库侧 `_len_bounded` 把、编译期不提前拒、于是这条 IR 要跑到执行才红）**已由执行记录 §二之八十五 收口**，编号保留不重号：新增第 43 项 `INBOX_PARAM_TOO_LONG`（`af_scanner.py:709-728`，**ERROR 级、拦发布**——契约 §1.3 已写明 DB 侧对超长载荷 fail-closed 丢弃，这条动作发出去也必死），上限从库侧 `INBOX_MAX_TEXT`/`INBOX_MAX_TITLE`/`INBOX_MAX_BODY` **现读**（`af_adapters/inbox.py:57` 那张表的格子里放的就是这三枚常量本身，不按名派发、不写数字），AF 侧零整数字面量，`(kind → 受限字段)` 名单与库源码里 `_len_bounded(arg, CONST, "field")` 的现读值由 AST 对撞钉死（`tests/unit/test_inbox_param_length_diagnostic.py`）。**护栏 3（按 source 限速）已由裁定 20261010 §四 收尾，AF 侧无可落码半边**：Q1 裁**甲**（DB 在 `inbox_events` 落表处按 `source` 计数，超了丢弃 + 审计——DB 是唯一看得见"这个来源这一分钟总共收到多少"的地方，生产者侧各自只看得见自己），Q2 裁**不设独立分钟级 N**（由冷却期 + 每日预算承担，理由：契约已写明 DB 收到后仍过 Sentinel 闸门含每日预算，分钟级再加一层是重复约束）。契约表 §1.3 护栏 3 那一行由 DCD 落笔改写，上限真源口径同时登记（摘录数字的真源＝`homesdk.presence.INBOX_MAX_*`）。⇒ 这一条**还剩的只有 `mosquitto_sub` 那格对端验收**（任务 #76，NAS 合并窗）。
10. 发布失败目前只有一个码（`ADM_ERR_BROKER_UNREACHABLE`，`rc=…` 只在 message 文本里）：ACL 拒绝与 broker 不可达不区分，是否拆码归 DCD。⇒ **申请已于 2026-10-10 正式递交**（`E:\NAS\关键决策部\inbox\20261010-AF-发布失败单码rc三类根因是否拆码-决策申请.md`，三问：Q1 拆不拆码＝甲新增具名码／乙把 `rc` 升成结构化一格／丙维持现状并留运维口径；Q2 `rc`→语义的翻译归库侧 `homesdk.mqtt.publish` 还是 AF 自建映射（AF 不主张后者——paho 常量在库侧那一层，AF 自建表就是判例禁止的第二份真值）；Q3 ACL 被拒而链路可达时 retained status 落哪一档）。现读证据一并交了出去：这枚码是**刻意**统一的（`af_mqtt_bridge.py:363-372` 的 docstring 自己写明"rc 的语义不在 AF 猜"），失败记账的唯一出口在 `:620-636`，AF 三处 rc 检查在 `:427`/`:609`/`:650`，库侧 `mqtt.py:168-177` 原样交回 rc、`presence.py:217-222` 与 `:111/:114` 把它丢掉。AF 不自加 `ADM_ERR_*`、不自改契约 §7.1 的 state 枚举；裁定回来后一次性落码＋判据＋变异。⇒ **裁定已回**（`decisions/20261010-AF拆码与MA自更新四问-裁定.md` §一：Q1 **甲** 新增一枚 `ADM_ERR_PUBLISH_REFUSED`、只加一枚不拆 `ADM_ERR_MQTT_ACL`／Q2 **甲** 翻译归**库侧**，"AF 三处 `_raise_if_refused` 退成接住库侧具名异常、映射表在 AF 仓内一份都不留"／Q3 **甲** ACL 被拒仍算 `degraded`，但 `reasons` 必须指向真因码）。**库侧那一半已提交并自证**（homesdk `37fc7aa`；现读 `572 passed / FULL_RC=0`、库门禁 `HS_GATES_RC=0`、副本树变异 `killed=9 survived=0 invalid=0`、`COPY_RESTORE_BAD=0`；执行记录 §二之九十一）。**AF 这一半仍挂着，卡的是发版动作不是判定**：本机 `import homesdk` 命中的是 site-packages 里的 0.3.2 **实体副本**（`direct_url.json` 现读 `E:/NAS/homesdk/dist/homesdk-0.3.2-py3-none-any.whl`，`hasattr(mqtt,'PublishResult')=False`、`len(ADM_ERRORS)=6`），镜像侧 `docker/Dockerfile.api:26-27` 按钉死文件名 COPY＋`pip install`，而"禁止 agent `pip install`"是生态纪律 ⇒ 新 wheel 的构建、投递与四处 pin 改谁执行属 DCD／owner，AF 已就此递第二份申请（`inbox/20261010-AF-拆码库侧已落地发版窗与消费半边前置-决策申请.md`：0.3.3 增量清单追认／发版执行人三档／`reasons` 换真因码后要不要给 DB 出读数变化说明，并案 #95 那一格）。⇒ B.10 现读状态：**半边落地、半边等版本窗**（任务位 #93）。
11. 联动**入向**（计划 §七 卡3）的验收也差对端那半边：仓内证到的是"契约要求的必填项缺了就在拒收计数里带着 `ADM_ERR_*`"，而 MA 实际发出的载荷是否逐键符合契约 §1.2 那两行，只有 NAS 合并窗拿 `mosquitto_sub -t 'ma/#' -v` 对撞得出来。另外这一路**没有面板格**：唯一读数面是 `/api/health` 的 `linkage.inbound`（本批刻意不加新路由，路由计数因此没动），也没有 per-source 限速（与第 9 条同一护栏——**该护栏已由裁定 20261010 §四 裁成"由冷却期 + 每日预算承担、不设独立分钟级 N，执行人在 DB 消费侧"**，所以这一格在 AF 侧不是待办，只在对端验收）。
12. 入向事件的**"按设备"半边已通，"按成员数组"的结构半边仍堵**（裁定 20261009 §四 甲+直译落地）：原先两道墙都在——`_trigger_repr`（`af_instance.py:466-475`）给实例上下文的只有 `{entity_id, state}`（`event` 不是 `Mapping` 时走这条；`BusEvent` 是 dataclass，实测 `_trigger_repr(BusEvent.custom('ma_presence', {...}))` 得 `{'entity_id': 'event.ma_presence', 'state': ''}`），而 `make_resolver`（`af_state.py:157-182`）对 `context.` 只做平表查找、`split_namespace` 用 `partition(".")` ⇒ `context.trigger.subject` 的键是 `"trigger.subject"`，不在表里就 `KeyError`。甲那一档只动第一道墙：`_trigger_flat_keys`（`af_instance.py:482-507`）在 `spawn` 里把三枚平键并进 `context`（`af_instance.py:264`），名单真源 `TRIGGER_FLAT_KEYS :479`，取值逐字直译契约 §1.2——`entity_id`→`trigger_entity_id`、主标识→`trigger_subject`（device-health 有 `entity_id` 就落它；presence 没有 `entity_id` 才落 `subject`，而那一格正是 `member_id` 串）、`kind`→`trigger_kind`；对端没报的那一格落**空串**，三枚键**恒定在场**，因为少一枚就是运行期整段 `KeyError`。解析器**一字未动**，所以 `context.trigger.members` 这类嵌套路径仍读不出——按裁定的"结构级半边留给乙或卡2（`ma_query` + 变量绑定）"。判据不是"读得出"而是"读出的值真会改分支"：一条 `on event.ma_device_health` → `if context.trigger_entity_id == "sensor.plug"` 的双岔自动化，`sensor.plug` 走 yes、`sensor.fridge` 走 no（`tests/unit/test_linkage_subscription.py`）。变异自证两枚：把平键值改成恒空串 ⇒ 3 条红（含那条分支判据，证明它不是摆设）；删掉 `spawn` 里那行合并 ⇒ 7 条红、报的正是 `KeyError: 'trigger_entity_id'`（证明"恒定在场"那一档是真在挡东西）。载荷本来就没丢：盘上记录与 `/api/health` 的 `inbound` 一直在，缺的只是当场能读的那三格。**词汇已由 DCD 登记进契约表**（`ADM联动主题注册表与消息契约.md` §1.6，2026-10-09 执笔），主标识口径裁**甲A**：DSL 的 `trigger_subject`＝**`entity_id` 优先**，盘上 `LinkageRecord.subject`＝**`device_id` 优先**（展示身份），**两格并存、各有用途**，`af_mqtt_bridge.py:906` 不改。
13. `forge serve` **不抽**联动队列（该进程没有 ticker，`af_api.py` 现读 `start_ticker`/`.tick(` 各 0 处）：入向事件照样落盘、`presence_in` 照样涨，但不会有自动化被驱动。设计分工，不是缺陷，却是最容易被误读成"接线没生效"的一格。
14. ~~**overwrite 导入的备份回收是静默的**~~ **已由执行记录 §二之八十九 收口**（编号保留、不占残余格，免得后面重号；本格与早前收口后删除的那格 B.14 无关）：五枚 `shutil.rmtree(..., ignore_errors=True)`（修前 `:616`/`:627`/`:640`/`:653`/`:658`）全部撤掉，删除收成**一个入口** `_purge_tree`（`af_store.py:73-95`，本模块唯一的 rmtree 站点在 `:86`；失败现场按 `onerror` 逐站收集，残留个数按盘上 `rglob` **现数**，不拿 `len(errors)` 代替——遍历可以只失败一半）。三档后果各归其位：① 写新成功后回收失败 ⇒ `_drop_stash:738` 返回残留明细，`import_bundle` 带进报告的**新格 `residual_backups`**（`:806` 初始化、`:867-869` 收集；MCP 面 `_t_import` 整份透传、CLI `af_cli.py:956-959` 有 echo），并落 WARNING `:749`；`ok`/`imported` 语义不动——导入这件事确实做成了，这一格不冒充"条目失败"，所以它不进 `errors`；② 回滚成功后壳没回收 ⇒ `_unstash_archive:699` 尾 WARNING `:732`（这一档在 `raise` 路径上、没有报告可写，**日志是唯一读数面**，如实这么写）；③ 让位前的预清理失败 ⇒ 不再让 rename 去抛那柄与真因毫不相干的 `FileExistsError [WinError 183]`，改成碰正式区之前就抛 `BackupNotReclaimed`（`:58`，继承 `ValueError` 与 `FidelityNotComparable` 同口径；抛出点 `:673`，实测抛后 live 版本一字未动、那份可捞的备份整份还在）。后缀真源 `BACKUP_DIR_SUFFIX :53`、明细封顶 `BACKUP_ERROR_DETAIL_MAX :55`。判据 `tests/unit/test_overwrite_backup_reclaim.py` 11 条腿（失败注入一律打在 `os.unlink`/`os.rmdir`＝rmtree 遍历内部；日志读数面 setup 里先发哨兵自证）+ 副本树变异 9 枚**全部被杀**、含"什么都不改"档。**上一批登记措辞的两处更正**（详见执行记录 §二之八十八 §三 的引用块）：清理触发点不是"没有"，而是只有修前 `:615-616` 那一枚（同名、下次 overwrite 前），实测它平时确实把残留回收掉；"盘上越攒越多"不成立——备份目录名固定为 `{目录名}.__ovbak__`，同名复用同一枚。**这一格还剩的半边**：HTTP 面 `_svc`（`af_api.py:777-784`）只映射 `ServiceError`/`IRValidationError`，`BackupNotReclaimed` 到 API 仍是 500——与修前同形（原来是 `FileExistsError` 的 500），把它接进 `:474` 那组 409 处理器要动登录线在途文件，随 #79 的窗口一起做。
15. **tick 自愈今天没有运行期消费者**（2026-10-10 第六轮审计 BUG-11 改判 ＋ 第二轮运行时审计⑨ ＋ 安全与暴露面第三轮"3+1 未接线"，三份报告各自独立现读到同一处）：`af_live.tick_watchdog_pass`（`af_live.py:83`）在 `src/` 内**无调用者**，`af_tick_supervisor.health_state` 原先那句"主线程 watchdog 仅在意外终止时重启本线程"是**写在源码里的第二份真值**——它承诺的补偿动作不存在。本批已把声明改成与控制流一致，并钉两条腿（`tests/unit/test_watch_lifecycle_r6_audit.py`：声明侧必须带"没有调用者"、`src/` 里一旦出现调用点即红；变异 M11／M12／M13 三案分别杀到）。**剩下的不是文档活**：接线（把守护巡检挂进主循环）还是降档（删掉函数、`/health` 只留读数）会改运行期语义，按 `decisions/20261010-AF与DB与DPP十件-裁定.md` §六 Q3 归 DCD 结案，AF 不自行记为"按设计"。⇒ 任务 #99，已攒进下一批集中申请（Q3 允许"攒一批"）。顺带一句为什么不顺手接线：`_tick_exit_reason`（`af_live.py:65`）是模块级全局，ticker 线程写、主线程读且无锁，接上去恰好**造出** BUG-11 描述的那个竞态。
16. ~~**`af_persist.claims()` 的"读不出租约"等于放行接管**~~ **已由执行记录 §二之九十四 收口**（编号保留、不占残余格，免得后面重号）：修前 `owner` 有值却 `_parse_iso(lease_until_wall)` 返回 `None` 时直接 `return True`——证明不了"已过期"却把实例交给另一个进程，与 F12「归属未知不再放行」同一条 doctrine 的两个面。现读形状：`af_persist.py:199-207` 命中 `lease is None` 时落一条 WARNING（带 owner 与**原始值** `%r`，读数面就是日志）并 `return False`；下游 `af_runtime.py:176-189` 对 `False` 的处理是**跳过且删文件**这条路径都不走，记成 `INSTANCE_LEASE_HELD`。判据两条：`tests/unit/test_v0_9_multiproc.py:311`（五种读不出形状全判 `False`，无主/本进程所有仍判 `True`，并断言日志里真有"读不出"那一句）＋ `:336`（端到端：抹掉 `lease_until_wall` 后刷新校验和 ⇒ `restored == []`、`INSTANCE_LEASE_HELD` 在场、那份记录还在盘上）。副本树变异三案：M20（改回 `return True`＝修前形状）杀这两条、M21（保留 fail-closed 却把 WARNING 删掉）杀 caplog 那条、M99b（只动这段散文的同义改写）仍全绿 ⇒ 钉的是行为不是字。**不成立的半边**照原样登记：报告建议"租约改用单调时钟"做不到，租约要跨进程、跨重启，单调时钟只在同进程内可比；真风险是时钟回跳且无漂移告警，那半格归 #10（homesdk.time 接入窗）。
17. ~~**`forge` 三条导出命令的产物写盘不是原子的**~~ **已由执行记录 §二之九十四 收口**：`af_cli.py:946`（bundle 导出）／`:1058`（spec 导出）／`:1168`（经验导出，喂 MA 那份）三处裸 `Path(out).write_text(...)` 全换成 `atomic_write_text`（import 在 `:23`），崩在半截不再留半个 `.json`／`.md`。同形状自查查出**审计没点名的第四处**：`af_service.py:2727` 的 watch pid 文件（同文件 `:2689` 早就走助手，唯独 pid 那格漏了），一并收掉。**为什么既有门禁扫不到这三处**：`scripts/check_atomic_write_sites.py` 的 D 腿射程按"这条写的路径看得出指向 `.auth`"收紧（`:356-374`），E 腿只管状态落盘腿，导出命令与 pid 文件两样都不在交叉线上——门禁读数因此从头到尾都是"裸写 0 处"，这一格是**门自己的盲区**而不是"报了没修"。补的同源判据在 `tests/unit/test_atomic_write_sites_fixes.py:303-338`：AST 扫 `src/autoforge` 全仓的 `write_text`/`write_bytes` 整份覆盖写，命中即红；接受 `atomic-write: exempt(理由)` 就地豁免但**理由为空仍判红**；反向自证 `:335` 在临时树里植三行、要求命中行号恰为 `["2","3"]`（证明扫描器不是摆设）。变异四案 M22/M23/M24/M25 各把一处改回裸写，四案全被 `test_no_bare_whole_file_writes_left_in_src` 杀。
18. ~~**`_seen_ids` 的"LRU"是名字，不是行为**~~ **已由执行记录 §二之九十四 收口**：`af_live.py:290` 改 `OrderedDict`（`from collections import OrderedDict` `:25`），命中时 `move_to_end`（`:303`），超 4096 时删队首 2048（`:307-311`）——队首从此确实是"最久没再见过"的那一半，注释里的 LRU 不再是假的。判据 `tests/unit/test_event_dedup.py:99`：造 4100 个事件，让一枚热 id 在表满前后各命中一次、一枚冷 id 只在开头出现过，断言热 id 不被带走（`count("hot")==1`）、冷 id 确实被重放（`count("c1")==2`）——**两条断言方向相反**，所以"什么都不淘汰"和"淘汰错的那一半"都能被杀（变异 M26 命中不 `move_to_end` 杀前一条、M27 把上限判据改成 `if False:` 杀后一条，两案打同一条腿的不同断言）。同形状在全仓 grep 出的另一处是 `af_bus.py:317-330`：那里的机制本来就是**真 FIFO**（`set` + `deque.popleft`，命中不重排），行为无缺陷，只有注释把它写成"LRU 槽位"——本批把措辞改成 FIFO 并点名淘汰站点，属声明面与控制流对齐，不占残余格。
19. **时区机制的双源 fallback 仍是"待裁"，不是"已裁要切"**（2026-10-10 按 owner 转来的裁定逐份对账时查出）：`20261001-AF-homesdk接入四问-裁定.md` 今天 17:27 被 DCD 追加了一段《台账补正 2026-10-10》，两格——① AF 2026-10-05 交的回填告知只落在 `关键决策部/inbox/`，**没回填进那份裁定末尾**（制度 §6.7 说的"回执与台账分家"）；② 该件顺带提出的"切除双源 fallback（`af_local`）、改单源 `homesdk.time`"**状态是待裁**。现读两格都对得上：`af_time.py:238` 的 `mechanism` 键仍会在库侧模块缺席时报 `"af_local"`（这格是**可观测**的，`:113` 写明"缺席是可观测的，不是静默的"），而 `decisions/20261010-AF与DB与DPP十件-裁定.md` 全文对 `homesdk.time`／`af_local`／`双源` 三个关键词 0 命中 ⇒ 没有新裁定覆盖它。⇒ AF 今天无可自决的动作；**但那条"A 档：`homesdk.time` 由 DCD 在 0.3.x 写"的前置其实已经兑现**，现读两面：本机 `af_time.house_tz_status()` 报 `{'tz_name': 'Asia/Shanghai', 'source': 'fallback', 'mechanism': 'homesdk.time', 'resolved_by_name': True, 'utc_offset': 8.0}`（`source` 那格是"没设环境变量、取缺省名"，与 `mechanism` 两码事——供数机制确实是库侧模块），模块实体在 `.../Python313/site-packages/homesdk/time.py`；镜像面用 zipfile 直读 `docker/homesdk/homesdk-0.3.2-py3-none-any.whl` ⇒ `namelist()` 含 `homesdk/time.py`。**顺带修掉一处过期声明**：`af_time.py:111` 那句"不可用时返回 None（vendor 的 wheel 里没有该模块）"与本仓自己那条绿判据（`tests/unit/test_af_homesdk_time.py:47` 直接断言 `house_tz_status()["mechanism"] == "homesdk.time"`）互相矛盾，已改成"库侧没装、或装的是不含 `time` 的旧版时走这一档"（只动 docstring，一个字节逻辑都没改，改后 `tr -dc '\r' | wc -c` 仍是 447，行尾没翻）。**回填那一格不由 AF 单方面动**：`E:\NAS\关键决策部` 不是 git 仓（现读 `git rev-parse` ⇒ `not a git repository`），往对方台账写字没有回退路径 ⇒ 等 owner 定谁来写（AF 可出可直接粘贴的回填稿）。任务位 #101。
20. ~~**门禁的两份台账互相打脸：`af_service.py` 在关键模块名单里，基线却供着它那条 `except-pass-broad` 指纹**~~ **已由执行记录 §二之九十五 收口**（第六轮审计 ARCH-01 里 AF 职权内的那半边）：`.gates.toml:14` 那句「关键模块：这里的 `except Exception: pass` 是硬错误，不许进基线」与 `.gates-baseline.txt`（HEAD `025616d` 那份）**第 17 行**的 `src/autoforge/af_service.py#except-pass-broad#health` 同时在场，而**没有任何一条门拿这两份对过账**——AST 门只判"新增不许超基线"，覆盖门只判"`check_*.py` 有没有被某条链跑到"，基线存量条目与 `critical_globs` 的交集从没人看过 ⇒ 这种矛盾可以无限期存在。严重度真源在 `homesdk/gates/scan.py:145`（`self.is_critical = matches_glob(rel_path, config.critical_globs)`），而 `:177-180` 是全仓**唯一**按这一旗抬级的分支，所以"硬错误"今天机械可判的集合只有 `except-pass-broad` 一枚（新判据里的 `CRITICAL_HARD_RULES` 就是照它写的，出处记在注释里，不是 AF 另立的名单）。修法三格：① 站点（`af_service.py:246 def health(` 里那段 tick 健康探测）把 `pass` 换成 `logger.debug("TICK_HEALTH_PROBE_SKIPPED", exc_info=True)`，**行为一寸没动**（探测失败照答 `ok: True`、`tick_health`／`ticker_alive`／`tick_exit_reason` 照 `None`），只是不再咽得无声——具名码命名与同模块的 `EXPERIENCE_OBSERVE_SKIPPED`／`ENTITY_HEALTH_MAP_SKIPPED` 一族；② 基线只按**门禁自报的"过期基线条目 1 条"**删掉那条指纹（`Report.is_red = bool(active) or bool(stale)` 让过期条目本身判红），86 行／83 条 ⇒ **85 行／82 条**（`except-pass-broad` 17⇒16、`fake-ok-const` 66 不动），`.gates-tally.txt` 一个数字没动、零新增豁免；③ 覆盖门加判据 ⑦——基线里任何 `except-pass-broad` 指纹的路径若命中 `critical_globs` 即判红，文案里把修法钉死（"改站点留痕或删这条指纹，别把模块移出名单——移出名单＝把口径改成追认缺陷"），实现复用库侧 `GateConfig.load_file`＋`matches_glob`（AF 不手抄第二份名单，与 ARCH-09 那族同源），另补三条**射程下限** `exit 2`（关键模块名单为数 0／`.gates-baseline.txt` 不在盘上／`.gates.toml` 解析不了）。判据 44 条腿（覆盖门 40 含本批新 9 ＋ 行为面 `test_arch01_health_trail.py` 4），副本树八案：M0／M99b 全绿、六案被杀（M31 清空硬错误集合与 M33 让基线遍历空转分别钉住"真在按规则判"与"真在遍历台账"，M32 只看名单不看规则＝拿反向腿钉住不许误伤那 66 条 `fake-ok-const`）。副产物：AST 全量 98→97、`error 1→0`、`except-pass-broad` 20→19 ⇒ 本文件 §十八 A 表那条"全量 98 > 登记上限 97"的计数棘轮红**因消掉一条真违规而转绿**（不是靠抬上限，这一句必须写清楚）。**报告自己的口径错一处**：它写"实测该文件当前有 86 条生效条目"，实为 86 行／83 条——头两行是 `#` 注释，它那张 `fake-ok-const 66 ＋ except-pass-broad 17` 的分类表相加也等于 83 而非 86。**还剩的半边不是代码格**：标题前半句"冻结了 86 条已承认的缺陷"是**评审口径判断**（基线存量算不算欠债、要不要限期清零），按 `decisions/20261010-AF与DB与DPP十件-裁定.md` §六 Q3 那条"不得在架构文档里自决记为按设计"的纪律，AF 既不上调 tally、也不宣布它们不是缺陷，等裁。
21. **第六轮审计 ARCH-02「缺版本单一真源」不复立口径、按裁定拆两半**（执行记录 §二之九十六）。**报告点名的格子复测后有三种下场**：① **它给的修法与既有裁定相反**——报告建议「把版本收敛到 `af_service.API_VERSION` 一处」，而 `decisions/20261001-DB目标模式与AF三题-裁定.md` §G 早就裁了 **A＋C 双轨显式映射**：包/API 版本 `0.1.0`（`pyproject.toml:7`、`src/autoforge/__init__.py:7`、`af_service.py:128 API_VERSION`——报告把这一枚写成 `:127`，`:127` 实为 `CONTRACT_VERSION = "1.0"`）是**机器契约**、`v2.x` 是**对外里程碑**，两套并行且都正确，且明文「`pyproject` 与 `API_VERSION` 保持 `0.1.0` 不动」，映射表就在 `docs/plan/版本开发计划.md` §〇。⇒ 本批**一枚版本号的取值都没改**，也不把这一格记成"AF 判它按设计"，只引那份裁定。② **报告自己的读数是过期的**：`git tag` 现读**一枚**（`pre-g2-dod`，报告标题写"无 tag"，它自己表格里又写了那一枚）；`ci.yml` 作业现读**六条**（`test`／`contracts`／`gates`／`architecture`／`ui`／`ui-user-mimo`，报告写"只有四个"）；不过「**没有 release／publish／镜像构建作业**」这半句按现读成立。③ **真缺陷是仓内手抄，AF 职权内已收**：`IR_VERSION` 字面量原先在 `af_ir/models.py:58` 与 `af_orchestrator.py:38` 各写一遍（报告点名的这一格成立，且它漏数了 `af_ir/models.py:67 GROUP_IR_VERSION = "0.3.0"`——那枚与 `:62 SUPPORTED_IR_VERSIONS` 同文件，属真源自身，不是手抄）。修法：orchestrator 从 `.af_ir` 导入，模块属性保留（`af_closedloop/loop.py:233` 经 `runtime.load_module()` 直接读 `mod.IR_VERSION`，那是这条 import 不能消失的原因），判据补两条腿在 `tests/test_af_ir_version.py`——AST 用 `ast.walk` 扫整棵 `src/autoforge`「把 IR 版本号重新写成字面量」（只放行 `af_ir/models.py`；不用 `tree.body` 扫，免得嵌套作用域静默零）＋断言 orchestrator 仍暴露同一枚真值（防"把 import 删干净却照样全绿"）。副本树三案：M0 `RC=0 / 6 passed`、M40 抄回字面量杀第一条、M41 摘掉 import 杀第二条。相关单测 `35 → 71 passed`。另清一份**会过期的副本**：README 原先钉着「回归基线 1300 passed / 51 skipped / 7 subtests（本机全绿，2026-09-30 核实）」而整树现读 `3028 passed / 43 skipped`，改成"README 不钉死计数、读数记在执行记录"；§1.1 同时按裁定 G 补双轨说明并把里程碑更新到 `v2.5`（与 `docs/roadmap/版本路线图.md` §二「✅ 本地交付（NAS 部署走变更窗口）」一致）。**还剩的半边 AF 不自决**：发布面要不要建——`/api/health` 键面现读只有 `ok`／`version`／`contract_version`／`milestones`／`readonly`／`write_gate`／`store_ok`／`linkage`／`tick_health`（**没有 commit sha／构建时间这一格**），`docker/Dockerfile.api:10` 只有 `FROM python:3.14-slim`（无 `ARG GIT_SHA`、无 `LABEL`），两处 compose 都写 `image: autoforge-api:latest`，`ls CHANGELOG*` 无匹配——于是"线上跑的是哪一版"目前只能靠人回忆，而这一格在本仓**已经付过一次账**（NAS 在役面上界读不出、验收靠 `md5` 逐文件对撞）。镜像命名要重烤（NAS 变更窗）、加运行期键＝对外多一张脸、`git tag`＋push 是远端写，三样都在 AF 职权外 ⇒ 递 `E:\NAS\关键决策部\inbox\20261010-AF-发布面与镜像可追溯-决策申请.md`（四问：追溯载体档位／`build` 字段进不进 ADM 契约表／tag 谁打／CHANGELOG 要不要与形态；末档都是维持现状）。查重过三份裁定确认不是重递（`20260928-AutoFlow-10议题裁定.md:37` 是另一仓的发版背书先例、`20261006-DPP-1.16.0发版三件套…:53` 是同部门"main 必须与 tag 同 commit"判例、AF 发布面此前无裁）。等裁期间本文件不写"发布面已具备"，也不写"按设计不需要"。
22. **第六轮审计 ARCH-03「CI 解释器口径」拆两半：手抄 rot 已由新门接住，对齐方向三问等裁**（执行记录 §二之九十七）。**现读形状不是"CI vs 本机"两方，是四方 spread**：`.github/workflows/ci.yml` 的 `jobs:`（`:8`）下**六条作业**（`test:9`／`contracts:29`／`gates:50`／`architecture:83`／`ui:108`／`ui-user-mimo:159`），其中四条带 `python-version: "3.11"`（`:16`、`:36`、`:57`、`:90` 四枚**手抄**，另两条是 node 面 `:115 node-version: "20"`、`:170 node-version: "22"`），`pyproject.toml:10 requires-python = ">=3.11"` **允许**这个钉值，而交付面 `docker/Dockerfile.api:10` 与测试面 `docker/Dockerfile.test:13` 都是 `FROM python:3.14-slim`，README `:155` 又明写「需要 Python 3.14＋」，本机门禁解释器自报 `Python 3.13.2`。⇒ 报告那句"CI 钉值低于包声明的最低版本"**按现读不成立**（`>=3.11` 容得下 3.11），它真正的账在别处：**四枚手抄没有东西保证同步**（改一个作业的解释器，另外三个继续跑旧口径，而每个作业各报各的绿），且**没有任何一条门看过"CI 钉值 ↔ 包声明 ↔ 镜像 base"这三面**。**报告那条「疑似 pip 回溯到老版本」可量、不用上 runner**（PyPI 元数据 578 枚 wheel：`>=3.11` 取到 `0.13.109`、`>=3.14` 取到 `0.13.371`，相隔 262 个发布），**但结论要倒过来**：`pyproject.toml:78` 的 `addopts = "-p no:homeassistant"` 在 CI 上把插件整个禁用，装到哪一版都不参与执行——所以"CI 与本机跑的仿真依赖不同版"在"跑"这层不成立，成立的是 `pytest tests -q -m vhass --collect-only` ⇒ **`10/3858 collected`** 这一族用例在 CI 只走 skip 分支，而"权威跑法"那份 `Dockerfile.test` 自己在 `:11` 写着「本机为 Windows，本文件未经实跑验证」，`ci.yml` 六条作业里**没有一条 docker 作业** ⇒ **真仿真从未在任何自动化链上跑过**（项目最核心的差异化能力没有持续集成保护，这是本条的真缺陷）。AF 职权内落的是**不判口径的 rot 防线**：新门 `scripts/check_ci_interpreter.py`（纯 stdlib）三规则——A 全部钉值同值、B 钉值被 `requires-python` 允许（解析不了要报不许跳）、C 钉值与镜像 base 不一致必须在 `INTERPRETER_DRIFT` 显式认领且理由带两个可核锚点（一个带钉值的真实作业＋一条盘上真在的路径），登记了却没差同样判红（豁免只减不增，语法与 `check_gates_coverage.py` 的 `CI_ONLY_EXEMPT` 同族）；五种"读不出"形状走 `exit 2`（`ci.yml` 缺／作业 0／钉值 0／`requires-python` 缺或解析不了／镜像无 `FROM python:` base）。**规则 C 刻意不判"该对齐成哪一边"**——变异 M53 把交付 base 抄成 `3.13` 门照样绿，就是在自证这一点（判方向＝替 owner 选口径＝越权）。判据 `tests/unit/test_ci_interpreter_gate.py` 19 条腿（含两枚真仓绿腿把 `jobs=6 / pins=4 / values=["3.11"] / images={api:3.14, test:3.14}` 钉死，＋一枚"改写登记理由措辞不动读数"防门去判散文）；副本树六案：M0／M53／M99 绿，M50 杀 A、M51 杀 B、M52 杀 C 的"只减不增"。README 两处口径鲜度：vhass 那栏「自动启用真 vhass」改成「**显式加载插件后**才启用真 vhass」（旧措辞与 `addopts` 打脸），表下补一段写明 **CI 不属于表里任何一栏**（10 条走 skip、CI 绿只证明内置 FakeHA 那条链过了）。递归接缝记一句：`ci.yml:81` 跑 `bash gates.sh`，而新门读的就是 `ci.yml`——runner 上 checkout 带 `.github/`，读不到就 `exit 2`，不会假绿。**交付／验证口径三问不自决、与 #99 那批同批递**：① CI 向镜像对齐还是镜像向 CI（或先统一 `requires-python` 与 README 那句 3.14＋）；② 要不要为真 vhass 增设一条**必跑**作业（等于把 CI 从"永远绿"改成"可能红"）；③ 仿真依赖要不要钉版本（`sim` 与 `dev` 两枚 extra 各列一次、两处都无约束：`pyproject.toml:19`／`:35`）。等裁期间本文件不写"CI 与交付环境一致"，也不写"skip 是真仿真保护的替代"。
23. **第六轮审计 ARCH-04「仓内随附的私有 wheel，且没有私有源策略」：报告"缺什么"三格里两格已被裁定接住、第三格按实测答完；复测挖出的真缝是引用面不对账**（执行记录 §二之九十八）。报告说的是 `docker/homesdk/homesdk-0.3.2-py3-none-any.whl`（`.gitignore:64` 为它开了白名单，49374 B，现读目录里就这一枚）。**逐句对撞**：① 「缺校验和清单」**已过期**——`decisions/20261007-MA五件与AF一件-裁定.md` §六 Q1 裁 **A（仓内随附＋文件名钉死＋权威 sha）**，那份摘要（`19bc83a6…`）既钉在 `tests/unit/test_mqtt_compose_env.py:163` 的 `AUTHORITATIVE_WHEEL_SHA256`（同文件那条判据实算并撞），也登记在库侧 `E:\NAS\homesdk\dist\VERSIONS.txt` 0.3.2 段（`:28` 段首自述"DCD 构建，2026-10-07"、`:32` 一行给全 sha／字节数／用途，只增不删）；② 「没有源码可比对」不成立（源码就在同一块盘的 `E:\NAS\homesdk`，`pyproject.toml:8` 现读 `version = "0.3.2"`、`:2-3` 是 `setuptools>=68` / `setuptools.build_meta`；构建命令登记在 `VERSIONS.txt:42`＝`python -m pip wheel --no-build-isolation --no-deps -w dist .`），**但"可复现"这格报告问对了方向、答法必须换**：实测那枚 wheel 的 22 个 zip 条目带着 **17 个不同 `date_time`**（2026-09-18→2026-10-06，就是各源文件自己的 mtime）⇒ 重烤必然得到**不同 sha256** ⇒ 摘要只能靠**登记值对撞**、不能靠"重建一次看对不对得上"（这一条现在写在指路文档的「来源与可再生性」里，免得下一个人拿重建当核对手段）；③ 「缺『本地开发怎么装、装不上怎么报错』的统一入口」按实测答：装法是裸 wheel 那一行，`.[homesdk]`（`pyproject.toml:61 homesdk>=0.3.2`）**不是可安装入口**——PyPI 404 已实测，它的失败形状是 pip 报 `No matching distribution found`；而"没装"这一侧不静默：`src/autoforge/af_executor.py:95` 是**模块级** `from homesdk.consent import YES, classify_answer`，用 `-I -S` 关掉 `site-packages` 导入包本体实测得到 `ModuleNotFoundError: No module named 'homesdk'`，serve 起不来 ⇒ **不另造 dev-setup 脚本**（报错点就是那条依赖本身，再加一层只会多一张要维护的脸；会静默的那一族 paho／桥已有 `check_mqtt_runtime_dep.py` 与 `paho_available()`／`broker_settings()`）。**复测挖出来的缺陷不在上面三格，在射程边界**：`_wheel()` 从 **`Dockerfile.api` 的 COPY 行**现取路径（设计上有道理——"部署面真正装的那枚才算数"），代价是 `ci.yml:25`／`:42`／`:63` 三条 `pip install`、`Dockerfile.test:26-27` 的 `COPY`＋`pip install`、`docker-compose.api.yml:56` 的注释提及**全都不在那条判据的对账范围里** ⇒ 把 CI 那一面指到目录里另一枚，测试算的仍是交付面那一枚，**CI 装的与镜像装的不是同一枚而 CI 照绿**。M44 是这一格的实测：只改 `Dockerfile.test` 的文件名，新门 `RC=1`，而 `test_vendored_wheel_is_the_exact_bytes_dcd_registered` 在同一棵副本树上 `1 passed`／`RC=0`。⇒ 新门 `scripts/check_vendored_wheel.py`（纯 stdlib）四判据各自可红：**A** 目录单枚／**B** 四个引用面的文件名全等于盘上真身（真源从盘上现取，门里没有第二份名单）／**C** 实算 sha256 等于 `test_mqtt_compose_env.py:163` 那一行（**门自己去读那一行，不抄第二份摘要**）／**D** `docker/homesdk/README.md` 在册、两处真源都指得到、**不许出现 64 位十六进制**；六种"读不出"一律 `exit 2`（目录缺／零枚／常量文件读不到／那一行不再是 64 hex／三份必读引用面任一缺文件或读不出任何引用）。判据 24 腿（含"只改 `Dockerfile.test` 必须红"这条盲区腿＋它的反向自证）＋真仓绿腿；副本树十二案：M0／M99 绿，M42 杀 A、M43／M44 杀 B、M45 杀 C、M46／M47／M48 杀 D 三条各自，M49／M50／M51 落 `exit 2`。接线后覆盖门自认（`check_*.py` 20→**21**、`gates.sh` 覆盖 19→**20**、echo 文案 68→**71** 行，**无需新豁免**），`gates.sh` 纯加法 22 增／0 删、`bash -n` `RC=0`；门禁三遍整份逐字节相同 8670 B／md5 `770bad825770e5faf08525dcbfb9ef58`（a／b 在代码＋README 落完、c 在本文件与执行记录改完，c 与 a 的 `diff` 为空；与上一批本体 8210 B／`da90f821…` 的差值全部由新节三行解释：剥掉那三行恰好回到 8210 B，md5 之差只剩两行等长数字——`undefined-name` 213→214 个文件、覆盖门那一行），`GATES_RC=1` 的两枚红仍是登录线在途 `af_api.py:984/:1005`，tally／baseline 未动。**两处不照办的偏差**：① 报告建议 README 记录"版本、来源仓库、构建命令**与 SHA256**"，本批那份**只有前三项**、且判据 D 把摘要写进文档就判红（第三份副本会过期，与 §二之九十六 同一纪律）——偏差已在执行记录 §二之九十八 §三显式登记请追认；② 报告建议"最好改私有 devpi／制品库＋锁文件"＝那份裁定的 **B／C 两档，当时已被驳回** ⇒ 不重递、不落地。报告另点名「wheel 是 `py3-none-any`、运行时未在 3.11／3.14 两侧都验过」——这一格与第 22 条的第 2 问（必跑的真仿真作业／解释器对齐）**同题**，随 #99 同批递，不在本批自决。等裁期间本文件不写"wheel 来源已可审计"以外的承诺，也不动任何一处引用面的文件名。
24. **第六轮审计 ARCH-05「信任边界」：边界第一次变成可 diff 的产物＋新门五判据默认拒绝；报告点名的两条绕过已在 DCD 申请里，另两处口径按现读钉回**（执行记录 §二之九十九）。**没有改过任何一处鉴权装配**——`af_api.py`／`af_auth.py` 是并发登录线的在途文件（现读两枚在工作树都带 `M`），本批零改动。**可核对的读数**：报告说「这些信息分散在 60 多个路由装饰器里」，AST 现读 `af_api.py` 是 **87 枚**（41 get／41 post／4 delete／1 patch，`(method,path)` 无重复对），而运行期 `build_app(store_root=<临时目录>)` 给 **86 条**第一方 `APIRoute`＋4 条内置＝**90 条路由对象**；87−86＝那枚**条件注册**的 SPA 兜底 `af_api.py:1409 @app.get("/{full_path:path}")`（挂在 `if dist is not None or requested_mimo:` 下，本门装配不带 `ui_dir` ⇒ 不进表；这一格明写成 §一 射程边界①而不是静默）。三档 scope 与报告逐条对上（read 30／write 39／live 3）。**非鉴权档第一方端点 14 条**（`anon` 10＋`optional` 2＋`bearer-in-handler` 2），报告按它的清单给 15，差的那一格是分类口径不是漏项。**公开读面只有两条 GET**：`GET /api/health` 与 `GET /api/auth/has-admin`；报告与 README §4.1 都写着「`/api/health`、`/api/metrics` 一类 `scope=None` 的门」，而 `/api/metrics` 现读是 `requires(read)` ⇒ `scope:read`，**那半句不成立**，本批按现读改掉 README 那一格，并把"逐条以清单为准、README 只写部署前提"写进同一节（防第三份副本再漂）。**报告这条锚点按现读成立**（与 ARCH-02／03 那批过期读数不同）：`POST /api/auth/register` 装饰器在 `:986`、处理器 `987-1009`，正是它写的 `af_api.py:986-1009`，「谁最先到达这个端口谁就成为管理员」的形状也成立；两条绕过（BUG-01 login 旁路／BUG-02 逃生舱放大）连同这一格**已在 DCD 申请里**（`20261010-AF-第六轮审计严重项匿名换owner令牌并穿透F3-决策申请`，见执行记录 §二之九十三 归属表）⇒ 不重递、不自裁。**复测露出、报告没点名的一族**：四条匿名 POST——`/api/build`、`/api/bind`、`/api/sim`、`/api/spec/compile`；它们身上那枚 `dependencies=[Depends(_readonly_guard)]` **不是鉴权**（判的是单写者租约：只读降级实例拒写），所以运行期读作 `anon`，其中 `POST /api/sim` 会随 store 真落 `_telemetry`，是四条里唯一真有写入的一格 ⇒ 清单刻意把它露在 §三 并写明"安全口径归 DCD"。另一格同样没被点名：`af_conflict_runtime.install_api` 那份 `_ROUTES` 表与报告点名的 `af_runtime_plugins.py:329` 一样裸挂载（不带 `dependencies=`，且整块包在 `except Exception: pass` 里 ⇒ 挂载失败也不响），一并量进射程。**落码**：新建 `docs/信任边界清单.md`（162 行；自动段由 `--write` 从运行期路由表生成，§三 非鉴权面登记 14 行／§四 未挂载安装器登记 2 行／§五 复测对撞 6 行）＋`scripts/check_trust_boundary.py`（477 行／24756 B，纯 stdlib＋导入 `build_app`；五判据各自可红：**A** 清单漂移（真源是运行期路由表，不是 `grep -c` 的装饰器条数）／**B** 默认拒绝（未登记即红）／**C** 登记不可核对（处理器名与现读不符、依据不是仓内真实文件、理由为空或占位词）／**D** 只减不增（过期登记不删即红）／**E** 安装器接线（未登记的挂端点机制即红；断言只认「未挂载」／「已挂载」两形状；判调用读 AST 的 `Call` 节点，注释里出现名字不算——本批真修过这一格）；八种射程塌法一律 `exit 2`）。`gates.sh` 纯加法 **26 增／0 删**（`bash -n` `RC=0`）。判据 `tests/unit/test_trust_boundary_gate.py` **32 腿**（`32 passed`）。**变异 15 案＋收尾**（副本树 `E:/tmp/tb_mut/tree`，结果 `E:/tmp/mutation_arch05.result.txt`）：M0 空操作 `RC=0`、MZ99 登记理由同义改写 `RC=0`（门不判散文）；MA1／MA2／MA3 各杀 A（改档位／凭空路由／删真在的路由）；MB1 杀 B；MC1／MC2／MC3 各杀 C 一条（处理器换人／依据越界／理由占位词）；MD1 杀 D；ME1／ME2／ME3 各杀 E 一条，其中 **ME3 就是这一族的真形状**——登记写「未挂载」却新加一个调用它的文件 ⇒ 报「**接线已经发生**」；MX1／MX2 落 `exit 2`；restore 后复跑 `RC=0`；`FAIL 0`。**一处偏差显式请追认**：报告建议「新端点必须**显式声明 scope**」，字面落地要给每条路由挂一枚新声明键——那要动在途的 `af_api.py`，且在依赖树之外多造一份真值（与本文件 §一 第一条口径冲突）。本批落的是**推导＋未登记即红**：档位从 `requires(X)` 闭包读（那枚闭包就是声明本身），非鉴权档必须逐条登记、登记只减不增；若 owner／DCD 仍要显式声明键，改动面在新脚本一处。**还剩的半边不归 AF**：这四条匿名 POST 该不该匿名、`/api/sim` 的写入要不要收进 `write` 档、零凭据换全权限那两格要不要收窄、"新端点必须声明 scope"要不要成为装配期硬约束——**全部从未递过**（`关键决策部/inbox` 对这四个路径名的匿名面申请现读 0 命中，两条绕过那件不覆盖它们）⇒ 与 #99 那批待裁项同批递，今天不投同日第三件 AF 件。等裁期间本文件既不写"匿名面已收窄"，也不写"按设计应当匿名"。
25. **第六轮审计 ARCH-06「进程模型与并发归属：三套互不相识的同步原语＋无 supervisor＋无优雅停机」：报告那句「`src/` 全树 `atexit`／`signal.signal` 命中数为 0」半句已过期；进程模型第一次成为可 diff 的对账资产，已经存在的两条收尾形状从巧合钉成门**（执行记录 §二之一百）。**没有改过任何一行运行时行为**：没有自建 supervisor、没有给 serve 加信号钩子、没有给任何模块加一枚锁。

    **复测四句的下场**：① 「`atexit`／`signal.signal` 命中数为 0」——`atexit.register` **0** 成立，`signal.signal` **1** 枚（`af_cli.py:631`，`SIGTERM`→`_on_sigterm`，外层函数 `watch`）**不成立**，那一半已被 §二之九十三 那批落地；② 「整个运行时没有停机钩子这个概念」**部分不成立**——概念有两半：serve 侧 `af_cli.py:1480` 的 `try` 其 `finally` 停桥，watch 侧 SIGTERM 收进同一个 `finally`（`stop.set()`→`ticker.join(…)`→`coord.release()`），真缺的是**信号钩子面**（serve 无处理器、无 `atexit`）；③ 「缺 supervisor」**成立但要分层**：全树 `subprocess.Popen` **1** 枚（`af_service.py:2716`，`start_new_session=True`）、`os.kill` **2** 枚（`:2604 stop_watch`／`:2682 start_watch`），而仓内 `af_tick_supervisor.py` 是 **tick 的内监护**（`af_live.py:448` 接线），不是进程 supervisor——两件事混在一起就会把"已经有了一半"写成"一格都没有"；④ 「没有等价物锁住『共享可变状态的归属』」**成立 ⇒ 本批落的就是这一格**（此前 12 道 `scripts/check_*.py` 里没有一道看过这三套原语）。

    **报告那两张清点表的行号按现读重钉**：进程内锁 **17 站点／10 个文件**（`af_service.py` 现在是 `:853`／`:1551`，报告写 `:851/1549`）；跨进程 **16 站点／6 个文件**，serve 租约在 `af_cli.py:1451`（报告写 `:1435`，off-by-16），且**报告漏了 `af_service.py` 自己的 3 枚**（`:239 write_gate`／`:1853 _single_writer_check`／`:2545 _lock_is_free`）；`global` 改写到的模块级名 **9 枚／6 个文件**（`af_live` 3、`af_premiere` 2，其余各 1），六个文件全中、行号漂 ±1～5，而 `af_flock._LOCAL_HELD`（`:49`）是就地 `add`／`discard`、本来不需 `global`。本批自己量出、报告没写的一条：那九枚名的改写函数**"函数体内有 `with`"整列读作「否」**，而 `af_live.py` 一个模块占 3 枚名、该模块进程内锁数为 **0**——要不要补锁属运行时语义（进下面问 3），**门判的是对账不是缺陷**，这一句写在清单 §一 口径里，防下一个人把九格「否」读成九个 bug。

    **落码四件（全是新增）**：`docs/进程模型清单.md`（160 行／15.8 KB；§一 口径五条含"`scripts/`／`tests/` 的子进程**明写排除**而不是静默"与"对账≠缺陷"；§二 自动段三张表＝生命周期站点 4 行／同步原语 33 行／共享名 9 行，各带现读计数行；§三 站点登记 4 行 × 五字段（拉起／收／收不到会怎样／依据／认领）；§四 每类上限 14 行——**多数为 0 是基线不是噪音**，任何一枚从 0 变 1 都必须先认领；§五 复测对撞表；§六 递裁三问）＋`scripts/check_process_model.py`（607 行，纯 stdlib 对整棵 `src/autoforge` 做 `ast.parse`，不导入 `autoforge`、不碰网络；五判据各自单独可红：**A** 自动段逐行对撞现读（含计数行，所以**计数下降也逼人来重生成**）／**B** 生命周期站点默认拒绝（`kind=` 要与现读一致、五字段齐、依据指得到仓内真实文件、后果不许写「待补」、认领只认 `AF`／`待裁`／`DCD`、过期登记要删）／**C** 每类上限只减不增／**D** serve 的 `uvicorn.run` 所在 `try` 其 `finally` 必须还在停桥／**E** 装 SIGTERM 的那个函数里必须有 `finally` 同时做 `stop.set()`／`ticker.join(`／`coord.release()`；九种射程塌法一律 `exit 2`，第 ⑧ 种是本门的反空集腿：**全树读不出任何一枚生命周期站点＝扫描器坏了，不是代码干净**；真源从盘上现取，门里没有第二份名单可抄；判调用只认 `ast.Call` 节点，注释里出现 `Popen` 不算——§二之九十九 修过的同一格教训）＋`tests/unit/test_process_model_gate.py`（488 行／**34 腿**：真仓 9 腿把读数本身钉死，合成树 25 腿覆盖 B 六种红法／C／A／D／E 与七种 `exit 2`，另含"整段登记被改成散文时仍逐枚点名 4 枚"这枚反退化腿；合成树的站点行号由门自己的扫描结果生成，注入只用「末尾追加」与「单行替换」两种形状，免得移动既有行号把别的登记顺带读成过期——红了也不知道红在哪）＋`gates.sh` 纯加法 **23 增／0 删**（`bash -n` `RC=0`；覆盖门自认现读「盘上 `check_*.py` **23** 个、`gates.sh` 覆盖 **22** 个、echo 文案 **77** 行」⇒ 新门不需要任何豁免）。

    **读数**：门自身 `RC=0`（`✓ 进程模型门禁干净（现读生命周期 4 枚全部在册且认领可读；同步原语 33 枚、模块级共享名 9 枚逐行对撞一致；serve 与 watch 两条收尾形状锚点都在）`）；本批判据 `34 passed in 39.41s`（首跑 `1 failed, 32 passed`，那枚失败是我把"登记整段变散文"错期望成 `exit 2`——现读语义是"每枚站点单独点名才叫红"，改成 `RC=1`＋逐枚点名，另补一枚"行形状整体改写才走 `exit 2`"，两条各管一头）；邻近四份门禁文件合跑 `106 passed, 1 skipped in 106.91s`；副本树十六案（`E:\tmp\pm_mut.py`，逐案 restore）**`总计 16 案，未达预期 0 案`**——M0 与 M99（登记行同义改写）全绿＝门不判散文，M60 追加未登记 `Popen`／M61 删登记行／M62 `kind` 不符／M63 依据指不到／M64 后果写「待补」／M65 超上限／M66 手改自动段／M67 拆掉停桥／M68 摘掉 SIGTERM／M69 收尾不放开协调锁 ⇒ **十案 `RC=1` 全被杀且规则字母对上**（M67 只出 D、M69 只出 E、M65 只出 C、M66 只出 A），M70 清单消失／M71 塞一份语法坏文件／M72 登记行形状整体改写／M73 掏空 `src/autoforge` ⇒ **四案 `RC=2` 全落射程档**；门禁 a／b／c 三遍整份逐字节相同（a／b 在四件代码＋清单落完、c 在本文与执行记录两处记账改完，`diff` a／c 为空） **9418 B／md5 `c2b574ade9286275e97945a0ff4a5ad2`**、`GATES_RC=1`（两枚红与本批无关＝登录线在途 `af_api.py:984`／`:1005`；`计数：except-pass-broad=19 | fake-ok-const=78` ⇒ 全量 97 条／上限 97 条，棘轮不溢出），与上一批 9088 B 的 **+330 B 全部由新节三行＋节前那枚空行 `echo` 解释**，其余三处是等长数字（`undefined-name` 215→**216** 文件、覆盖门 `check_*.py` 22→**23**／`gates.sh` 覆盖 21→**22**／echo 74→**77** 行），**`.gates-tally.txt`／`.gates-baseline.txt` 一字未动**。

    **一处偏差显式请追认**：报告"缺什么"给的是**统一状态归属模型／supervisor／优雅停机**三格，本批落的是它们**下面的那一层**（把"谁拉起、谁收、哪份状态被谁改写"变成可 diff 的资产，并把已存在的两条收尾路径钉成形状锚点），**三格本体一个都没建**——这不是漏做：三件事都要改常驻进程的运行时语义或部署口径。**递 DCD 三问进 #99 那批攒递**：① 进程 supervisor 归谁（compose 的 `restart:`／NAS 那条通道／仓内"serve 退出前收子进程"兜底）；② serve 要不要信号钩子（`atexit`／SIGTERM 现读 0 命中就是那一格）；③ `af_live` 那三枚 `global`（`_ticker_thread`／`_tick_supervisor`／`_tick_exit_reason`）要不要按成文约定补锁。等裁期间清单 §六 那一节既不写"进程模型已闭环"，也不写"按设计不需要 supervisor"。

26. **裁定 20261011《十三问》§3 Q4.1＋Q4.3 的窗内半边：口径第一次进机器判据（解释器门加判据 D）＋ 依赖下界成新门；"对齐成哪一档"仍然等裁**（执行记录 §二之一百一十四）。**本批没有改过任何一枚解释器版本取值**（`ci.yml` 四枚钉值仍 `3.11`、`Dockerfile.api:10`／`Dockerfile.test:13` 的 base 仍 `3.14`、`requires-python` 仍 `>=3.11`），也**没有钉任何上界**。裁定 Q4.1 裁的是「先统一口径再谈对齐」，所以落的是"口径可被机器核对"这一层，取值方向是 DCD 的问。
    - **Q4.1 的落法**：`README.md` 新增「### 解释器口径」小节（四行表：面／现读口径／真源锚点），表头明写唯一真源＝`pyproject.toml:10 requires-python`；`scripts/check_ci_interpreter.py` 从三规则加到四规则——**新判据 D 逐格对撞那张表**：① 声称值等于现读真源，② 锚点 `路径:行号` 那一行在盘上真在，③ **那一行也含着声称的那个值**（比 C 的"行存在"更严：行在而值不对＝文档与仓脱钩），④ 表只能加长不能缩（新增真源面不写进表即红）；`exit 2` 射程形状 5→6。`INTERPRETER_DRIFT` 两格理由改为引用裁定 §3 Q4.1／Q4.2／Q4.3 ＋ `pyproject.toml:84`。README 快捷启动那句「需要 Python 3.14+」按现读改掉——那是**镜像口径**被抄成了项目门槛，真实形状是「内核＋内置 FakeHA 按包声明 `>=3.11` 即可，要最新那版真 vhass 才需要 3.14」。
    **我自己造成的一格漂移（如实记）**：本批往 `pyproject.toml` 加的依赖注释把 `addopts` 从 `:78` 顶到了 `:84`，而 `INTERPRETER_DRIFT` 的理由正引用着 `:78` ⇒ 引用在那一刻是假的。改法不是"删注释让行号回去"，是改成 `:84` 并**让门去核**（D 核锚点行含值、C 核锚点在盘上真在）。教训：改带注释的行段前先 grep 有没有别的锚点在引用它之后的行。
    - **Q4.3 的落法**：新门 `scripts/check_sim_dep_floor.py`（纯 stdlib，`tomllib`＋正则）三规则各自可红——**A** 每一枚依赖带版本约束（核心＋全部 extra，**无豁免档**）／**B** `SIM_DEP_FLOORS` 的在册下界在该包出现的**每一枚 extra** 逐字符同值，且在册项不许静默蒸发（从 extra 里删掉即红）／**C** 依据非空、含「裁定」二字、至少一个盘上真实存在的锚点。`sim`／`dev` 两枚 `pytest-homeassistant-custom-component` 从裸名钉成 `>=0.13.109`；**同包两枚手抄**正是 B 要求逐面同值的原因。取值依据是 PyPI 现读（第 22 条那条：578 枚 wheel，`>=3.11` 档最新 `0.13.109`、`>=3.14` 才是 `0.13.371`）⇒ 这个下界**不改动任何一面的解析结果**（CI 面本来就只装得到它，镜像面继续拿最新），它只禁止"某次重建回溯到更老的不兼容版本"这一族。
    - **读数**：两门各 `RC=0`（解释器门自报「6 个作业、4 枚钉值全为 `3.11` 且被 `>=3.11` 允许；镜像两面 base `3.14`，与钉值不一致的 2 格已逐条核过登记理由的两个锚点；README 口径表 4 行逐格对撞真源同值、锚点行也含着声称值」；下界门自报「18 条依赖——核心 2 条、extra 面 `{'api': 2, 'dev': 10, 'ha': 1, 'homesdk': 1, 'mqtt': 1, 'sim': 1}`——无一条裸名；`pytest-homeassistant-custom-component` 现读 `[('dev', '>=0.13.109'), ('sim', '>=0.13.109')]`」）；本批两份判据 `44 passed`，相邻两份（覆盖门／wheel 门——本批动了 `gates.sh` 与 `README.md`，怕连带红）`64 passed`；`bash -n gates.sh` `RC=0`；副本树六案（README 一格改抄 3.13／口径表删一行／ci.yml 一枚钉值改 3.12／`sim` 下界单独降低／在册包从 `dev` 删除／依赖裸名）**全部 KILLED、串扰 0、存活 0**；门禁两遍整份逐字节相同 **10341 B／md5 `8a9fd3ec434a64926809c3882d55ef56`**、`GATES_RC=1` 的唯一红源仍是登录线在途两枚 `fake-ok-const`（`af_api.py:984`／`:1005`）。覆盖门自认现读「盘上 `check_*.py` **25** 个、`gates.sh` 覆盖 **24** 个、工作流覆盖 1、独立作业豁免 1 格」⇒ **新门不需要任何豁免**；`.gates-tally.txt`／`.gates-baseline.txt` 一字未动。
    - **⛔ 不在 AF 名下的三问**：CI 3.11 与镜像 3.14 谁向谁对齐（Q4.1 后半）、真仿真要不要一条**必跑**链（Q4.2，依赖前一问）、`pyproject` 要不要加依赖上界。等裁期间本文件不写"CI 与交付环境一致"，README 那张口径表也只报现读、不报意图。

27. **裁定 20261011 §3 Q3 乙的门禁半边：`_revoked` 从「有界缓存」基线挪进独立的「持久化单调集」档（判据 F）；这一档不声称有界，只声称「无界＋持久化＋坏档一律拒绝」**（执行记录 §二之一百一十五）。**本批一行产品代码都没改**——`af_auth.py`／`af_api.py` 是并发登录线在途文件（禁碰），落的全在注册表面与门禁面：`src/autoforge/af_bounded_caches.py` 加第三张表、`scripts/check_bounded_caches.py` 加判据 F、`tests/unit/test_bounded_caches_gate.py` 加 11 条腿。**甲档已被裁定驳回**（给撤销表加淘汰＝那个 jti 可能重新被接受，是安全语义倒退），**丙档（当前条数读数 + `/api/metrics` 那一半）落点在在途文件**，排在窗口之后。
    - **为什么单开一档**：`_revoked` 无界、持久化、按设计只能单调增长。把它留在"有界缓存"的基线名单里，等于**门禁在替一个不存在的性质盖章**——下一个人会以为可以给它加淘汰。所以这一档给的三条腿不是 TTL 与上限，而是**落盘入口** `_persist_revoked`／**重启恢复入口** `_load_revoked_file`／**坏档 fail-closed 标志位** `_revoked_poisoned`，全部由门禁回到 `af_auth.py` 源码按名字核对（名字读不到即红）。表里**只写文件锚点、不写 `路径:行号`**：这一档的判定手段是"那个名字在这个模块里出现"，行号对结论没有贡献，还会随别的批次加注释而漂（本仓真踩过）。
    - **反蒸发走的是结构，不是一条新规则**：整张表被删掉 ⇒ `_revoked` 立刻变成"未登记的增长容器" ⇒ **既有判据 C 把它判红**。所以本批**没有**加"这张表不许为空"这种只防一种塌法的规则，也**没有**改 `read_registry` 的三元组返回契约（8 处测试按位置解包它，动契约是拿回归换便利）。
    - **F 不遮 E**（这条是本档的形状边界）：登记回答的是"这份数据有没有界"，回答不了"这份数据有没有人读"。判据 F 因此**不参与**死写豁免——合成树里有一条腿专门删掉那份撤销集的读取点，仍然期望「死写容器」红。
    - **判据 F 五条各自可红**：① 任一字段缺（`module`／`attr`／`persist`／`reload`／`poison`／`bound`／`why`）；② 那三条腿的名字在对应模块里读不出来；③ `bound` 不含「裁定」二字或不含 8 位日期编号（依据必须是盘上真在的裁定，不许散文）；④ 同一枚键**双重登记**（既在本档又在 `BOUNDED_CACHES`／`FIXED_KEY_CACHES`／基线）；⑤ **过期登记**（本档挂着、模块里那枚容器已经没有了）。射程塌（表整体 `ImportError`／语法错）走 `exit 2`，不判成红。
    - **读数**：门 `RC=0` 自报「注册表 3 项双腿齐全且测试 id 被收集；固定键 3 项带理由；**持久化单调集 1 项三条腿齐全且依据指得到裁定**；扫到增长容器 **129** 个，其中**基线冻结 113 个**、就地豁免标记 16 处；死写容器 0 个（判据 E 按名字在全仓数读取点，4111 个名字被读到过）」——**基线 114 → 113 就是这一枚键的搬家**，扫到数 129 不变（容器没增没减，只是换了档）。本批判据 `45 passed`、邻近合跑 `35 passed`、覆盖门 `RC=0`；`gates.sh` 两遍**本体逐字节相同 10406 B／md5 `889eb4c97ecc0a72455b8313596a11ee`**，唯一红源仍是登录线在途 `af_api.py:984`／`:1005` 那两枚 `fake-ok-const`；副本树八案 **M0–M7 全部 KILLED、串扰 0**（M0 是"什么都不改"档）。
    - **本批自己的两处自证缺陷（都已修，如实记）**：① M3 变异首跑**存活**——锚点用了裸名 `MONOTONIC_PERSISTENT_SETS`，`replace(..., 1)` 命中的是 docstring 里那次出现，赋值行纹丝不动，门照样读"1 项"。改成整行锚点 `MONOTONIC_PERSISTENT_SETS: list[dict[str, str]] = [` ⇒ KILLED，读数「持久化单调集 0 项」＋`RC=1`。教训：**变异锚点必须取全仓唯一可匹配的那一行**，裸名在带散文引用的表上是第二枚 occurrences。② 用 `python drive_mut.py | tail -30` 跑驱动时那条命令退出码 0 属于 `tail`，而驱动自己印了「串扰/存活问题：1」——这是那条已知陷阱（要取 RC 就不许把驱动接进管道）的第二回，改成 `> file 2>&1; echo driver_rc=$?`。

---

## 十九、变更记录

| 日期 | 版本基准 | 动作 |
|---|---|---|
| 2026-10-09 | HEAD `e5b3fd5` | 按现状整体重写：新增三面/两态拓扑、写侧四档 + `dry_run` 零写入清单、真机三档、band 三档、冲突三档与 fail-closed 站点表、证据五档与双轨、测试通道、前端三棵树与 `/mimo` 同源、登录正规化与令牌面、写闸与租约、出向唯一生产者、持久化拒写族、门禁体系、部署现状、HEAD 现读残余；删除明文 MCP 令牌；订正 12 条旧断言（§〇 表） |
| 2026-10-09 | 工作区混合态（未提交，含并发批次的鉴权改动） | 现场回灌五件：`POST /api/watch/start` 假绿已修（sidecar 身份必须等于本次 IR，失败分三档）+ 档位如实命名 `tier`/`real_device`；**HTTP/用户视角到今天没有常驻真机通道**（申请 `20261009-AF-用户视角到真机的常驻通道`）；`/api/automations` 卡片改按 automation 级取数、`trial` 读不出就给 `null`、启停写侧走 `store.resave_raw`；订正 `forge watch` 没有 `--live`/`--vhass` 两枚旗子；记入 NAS `AUTOFORGE_LIVE_ENABLED=1` 与仓内缺省 0 的分歧；记入 `requires_confirm` 无运行期消费者、`canary` 有；重钉 12 处行号锚点 |
| 2026-10-09 | 同上，保真复核 | 逐项对撞文档清单与代码注册表：`TOOLS=31`、`CHECKS=40`、含 methods 路由 `=90`、CLI 命令 `=18` 四项全等；安全闸表由 42 名收成正好 40 键（剔掉非注册表的 `IR_SCHEMA`、`L2_NEEDS_CANARY`）；时区键改为 `HOMESDK_TZ`（规范）/`AF_TZ`（别名）并给全序；新增残余 B.8（`L2_NEEDS_CANARY` 发诊断却未注册） |
| 2026-10-09 | HEAD `e5b3fd5` + 收件箱批次（未提交态） | 计划 §七 卡1 落地并同步本文：§二 适配器表加 `inbox`、出向行改成"事件与收件箱投递都只从桥出去"；§四 运行链把分岔点如实写成 `build_runtime(dry_run=…)` 这一个口（HA/HTTP/Inbox 拿同一枚旗子，`af_runtime.py:291-296`）；§六 补收件箱在预演档的口径（零上线字节 + 缺桥 = 缺通道 ⇒ `ADM_ERR_AUTH_REQUIRED`）；§十四 主题表加 `butler/inbox/speak\|notify\|tv` 行、锚点按现读重钉（`observe_terminal` 调用点 `:673/:676`、`_envelope :622`、`inbox_publish :405`、`FORBIDDEN_SUBSCRIPTIONS :85`），并新增三条机制说明（收件箱不经过 `_envelope`、前缀只有一枚、发布不许静默＝`rc` 判据）；§十六 门禁表把 `check_mqtt_writers.py` 改成四条判据、加两份收件箱判据文件；§十八 残余加 B.9（`mosquitto_sub` 半边 / §1.3 限速 / 编译期长度）与 B.10（`rc` 单码待 DCD） |
| 2026-10-09 | HEAD `2d92bb1` + 联动入向批次（未提交态） | 计划 §七 卡3 落地并同步本文：§二 模块地图把"出向"行改成"联动：出向与入向"并加 `af_linkage_feed.py`，顶层模块/包计数改成现读口径（71 个顶层 `.py`（不含 `__init__.py`）+ 4 个包目录 / 全 102 个 `.py`，旧写的"62 + 5"两处都不对）；§十四 主题表加 `ma/presence`/`ma/device-health` 两行（入向 only）并新增四条机制说明（主题名单真源、收/消费跨线程接缝、落盘形状与载荷裁剪分两半、水位线毫秒优先那个静默失效形状、拒收只按契约）；§十五 持久化表加 `linkage_events` 行并写明它与 `insight_proposals` 的**淘汰策略相反**（提案满则拒收、快照满则裁最旧）；§十六 门禁表加 `check_mqtt_subscriptions.py` 与两份新判据文件；§十八 A 表四条计数棘轮红改判"本批已收"并留两树逐位对撞、AST 棘轮那条明确点名不碰（要改并发会话正躺着的 `af_api.py`），B 表加 11-13（对端载荷未对撞 / 事件载荷读不进节点 / `serve` 不抽队列） |
| 2026-10-09 | HEAD `925f56e` + 平键批次（未提交态） | 裁定 20261009 §四 甲/直译落地：`af_instance.py` 加 `_trigger_flat_keys`（`:482-507`）与名单真源 `TRIGGER_FLAT_KEYS`（`:479`），`spawn` 在 `:264` 把 `trigger_subject`/`trigger_kind`/`trigger_entity_id` 三枚平键并进 `context`；`af_state.py:157-182` 的解析器与 `_trigger_repr` 一字未动（甲的边界）。§十八 B 表第 12 条按现状改写（"按设备"半边通、"按成员数组"仍堵），命名空间词汇同步进 `IR_AND_RUNTIME.md:77`。判据两树：单测 12 绿 + 联动 17 绿、相关七腿 983 passed、变异两枚分别杀 3 条与 7 条（后者报 `KeyError: 'trigger_entity_id'`） |
| 2026-10-09 | HEAD `bb800bc` | 裁定 20261009 第二份（`20261009-AF两件ok字面量与平键登记-裁定`）同步：三枚触发平键**已由 DCD 登记进契约表 §1.6**（跨仓公开词汇），主标识口径裁**甲A**——DSL 的 `trigger_subject`＝`entity_id` 优先、盘上 `LinkageRecord.subject`＝`device_id` 优先（展示身份），**两格并存、各有用途**，`af_mqtt_bridge.py:906` 不动；平键批次落码为 `bb800bc`，`_trigger_flat_keys` 锚点从 `:482-508` 重钉 `:482-507`（同批清掉一枚赋值后不读的死变量，行为不变，DCD 回执里引的是旧锚点）。§三 那一格按新判例「编译期看见 ≠ 运行期兑现」列成**开常驻通道的硬前置**（`requires_confirm` 运行期消费者，现读 `af_executor.py` 0 命中）。AST 两枚 `ok` 裁"写端点派生 / 纯查询删键"，但落码窗口钉在登录线提交后 ⇒ 本轮不碰 `af_api.py`，那两条红照旧在 |
| 2026-10-09 | HEAD `0300150` + 确认闸批次（未提交态） | 裁定 20261009 §三 硬前置落地：`requires_confirm` 的运行期消费者——`af_executor.py:648-659` 在 `_do` 入口判旗（未授权一次都不下发）、`:864-891` 挂成人工确认会话（复用 `pending_asks` → `/api/asks*` / sidecar / inbox 这张已有脸，`AskSession` 不加字段、`af_api.py` 不动）、`:211-241` 唤醒（yes ⇒ `confirm_granted` 一次性把手 + 重进同一节点执行一次；no / `on_timeout` ⇒ fail-closed）。新增两枚具名审计 `confirm_granted`/`confirm_denied`（`af_audit.py:51-52`、`ALL_EVENT_TYPES :75-76`），确认会话沿用本实例最近一次 ask 的 room（`:845-849`）。§七 两段随本批重钉锚点（canary 的 dry 跳过从 `:546-555` 挪到 `:646` + `:663-670`），并写清这枚闸与 band 正交、实际只在 `auto` 带上真拦。口径差一处：dry 适配器不挂起、只留 `confirm_skipped_dry_run` 痕，与先前落码档「预演档不豁免」不同，理由与代价见执行记录 §二之八十一，已交 DCD 追认 |
| 2026-10-09 | HEAD `0300150` + 恢复重挂批次（未提交态，叠在确认闸批次之上） | §十八 残余第 14 条收口：崩溃恢复后挂起的 `ask` / 人工确认会话重挂回 `pending_asks`（`af_executor.py:507-565` 新增 `reseed_sessions`，`af_runtime.py:203` 调用）。同批查出真凶：`pending_confirm` 原先写在 `instances.suspend()` **之后**，而落盘恰好发生在那次状态转换里 ⇒ 盘上记录永远缺这枚标记（现在 `:881-884`、`:845-852` 都改成先写再挂）。新增具名审计 `instance_session_lost`（`af_audit.py:35-37`、注册 `:66`，枚举现读 **22 枚**）。判据 `tests/unit/test_restore_pending_sessions.py` 11 条；变异六枚分别杀 10 / 1 / 1 / 1 / 4 / 11 条红（不重挂、见挂起就重挂、问句用落盘旧动作名、静默跳过、标记写在落盘之后、恢复即放行）。确认闸那批的锚点因本批在执行器前部插入而整体后移，两份架构文档 + 契约 + 执行记录已按现读重钉 |
| 2026-10-10 | HEAD `8efc134` + 目录收口批次（未提交态） | 收 §十八 B.8：`L2_NEEDS_CANARY` 登记进 `CHECKS`（`af_scanner.py:54`）与 `CODE_HINT`（`:107`），检查面读数从 40 项翻成 **41 项**（本文 §〇/§二/§三 三处与知识文档四处同步）；新增通用判据 `tests/unit/test_diagnostic_code_catalog.py`（8 条腿：字面量发出的码必在两本目录、hint 非空、反向幽灵键棘轮、L2 两分支各判一次、目录枚数现读）；本文 §五 `--dry-live` 那组锚点因两本目录各插一行整体 +3（`:1248/1275/1285-1286`），残余 B.8 改写为"已收口"并保留编号 |
| 2026-10-10 | HEAD `086cf09` + 拒绝出口诊断批次（未提交态） | 落裁定 20261009（第二份）§二 2.2：新增第 42 项 `CONFIRM_WITHOUT_DENY_PATH`（**WARNING 级、不进 `ScanResult.ok` 的判红**），检查方法 `af_scanner.py:503-521`、调用点 `:364`、`CHECKS :55`、`CODE_HINT :97-98`；§六 的 dry-live 锚点随本批 +24（`:1272/1299/1309-1310`）；§七 那格补两句——被拒后落 `done` 是 Q2 裁的甲、诊断只把这条走向挪到编译期可见，以及 dry 适配器不挂起 + 留痕的口径**已由 §一 追认**（痕是关键半边，撤掉＝预演不诚实）。顺带发现并修掉一处上一批漏翻的副本读数：本文 §〇 分工行还写着"安全闸 40 项"（那时已经是 41）。新增判据 `tests/unit/test_confirm_exit_diagnostic.py`（11 条腿）、`test_diagnostic_code_catalog.py` 目录枚数腿 41→42 |
| 2026-10-10 | HEAD `033866f` + 收件箱长度预拒批次（未提交态） | 收 §十八 残余第 9 条的后半格：新增诊断码第 **43** 项 `INBOX_PARAM_TOO_LONG`（**ERROR 级、拦发布**）。上限一个数字都不写在 AF：`af_adapters/inbox.py` 直接 `from homesdk.presence import INBOX_MAX_TEXT/INBOX_MAX_TITLE/INBOX_MAX_BODY`，`INBOX_LEN_LIMITS`（`:57`）格子里放的就是这三枚常量本身，`inbox_overlong_fields()`（`:74`）是唯一判定入口；扫描器 `_check_inbox_payload_len`（`af_scanner.py:709-728`，字面量 `:720`、调用点 `:379`）零整数字面量。**形状返工过一次并如实记账**：初版把常量名存进表、运行期 `getattr` 现读，被 `check_mqtt_writers.py` D 判据判红（桥外按名派发机制层成员），而那条红正是 #86/#87 要防的漂移形状——终版改成直接 import，库侧改名从运行期 `AttributeError` 提前成 import 期 `ImportError`。同批删掉 `inbox.py` 文档串里手抄的 `text≤500`/`title≤80`/`body≤500`，本文 §五 收件箱那行改成常量名 + 第三件事（编译期读一次去预拒）。锚点随本批重钉（方法体之后一律 +27）：`CHECKS :38`→`:39`、`__post_init__ :174-176`→`:179-181`、L2 发出点 `:423`→`:429`、`CONFIRM_WITHOUT_DENY_PATH` 方法 `:503-521`→`:509-527`、变量传码 `:1120-1131`→`:1147-1158`、`LIVE_*` 常量 `:1260-1263`→`:1287-1290`、dry-live 组 `:1272/1299/1309-1310`→`:1299/1326/1336-1337`。判据 `tests/unit/test_inbox_param_length_diagnostic.py` 30 条腿（含 AST 对撞库源码名单+现读值、边界成对、两端同判、本文件零 `getattr` 派发），目录枚数腿 42→43。**剩余只有 §1.3 护栏 3**（按 source 限速），执行人与 N 值已交 DCD |
| 2026-10-10 | HEAD `bf8c563` + 测试区删除面批次（代码面已提交 `125c646`，未推） | 收 §十八 残余 B.3：`af_test.clear()` 的无守卫 `rmtree` + `ignore_errors` 假绿。修前探针在临时树里量出两格真缺陷（报称 `cleared/ok: True` 却仍有残留；`test_root` 递成正式根的父目录时整条归档线被删掉）。修法：删前 `assert_test_area_deletable(test_root, protected_root)`（两枚具名码，对照量由调用方现递 `store.root`、两侧 `resolve()` 后比），`protected_root` 做成**必填关键字参数**（少递 ⇒ `TypeError`，绝不默默删），MCP 工具 schema 保持零参数 ⇒ 删除目标 Agent 递不进，守卫拒判回 `ok: False` + 具名 `code`；删后残留按盘上 `rglob` **数出来**（量在 `_ensure_dirs()` 之前），失败现场按 `onerror` 收进 `errors`（`DELETE_ERRORS_MAX = 20` 封顶 + 如实 `errors_total`）。判据 `tests/unit/test_test_area_delete_guard.py` 17 条腿（两树全绿：工作区 11.34s、副本树 6.19s），14 枚变异逐枚点名杀腿（MU1 就是 HEAD 形状复现）。门禁两遍 byte-identical、`GATES_RC=1`：本批让 `fake-ok-const` 79→78，随之 `TestChannel.clear` 那条基线指纹**已不再命中**，按门禁自身语义（过期条目也判红）删除 ⇒ 存量 97→96、全量 99→98、上限 97 **未动**、未新增豁免；剩余那 2 枚红仍归登录线（#79）。§十 表格与两条边界、§十八 A 的 AST 棘轮格、B.3、B.9/B.11 的护栏 3 口径（裁定 20261010 §四）按现读重钉 |
| 2026-10-10 | HEAD `3b00224` + 保真规范化批次（代码面已提交 `7800d5c`，未推） | 收 §十八 残余 B.2：`af_fidelity._canon` 的裸 `json.dumps` 换掉。两格缺陷都在 HEAD 上探针实测出来（`%TEMP%/probe_b2_head.out`）：① 抛穿——date/set 值抛 `TypeError`、自引用抛 `ValueError: Circular reference detected`、混合键型抛 `TypeError: '<' not supported…`，三种都绕过遍历闸门直接砸穿 `verify_roundtrip`；② 塌陷（假绿）——`_canon(("x","y"))==_canon(["x","y"])`、`_canon({1:1})==_canon({"1":1})`、`_canon(nan)='NaN'`，端到端 `fidelity_equal` 对这两格都返回 `True`。修法：`_canon`（`:54-93`）改带类型标记的递归编码（JSON 词汇表、`bool` 先于 `int`、tuple/list 分打 `t`/`l`、非 str 键与非有限浮点拒比、深度走 `check_param_depth` 那份单一真值源），表外形状抛 `FidelityNotComparable`（`:35`、`:38-51`，带 `code`/`where`/`detail`、仍继承 `ValueError`），`verify_roundtrip`（`:294-299`）只捕三枚具名错并落成 `FidelityReport.not_comparable`（`:261-270`）一档、`ok` 与 `projection_fidelity` 同时 False，`fidelity_equal` 遇同一格坚持抛而不返回 `False`。判据 `tests/unit/test_fidelity_canon_vocabulary.py` 21 条腿；副本树变异 8 枚：M1（HEAD 形状复现）杀 12、M2 杀 2、M3/M4/M6 各杀 1、M7 杀 3、M8 杀 8，**M5 是等价变异**（`ok` 里 `and not not_comparable` 被 `projection_fidelity=False` 蕴含，摘掉仍 21 绿）⇒ 如实登记为不变式而非杀腿。归属核对：纯 HEAD 副本树整切片 `3 failed, 3094 passed`，同一棵树只叠本批两份文件 ⇒ `3 failed（同名三条）, 3115 passed`；工作区那 10 枚红全在鉴权线（两条由在途 `af_api.py`/`af_auth.py` 引起、两条 HEAD 上同切片也红、其余六枚随切片顺序漂），与本批无关。门禁 `GATES_RC=1` 读数与本批前逐字相同（`新增/未获批 2 条`、`存量 96`、`过期 0`、`20|78` ⇒ 全量 98／上限 97），本批未新增违规也未产生过期指纹 |
| 2026-10-10 | HEAD `a734a02` + 裁定 20261010（测试区删除面）回执批次（纯文档面） | 三格全裁**甲**、AF 侧**零代码改动**：§二 维持形状守卫（不新增"只准删 `{root}/test` 前缀"的白名单；乙列候选但要先由 DB 给一句权威树的现读路径，否则前缀就是 AF 自己抄的第二份部署事实；丙要先给测试区落一套归属标记，跨 `af_test`/`af_store`）；§三 维持 scope `write` + 无面板（采用的依据与 AF 现读一致：两棵第一方 UI 树对 `af_test` 0 命中、路由表 `api/test` 0 条）；§四 判例入册＝**不可逆操作的守卫，对照量必须由调用方现递，被守卫的模块不许自带第二份真值**。本文 §十 那条"仍待裁的政策半边"翻成三条并存的口径（射程边界／权限与面板／判例原文），同级目录照删从此是**裁定的已知边界**而不是待办；§十八 B.3 的残余措辞同步收尾。知识文档 §九 那条 ⚠️ 与 §十六 第 5 条同批改口，执行记录加 §二之八十八。**另按 §四 那条判例做了一次同源自查**，查出一格新残余并登记成 §十八 B.14（`af_store.py` 的 overwrite 备份 `__ovbak__` 用 5 枚 `ignore_errors=True` 的 `rmtree` 收尾 ⇒ 删失败零读数、残留对 `history()`/`names()` 全隐形、判据 0 条点名；无越界风险，形状只有"静默"）⇒ 本批只登记不修，任务 #92 |
| 2026-10-10 | HEAD `d6802bc` + §十八 B.14 收口批次（代码面已提交 `3ace9b6`，未推） | overwrite 导入的备份回收补上三枚读数面：五枚 `shutil.rmtree(..., ignore_errors=True)`（修前 `:616`/`:627`/`:640`/`:653`/`:658`）全撤，删除收成一个入口 `_purge_tree`（`af_store.py:73-95`，rmtree 站点唯一在 `:86`；失败按 `onerror` 逐站收集，残留数按盘上 `rglob` 现数）。写新成功后回收失败 ⇒ 报告新格 `residual_backups`（`:806` 初始化、`:867-869` 收集；MCP 整份透传、CLI `af_cli.py:956-959` echo）＋ WARNING `:749`；回滚成功后壳没回收 ⇒ WARNING `:732`；让位前预清理失败 ⇒ 碰正式区之前抛 `BackupNotReclaimed`（`:58`，抛出点 `:673`），不再让 rename 抛那柄伪装成撞车的 `FileExistsError [WinError 183]`。判据 `tests/unit/test_overwrite_backup_reclaim.py` 11 条腿（注入点在 `os.unlink`/`os.rmdir`＝rmtree 遍历内部；日志面 setup 先发哨兵自证）＋副本树变异 9 枚全杀、含"什么都不改"档。上一批登记措辞就地更正两处（清理触发点不是"没有"而是只有修前 `:615-616` 那一枚；"备份目录越攒越多"不成立，名字按归档固定、同名复用）。剩半边：HTTP `_svc`（`af_api.py:777-784`）不映射这枚新错、到 API 仍 500（与修前同形），接 409 要动在途文件 ⇒ 随 #79。门禁 `GATES_RC=1`／新增未获批 2 条／基线 96／7877 字节，与前三批逐字节相同 |
| 2026-10-10 | HEAD `fa71f86` + §十八 B.4 收口批次（代码面已提交 `d8f2234`，未推） | 收 §十八 残余 B.4：试演档的守卫失明不再静默。原句"这段时间的降级通知半边由 `_notify_guard_blind` 承担"经探针实测**不成立**（observe 档 introspect/request 两站的调用点站在 `if observe:` 回落之后，一次都没走到），只有 band 那站真会通知。修法：通知半边按**拦没拦**分栏，`blocked` 由三枚调用点现给（`:300`/`:318-320`/`:338`），被调方不自己读模式；仓内常驻指示两档都落（detail 新键 `blocked`），对端 retained `af/status` 只在真拒发时发，试演档落一条 WARNING（`:496-501`）；band 站不看模式＝裁定 20261008 §四 的既有形状，本批只加 CONTROL 腿与两枚变异钉住它。§八 那张 fail-closed 站点表加"通知半边"一列并把六行位置全部按现读重钉（`:294-304`/`:306-309`/`:310-321`/`:331-343`）。判据 24→**32 条腿**（+8，含两枚 AST 结构腿）；副本树变异 M0 全绿、M1–M9 全部被杀；门禁两遍逐字节相同 7877 字节、基线读数未动。剩的半边（试演期失明是否对端可见）是跨仓 `degraded` 词汇，AF 不自加、留 DCD。执行记录 §二之九十 |
| 2026-10-10 | HEAD `62a859f` + watch 生命周期批次（第六轮审计 BUG-04／05／06／07／14，未提交态） | 五格落码：清理不再 `unlink` 协调锁本体（锁在 fd 上，删文件不释放锁、反而放第二个 watcher 去锁**新 inode** ⇒ 双监听零日志），改为只回收 sidecar＋PID 并回 `lock_file_kept`／`errors`；发信号前按 sidecar 的 `owner`（`{hostname}-{pid}-{uuid8}`）核验，七种读数只有 `verified` 允许 `os.kill`，等不到锁自由就回 `exit_unconfirmed` 且**刻意不升 SIGKILL**，CLI 侧 `af_cli.py:618-630` 把 SIGTERM 接回既有 `finally`；探测预算塌成一枚真源 `_WATCH_PROBE_DELAYS_S`（首探 0.1s，合计 11.9s）＋文案 f-string 读回；子进程日志句柄在父进程 `with` 关掉；桥 `stop()` 补齐 offline→摘 `on_message`→`loop_stop`／`disconnect`（抛穿不静默）、`start()` 重复上线抛 `BridgeUnavailable`——这一格**不等 0.3.3**（0.3.2 的 `mqtt.get_client()` 给的是未连接裸 client，`connect`／`loop_start` 都写明是调用方的活，对称收尾也是）。§四 新增"watch 生命周期的五条规矩"整块，§四 末与 §二 模块地图把 tick 那句"心跳自愈"改成"心跳读数"并写清未接线（§十八 第 15 条）；§〇 的 `watch.lock` 锚点与 §六 的 `start_watch`／sidecar 锚点因 2400+ 区插了五枚助手而按现读重钉（`:2569-2571`、`:2651`、`:2745-2800`、`af_cli.py:600`）。§十八 新开 15／16／17／18 四条（tick 未接线等裁、`claims()` 读不出租约即放行、`af_cli` 三处裸 `write_text`、`_seen_ids` 的"LRU"是插入序淘汰）。判据新增 30 条腿（`test_watch_lifecycle_r6_audit.py` 26 ＋ 桥文件 4），副本树十六案变异：M0 `89 passed`、M99 同义改写 `89 passed` 全绿，其余 **14 案 RC=1 全被杀**（M12 专门证明定义侧断言不是装饰）。归属两笔红都不在本批：AST 新增 2 枚＝登录线在途 `af_api.py:984/:1005`，计数棘轮 98>97 在 `git archive HEAD` 单棵树上就红（head／mine7／工作树三读同为 98），鉴权 6 条红只出现在带未提交改动的工作树（mine7 树上三份鉴权文件 `48 passed`）⇒ 未动 `.gates-tally.txt`／`.gates-baseline.txt`。落码与分派授权：`decisions/20261010-AF与DB与DPP十件-裁定.md` §五 Q4 甲；"死码／无调用方"的定性口径按同裁定 §六 Q3 归 DCD。执行记录 §二之九十三 |
| 2026-10-10 | HEAD `219dd93` + 残余三格批次（BUG-10／12／13，现读已提交 `2724c4f`，未推） | 收 §十八 15／16／17／18 里的后三条：`af_persist.claims()` 的"读不出到期"不再等于放行（`:199-207` 落 WARNING 带原始值＋`return False`，`af_runtime` 对 `False` 只跳过不删文件并记 `INSTANCE_LEASE_HELD`）；`af_cli.py:946/1058/1168` 三处导出裸写与审计未点名的第四处 `af_service.py:2727`（watch pid）全走 `atomic_write_text`，并补上覆盖全 `src` 的裸写同源判据（`test_atomic_write_sites_fixes.py:303-338`，含空理由仍判红、植桩反向自证——既有门禁 D／E 两腿的射程都不含导出命令与 pid，这一格是门的盲区而非报了没修）；`af_live._seen_ids` 换 `OrderedDict`＋命中 `move_to_end`，"LRU"从名字变成行为。同形状自查另两处：`af_bus.py:317-330` 机制本是**真 FIFO**（命中不重排、`popleft` 淘汰），行为无缺陷，只把注释里那句"LRU 槽位"改成 FIFO；`af_time.py:111` 的 docstring 那句"vendor 的 wheel 里没有该模块"经现读已过期（本机与镜像 wheel 两面都有 `homesdk/time.py`），改成"库侧没装、或装的是不含 `time` 的旧版时走这一档"——两条都是声明面与执行面对齐，不占残余格。新增 5 条判据腿，副本树九案变异：M0 与 M99b（同义改写）`134 passed, 2 skipped` 全绿，M20 杀 2、M21／M22／M23／M24／M25／M26／M27 各杀 1（M26 与 M27 打同一条去重腿的不同断言：一头是"热 id 不该被带走"，一头是"队首旧 id 必须真被丢掉"）。门禁四遍逐字节相同 7877 字节（a／b 跑在落码后，c 复跑在 `af_bus` 注释更正后、d 复跑在 `af_time` docstring 更正后）、`GATES_RC=1`，`新增/未获批 2 条（error 0 / warn 2）`、`计数：except-pass-broad=20 | fake-ok-const=78` ⇒ 本批零新增违规；早前那次 head／mine 两树 severity 对不上（`af_service.py:285` 一 ERROR 一 WARN）经复核是**副本树没带 `.gates.toml`**（`critical_globs` 的关键模块名单就在里面），补上后两树逐字同读 `98 条（error 1 / warn 97）`——记下来是因为这条差值曾被当成"本批改了严重度"。两条红仍归登录线（`af_api.py:984/:1005`）与 HEAD 上就有的棘轮溢出（98>97），未动 tally／baseline。执行记录 §二之九十四 |
| 2026-10-10 | HEAD `025616d` + ARCH-01 批次（现读已提交 `cee6a3a`，未推） | 收 §十八 第 20 条（第六轮审计 ARCH-01 里 AF 职权内的半边）：关键模块名单与基线**第一次互相对账**——`.gates.toml` 声明 `af_service.py` 的 `except Exception: pass` 是硬错误、`.gates-baseline.txt:17` 却供着 `af_service.py#except-pass-broad#health`，而没有任何一条门看过这两个集合的交集。站点改成具名留痕 `TICK_HEALTH_PROBE_SKIPPED`＋`exc_info`（`/api/health` 行为不变：探测失败照答 `ok: True`、`tick_health` 照 `None`，只是不再无声）；基线只按门禁自报的"过期基线条目 1 条"删那条指纹（86 行／83 条 ⇒ 85 行／82 条），`tally` 未动；`check_gates_coverage.py` 加判据 ⑦＋三条射程下限 `exit 2`（名单为空／基线缺文件／`.gates.toml` 解析不了），实现走库侧 `GateConfig.load_file`／`matches_glob`，AF 不抄第二份名单。判据 44 条腿（覆盖门 40＋行为面 4，含"把修前形状种回去必须被扫出"这枚反空集腿）＋副本树八案（M0／M99b 全绿；M30 杀 3、M31 杀 4、M32 杀 2、M33 杀 4、M34／M35 各杀 1）。副产物：AST 全量 98→97、error 1→0 ⇒ HEAD 上那条"98 > 登记上限 97"的计数棘轮红转绿，**不是抬上限**。报告口径更正一处（"86 条生效"实为 86 行／83 条，它自己的 66+17 分类表也对不上）。门禁六遍（a／b 落码后、c／d 文档初稿后、e／f 末轮措辞后）本体逐字节相同 7824 字节／md5 `89a4b43f53f8f07120ac97a4ea39d284`、`GATES_RC=1`（两枚红＝登录线在途 `af_api.py:984/:1005`），棘轮站现读"全量违规 97 条 / 登记上限 97 条"；整树单测 `6 failed, 3028 passed, 43 skipped`，六条与 §十八 A 表 2026-10-09 混合态追注**同名同因**、全在鉴权面，本批五份文件一条不在名单。标题前半句"冻结了 86 条已承认的缺陷"是评审口径，按裁定 §六 Q3 不自决、等裁。执行记录 §二之九十五 |
| 2026-10-10 | HEAD `0a26dd9` + ARCH-02 批次（现读已提交 `5616dc6`，未推） | 收 §十八 第 21 条（第六轮审计 ARCH-02 的复测与分派）：**版本号取值一枚没改**——审计建议"收敛到 `API_VERSION` 一处"与 `decisions/20261001-DB目标模式与AF三题-裁定.md` §G 的 A＋C 双轨裁定正面相反，按裁定驳回不照办（`0.1.0`＝机器契约、`v2.x`＝里程碑，映射表在版本计划 §〇）；落的是**真缺陷那一格**——`IR_VERSION` 字面量在 `af_ir/models.py:58` 与 `af_orchestrator.py:38` 各写一遍，改为 orchestrator 从 `.af_ir` 导入并保留模块属性（`af_closedloop/loop.py:233` 经 `load_module()` 读它），判据 +2 条腿（AST 全树扫字面量重抄，只放行真源／断言 orchestrator 仍暴露真值，防"删干净还全绿"），副本树 M0 绿／M40／M41 各杀一条，相关单测 35→71 全绿。报告三处过期读数按现读钉回：`git tag` **一枚**（非"无"）、`ci.yml` **六个作业**（非四个）、`API_VERSION` 在 `af_service.py:128`（非 `:127`）；"无 release／publish 作业"与"无 CHANGELOG"两句成立。README 另清两格副本：删掉钉死的「1300 passed / 51 skipped」回归读数（整树现读 `3028 passed / 43 skipped`）、§1.1 改双轨说明＋里程碑 `v2.5`，并把 13 处 `](…)` 死链按 `git ls-files` 重钉 11 处（文档早重排进 `reference/`／`architecture/`／`archive/`／`handoff/`），剩 2 处不猜目标（`docs/roadmap/ADM-路线图-AF.md` 两候选并存、`docs/交接卡_模板.md` 全仓读不到）。**发布面四问递 DCD**（`/api/health` 无 commit／构建键、`Dockerfile.api` 无 `ARG GIT_SHA`／`LABEL`、compose 两处 `image: autoforge-api:latest`、无 CHANGELOG、无发布作业——"线上哪一版"目前靠人回忆，且本仓已为此付过一次账：NAS 在役面上界读不出）：`inbox/20261010-AF-发布面与镜像可追溯-决策申请.md`，等裁期间本节既不写"发布面已具备"也不写"按设计不需要"。门禁两遍（代码＋README 改完／死链重钉改完）与上一批 `gates_arch01_c.txt` **三份逐字节相同**（md5 `3e32e25d7c38a24b496d2faf38e0aab8`，含 `GATES_RC=1` 标签行的整份口径；§二之九十五 记的 `89a4b43f…` 是削掉末行的"本体 md5"，两者不冲突），两枚红仍是登录线在途 `af_api.py:984/:1005`。执行记录 §二之九十六 |
| 2026-10-10 | HEAD `5616dc6` + ARCH-03 批次（现读已提交 `bfdc8b1`，未推） | 收 §十八 第 22 条（第六轮审计 ARCH-03 的复测与分派）：**没有动过任何一枚解释器版本取值**（`ci.yml` 四枚钉值仍 `3.11`、`Dockerfile.api:10`／`Dockerfile.test:13` base 仍 `3.14`、`requires-python` 仍 `>=3.11`），落的是"三面互看"的 rot 防线——新建 `scripts/check_ci_interpreter.py`（A 全部 `python-version:` 同值／B 钉值被 `requires-python` 允许／C 与镜像 base 不一致要在 `INTERPRETER_DRIFT` 显式认领、理由带"一个带钉值的真实作业＋一条盘上真在的路径"两枚可核锚点、对齐了还挂着判红、豁免只减不增；五种读不出走 `exit 2`），`gates.sh` 纯加法接入（22 增／0 删，`bash -n` RC=0）。报告四处读数按现订正：作业数 **6 条**（非"四个作业"，4 条带 Python 钉值、另 2 条 node 面）、三处行号 off-by-one（`:35/:56/:88` → `:36/:57/:90`）、"CI 低于包声明最低版本"不成立（`>=3.11` 容得下 3.11，真实形状是 CI 3.11／本机 3.13.2／两份镜像 3.14／README 3.14＋的**四方 spread**）、那条标「疑似」的 pip 回溯**可量**（PyPI 578 枚 wheel：`>=3.11`→`0.13.109`、`>=3.14`→`0.13.371`，隔 262 个发布）**但后果反转**——`pyproject.toml:78` 的 `-p no:homeassistant` 让那一版在 CI 根本不参与执行，真缺陷是 `-m vhass` 收到的 **10/3858** 条真仿真用例在 CI 只走 skip、`Dockerfile.test:11` 自陈未经实跑验证、`ci.yml` 无 docker 作业 ⇒ **真仿真从未在自动化链上跑过**。README 两处口径鲜度：vhass 栏改成「显式加载插件后才启用真 vhass」＋新增一段写明 CI 不属于表里任何一栏。判据 19 条腿（`test_ci_interpreter_gate.py`，含真仓绿腿把 `jobs=6 / pins=4 / values=["3.11"] / images={api:3.14, test:3.14}` 钉死）＋近邻三份门禁判据合跑 83 passed；副本树六案：M0／M53／M99 绿（M53 把交付 base 抄成 `3.13` 照样绿＝自证本门**不判该对齐成哪一边**），M50 杀 A、M51 杀 B、M52 杀 C 的"只减不增"。门禁三遍**本体逐字节相同**（a／b 在代码＋README 改完、c 在本文件与执行记录改完；剥掉驱动 `pass a`/`pass b`/`pass c` 标签行后均 8210 B／md5 `da90f82142101549244127f28d9902f1`；整份 8248 B 的差只在偏移 8242 那一枚标签字节，c 与 a 的 `diff` 为空），与上一批本体 7799 B／`a53d3410…` 差 411 B 全部由新增那一节解释（`diff` 三处移动：新节 3 行／覆盖门自认新脚本 `check_*.py` 19→20、`gates.sh` 覆盖 18→19、echo 文案 65→68 行、`undefined-name` 扫描 212→213 个文件），AST 面两批同读 `新增/未获批 2 条（error 0 / warn 2）`＋`全量违规 97 条 / 登记上限 97 条`，tally／baseline 未动。同时更正上一节我自己写歪的那半句：「`ci.yml` 全部作业钉 3.11」不准确——6 条作业里只有 4 条带 Python 钉值。**交付／验证口径三问不自决、与 #99 同批递**：CI 与镜像谁向谁对齐／要不要为真 vhass 增设必跑作业／仿真依赖要不要钉版本（`sim` 与 `dev` 两枚 extra 各列一次且都无约束：`:19`／`:35`）。ARCH-04 原文随手提取但**只抽查两枚锚点**（`.gitignore:64` 的 `!docker/homesdk/*.whl`、盘上 wheel 49374 B），其余未复测。执行记录 §二之九十七 |
| 2026-10-10 | HEAD `d5a222b` + ARCH-04 批次（现读已提交 `af5f1cc`，未推） | 收 §十八 第 23 条（第六轮审计 ARCH-04 的复测与分派）：**没有换过任何一枚 wheel、没有动过任何一处引用面的文件名、没有动过权威摘要**。**报告"缺什么"三格逐句对撞**：「缺校验和清单」与「没有源码可比对」两句**已被裁定接住**（`decisions/20261007-MA五件与AF一件-裁定.md` §六 Q1 裁 A＝仓内随附＋文件名钉死＋权威 sha；摘要在 `tests/unit/test_mqtt_compose_env.py:163` 与库侧 `dist/VERSIONS.txt:32` 两处，源码在 `E:\NAS\homesdk`、构建命令登记在 `VERSIONS.txt:42`）；「可复现」这一格**问对方向但答法要换**——实测那枚 wheel 的 22 个 zip 条目带 **17 个不同 `date_time`**（就是各源文件 mtime）⇒ 重烤必然得到不同 sha ⇒ 摘要只能靠登记值对撞，这条按现读写进新指路文档的「来源与可再生性」；「装不上怎么报错」用 `-I -S` 关掉 `site-packages` 实测 ⇒ `ModuleNotFoundError: No module named 'homesdk'`（`af_executor.py:95` 是模块级 import，缺包是响的不是静默），故**不另造 dev-setup 入口**。**复测挖出的真缝不在那三格里**：既有那条字节判据的 wheel 路径只从 `Dockerfile.api` 的 COPY 行取，于是 `ci.yml:25/:42/:63`、`Dockerfile.test:26-27`、compose `:56` **全不在其对账范围** ⇒ CI 那一面指到另一枚时"CI 装的与镜像装的"脱钩而 CI 照绿；M44 实测＝只改 `Dockerfile.test` 新门 `RC=1`、同一棵树既有判据 `1 passed`。落码：新建 `scripts/check_vendored_wheel.py`（四判据 A 目录单枚／B 四面同名／C 实算 sha 撞登记行（门自己去读那一行、不抄第二份摘要）／D README 在册＋指到两处真源＋不许钉 64 位摘要；六种射程塌法 `exit 2`）＋`docker/homesdk/README.md`（90 行，报告建议的那份，版本／来源／构建命令／引用面表／两种失败形状／0.3.2→0.3.3 六步更换流程）＋`tests/unit/test_vendored_wheel_gate.py`（24 腿，含盲区腿与它的反向自证）＋`gates.sh` 纯加法 22 增／0 删（`bash -n` RC=0）。副本树十二案：M0／M99 绿，M42 杀 A、M43／M44 杀 B、M45 杀 C、M46／M47／M48 各杀 D 一条，M49／M50／M51 三种射程塌各 `RC=2`，restore 后 `RC=0`；相关判据合跑 `50 passed`、副本树上同 24 腿 `24 passed`。门禁三遍整份逐字节相同 **8670 B／md5 `770bad825770e5faf08525dcbfb9ef58`**、`GATES_RC=1`（两枚红＝登录线在途 `af_api.py:984/:1005`；a／b 在代码＋README 落完、c 在本文件与执行记录改完，c 与 a 的 `diff` 为空）；与上一批本体 8210 B 的差值全部由新节三行解释（剥掉那三行恰好 8210 B，md5 只差在两行等长数字：`undefined-name` 213→214 文件、覆盖门 `check_*.py` 20→21／覆盖 19→20／echo 68→71 行，**无需新豁免**），tally／baseline 未动。**两处不照办**：① README 不钉 SHA256（判据 D 反而禁止）＝对报告建议的显式偏差，已登记请追认；② "改私有 devpi／制品库＋锁文件"是那份裁定当场驳回的 B／C 两档 ⇒ 不重递不落地。报告「未在 3.11／3.14 两侧真跑验证」与第 22 条第 2 问同题，随 #99 攒批递。§十七 交付面那一格补指路一句。执行记录 §二之九十八 |
| 2026-10-10 | HEAD `079facc` + ARCH-05 批次（现读已提交 `fe34e2b`，未推；工作树里 `af_api.py`／`af_auth.py`／`docker/*`／`ui-user-mimo/*` 都是并发在途文件，一条不在本批改动面） | 收 §十八 第 24 条（第六轮审计 ARCH-05 的复测与分派）：**没有改过任何一处鉴权装配、没有新增路由、没有动过任何一枚 scope**；落的是"边界从可读的代码变成可 diff 的产物"——新建 `docs/信任边界清单.md`（162 行，自动段由 `--write` 从运行期路由表生成）＋`scripts/check_trust_boundary.py`（477 行，五判据 A 清单漂移／B 默认拒绝／C 登记不可核对／D 只减不增／E 安装器接线，八种射程塌 `exit 2`）＋`tests/unit/test_trust_boundary_gate.py`（32 腿）＋`gates.sh` 纯加法 26 增／0 删。**三个数对上**：AST 装饰器 87（41 get／41 post／4 delete／1 patch）− 条件注册的 SPA 兜底 `af_api.py:1409` ＝ 运行期第一方 86 ＋ 内置 4 ＝ 90 条路由对象；非鉴权档第一方 14 条（anon 10／optional 2／bearer-in-handler 2），三档 scope 与第三轮报告逐条对上（read 30／write 39／live 3）。**报告口径按现读更正两处**：`/api/metrics` 不是公开读（现读 `requires(read)`，那句还躺在 README §4.1，本批改掉）；"60 多个装饰器"实为 87。报告这条锚点 `af_api.py:986-1009`（register 匿名先到先得）**逐字成立**，两条绕过已在 DCD 申请里不重递。**复测露出报告没点名的两格**：四条匿名 POST（`/api/build`／`/api/bind`／`/api/sim`／`/api/spec/compile`——身上那枚 `_readonly_guard` 是单写者租约不是令牌，`/api/sim` 还随 store 真落遥测）＋第二份裸挂载表 `af_conflict_runtime.install_api`（报告只点名 `af_runtime_plugins.py:329`）；两者都在挂载上不带 `dependencies=`，后者整块包在 `except Exception: pass` 里＝挂载失败也不响，现读都无 src 调用者（判调用读 AST `Call` 节点，注释里的名字不算）。副本树十五案＋收尾 `FAIL 0`（M0／MZ99 绿；MA1／MA2／MA3 杀 A、MB1 杀 B、MC1／MC2／MC3 杀 C、MD1 杀 D、ME1／ME2／ME3 杀 E，ME3 报「接线已经发生」；MX1／MX2 落 `exit 2`），途中 MX2 第一版因"`re.sub` 没命中却没断言命中数"假绿一次，故给每腿加"注入未生效即 FAIL"护栏。分段单测 `6 failed, 3103 passed, 43 skipped`（六条同名同因＝登录线在途鉴权面）＋contract 57／f14 104／acceptance 38＋10 skipped／仓根 553＋65 subtests 全绿；门禁**三遍逐字节相同** 9088 B／md5 `1c13299d4bfe894d71be72971f6ded16`、`GATES_RC=1`（两枚红＝登录线在途 `af_api.py:984/:1005`；a 在代码＋清单＋README 落完、b 在两份 docs 记账落完、c 复跑，两两 `diff` 为空），与上一批 8670 B 的 418 B 差值逐字节解释（新节三行 407 B＋标签行 11 B，其余只有等长数字：`undefined-name` 214→215 文件、覆盖门 21→22／20→21／echo 71→74 行，**无需新豁免**），tally／baseline 未动；并更正上一节自己那句"整份含 `GATES_RC=1` 标签行"的口径（那份 8670 B 的末行是"结论：AST 门禁红…"）。**一处偏差显式请追认**：报告建议"新端点必须显式声明 scope"不落声明键（要动在途文件＋造第二份真值），改成推导＋未登记即红。四问递 DCD（四条匿名 POST 该不该匿名／收到哪档／声明键要不要成装配期硬约束／登记理由要不要逐条签认）＝与 #99 同批攒递。执行记录 §二之九十九 |
| 2026-10-10 | HEAD `d295b7f` + ARCH-06 批次（代码面已提交 `73edce9`，未推） | 收 §十八 第 25 条（第六轮审计 ARCH-06 的复测与分派）：**没有改过任何一行运行时行为**——没有自建进程 supervisor、没有给 serve 加信号钩子、没有给任何模块加一枚锁。报告那句「`src/` 全树 `atexit`／`signal.signal` 命中数为 0」按现读**半句过期**（`atexit.register` 0 成立；`signal.signal` 1 枚＝`af_cli.py:631`，是 §二之九十三 那批落的），「没有停机钩子这个概念」**部分不成立**（serve `af_cli.py:1480` 的 `try/finally` 停桥、watch 侧 SIGTERM 收进同一 `finally`；真缺的是信号钩子面），「缺 supervisor」**成立但要分层**（`Popen` 1 枚 `af_service.py:2716`／`os.kill` 2 枚 `:2604`+`:2682`，而 `af_tick_supervisor.py` 是 tick 的内监护 `af_live.py:448`，不是进程 supervisor），「没有等价物锁住共享可变状态的归属」成立⇒本批落的正是这一格。两张清点表行号按现读重钉：进程内 **17 站点／10 文件**（`af_service.py` 是 `:853`／`:1551`）、跨进程 **16 站点／6 文件**（serve 租约 `af_cli.py:1451`，报告 `:1435` off-by-16；**报告漏了 `af_service.py` 自己 3 枚** `:239`／`:1853`／`:2545`）、`global` 改写名 **9 枚／6 文件**（`af_live` 3、`af_premiere` 2；`_LOCAL_HELD` 是就地 `add/discard` 不需 `global`）。本批新量、报告没写的一条：那九枚的改写函数"体内有 `with`"整列读「否」，而 `af_live.py` 占 3 枚名且该模块进程内锁数 0——**门判的是对账不是缺陷**，这句写进清单 §一 口径。**落码四件全新增**：`docs/进程模型清单.md`（160 行：口径五条含"`scripts/`／`tests/` 明写排除"＋自动段三张表 4／33／9 行各带现读计数＋§三 站点登记 4 行×五字段＋§四 每类上限 14 行（多数为 0 是基线不是噪音）＋复测对撞＋递裁三问）＋`scripts/check_process_model.py`（607 行，纯 stdlib 对整棵 `src/autoforge` 做 `ast.parse`、不导入产品、不碰网络；五判据各自可红 A 自动段逐行对撞（含计数行，**计数下降也逼人来重生成**）／B 生命周期站点默认拒绝／C 每类上限只减不增／D serve `uvicorn.run` 那个 `try` 的 `finally` 还在停桥／E 装 SIGTERM 的函数里那个 `finally` 同时做 `stop.set()`＋`ticker.join(`＋`coord.release()`；九种射程塌 `exit 2`，第 ⑧ 种是反空集腿＝全树读不出任何一枚站点算扫描器坏了；判调用只认 `ast.Call`，注释里出现 `Popen` 不算）＋`tests/unit/test_process_model_gate.py`（488 行／34 腿：真仓 9 腿钉读数本身＋合成树 25 腿；行号由门的扫描结果生成、注入只用"末尾追加"与"单行替换"，免得移动行号把别的登记顺读成过期）＋`gates.sh` 纯加法 23 增／0 删（`bash -n` `RC=0`；覆盖门自认现读 `check_*.py` **23**／`gates.sh` 覆盖 **22**／echo **77** 行，无需豁免）。读数：门 `RC=0`、本批 `34 passed in 39.41s`（首跑 `1 failed, 32 passed`＝我把"登记整段变散文"错期望成 `exit 2`，现读语义是逐枚点名才红，另补"行形状整体改写才 `exit 2`"两条各管一头）、邻近四份合跑 `106 passed, 1 skipped in 106.91s`、`tests/unit` 整段 `6 failed, 3137 passed, 43 skipped in 361.00s`（六条与 §十八 A 表 2026-10-09 混合态追注同名同因＝登录线在途鉴权面，本批那份一条没进名单）、副本树十六案 `总计 16 案，未达预期 0 案`（M0／M99 绿＝门不判散文；M60–M69 十案全被杀且规则字母对上，M67 只出 D／M69 只出 E／M65 只出 C／M66 只出 A；M70–M73 四案落 `RC=2`）、门禁 a／b／c 三遍整份逐字节相同（a／b 在四件代码＋清单落完、c 在本文与执行记录两处记账改完，`diff` a／c 为空） **9418 B／md5 `c2b574ade9286275e97945a0ff4a5ad2`**、`GATES_RC=1`（两枚红＝登录线在途 `af_api.py:984/:1005`；`except-pass-broad=19 | fake-ok-const=78`⇒全量 97／上限 97），与上一批 9088 B 的 **+330 B 全由新节三行＋节前那枚空行 `echo` 解释**，其余三处等长数字（`undefined-name` 215→216 文件、覆盖门 22→23／21→22／echo 74→77 行），tally／baseline 未动。**一处偏差显式请追认**：报告"缺什么"三格（归属模型／supervisor／优雅停机）本批**本体一个都没建**，落的是它们下面的对账层——三件事都要改常驻进程运行时语义或部署口径；**三问进 #99 攒批递**（supervisor 归谁／serve 要不要信号钩子／`af_live` 三枚 `global` 要不要补锁）。等裁期间清单 §六 既不写"进程模型已闭环"也不写"按设计不需要 supervisor"。执行记录 §二之一百 |
| 2026-10-11 | HEAD `9785dd2` + Q4 批次（本批改动面：`README.md`／`pyproject.toml`／`gates.sh`／`scripts/check_ci_interpreter.py`／新门 `scripts/check_sim_dep_floor.py`／两份判据文件／两份 docs 记账；工作树里 `af_api.py`／`af_auth.py`／`docker/*`／`ui-user-mimo/*` 仍是并发在途文件，一条不在本批改动面） | 收 §十八 第 26 条（裁定 20261011 §3 Q4.1＋Q4.3 的窗内半边）：**没有改过任何一枚解释器版本取值、没有钉任何上界**。Q4.1 落口径：`README.md` 新增「### 解释器口径」四行表（真源＝`pyproject.toml:10 requires-python`）＋解释器门新增**判据 D**（逐格对撞声称值／锚点在盘上真在／那一行也含着声称值／表只加长不缩短），`exit 2` 形状 5→6，`INTERPRETER_DRIFT` 两格理由改成引用裁定 §3 Q4.1／Q4.2／Q4.3 与 `pyproject.toml:84`；README 那句「需要 Python 3.14+」（镜像口径被抄成项目门槛）按现读改掉。Q4.3 落新门 `check_sim_dep_floor.py`（A 每枚依赖带约束·无豁免档／B 在册下界逐枚 extra 同值＋蒸发即红／C 依据含「裁定」＋盘上真锚点），`sim`／`dev` 两枚 `pytest-homeassistant-custom-component` 从裸名钉成 `>=0.13.109`（依据＝第 22 条的 PyPI 现读：`>=3.11` 档最新即它 ⇒ 不改动任何一面解析结果）。读数：两门各 `RC=0`；本批判据 `44 passed`、相邻两份 `64 passed`；`bash -n gates.sh` `RC=0`；副本树六案全 KILLED／串扰 0／存活 0；门禁两遍逐字节相同 10341 B／md5 `8a9fd3ec…`，`GATES_RC=1` 的唯一红源仍是登录线在途 `af_api.py:984`／`:1005`；覆盖门自认 `check_*.py` 25／`gates.sh` 覆盖 24／工作流 1／豁免 1 格 ⇒ 新门无需豁免，tally／baseline 一字未动。**本批自己造成并已修的漂移**：`pyproject.toml` 新注释把 `addopts` 从 `:78` 顶到 `:84`，而 `INTERPRETER_DRIFT` 引用的正是 `:78` ⇒ 改成 `:84` 并让门去核。**三问不自决**：CI 3.11 与镜像 3.14 谁向谁对齐／真仿真要不要一条必跑链／要不要依赖上界。执行记录 §二之一百一十四 |
| 2026-10-11 | HEAD `6c3bf1d` + Q3 乙门禁半边批次（本批改动面：`src/autoforge/af_bounded_caches.py`／`scripts/check_bounded_caches.py`／`tests/unit/test_bounded_caches_gate.py`／两份 docs 记账；`af_api.py`／`af_auth.py`／`docker/*`／`ui-user-mimo/*` 仍是并发在途文件，**本批一条没碰**） | 收 §十八 第 27 条（裁定 20261011 §3 Q3 乙的门禁半边）：**产品代码一行没改**，`af_bounded_caches.py` 从两张表加到三张（新档 `MONOTONIC_PERSISTENT_SETS`＝无界＋持久化＋坏档拒绝的安全集，三条腿是落盘入口／重启恢复入口／坏档 fail-closed 标志位，**不是** TTL 与上限），`check_bounded_caches.py` 新增判据 F 五红（缺字段／腿名在模块里读不出／`bound` 无「裁定」＋ 8 位编号／同键双登记／过期登记），表整体 `ImportError` 走 `exit 2` 不判红。反蒸发不加新规则：删掉整张表 ⇒ `_revoked` 成了未登记容器 ⇒ **既有判据 C 自己会红**；`read_registry` 的三元组契约一字未动（8 处测试按位置解包）。**F 不遮 E**：登记答"有没有界"，不答"有没有人读"，另有一条腿删掉合成读取点仍期望死写红。基线 **114 → 113**（`af_auth.py::TokenRegistry._revoked` 搬家），扫到 129／标记 16／读取名 4111／死写 0 四项不变；门 `RC=0`、本批 `45 passed`、邻近 `35 passed`、覆盖门 `RC=0`、`gates.sh` 两遍本体逐字节 10406 B／md5 `889eb4c97ecc0a72455b8313596a11ee`（唯一红仍是登录线 `af_api.py:984/:1005`），副本树 M0–M7 全 KILLED／串扰 0。**两处自证缺陷就地修**：M3 首跑用裸名当变异锚点命中了 docstring ⇒ 存活，改整行锚点才 KILLED；`drive_mut.py \| tail -30` 的退出码属于 `tail`（那条老陷阱第二回），改 `> file 2>&1; echo driver_rc=$?`。甲档按裁定驳回（给撤销表加淘汰＝安全语义倒退），丙档（当前条数读数＋`/api/metrics`）落点在在途文件 ⇒ 随 #79 窗口。执行记录 §二之一百一十五 |
| 2026-09-24 | 当时 HEAD | 初版（端到端实测后） |

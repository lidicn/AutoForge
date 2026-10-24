# DB 席位交付回执（2026-10-24，路线图 db_roadmap_20261024）

分支 `db/task-01`（worktree busy-meninsky-653c24）。一笔 commit 一件事，未合回 master。

## 完成清单

| 编号 | 缺口 | commit | 判据腿 |
|---|---|---|---|
| DB-01 | G5 draft_intent 类型闸 | `93aa058`（并发同批，内容一致） | tests/unit/test_draft_intent_type_guard.py · 5 条 |
| DB-02 | G8 过期令牌剔除审计 | `deef0ed` | tests/unit/test_auth_purge_alerting.py · 5 条 |
| DB-03 | G9 双令牌名 legacy 告警 | `fcff6f9`（+清单 b0fa69c） | tests/unit/test_auth_legacy_env_warning.py · 6 条 |
| DB-04 | G10 canary+duration 掐链判据腿 | `2c9b502` | tests/unit/test_canary_duration_chain_break.py · 2 条（只加测试） |

## 新错误码 / 新审计事件
- 错误码：`E_INTENT_NOT_OBJECT`（af_draft.py）
- 审计事件：`TOKEN_EXPIRED_PURGED`（af_audit.py）、`TOKEN_ENV_LEGACY_NAME`（af_audit.py，已登记进 ALL_EVENT_TYPES）
- 均不带令牌明文 / token value（DB-02/03 各有一条 `assert plain not in json.dumps(event)` 安全腿）。

## gates.sh 状态
- DB-01/DB-02：gates 全绿（结论：门禁干净）。
- DB-03 后进程模型/可观测性门报**纯行号漂移**（af_auth/af_audit 加行使 Lock、logger 站点行号下移），已按门规 `--write` 重生成两份清单自动段并入库。
- ⚠️ 残留红：台账同批门。根因是**本 worktree 有并发席位（PM/另一 db）交错提交**（9de3524、779b9ed 等非我所写的 commit 改了 af_auth/host_update.sh 却未带对应清单），属跨席位历史债，非本批回归。我这四笔各自的源码改动均已把对应清单同批带上。

## 新增 except:pass 数
0 枚（基线 16 枚只减不增，本批未加任何吞异常）。

## 我没做的事（硬要求）
- 没碰禁区：af_api.py / af_mcp.py / af_executor.py / af_canary.py / af_vhass/fake.py / check_*.py / .gates.* / 发版 bump 1.13.4（PM 的活）。
- DB-04 只加测试，**未改** af_executor.py / af_canary.py——修 d3→then 边是 PM 的活，测试钉住的是当前缺陷行为（d4 不跑），不为绿放宽断言。
- 没 push origin master / 没打 tag；commit message 无真实令牌值。
- 未合回 master，留待 PM review 统一合。

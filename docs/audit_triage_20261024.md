# 审计报告 triage 核实回执（2026-10-24）

对象：`E:\NAS\ADM-auditkit\审计报告\四项目审计\AutoForge_审计报告.md`（604 条候选）。
方法：逐条读源码核实，区分真缺陷 / 误报。**不盲目改码**——误报乱改会破坏既有安全设计。

## 结论先行
- 604 条候选里，机器扫描段（§1）我抽查全部 high（67 条）后判定：**当前可安全修复的真 bug = 0 条新发现**。
- 唯一已确证真缺陷仍是 §0 的 **AF-001（设计级）**，需架构决策，不在本轮补丁范围。

## high 段逐条核实
| 报告# | 位置 | 扫描规则 | 核实结论 |
|---|---|---|---|
| 1 | ma.py:70 | 硬编码凭据 | **误报**。`MA_TOKEN_SECRET="AUTOFORGE_MA_MCP_TOKEN"` 是环境变量**名字常量**，非凭据值；注释明写 secret 文件>env。 |
| 2 | af_api.py:1273 | async 内同步 sleep 阻塞 | **误报**。实代码是 `await asyncio.sleep(1.0)`（SSE 循环，标准异步让出），行号已漂移。扫描器只认 `sleep(` 不认 `await`。 |
| 3 | af_service.py:2675 | except return True fail-open | **误报**。锁回收探活：读不到 /proc/stat 时**有意**保守判存活，交内核锁真值裁决（docstring 明写），非安全放行。 |
| 4–8 | check_outbound_guard.py:84-88 | 无界容器 | **误报**。单次进程内 AST Visitor，文件扫完即退出，list 长度=被扫源码规模，不常驻。 |
| 9 | mock.py:31 | calls 无界 | **误报**。测试/仿真夹具调用记录，不部署常驻。 |
| 10 | af_auth.py:162 `_revoked` | 无界 set | **误报（不能界化）**。撤销令牌黑名单，淘汰=已撤销令牌复活，安全上必须只增不减。 |
| 11–12 | af_conflict.py `_release_log`/`_circuits` | 无界 | 按 automation key 注册，家庭自动化数量有界；`_circuits` 是断路器状态表。 |
| 20 | af_pretrigger.py:65 `_events` | 无界 | **误报**。已 C-3 界化（每自动化 1000 条环形裁剪），扫描器只看 `__init__` 没看 add 处裁剪。 |
| 其余 high | 各 fake/bridge/adapter `calls`/`_attributes` | 无界 | 多为 vhass 仿真夹具或按实体注册的固定 dict，非常驻泄漏。 |

## 中/低段（约 530 条）
- `py.silent_except_no_trace`（~300 条）：仓库基线 16 枚吞异常只减不增，绝大多数 except 带 `logger.warning`/`# noqa` 注释或在安全隔离分支（如 canary 漂移检查异常不阻断流程）。
- `py.unbounded_container`（~200 条）：dict 按实体 key 注册（key 有界=家庭设备数）、AST Visitor 单次进程、fake 夹具。
- 这些是扫描器**规则粗**产生的系统性误报，不是逐行真 bug。

## 真缺陷 AF-001（已修，commit 05dc1f2）
`/api/asks/answer` 用 `AUTOFORGE_INBOX_KEY` HMAC 签 body，af_live 同 key 校验——原消息无新鲜度，旧/重放 inbox 文件无限接受。
- **修法**：写侧签名消息加签发 `ts`（并入签），body 落 `ts`；读侧无 ts 或 `|now-ts|>300s` 一律拒（旧裸签名作废）。篡改 ts 即 sig 失效。
- 新增测试 `test_read_inbox_rejects_stale_ts_replay`；41 passed（ask_flow+live）。
- 非对称（Ed25519 私钥签/公钥验）是更彻底方案，但需部署侧换密钥对，留后续；本次先收敛重放面。

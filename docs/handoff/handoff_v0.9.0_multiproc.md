# 交接卡 v0.9.0 — 跨进程与多写者

## 改动清单（1 新增 + 6 修改 + 测试 + 文档）

| 文件 | 变更 |
|---|---|
| `src/autoforge/af_flock.py` | **新增**：跨进程原语。`owner_id()`（hostname-pid-uuid8，进程内缓存）；`FileLock`（POSIX `fcntl.flock` / Windows `msvcrt.locking`，阻塞 acquire 带超时 + 非阻塞 try_acquire；持有者信息写 **sidecar** `{path}.info`——Windows 字节区间锁会拒绝其他句柄读锁文件本体，故不能写锁内；进程死亡内核自动释放，无陈旧锁）。 |
| `src/autoforge/af_audit.py` | 新增 `WRITE_CONFLICT` / `INSTANCE_LEASE_HELD` 常量（并入 ALL_EVENT_TYPES）；`record_conflict()` 向跨进程共享 JSONL 追加冲突条目（单行 O_APPEND，无锁并发不撕裂）。 |
| `src/autoforge/af_store.py` | `save()`：版本号自增移入 `FileLock({name}/.lock)` 临界区 + 原子替换（tmp+`os.replace`）；记录新增 `writer` 署名；新增 `expect_version` 乐观锁（不匹配 → 写 `write_conflicts.jsonl` + 抛 `WriteConflictError`）。`set_tags()` 读改写**整体**加锁（读在外会有丢失更新）；conf/tags 写全部原子化；`restore_context` 携带 owner。 |
| `src/autoforge/af_persist.py` | `PersistStore(root, owner=None, lease_s=60)`：落盘记录补 `owner` + `lease_until_wall`（每次保存续租）；新增 `claims(record, clock)` 租约仲裁（无主/自持/过期 → 可接管）。 |
| `src/autoforge/af_instance.py` | `InstanceContext.owner: str = ""`（可序列化红线不破），to_dict 纳入。 |
| `src/autoforge/af_runtime.py` | `restore_persisted()`：租约仍属其他进程 → 跳过恢复 + 审计 `instance_lease_held`，**不删文件、不双跑**。 |
| `src/autoforge/af_live.py` | 新增 `WatchCoordinator`：抢 `{persist_dir|store_root}/watch.lock`，单活跃 watcher，身份入 sidecar 供拒绝方诊断。 |
| `src/autoforge/af_cli.py` | `forge watch`：启动前抢协调锁，被拒则打印持有者（watcher/graph/HA 地址）并退出；正常退出/异常均释放。 |
| `tests/unit/test_v0_9_multiproc.py` | **新增** 9 项测试，含 3 个**真子进程**测试（锁互斥、3×5 并发保存、tags 并发读改写）。 |

## 行为变化

- 归档记录新增 `writer` 字段；实例落盘记录新增 `owner` / `lease_until_wall`（旧记录无这两键 → 视为无主，可接管，向后兼容）。
- `save(expect_version=N)` 为新可选参数，缺省 `None` 行为不变。
- `forge watch` 新增启动互斥：同 store/persist 目录第二个 watch 直接拒绝（exit 1）。
- 未配置持久化 / 单进程场景行为完全不变（同进程内 owner_id 相同 → claims 恒 True，既有 P1 恢复测试零改动通过）。

## 已知风险 / 注意

- 锁是 advisory：所有写方必须走 `GraphStore`/`PersistStore`/`WatchCoordinator` 才受保护；绕过直写文件不受保护。
- Windows 侧车 `.info` 在进程崩溃后残留最后持有者信息，仅诊断用途（锁本身随进程死亡自动释放，不影响正确性）。
- 租约续租依赖持有进程持续落盘；若持有进程活着但实例长时间无状态变更且租约（默认 60s）到期，会被其他进程接管——需要更稳的场景调大 `lease_s` 或缩短落盘周期。
- `write_conflicts.jsonl` 无轮转，长期高频冲突需运维关注（原型期量级可忽略）。
- 测试子进程通过 `PYTHONPATH=src` 注入包；NAS 容器（Linux）走 fcntl 分支，与 Windows 分支共用同一测试。

## 验证

- 本机：`python -m pytest -q` → **306 passed / 10 skipped / 0 failed**（v0.8.0 基线 297 + 9）。
- 新增测试覆盖：跨进程锁互斥（父持锁 → 子 BUSY 且读到持有者 → 释放后子 ACQUIRED）、3 进程并发保存 15 版本号连续且全部可解析、expect_version 冲突抛错 + JSONL 审计条目字段齐全、tags 双进程并发双方键保留、owner 往返、落盘续租（08:00+60s）、租约仲裁四态（自持/他持/过期/无主）、Runtime 恢复尊重租约（未到期跳过 + 审计 + 不删文件；过期接管）、watch 协调锁单活跃。
- 开发中修复的两个坑（已固化在代码注释）：① Windows 锁文件本体不可读 → sidecar；② `set_tags` 读在锁外的丢失更新竞态 → 读改写整体加锁。

## 合并影响

- 对 AutoForge-UI 无影响（API 层未改）；`forge watch` 运维侧多开会被拒，属预期。
- v1.0.0（表达式强化 + `fn` 评估）为路线图最后一个待办版本。

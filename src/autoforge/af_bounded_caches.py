"""有界缓存注册表（裁定 20261004 §一 3 B）：哪些容器已经"双腿齐全、且有测试钉住回收"。

约定本体来自第六轮审计 §三：**新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言"纯写不读也必须
被回收"**。这句话原先只活在审计正文里——`grep -n "有界\\|TTL" gates.sh scripts/*.py` 零命中，
没有任何东西能判红。本模块把它变成 `scripts/check_bounded_caches.py` 能核对的形状。

三项约定的落点：
- `BOUNDED_CACHES` 每一项给出**两条腿的出处**（模块里的常量名 / 裁剪方法名）与一条真实测试 id。
  门禁脚本按名字回到模块源码里核对：写不出那条腿就不许登记（不允许用注册表给一个说法盖章）。
- `FIXED_KEY_CACHES` 是"看着像增长容器、其实键空间封闭"的那几处，逐条带理由。它们的理由同时以
  `# bounded-cache: exempt(理由)` 出现在被豁免那一行——门禁按行核对，两处口径必须一致。
- `MONOTONIC_PERSISTENT_SETS` 是**反过来那一类**：无界、持久化、按设计不许淘汰的安全集。它给的三条腿是
  落盘入口／重启恢复入口／坏档 fail-closed 标志位，**不是** TTL 与上限——把这类容器留在基线名单里，
  等于门禁默认它"有界"。
- 其余存量增长容器**不在这个文件里**，冻结在门禁脚本的基线名单（§一 3 B 的"基线冻结、新增必须
  登记"）。新增一个容器要么进这张表（两条腿 + 测试），要么就地带理由豁免，否则判红。

基线冻的是"**有没有界**"，冻不掉"**这份数据根本没人读**"（稳定性审计 §六 P1 的判据 E）：一个只在基线里
挂号、写入点一堆、全仓读不到的容器，仍然会被判红——稳定性审计 BUG-01 第一半删掉的那个「节点访问累积
列表」就是这一族，而它当时**就在基线内**。所以"进了基线"不等于"这份数据有存在理由"。
（为什么这里不写容器原名：`tests/unit/test_audit_stability_defects.py` 那条按名字的哨兵把 `src/` 整棵树
都算射程，散文里复述原名会让判据在**没有任何回归**时报红——引用历史记录请写审计编号。）

本文件目前有三张表：`BOUNDED_CACHES`（缓存：TTL + 硬上限两条腿都有语义）、`FIXED_KEY_CACHES`
（键空间封闭）与 `MONOTONIC_PERSISTENT_SETS`（**无界但持久化的单调增长安全集**，裁定 20261011 §3 Q3 乙）。
第三张表存在的理由：拿「有界」去登记一份持久化安全集，等于**门禁在替一个不存在的性质盖章**——
它没有 TTL、不许有上限，"进了基线"会让下一个人以为可以给它加淘汰。
"诊断型只写环形清单"（`HAAdapter.intents` / `HTTPAdapter.intents` / `Scheduler.rejections`，本批
已各加条数封顶）**没有诚实的 TTL 可填**，因此 AF 没有把它们塞进 `BOUNDED_CACHES` 给一条不存在的腿盖章；
是否加第四张单腿表（`DIAGNOSTIC_RINGS`）已交 DCD（`inbox/20261006-AF-诊断型只写日志的第二条腿与判据E同名遮蔽-决策申请.md`），
裁定落地前本文件保持这三张表。
"""

from __future__ import annotations

__all__ = ["BOUNDED_CACHES", "FIXED_KEY_CACHES", "MONOTONIC_PERSISTENT_SETS"]

#: 「持久化单调集」档（裁定 20261011 §3 Q3 乙）：无界、但**持久化且按设计只能单调增长**的安全集。
#: 每一枚给出三件可核对的事：落盘入口 / 重启恢复入口 / 坏档 fail-closed 的标志位。
#: `bound` 与 `why` 不许写成散文——门禁按字面要「裁定」与编号，并回到模块源码里核那三个名字。
#: 为什么这里只给**文件锚点**不给 `路径:行号`：行号会因为别的批次加注释而漂（本仓真踩过一次），
#: 而这一档的核对手段是"名字在那个模块里出现"，行号对判定没有任何贡献。
MONOTONIC_PERSISTENT_SETS: list[dict[str, str]] = [
    {
        "module": "af_auth",
        "attr": "TokenRegistry._revoked",
        "persist": "_persist_revoked",
        "reload": "_load_revoked_file",
        "poison": "_revoked_poisoned",
        "bound": "裁定 20261011-AF第六轮与第二期审计攒批十三问-裁定.md §3 Q3 乙：增长上界不是编译期封闭的，"
                 "唯一的写入通道是 revoke() 与重启时读那份黑名单文件；写入频率受同文件授权面限速"
                 "（`af_auth.py` 的 `RateLimiter`）间接约束。⇒ 这一档**不声称有界**，只声称"
                 "「无界＋持久化＋坏档一律拒绝」。",
        "why": "裁掉一条＝那个 jti 可能重新被接受，是安全语义倒退（同一份裁定的甲档已明确驳回）。"
               "所以它既不是缓存也没有淘汰路径，不许躺在「有界缓存」的基线名单里；"
               "读数（当前条数）与 /api/metrics 那一半属丙档，落点在并发在途文件，排在窗口之后。",
    },
]


BOUNDED_CACHES: list[dict[str, str]] = [
    {
        "module": "af_service",
        "attr": "_SESSIONS",
        "cap": "SESSION_MAX",
        "ttl": "SESSION_TTL_S",
        "trim": "_purge_sessions",
        "test": "tests/unit/test_af_session_bounds.py::test_hard_cap_evicts_oldest_even_within_ttl",
    },
    {
        "module": "af_undo",
        "attr": "UndoStore._records",
        "cap": "MAX_DEPLOYS",
        "ttl": "window_s",
        "trim": "_trim",
        "test": "tests/unit/test_reclaim_callers_wired.py::test_undo_snapshot_count_is_capped_without_reads",
    },
    {
        # 裁定 20261006 §一 的超限锁定表：`check()` 只删被挡到的那一条，换源 IP 刷的写入侧
        # 必须自己回收，否则这张表跟着扫描器的 IP 数长。
        "module": "af_auth",
        "attr": "RateLimiter._blocked",
        "cap": "LOCK_MAX_KEYS",
        "ttl": "lock_s",
        "trim": "_prune_blocked",
        "test": "tests/unit/test_dcd_20261006_pairing_bootstrap.py::test_blocked_map_is_pruned_without_any_read",
    },
]

FIXED_KEY_CACHES: list[dict[str, str]] = [
    {
        "module": "af_pretrigger",
        "attr": "PreTriggerService._stats",
        "reason": "固定键计数器：所有写入点用的都是字面键，键空间编译期封闭",
    },
    {
        "module": "af_vhass/device_sm",
        "attr": "DeviceSM.attributes",
        "reason": "键集由 domain 属性词表决定（每实体 ≤ 十来个键），且 reset() 整体替换字典",
    },
    {
        # 计划 §六 第 3 项（降级播报）带进来的那份 caps 快照：重发要靠它，键集来自唯一写入口。
        "module": "af_mqtt_bridge",
        "attr": "AfMqttBridge.caps",
        "reason": "整体替换的 caps 快照：唯一写入口 advertise() 每次 dict(caps) 覆盖，键集 = caps_payload() 的 mcp/tools/version 三键，不做增量增长",
    },
]

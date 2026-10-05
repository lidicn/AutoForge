"""有界缓存注册表（裁定 20261004 §一 3 B）：哪些容器已经"双腿齐全、且有测试钉住回收"。

约定本体来自第六轮审计 §三：**新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言"纯写不读也必须
被回收"**。这句话原先只活在审计正文里——`grep -n "有界\\|TTL" gates.sh scripts/*.py` 零命中，
没有任何东西能判红。本模块把它变成 `scripts/check_bounded_caches.py` 能核对的形状。

三项约定的落点：
- `BOUNDED_CACHES` 每一项给出**两条腿的出处**（模块里的常量名 / 裁剪方法名）与一条真实测试 id。
  门禁脚本按名字回到模块源码里核对：写不出那条腿就不许登记（不允许用注册表给一个说法盖章）。
- `FIXED_KEY_CACHES` 是"看着像增长容器、其实键空间封闭"的两处，逐条带理由。它们的理由同时以
  `# bounded-cache: exempt(理由)` 出现在被豁免那一行——门禁按行核对，两处口径必须一致。
- 其余存量增长容器**不在这个文件里**，冻结在门禁脚本的基线名单（§一 3 B 的"基线冻结、新增必须
  登记"）。新增一个容器要么进这张表（两条腿 + 测试），要么就地带理由豁免，否则判红。

基线冻的是"**有没有界**"，冻不掉"**这份数据根本没人读**"（稳定性审计 §六 P1 的判据 E）：一个只在基线里
挂号、写入点一堆、全仓读不到的容器，仍然会被判红——稳定性审计 BUG-01 第一半删掉的那个「节点访问累积
列表」就是这一族，而它当时**就在基线内**。所以"进了基线"不等于"这份数据有存在理由"。
（为什么这里不写容器原名：`tests/unit/test_audit_stability_defects.py` 那条按名字的哨兵把 `src/` 整棵树
都算射程，散文里复述原名会让判据在**没有任何回归**时报红——引用历史记录请写审计编号。）

本文件目前只有 `BOUNDED_CACHES`（缓存：TTL + 硬上限两条腿都有语义）与 `FIXED_KEY_CACHES`（键空间封闭）
两张表。"诊断型只写环形清单"（`HAAdapter.intents` / `HTTPAdapter.intents` / `Scheduler.rejections`，本批
已各加条数封顶）**没有诚实的 TTL 可填**，因此 AF 没有把它们塞进 `BOUNDED_CACHES` 给一条不存在的腿盖章；
是否加第三张单腿表（`DIAGNOSTIC_RINGS`）已交 DCD（`inbox/20261006-AF-诊断型只写日志的第二条腿与判据E同名遮蔽-决策申请.md`），
裁定落地前本文件保持两张表。
"""

from __future__ import annotations

__all__ = ["BOUNDED_CACHES", "FIXED_KEY_CACHES"]

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
]

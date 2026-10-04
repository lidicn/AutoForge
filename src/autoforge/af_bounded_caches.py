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

# examples/scene_demo.py —— 场景模式最小闭环（含重启状态恢复）
from __future__ import annotations

import json, os
from autoforge.af_scene import Scene, SceneManager


class DeviceBridge:
    """现场 executor 适配层：把场景编排接到 af_executor。

    af_executor 的真实签名由现网决定；只要暴露 set_automation_enabled /
    enable+disable / enable_automation+disable_automation 三者之一即可直接注入。
    """

    def __init__(self, real_executor):
        self._ex = real_executor

    def set_automation_enabled(self, automation_id: str, enabled: bool) -> bool:
        print(f"  [bridge] {'开' if enabled else '关'} automation={automation_id}")
        return self._ex.set_automation_enabled(automation_id, enabled)  # 按现网签名调整


def build_scenes(cfg: dict) -> list[Scene]:
    return [
        Scene(
            scene_id=s["scene_id"],
            name=s["name"],
            automations=list(s.get("automations", [])),
            exclusive_group=s.get("exclusive_group"),
        )
        for s in cfg["scenes"]
    ]


if __name__ == "__main__":
    config = {
        "scenes": [
            {"scene_id": "away",  "name": "离家模式", "automations": ["light_off", "lock_up", "guard_on"],  "exclusive_group": "presence"},
            {"scene_id": "home",  "name": "回家模式", "automations": ["light_on", "hvac_comfort"],           "exclusive_group": "presence"},
            {"scene_id": "movie", "name": "观影模式", "automations": ["curtain_down", "dim_all"],            "exclusive_group": None},
        ]
    }
    persist_dir = "./var/scene_state"

    # 第一次运行：激活「离家」+「观影」（非互斥可共存）
    mgr = SceneManager(build_scenes(config), DeviceBridge(executor), persist_dir)
    print("activate away  ->", mgr.activate("away"))   # True
    print("activate movie ->", mgr.activate("movie"))  # True，与 away 并存
    print("active:", [s.scene_id for s in mgr.active_scenes()])

    # 切到「回家」：同互斥组的「离家」被自动关闭
    print("activate home  ->", mgr.activate("home"))   # True
    print("active:", [s.scene_id for s in mgr.active_scenes()])  # ['home', 'movie']

    print("deactivate movie ->", mgr.deactivate("movie"))  # True

    # 进程重启：状态从 persist_dir/scenes.json 恢复
    mgr2 = SceneManager(build_scenes(config), DeviceBridge(executor), persist_dir)
    print("after restart, active:", [s.scene_id for s in mgr2.active_scenes()])  # ['home']
    with open(os.path.join(persist_dir, "scenes.json"), encoding="utf-8") as fh:
        print("persisted:", json.dumps(json.load(fh)["scenes"]["home"], ensure_ascii=False))
# scripts/demo_af_version.py —— 端到端演示（不改任何现有文件）
from autoforge.af_version import VersionManager

# 1) 构建版本仓库：IR 读写全部注入（这里用内存 dict 模拟 af_store 的读写接口）
live_ir = {"auto_a": {"id": "auto_a", "trigger": {"kind": "manual"},
                      "nodes": {"n1": {"id": "n1", "kind": "do", "action": "switch.on",
                                       "entities": ["light.kitchen"]}}}}

def read_ir(automation_id):                 # 真机替换成 af_store 的公开读接口
    return live_ir[automation_id]

def write_ir(automation_id, ir):            # 真机替换成 af_store 的公开写接口
    live_ir[automation_id] = ir

versions = VersionManager(root="E:/NAS/AutoForge",
                          ir_provider=read_ir, ir_writer=write_ir)

# 2) 每次部署前自动快照（无需手动触发）：把 deployer 包一层，接到 ProposalManager 注入点
#    from autoforge.af_proposal import ProposalManager
#    proposal_mgr = ProposalManager(conf=conf, recorder=recorder, clock=clock, audit=audit,
#                                   deployer=versions.wrap_deployer(real_deployer))

# 3) 手动演示同样的链路
v1 = versions.snapshot("auto_a")                       # 快照（存 IR 完整快照）
live_ir["auto_a"]["nodes"]["n2"] = {"id": "n2", "kind": "ask", "prompt": "开灯？",
                                    "when": "x > 1"}   # 改一版
v2 = versions.snapshot("auto_a")

print(versions.diff(v1, v2)["summary"])
# {'nodes_added': 1, ..., 'conditions_added': 1, 'total_changes': 2}
print(versions.diff(v1, v2)["nodes"]["added_ids"])     # ['n2']

versions.tag(v1.version_id, "stable")                  # 打标签
versions.tag(v2.version_id, "canary")

print([ (v.version_id, v.label) for v in versions.history("auto_a") ])
# 倒序：[(v2, 'canary'), (v1, 'stable')]

assert versions.rollback("auto_a", v1.version_id) is True   # 一键回滚
assert live_ir["auto_a"] == v1.ir_snapshot                   # IR 已恢复
assert versions.history("auto_a")[0].parent_id == v2.version_id  # 自动新快照
assert versions.rollback("auto_a", v2.version_id) is True   # 可再次回滚（滚回去）

# 4) 重启后：新实例直接读 .forge/versions/<automation_id>.json
fresh = VersionManager(root="E:/NAS/AutoForge", ir_provider=read_ir, ir_writer=write_ir)
print(len(fresh.history("auto_a")))                     # 6 —— 历史仍在
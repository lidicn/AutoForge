"""装配层：把现网对象接给 af_evo，跑一轮进化扫描。"""
from autoforge.af_evo import EvoScanner, EvoPolicy, EvoStatus

scanner = EvoScanner(
    health_engine=health_engine,        # af_health.HealthEngine（只读 health_score / inputs）
    proposal_manager=proposal_manager,  # af_proposal.ProposalManager（只用 submit()）
    graph=graph,                        # af_ir.Graph
    executor_stats=executor_stats,      # 执行统计（shadow_hits / intervention_rate，duck typing）
    simulator=sim_run,                  # 仿真器（见 4.2）
    policy=EvoPolicy(low_health_only=True, low_health_below=60.0),
)

for p in scanner.scan():
    print(f"[{p.status.value}] {p.strategy.value} conf={p.confidence:.2f}")
    print(f"  目标: {p.automation_id}  关联: {p.related_ids}")
    print(f"  理由: {p.reason}")
    if p.status is EvoStatus.DEFERRED:
        print("  ⚠ 未注入 simulator，提案暂存待人工/补 sim 后再入队")
    elif p.status is EvoStatus.QUEUED:
        print(f"  已进审批队列: {p.queued_id}（submitted_conf={p.meta['submitted_conf']}）")
    elif p.status is EvoStatus.REJECTED:
        print(f"  sim 未通过: {p.sim['reason']}")
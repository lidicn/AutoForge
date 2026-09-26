# scripts/health_demo.py —— 装配层把真机对象注进来，af_health 自己不 import 它们
from autoforge.af_canary_supervisor import CanarySupervisor
from autoforge.af_conf import ConfidenceStore
from autoforge.af_conflict_audit import ConflictAuditor
from autoforge.af_intervention import InterventionDetector
from autoforge import af_health

engine = af_health.configure(
    executor_stats=executor.stats_bus,          # 任何能 .stats(id) 的对象 / 映射
    conflict_audit=ConflictAuditor(persist_dir=".forge"),
    intervention_detector=detector,
    canary_supervisor=supervisor,
    conf_store=ConfidenceStore().seed(graph),
    demoter=lambda aid: strip_canary(aid),      # 产品语义定了就换这里（§7-5）
)

engine.register(*graph.automations.keys())      # 一次没跑过的也纳入报告

print(engine.health_score("light_study_auto"))  # → 87（int）
for row in engine.health_report():
    print(row["automation_id"], row["score"], row["band"], row["failing"])

demoted = engine.auto_demote(30)                # → ["light_study_auto"]（低于 30 才有）
for e in engine.alert(50):                      # → [{kind, severity, score, failing, ...}]
    notify(e)                                   # 告警投递是调用方的事，引擎零 IO

engine.snapshot()                               # 显式记历史（可放到定时任务里）
print(engine.history("light_study_auto", days=7))  # → 按 at 升序的 [{at, score, dims, conf}]
import json
from autoforge.af_store import GraphStore
from autoforge.af_spec import graph_to_raw

store = GraphStore("/data")
name = "binary_sensor.0x00158d0001f34db6_contact on → turn_on light.mijia_cn_group_1861372413196005378_group4_s_2_light"
graph = store.load(name)
ir = graph_to_raw(graph)
with open("/tmp/test_ir.json", "w", encoding="utf-8") as f:
    json.dump(ir, f, ensure_ascii=False, indent=2)
print("exported to /tmp/test_ir.json")

import json

with open("/tmp/test_ir.json", "r", encoding="utf-8") as f:
    automations = json.load(f)

# 包装成完整 IR 对象
ir = {"automations": automations}

with open("/tmp/test_ir.json", "w", encoding="utf-8") as f:
    json.dump(ir, f, ensure_ascii=False, indent=2)

print("fixed IR format")

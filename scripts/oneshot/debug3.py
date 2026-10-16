from autoforge.af_store import GraphStore
s = GraphStore('/data')
g = s.load('case01_day_light', 2)
a = g.automations[0]
print("methods:", [m for m in dir(a) if not m.startswith('_')])
print("---")
# pydantic?
if hasattr(a, 'model_dump'):
    print("model_dump:", list(a.model_dump().keys()))
elif hasattr(a, 'dict'):
    print("dict:", list(a.dict().keys()))

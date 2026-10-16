from autoforge.af_store import GraphStore
s = GraphStore('/data')
for r in s.history()[:2]:
    g = s.load(r['name'], r['version'])
    print("name:", r['name'])
    print("  automations attr:", type(getattr(g, 'automations', 'MISSING')))
    autos = getattr(g, 'automations', None)
    if autos is not None:
        print("  autos type:", type(autos), "len:", len(autos) if hasattr(autos, '__len__') else '?')
        for a in autos:
            print("    auto:", type(a), a.id if hasattr(a, 'id') else '?')
            print("    has to_dict:", hasattr(a, 'to_dict'))

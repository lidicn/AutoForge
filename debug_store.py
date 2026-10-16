from autoforge.af_store import GraphStore
s = GraphStore('/data')
for r in s.history():
    g = s.load(r['name'], r['version'])
    print("type:", type(g))
    print("attrs:", [a for a in dir(g) if not a.startswith('_')][:10])
    try:
        items = list(g)
        print("list(g) len:", len(items))
    except Exception as e:
        print("list(g) err:", e)
    break

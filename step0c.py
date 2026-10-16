import inspect
import autoforge.af_conflict as ac
# 找所有类和方法
for name in dir(ac):
    obj = getattr(ac, name)
    if inspect.isclass(obj):
        print(f"=== class {name} ===")
        for m in dir(obj):
            if 'priority' in m.lower() or 'base' in m.lower():
                print(f"  method: {m}")
# 直接 grep 源码
src = inspect.getsource(ac)
for i, line in enumerate(src.split("\n")):
    if "priority" in line.lower() and ("meta" in line or "conf" in line or "get(" in line or "." in line):
        print(f"  L{i}: {line.strip()}")

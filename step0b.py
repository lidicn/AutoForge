import inspect
import autoforge.af_conflict as ac
src = inspect.getsource(ac._base_priority)
print("=== _base_priority 源码 ===")
print(src)

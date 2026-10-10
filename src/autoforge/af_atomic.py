"""原子落盘助手：随机 tmp 名 + `fsync` + `os.replace` + 目录 `fsync`（L0 内核底座）。

为什么单独成模块，而不是继续放在 `af_store`：两条都是实测的，不是猜的。

- `af_store → af_flock` 这条边本来就在（grimp 最短链 `af_store -> af_flock`），而 `af_flock` 在
  `scripts/check_imports.py` 的 L0 名单里。助手留在 `af_store`，`af_flock` 就只能从 L0 反向够 L1
  的存储层——那是字面的环，分层口径也更不该由内核去依赖存储层。
- `af_version` 自己写着硬约束「**不 import af_store 内部**」（IR 读写全走注入接口）。助手留在
  `af_store`，收口这 9 站就要先破它自己的约束。
- 其余几站（`af_premiere`/`af_scene`/`af_fire_recorder`/`af_predict`/`af_shadow`）此刻与 `af_store`
  **双向都无边**（上面同一张图逐条查过）。为了拿一个助手而新拉一条到存储层的边，是拿分层去换便利。

本模块只依赖标准库，谁都能 import 而不引入边——所以它能同时站在 L0 和这 9 站脚下。

三条判据来自 P1-18 那次修复，一条都不能少：
- 随机 tmp 名（`tempfile.mkstemp`）：固定名 `{path}.tmp` 在"同一文件存在第二个写者"时会互相截断。
- 写后 `fsync` 文件句柄：掉电/容器强杀后不留"名字合法、内容半截"的文件。
- `replace` 后尽力 `fsync` 目录：确保目录项本身持久化（Windows 上可能拿不到，尽力而为）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def refuse_when_shape_unreadable(path: Path | str, label: str, *, expect: str = "dict") -> None:
    """整档覆盖式落盘前的护栏：`path` 存在却读不出 `expect` 形状时**拒写**（fail-closed）。

    这一族的共同形态（第一期 F10/F11/F12/F13，第二期第二轮 AF2/AF3/AF4）：`_load` 在文件损坏
    时只会静默得到空容器，而写侧是**整档由内存快照重写**——于是一次正常写入就把盘上全部历史
    抹平，坏字节的现场也一起没了。护栏必须紧邻落盘调用（第十轮 W30：装在调用方不走的方法上
    等于没装）。文件不存在是正常首写场景，不拒。

    放在本模块而不是各自抄一份，是因为这里已经是 9 站共同脚下、且只依赖标准库（见模块 docstring）：
    加一个 `json` 不引入任何新的仓内边。
    """
    target = Path(path)
    if not target.is_file():
        return
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"{target.name} 已损坏，拒绝写入以保护已有{label}；"
            f"请修复或隔离该文件后重试（{type(exc).__name__}）"
        ) from exc
    want = {"dict": dict, "list": list}[expect]
    if not isinstance(data, want):
        raise ValueError(f"{target.name} 形状不是{expect}，拒绝写入以保护已有{label}")


def atomic_write_text(path: Path | str, text: str) -> None:
    """把 `text` 整份原子写进 `path`（自动建父目录）。

    失败时抛底层异常（`OSError`/`json` 序列化错误由调用方决定吞或报），本函数不做任何
    "静默当成写成功"的处理；残留的随机名 tmp 一定被清理。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass

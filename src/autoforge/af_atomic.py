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
import subprocess
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
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        # 文件在但读不出来（权限/IO）——按损坏处理：不能当空态放行，否则整档覆盖把现场抹掉。
        raise ValueError(
            f"{target.name} 已损坏，拒绝写入以保护已有{label}；"
            f"请修复或隔离该文件后重试（{type(exc).__name__}）"
        ) from exc
    # AF52：0 字节 / 纯空白 = 合法空态（该站点尚未初始化），放行初始化——**不是**"已损坏"。
    # 这一态必须窄：只在"剥掉空白后一字节都没有"时放行，绝不能把"有内容却读不出"一起放过去
    # （那正是 AF76 补丁自伤：空文件放行把损坏文件也放行，整档覆盖抹掉读不出的那份；
    #   AF77 要求损坏检测覆盖"能打开但 json.loads 失败"——下面的非空分支就是它的落点）。
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except ValueError as exc:
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


#: 收紧时**显式保留**的授权主体（除当前用户之外）。留 SYSTEM／Administrators 是给服务与运维留路，
#: 断掉的只是"从目录继承来的那一片可读可改"。
ACL_KEEP_PRINCIPALS = (r"NT AUTHORITY\SYSTEM", r"BUILTIN\Administrators")

#: `restrict_file_acl` 的全部返回档。调用方只认这几枚，读到名单外的值一律按"没收紧"处理。
ACL_STATES = ("not_windows", "no_identity", "restricted", "still_inherited", "refused", "failed")


def _current_principal() -> str:
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    user = (os.environ.get("USERNAME") or "").strip()
    if not user:
        return ""
    return f"{domain}\\{user}" if domain and domain != user else user


def restrict_file_acl(path: Path | str, *, runner=subprocess.run, platform_name: str | None = None) -> tuple[str, str]:
    r"""把一枚刚落盘的明文凭据文件收紧到"当前用户＋SYSTEM＋Administrators"，其余继承断开。

    为什么 `os.chmod(0o600)` 不够（安全第三轮审计 F-10，本机 2026-10-11 现读）：Windows 上 chmod
    只动只读位，**NTFS ACL 一字不变**——实测 `mkstemp` 出来的文件 `st_mode` 读回 `0o666`，`chmod`
    前后 `icacls` 输出逐字节相同。而 store 目录 `.forge/` 的继承 ACE 现读是
    `BUILTIN\Users:(I)(RX)` 加 `NT AUTHORITY\Authenticated Users:(I)(M)` ⇒ 落在里面的
    `credentials.json` 对本机任何已认证用户**可读可改**。限权只能由 ACL 完成，不是由 chmod 完成。

    口径三条（AF 不替部署方决定谁能读，也不许这一手把服务写挂）：
    - 只断**继承**，显式授权一律给回当前用户＋`ACL_KEEP_PRINCIPALS` 两档；
    - **尽力而为，绝不影响写入结果**：`icacls` 缺席、身份读不出、rc≠0、回读仍带继承——全部收成
      `ACL_STATES` 里的一档交调用方留痕，⛔ 不抛穿、不拒写（拒写会让一次凭据热重载整档失败）；
    - **POSIX 一律 no-op**：那边 `mkstemp` 天生 0600，`chmod` 就是真限权，无需第二个机制。

    `runner`／`platform_name` 两处接缝是为了"Windows 那五档在 Linux CI 上也能逐档判红"——
    桩子不许改口径，只补一个本地拿不到的入口。
    """
    target = Path(path)
    if (platform_name or os.name) != "nt":
        return "not_windows", "POSIX 上 0o600 就是真限权，本助手不碰盘"
    who = _current_principal()
    if not who:
        return "no_identity", "USERDOMAIN／USERNAME 都读不出：不猜该给谁留授权"
    argv = ["icacls", str(target), "/inheritance:r", "/grant:r", f"{who}:F"]
    for principal in ACL_KEEP_PRINCIPALS:
        argv = argv + ["/grant:r", f"{principal}:F"]
    try:
        done = runner(argv, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", f"icacls 没起来（{type(exc).__name__}）"
    if getattr(done, "returncode", 1) != 0:
        return "refused", f"icacls rc={getattr(done, 'returncode', None)}：{(done.stderr or '').strip()[:200]}"
    try:
        back = runner(["icacls", str(target)], capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return "failed", f"收紧发出去了，回读没起来（{type(exc).__name__}）"
    out = (getattr(back, "stdout", "") or "").strip()
    if getattr(back, "returncode", 1) != 0:
        return "failed", f"回读 rc={getattr(back, 'returncode', None)}"
    if "(I)" in out:
        return "still_inherited", f"回读仍见继承 ACE：{out[:200]}"
    return "restricted", f"{who} 与两档保留主体之外没有别的授权"

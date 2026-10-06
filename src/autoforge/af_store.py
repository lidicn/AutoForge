"""G6 版本审计与 diff。

三件事：
1. **版本化存储**（JSON 文件）：`GraphStore` 按 `name` 归档每次保存的 Graph 快照，
   版本号自增、带 `saved_at` / `note`，可回读任意版本。
2. **置信度持久化**：G4 的 `ConfidenceStore` 原型期全内存，此处落盘（values + samples）。
3. **可寻址 + diff**：`automation_id:node_id` 寻址（`find_node`）；
   `diff_graphs(old, new)` 做节点/边/参数级差异，并渲染人类可读结果（`forge diff`）。

设计边界：全部 JSON 文件、无外部依赖；实例上下文沿用 `Instance.to_dict()`（可序列化红线）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .af_atomic import atomic_write_text
from .af_audit import record_conflict
from .af_conf import ConfidenceStore
from .af_flock import FileLock, owner_id
from .af_instance import Instance, InstanceContext
from .af_ir import Graph, Node, load_graph
from .af_ir.models import IRValidationError, validate_automation

logger = logging.getLogger("autoforge.store")

__all__ = [
    "GraphStore",
    "GraphDiff",
    "WriteConflictError",
    "ArchiveNameConflict",
    "diff_graphs",
    "find_node",
    "dump_confidence",
    "load_confidence",
    "instance_record",
    "restore_context",
]

DEFAULT_STORE_ROOT = ".forge"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _graph_raw(graph: Graph) -> dict[str, Any]:
    """把 Graph 转回 IR 原始 JSON（统一用 `{"automations": [...]}` 容器形态）。"""
    return {"automations": [dict(auto.raw) for auto in graph]}


def _bundle_checksum(bundle: Mapping[str, Any]) -> str:
    """对 bundle 的规范化 JSON（排除 checksum 字段本身）做 SHA256。"""
    data = {k: v for k, v in bundle.items() if k != "checksum"}
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_graph_dict(graph_dict: Any) -> None:
    """按 IR Schema 校验 graph dict（`{"automations":[...]}` 或单条 automation）。"""
    if not isinstance(graph_dict, dict):
        raise IRValidationError("graph 必须是对象")
    items = graph_dict.get("automations") if "automations" in graph_dict else [graph_dict]
    if not isinstance(items, list):
        raise IRValidationError("automations 必须是数组")
    for auto in items:
        validate_automation(auto)


# ─────────────────────────────────────────────────────────────────────
# 版本化存储
# ─────────────────────────────────────────────────────────────────────


class WriteConflictError(Exception):
    """v0.9.0 跨进程写入版本冲突：`expect_version` 与当前最新版本不符（拒绝覆盖写入）。"""


class ArchiveNameConflict(Exception):
    """两个不同归档名折叠进同一目录时拒绝写入/删除（v1.5.x，审计 §四「路径处理」那一项）。

    `_dir()` 的字符白名单是**多对一**映射：`"客厅 v2"` 与 `"客厅_v2"` 都是 `客厅_v2`，
    `"###"`、`"@@@"`、`""` 都落到兜底的 `graph`。穿越这一半在 HEAD 上盘过、不成立（保留集里没有
    `/` 与 `\\`）；成立的是这一半——两个不同名字共享同一个版本目录后，写是静默互相覆盖
    （新版本记录里的 `name` 还是上一个名字，别名就此隐身），而 `overwrite` 导入与
    `DELETE /api/automations/{name}` 走的是删除，删 A 连带把 B 的整条归档 rmtree 掉。
    ⇒ 判据落在"名字与目录的归属对不上"这一步，抛具名异常而不是继续写。
    """


def _alias_conflict(name: str, owner: str, dir_name: str) -> str:
    return (
        f"归档名 {name!r} 与 {owner!r} 折叠到同一目录 {dir_name!r}"
        f"（`_dir` 的字符白名单是多对一映射）；拒绝写入以免两条归档互相覆盖——"
        f"请改用不会被折叠的名字"
    )


def append_jsonl(path: Path, obj: Any) -> None:
    """append-only 追加一行 JSON（单行写入不撕裂，供 telemetry/error_knowledge 复用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_jsonl_bounded(path: Path, limit: int) -> list[dict[str, Any]]:
    """读 JSONL，最多返回**最后** `limit` 行（有界内存）；坏行/非对象行跳过。"""
    if limit <= 0 or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


class GraphStore:
    """按名字归档 Graph 版本的 JSON 文件存储。

    目录结构：`{root}/{name}/v{version}.json`，文件内容：
    `{"name":..., "version":..., "saved_at":..., "note":..., "writer":..., "graph": {...}}`。

    v0.9.0 跨进程安全：版本号计算 + 写入在 `FileLock`（`{name}/.lock`）内完成，
    文件本体原子替换；`save(expect_version=...)` 提供乐观并发检测（冲突落审计日志并抛
    `WriteConflictError`）。
    """

    def __init__(self, root: str | Path = DEFAULT_STORE_ROOT, lock_timeout: float = 10.0):
        self.root = Path(root)
        self.lock_timeout = float(lock_timeout)

    def _dir(self, name: str) -> Path:
        # P1-18 修复：白名单只允许字母数字、-、_，不允许 "."（防止 ../ 目录逃逸）
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "graph"
        return self.root / safe

    def _dir_owner(self, name: str) -> str | None:
        """该归档目录**最新**记录里的 `name`；目录不存在或没有记录时 None。"""
        directory = self._dir(name)
        if not directory.is_dir():
            return None
        target = self.latest(name)
        if target is None:
            return None
        try:
            record = json.loads((directory / f"v{target}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        owner = record.get("name") if isinstance(record, dict) else None
        return owner if isinstance(owner, str) else None

    def _assert_name_owns_dir(self, name: str) -> None:
        """写入前的归属核对：目录里最新记录若属于另一个名字，就是别名撞车，拒绝继续写。

        只读**最新那一条**记录（一次 `json.loads`），不扫全目录——按版本数线性放大读是另一条账
        （第五轮审计判的正是"只增不减"那一族）。别名一旦出现，最新记录必然带着上一个主人的名字。
        """
        owner = self._dir_owner(name)
        if owner is not None and owner != name:
            raise ArchiveNameConflict(_alias_conflict(name, owner, self._dir(name).name))

    def assert_deletable(self, name: str) -> None:
        """删除前的归属核对：目录里**每一条**记录都得属于这个名字。

        比写入那条严格（写入只看最新一条）：删除是不可逆的，而 `DELETE /api/automations/{name}`
        与 `overwrite` 导入会 `rmtree`/逐条 unlink 整个目录——只要目录里混进过别人的版本记录，
        删下去就是连带销毁另一条归档。
        """
        directory = self._dir(name)
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("v*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 坏记录由加载路径各自处置，这里不为它放行别名
            if isinstance(record, dict) and isinstance(record.get("name"), str) \
                    and record["name"] != name:
                raise ArchiveNameConflict(
                    f"归档目录 {directory.name!r} 里混有 {record['name']!r} 的版本记录，"
                    f"拒绝按 {name!r} 删除（删除不可逆，会连带销毁对方整条归档）"
                )

    @property
    def conflicts_path(self) -> Path:
        """跨进程共享的 write_conflict JSONL 日志。"""
        return self.root / "write_conflicts.jsonl"

    def versions(self, name: str) -> list[int]:
        """列出某名字已保存的版本号（升序）。"""
        directory = self._dir(name)
        if not directory.is_dir():
            return []
        out: list[int] = []
        for path in directory.glob("v*.json"):
            try:
                out.append(int(path.stem[1:]))
            except ValueError:
                continue
        return sorted(out)

    def latest(self, name: str) -> int | None:
        versions = self.versions(name)
        return versions[-1] if versions else None

    def save(
        self,
        graph: Graph,
        name: str,
        note: str = "",
        tags: list[str] | None = None,
        expect_version: int | None = None,
        owner: str = "",
    ) -> int:
        """保存一个 Graph 版本，返回新版本号（自增）。

        `tags` 非空时一并写入该名字的标签元数据（v0.6.0）。
        `expect_version` 非空时校验当前最新版本（乐观锁，v0.9.0）：
        不匹配 → 写入 `write_conflicts.jsonl` 审计并抛 `WriteConflictError`。
        `owner`（v1.3.0）：**归档归属**——创建/持有该版本的主体（多 agent 接入后用于
        所有权隔离）。与 `writer`（落盘进程 id）不同，这是「谁建的」。
        """
        self._assert_name_owns_dir(name)
        directory = self._dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        with FileLock(directory / ".lock", timeout=self.lock_timeout):
            latest = self.latest(name)
            if expect_version is not None and latest != expect_version:
                record_conflict(
                    self.conflicts_path,
                    name=name,
                    expected=expect_version,
                    actual=latest,
                    writer=owner_id(),
                    note="save(expect_version) 版本冲突",
                )
                raise WriteConflictError(
                    f"{name!r} 版本冲突：期望 v{expect_version}，实际最新 v{latest}"
                    f"（冲突已记入 {self.conflicts_path.name}）"
                )
            version = (latest or 0) + 1
            record = {
                "name": name,
                "version": version,
                "saved_at": _utc_now_iso(),
                "note": note,
                "writer": owner_id(),
                "owner": owner,  # v1.3.0 归档归属（谁建的）
                "graph": _graph_raw(graph),
            }
            atomic_write_text(
                directory / f"v{version}.json",
                json.dumps(record, ensure_ascii=False, indent=2),
            )
        if tags is not None:
            self.set_tags(name, tags)
        return version

    def resave_raw(self, name: str, mutate: Any) -> int:
        """加载最新记录、就地改 `graph` 子字典、以新版本落盘（"只翻一个旗子"的写）。

        为什么在 store 层而不是调用方：`.lock` 的纪律、版本号与原子写必须和写盘在同一处。
        调用方手抄这一段（旧形状在 `af_api._resave_graph_raw`）会同时丢掉锁与随机 tmp 名——
        两条并发启停算出同一个 `v{N}`、写同一个固定名 `v{N}.tmp`，最后一次 `os.replace`
        装上的就是被交错过的半截 JSON，而它带着合法的名字躺在目录里等着当最新记录读。
        """
        directory = self._dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        with FileLock(directory / ".lock", timeout=self.lock_timeout):
            rec = self.load_record(name)
            # 别名核对在锁内、且**复用刚读出来的那条记录**——resave_raw 本来就要 load_record，
            # 归属检查这里一分钱 IO 都不多花（save 那侧才需要额外读）。
            owner = rec.get("name")
            if isinstance(owner, str) and owner != name:
                raise ArchiveNameConflict(_alias_conflict(name, owner, directory.name))
            mutate(rec.setdefault("graph", {}))
            version = (self.latest(name) or 0) + 1
            rec["version"] = version
            rec["saved_at"] = _utc_now_iso()
            atomic_write_text(
                directory / f"v{version}.json",
                json.dumps(rec, ensure_ascii=False, indent=2),
            )
        return version

    def load_record(self, name: str, version: int | None = None) -> dict[str, Any]:
        """读取某版本记录（缺省取最新）；不存在抛 FileNotFoundError。"""
        target = version if version is not None else self.latest(name)
        if target is None:
            raise FileNotFoundError(f"未找到 {name!r} 的任何版本")
        path = self._dir(name) / f"v{target}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def load(self, name: str, version: int | None = None) -> Graph:
        """读取某版本并重建 Graph。"""
        return load_graph(self.load_record(name, version)["graph"])

    def history(self) -> list[dict[str, Any]]:
        """全部归档记录索引（不含 graph 体），按保存时间升序。"""
        out: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return out
        for path in sorted(self.root.glob("*/v*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            out.append(
                {
                    "name": record.get("name", path.parent.name),
                    "version": record.get("version"),
                    "saved_at": record.get("saved_at", ""),
                    "note": record.get("note", ""),
                    "owner": record.get("owner", ""),  # v1.3.0 归档归属（所有权隔离/列表展示用）
                }
            )
        return out

    # ── 标签元数据（v0.6.0）────────────────────────────────────────────
    def _tags_path(self) -> Path:
        return self.root / "tags.json"

    def _read_tags(self) -> dict[str, list[str]]:
        try:
            return json.loads(self._tags_path().read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            return {}

    def _write_tags(self, data: dict[str, list[str]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # v0.9.0：read-modify-write 是跨进程竞态窗口，整段拿锁 + 原子替换
        with FileLock(self.root / "tags.lock", timeout=self.lock_timeout):
            atomic_write_text(
                self._tags_path(),
                json.dumps(data, ensure_ascii=False, indent=2),
            )

    def set_tags(self, name: str, tags: list[str]) -> None:
        """设置某归档名字的标签（覆盖式）；空列表 = 清空。

        v0.9.0：read-modify-write 整体在锁内完成（读在锁外会有丢失更新竞态）。
        """
        clean = sorted({str(t).strip() for t in (tags or []) if str(t).strip()})
        with FileLock(self.root / "tags.lock", timeout=self.lock_timeout):
            data = self._read_tags()
            if clean:
                data[name] = clean
            else:
                data.pop(name, None)
            atomic_write_text(
                self._tags_path(), json.dumps(data, ensure_ascii=False, indent=2)
            )

    def get_tags(self, name: str) -> list[str]:
        return list(self._read_tags().get(name, []))

    def all_tags(self) -> dict[str, list[str]]:
        return self._read_tags()

    # ── 已归档名字（v0.7.0）────────────────────────────────────────────
    def names(self) -> list[str]:
        """全部已归档名字（去重、保持出现顺序）。"""
        seen: list[str] = []
        for record in self.history():
            name = str(record.get("name", ""))
            if name and name not in seen:
                seen.append(name)
        return seen

    # ── 模板导出与备份恢复（v0.7.0）────────────────────────────────────
    def export_bundle(self) -> dict[str, Any]:
        """导出整个 store 为可携带 bundle（含 tags，v0.6.0 依赖）。

        bundle 形态（自描述 JSON）：
            {"format":"autoforge-bundle","version":1,"exported_at":...,"checksum":<sha256>,
             "entries":[{"name","tags","versions":[{"version","saved_at","note","graph"}]}, ...],
             "confs":[{"name","payload"}, ...]}
        `checksum` 为对 entries/confs 的规范化 JSON 做 SHA256（不含 checksum 字段本身），
        导入时复算并比对，可侦测传输/存储损坏。
        """
        entries: list[dict[str, Any]] = []
        for name in self.names():
            versions: list[dict[str, Any]] = []
            for v in self.versions(name):
                rec = self.load_record(name, v)
                versions.append(
                    {
                        "version": rec["version"],
                        "saved_at": rec.get("saved_at", ""),
                        "note": rec.get("note", ""),
                        "graph": rec["graph"],
                    }
                )
            entries.append({"name": name, "tags": self.get_tags(name), "versions": versions})
        confs: list[dict[str, Any]] = []
        for name in self.names():
            try:
                payload = json.loads(
                    (self.root / f"{self._dir(name).name}.conf.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            confs.append({"name": name, "payload": payload})
        bundle: dict[str, Any] = {
            "format": "autoforge-bundle",
            "version": 1,
            "exported_at": _utc_now_iso(),
            "entries": entries,
            "confs": confs,
        }
        bundle["checksum"] = _bundle_checksum(bundle)
        return bundle

    def save_version_raw(
        self,
        name: str,
        graph_dict: Mapping[str, Any],
        version: int,
        saved_at: str,
        note: str,
    ) -> int:
        """直接写入某版本记录（不经 Graph round-trip，保证 raw 级一致）。

        P1-18 修复：写入在 FileLock 内完成，防止并发写同一归档时版本号冲突。
        """
        self._assert_name_owns_dir(name)
        directory = self._dir(name)
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "name": name,
            "version": version,
            "saved_at": saved_at,
            "note": note,
            "writer": owner_id(),
            "graph": dict(graph_dict),
        }
        with FileLock(directory / ".lock", timeout=self.lock_timeout):
            atomic_write_text(
                directory / f"v{version}.json", json.dumps(record, ensure_ascii=False, indent=2)
            )
        return version

    def save_conf_raw(self, name: str, payload: Mapping[str, Any]) -> Path:
        """直接写入置信度快照 payload（不经 ConfidenceStore round-trip）。

        conf 文件名同样过 `_dir()`，所以别名会写进同一个 `.conf.json`——这里也必须先核对归属。
        """
        self._assert_name_owns_dir(name)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self._dir(name).name}.conf.json"
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def _delete_archive(self, name: str) -> None:
        """彻底删除某归档名（版本目录 + 置信度文件 + 标签侧车），供 overwrite 导入。

        标签那半必须**整段在 `tags.lock` 内**读-改-写：读在锁外等于用一份可能过期的
        全量字典覆盖别人刚写进去的标签（`set_tags` 已经这样收口，见它的 docstring）。
        这里不套 `self._write_tags()`——它自己会再拿一次同一把锁，而 `FileLock` 不做
        重入引用计数，嵌套会由内层 `release()` 把外层的锁放开。
        """
        self.assert_deletable(name)
        directory = self._dir(name)
        if directory.is_dir():
            for p in directory.glob("v*.json"):
                p.unlink()
            try:
                directory.rmdir()
            except OSError:
                pass
        conf_path = self.root / f"{directory.name}.conf.json"
        if conf_path.exists():
            conf_path.unlink()
        with FileLock(self.root / "tags.lock", timeout=self.lock_timeout):
            data = self._read_tags()
            if name in data:
                data.pop(name, None)
                atomic_write_text(
                    self._tags_path(), json.dumps(data, ensure_ascii=False, indent=2)
                )

    # ── overwrite 导入的可回滚让位（新增审计 BUG-11）────────────────────
    def _stash_archive(self, name: str) -> Path | None:
        """把旧归档（版本目录 + conf 边车）改名让位，返回备份目录；无归档返回 None。

        `overwrite` 导入用它替代「先 `_delete_archive()` 再写」：删除不可逆，一旦
        后续写新版本失败，旧归档就永久没了。改成 rename 让位后，失败可原样回滚。
        调用前必须已通过 `assert_deletable()`（归属核对不能因为换实现而丢）。
        """
        directory = self._dir(name)
        conf_path = self.root / f"{directory.name}.conf.json"
        if not directory.is_dir() and not conf_path.exists():
            return None
        backup = self.root / f"{directory.name}.__ovbak__"
        if backup.exists():  # 上一轮未清理干净的残留，先清掉再让位
            shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir(parents=True, exist_ok=True)
        moved = False
        if directory.is_dir():
            # fixed-tmp: exempt(overwrite 回滚用的归档「让位」，不是 tmp→正名的原子替换：目标名每次唯一且残留先清，崩溃只留可识别的备份目录，不会把正名写成半截)
            directory.rename(backup / "versions")
            moved = True
        if conf_path.exists():
            conf_path.rename(backup / "conf.json")
            moved = True
        if not moved:
            shutil.rmtree(backup, ignore_errors=True)
            return None
        return backup

    def _unstash_archive(self, name: str, backup: Path | None) -> None:
        """写新失败：把备份改回原名，旧归档回到原位。"""
        if backup is None or not backup.is_dir():
            return
        directory = self._dir(name)
        conf_path = self.root / f"{directory.name}.conf.json"
        try:
            if (backup / "versions").is_dir():
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)
                # fixed-tmp: exempt(回滚让位的逆操作，把备份改回正名；理由同 _stash_archive)
                (backup / "versions").rename(directory)
            if (backup / "conf.json").is_file():
                if conf_path.exists():
                    conf_path.unlink()
                (backup / "conf.json").rename(conf_path)
        except OSError:
            # 恢复失败必须留痕：备份目录还在，可人工捞回
            logger.error(
                "overwrite 导入失败后恢复旧归档出错，备份仍保留在 %s：%s", backup, name, exc_info=True
            )
            return
        shutil.rmtree(backup, ignore_errors=True)

    def _drop_stash(self, backup: Path | None) -> None:
        """写新成功：丢弃备份（此时旧归档已不再需要）。"""
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

    def _unique_name(self, base: str) -> str:
        """为 rename 策略找一个未占用名字：`base_import` / `base_import2` …"""
        candidate = f"{base}_import"
        i = 1
        while self.versions(candidate):
            i += 1
            candidate = f"{base}_import{i}"
        return candidate

    def import_bundle(self, bundle: Mapping[str, Any], strategy: str = "skip") -> dict[str, Any]:
        """导入 bundle（v0.7.0）。

        - 校验 `format == autoforge-bundle` 与 `checksum`（损坏即拒）；
        - 每个版本导入前经 IR Schema 校验（不通过的版本记入 errors，不落地）；
        - 冲突策略（目标 store 已存在同名归档时）：
            `skip`      ：跳过该归档（默认，最安全）；
            `overwrite` ：先删除已存在归档，再导入（版本号重置为原 v1…）；
            `rename`    ：导入到 `name_import` / `name_import2` … 新名字。
        返回报告：{imported, skipped, renamed, errors}。
        """
        if not isinstance(bundle, dict) or bundle.get("format") != "autoforge-bundle":
            raise ValueError("不是合法的 AutoForge bundle（缺少 format=autoforge-bundle）")
        expected = bundle.get("checksum")
        if expected and _bundle_checksum(bundle) != str(expected):
            raise ValueError("bundle 校验和不匹配（可能已损坏）")
        if strategy not in ("skip", "overwrite", "rename"):
            raise ValueError(f"未知冲突策略：{strategy!r}（skip/overwrite/rename）")

        report: dict[str, Any] = {
            "ok": True,
            "imported": [],
            "skipped": [],
            "renamed": {},
            "errors": [],
        }
        for entry in bundle.get("entries", []):
            name = str(entry.get("name", ""))
            if not name:
                report["errors"].append({"name": name, "error": "bundle 条目缺少 name"})
                continue
            target = name
            if self.versions(name):
                if strategy == "skip":
                    report["skipped"].append(name)
                    continue
                elif strategy == "rename":
                    target = self._unique_name(name)
                    report["renamed"][name] = target
                # overwrite：P1-17 修复——先校验全部版本，通过后再删除旧归档
            # P1-17：先校验所有版本，非法版本跳过，合法版本仍写入
            validated = []
            entry_has_error = False
            for ver in entry.get("versions", []):
                graph_dict = ver.get("graph")
                try:
                    _validate_graph_dict(graph_dict)
                    validated.append((ver, graph_dict))
                except IRValidationError as exc:
                    report["errors"].append(
                        {"name": target, "version": ver.get("version"), "error": str(exc)}
                    )
                    entry_has_error = True
            # overwrite 策略：全部版本校验通过后才动旧归档，且**改名让位而非删除**——
            # 写新版本中途失败时旧归档可原样回滚，不留下不可逆的空洞
            # （新增审计 BUG-11；P1-17 的「先校验后删」只挡住了校验失败那一半）。
            stash: Path | None = None
            if self.versions(name) and strategy == "overwrite" and not entry_has_error:
                self.assert_deletable(name)  # 归属核对不能因为换实现而丢
                stash = self._stash_archive(name)
            try:
                # 合法版本仍然写入（非法版本已被跳过）
                for ver, graph_dict in validated:
                    self.save_version_raw(
                        target,
                        graph_dict,
                        int(ver.get("version", 1)),
                        ver.get("saved_at", ""),
                        ver.get("note", ""),
                    )
                if entry.get("tags"):
                    self.set_tags(target, list(entry["tags"]))
            except Exception:
                self._unstash_archive(name, stash)
                raise
            self._drop_stash(stash)
            report["imported"].append(target)

        # 置信度快照：跟随 rename 映射到新名字；skip 的归档不恢复其 conf
        renamed = report["renamed"]
        skipped = set(report["skipped"])
        for conf in bundle.get("confs", []):
            cname = str(conf.get("name", ""))
            if not cname:
                continue
            target = renamed.get(cname, cname)
            if cname in skipped:
                continue
            if target != cname and not self.versions(target):
                continue
            self.save_conf_raw(target, conf.get("payload", {}))
        # P1-17 修复：ok 由 errors 驱动（有错误即 ok=False，之前永为 True）
        report["ok"] = not report["errors"]
        return report

    # ── 置信度持久化（G4 ConfidenceStore）──────────────────────────────
    def save_conf(self, store: ConfidenceStore, name: str, note: str = "") -> Path:
        """把 ConfidenceStore 落盘到 `{root}/{name}.conf.json`。"""
        self._assert_name_owns_dir(name)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self._dir(name).name}.conf.json"
        payload = {"name": name, "saved_at": _utc_now_iso(), "note": note, "conf": dump_confidence(store)}
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
        return path

    def load_conf(self, name: str) -> ConfidenceStore:
        path = self.root / f"{self._dir(name).name}.conf.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return load_confidence(payload["conf"])


# ─────────────────────────────────────────────────────────────────────
# 置信度序列化
# ─────────────────────────────────────────────────────────────────────


def dump_confidence(store: ConfidenceStore) -> dict[str, Any]:
    """ConfidenceStore → 可 JSON 化 dict（samples 的 tuple 转 list）。"""
    return {
        "values": dict(store.values),
        "samples": {k: [[kind, value] for kind, value in v] for k, v in store.samples.items()},
    }


def load_confidence(data: Mapping[str, Any]) -> ConfidenceStore:
    """dict → ConfidenceStore（samples 还原为 tuple）。"""
    values = {str(k): float(v) for k, v in (data.get("values") or {}).items()}
    samples: dict[str, list[tuple[str, float]]] = {}
    for key, items in (data.get("samples") or {}).items():
        samples[str(key)] = [(str(kind), float(value)) for kind, value in items]
    return ConfidenceStore(values=values, samples=samples)


# ─────────────────────────────────────────────────────────────────────
# 实例快照 / 可回放
# ─────────────────────────────────────────────────────────────────────


def instance_record(instance: Instance) -> dict[str, Any]:
    """实例的持久化形态（沿用 ctx.to_dict，保证可 JSON 化红线）。"""
    return instance.to_dict()


def restore_context(data: Mapping[str, Any]) -> InstanceContext:
    """从持久化形态还原 `InstanceContext`（用于审计回放/检查）。"""
    return InstanceContext(
        instance_id=str(data.get("instance_id", "")),
        automation_id=str(data.get("automation_id", "")),
        version=int(data.get("version", 1)),
        mode=str(data.get("mode", "single")),
        current_node=str(data.get("current_node", "")),
        state=str(data.get("state", "created")),
        snapshot=dict(data.get("snapshot") or {}),
        vars=dict(data.get("vars") or {}),
        context=dict(data.get("context") or {}),
        timers=list(data.get("timers") or []),
        created_at=str(data.get("created_at", "")),
        trace=list(data.get("trace") or []),
        trace_dropped=int(data.get("trace_dropped", 0)),
        segments=int(data.get("segments", 0)),
        steps=int(data.get("steps", 0)),
        cap_warned=bool(data.get("cap_warned", False)),
        owner=str(data.get("owner", "")),
    )


# ─────────────────────────────────────────────────────────────────────
# 可寻址
# ─────────────────────────────────────────────────────────────────────


def find_node(graph: Graph, address: str) -> Node:
    """按 `automation_id:node_id` 寻址节点（也接受纯 `node_id` 的唯一匹配）。"""
    if ":" in address:
        automation_id, _, node_id = address.partition(":")
        automation = graph.get(automation_id)
        return automation.node(node_id)
    matches = [auto.node(address) for auto in graph if address in auto.nodes]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(f"未找到节点：{address!r}")
    raise KeyError(f"节点 id {address!r} 在多条自动化中重复，请用 automation_id:node_id 寻址")


# ─────────────────────────────────────────────────────────────────────
# Graph diff
# ─────────────────────────────────────────────────────────────────────


@dataclass
class GraphDiff:
    """两份 Graph 的差异（节点/边/参数/元信息级）。"""

    added_automations: list[str] = field(default_factory=list)
    removed_automations: list[str] = field(default_factory=list)
    added_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    changed_nodes: list[tuple[str, list[str]]] = field(default_factory=list)
    added_edges: list[str] = field(default_factory=list)
    removed_edges: list[str] = field(default_factory=list)
    meta_changes: list[tuple[str, str, Any, Any]] = field(default_factory=list)
    #: v1.3.0：节点 id 重命名（`(旧地址, 新地址)`）——靠签名兜底配对识别，
    #: 否则 Agent 重写 IR 改个 id 就会被误报成「删了旧的 + 加了新的」。
    renamed_nodes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.added_automations,
                self.removed_automations,
                self.added_nodes,
                self.removed_nodes,
                self.changed_nodes,
                self.added_edges,
                self.removed_edges,
                self.meta_changes,
                self.renamed_nodes,
            )
        )

    def render(self) -> str:
        if self.empty:
            return "无差异"
        lines: list[str] = []
        for auto_id in self.added_automations:
            lines.append(f"+ 自动化 {auto_id}")
        for auto_id in self.removed_automations:
            lines.append(f"- 自动化 {auto_id}")
        for address in self.added_nodes:
            lines.append(f"+ 节点 {address}")
        for address in self.removed_nodes:
            lines.append(f"- 节点 {address}")
        for old_addr, new_addr in self.renamed_nodes:
            lines.append(f"≈ 节点 {old_addr} → {new_addr}（id 变更，按签名配对）")
        for address, fields in self.changed_nodes:
            lines.append(f"~ 节点 {address}：{', '.join(fields)}")
        for edge in self.added_edges:
            lines.append(f"+ 边 {edge}")
        for edge in self.removed_edges:
            lines.append(f"- 边 {edge}")
        for auto_id, field_, old, new in self.meta_changes:
            lines.append(f"~ {auto_id}.{field_}：{old!r} → {new!r}")
        return "\n".join(lines)


_META_FIELDS = ("mode", "version", "confidence", "snapshot", "persist", "meta")


def _node_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k != "id"}


def _edge_key(edge: Any) -> tuple[str, str, str]:
    return (edge.from_, edge.to, edge.kind)


def _node_sig(node: Any) -> tuple[str, str, str]:
    """节点签名 —— **v1.3.0**：id 变了但其实是同一个节点时的兜底配对依据。

    痛点：AutoForge 的 node id（`a1` / `i1` / `d1`）是 **Agent 随手起的**。
    Agent 重写同一条自动化时把 `d1` 起成 `do1`，纯 id 比对就会报「删了 d1、加了 do1」——
    人审时看到一堆红绿，无法判断真实变更（参考前身 autoflow `flow_diff` 的做法）。

    签名 = `(kind, action|prompt|var, 主 entity_id)`。签名全空的节点（如纯 `pass`）
    不参与兜底，避免把两个不同用途的占位节点误配成对。
    """
    entities = sorted(node.target_entities()) if hasattr(node, "target_entities") else []
    if not entities and getattr(node, "trigger", None) is not None:
        entities = sorted(node.trigger.entity_ids())
    actionish = (
        str(getattr(node, "action", "") or "")
        or str(getattr(node, "prompt", "") or "")
        or str(getattr(node, "var", "") or "")
    )
    return (str(getattr(node, "kind", "")), actionish, entities[0] if entities else "")


def _pair_nodes(
    old_nodes: Mapping[str, Any],
    old_only: list[str],
    new_nodes: Mapping[str, Any],
    new_only: list[str],
) -> tuple[list[tuple[str, str]], set[str]]:
    """把「只在一边的节点」按签名配对，识别 **id 重命名**。

    返回 `(renames, taken)`：`renames` 为 `(旧 id, 新 id)`，`taken` 为已配对的新 id。

    **两轮，且都有防误配约束**：
    - 一轮：只配**非空签名**（`kind` + actionish + 主 entity_id 至少有一个非空）——
      这是主要场景（`d1` → `do1`），签名足够区分，可放心贪心；
    - 二轮：对剩余的按签名做 **1:1 配对**（含空签名如 `pass`）——
      **只有某签名在两侧各恰好剩 1 个时才配**，两个 `pass` 仍是 2:2，不会互配。
      这一轮让整条链路的边也能归一化（否则改名后 `d1→p1` 与 `do1→end1` 仍会被报成边增删）。
    """
    renames: list[tuple[str, str]] = []
    taken: set[str] = set()
    if not (old_only and new_only):
        return renames, taken

    def sig_new_map(ids: list[str]) -> dict[tuple[str, str, str], list[str]]:
        out: dict[tuple[str, str, str], list[str]] = {}
        for nid in ids:
            out.setdefault(_node_sig(new_nodes[nid]), []).append(nid)
        return out

    # 一轮：非空签名优先
    sig_new = sig_new_map(new_only)
    for oid in old_only:
        sig = _node_sig(old_nodes[oid])
        if not (sig[1] or sig[2]):
            continue
        for nid in sig_new.get(sig, ()):
            if nid in taken:
                continue
            renames.append((oid, nid))
            taken.add(nid)
            break

    # 二轮：剩余按签名 1:1 配对（含空签名）
    rest_old = [n for n in old_only if n not in {o for o, _ in renames}]
    rest_new = [n for n in new_only if n not in taken]
    sig_rest = sig_new_map(rest_new)
    for oid in rest_old:
        candidates = [n for n in sig_rest.get(_node_sig(old_nodes[oid]), ()) if n not in taken]
        if len(candidates) == 1:  # 仅 1:1 才配，防把同类节点互配
            renames.append((oid, candidates[0]))
            taken.add(candidates[0])

    return renames, taken


def diff_graphs(old: Graph, new: Graph) -> GraphDiff:
    """对比两份 Graph：节点/边/参数/元信息级差异。

    v1.3.0：当同一自动化内出现「只在一边的节点」时，先按 `_node_sig` 做**签名兜底配对**，
    把 id 重命名识别为 `renamed_nodes`（并据此归一化的边），而不是误报成删+加。
    """
    diff = GraphDiff()
    old_map = {a.id: a for a in old}
    new_map = {a.id: a for a in new}

    diff.added_automations = sorted(set(new_map) - set(old_map))
    diff.removed_automations = sorted(set(old_map) - set(new_map))

    for auto_id in sorted(set(old_map) & set(new_map)):
        old_auto, new_auto = old_map[auto_id], new_map[auto_id]

        for field_ in _META_FIELDS:
            old_value, new_value = getattr(old_auto, field_), getattr(new_auto, field_)
            if old_value != new_value:
                diff.meta_changes.append((auto_id, field_, old_value, new_value))

        old_nodes = {n.id: n for n in old_auto.nodes.values()}
        new_nodes = {n.id: n for n in new_auto.nodes.values()}

        # ── v1.3.0 签名兜底配对 ────────────────────────────────────────
        old_only = sorted(set(old_nodes) - set(new_nodes))
        new_only = sorted(set(new_nodes) - set(old_nodes))
        renames, taken = _pair_nodes(old_nodes, old_only, new_nodes, new_only)
        renamed_old = {o for o, _ in renames}
        diff.renamed_nodes.extend(
            (f"{auto_id}:{o}", f"{auto_id}:{n}") for o, n in renames
        )

        for node_id in sorted(set(new_only) - taken):
            diff.added_nodes.append(f"{auto_id}:{node_id}")
        for node_id in sorted(set(old_only) - renamed_old):
            diff.removed_nodes.append(f"{auto_id}:{node_id}")

        # 共用 id 的节点：按 id 比字段
        for node_id in sorted(set(old_nodes) & set(new_nodes)):
            _collect_node_change(diff, auto_id, node_id, old_nodes[node_id], new_nodes[node_id])
        # 重命名配上的节点：按新 id 比字段（id 差异已在 renamed_nodes 里体现）
        for oid, nid in renames:
            _collect_node_change(diff, auto_id, nid, old_nodes[oid], new_nodes[nid])

        # 边：把重命名映射施加上，避免整条边被误报成删+加
        id_map = {o: n for o, n in renames}

        def _map(nid: str) -> str:
            return id_map.get(nid, nid)

        old_edges = {(_map(e.from_), _map(e.to), e.kind) for e in old_auto.edges}
        new_edges = {_edge_key(e) for e in new_auto.edges}
        for from_, to, kind in sorted(new_edges - old_edges):
            diff.added_edges.append(f"{auto_id}:{from_}->{to}:{kind}")
        for from_, to, kind in sorted(old_edges - new_edges):
            diff.removed_edges.append(f"{auto_id}:{from_}->{to}:{kind}")

    return diff


def _collect_node_change(
    diff: GraphDiff, auto_id: str, address_id: str, old_node: Any, new_node: Any
) -> None:
    """比对同（或配对上的）节点的字段差异，追加到 `changed_nodes`。"""
    old_fields = _node_fields(old_node.raw)
    new_fields = _node_fields(new_node.raw)
    changed = sorted(
        key
        for key in set(old_fields) | set(new_fields)
        if old_fields.get(key) != new_fields.get(key) and key != "id"
    )
    if changed:
        diff.changed_nodes.append((f"{auto_id}:{address_id}", changed))

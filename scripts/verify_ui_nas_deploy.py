#!/usr/bin/env python3
"""NAS 上「用户视角 UI（``/mimo/``）」的部署现状核对——**只读**，一条写动作都不发。

起因：交付链的构建半边在 CI（``.github/workflows/ci.yml`` 的 ``ui-typecheck-build``），
产物→NAS、起容器、验收这三环一直靠现场手敲记忆里的命令形状。本脚本把**核对**那一格固化下来，
部署本身（``build`` → ``up -d --force-recreate``）仍要 owner 点头——容器 churn 是
``E:\\NAS\\开发规范.md`` §0 坐实的整机冻结触发路径，不由脚本自动执行。

判的八条腿，各自单独可红：

- **L1 通道**：SSH 只读探活。读不到＝``exit 2``（射程断不是"没问题"）。
- **L2 端口绑**：``docker ps`` 现读，AF 系容器凡绑到 ``0.0.0.0``／``[::]`` 即违规范 §6.1。
- **L3 重启策略／健康探针**：NAS 上那份 compose 的 ``restart:`` 与 ``healthcheck``——规范 §2-7
  点名的崩溃循环形状（``unless-stopped`` ＋ 无探针）。
- **L4 资源名对撞**：仓 ``ui-user-mimo/dist/index.html`` ↔ 服务端 index。URL 前缀从 NAS 那份 compose
  的 ``--ui-user-dir`` 现取，⛔ 不抄第四份 ``"mimo"``（第六轮审计 ARCH-09 的口径）。状态码 200 不算证据
  （假部署那次三张脸全 200）。
- **L5 md5 三方**：仓 dist ↔ NAS 宿主机 dist ↔ 服务端引用的名字，逐文件实算对撞。
- **L6 部署树 HEAD**：NAS 工作副本的 ``rev-parse --short HEAD`` ＋未提交计数。
- **L7 compose／``.env`` 在册**：不带 ``-f``／``--env-file`` 直接 build 会报"没有配置文件"。
- **L8 镜像身份**：``docker images`` 里有没有 ``autoforge-api:<sha7>``、``/api/health`` 里有没有
  ``build``。这一格按 ``decisions/20261011-AF发布面与镜像可追溯-裁定.md`` §3 Q1 排登录窗之后，
  所以现读读不到时判 **NA 并明写"线上是哪一版仍读不出"**，⛔ 不许写成通过。

退出码：``0``＝全绿；``1``＝有不一致或违例；``2``＝前置塌（通道不可用／本地 dist 不在）。
用法：``python scripts/verify_ui_nas_deploy.py``（可 ``--target``／``--base-url`` 覆盖）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST_DIR = REPO / "ui-user-mimo" / "dist"
NAS_USER_DIR = "/vol1/1000/docker/autoforge"
NAS_COMPOSE = f"{NAS_USER_DIR}/docker/docker-compose.api.yml"
NAS_ENV = f"{NAS_USER_DIR}/docker/.env"
NAS_DIST = f"{NAS_USER_DIR}/ui-user-mimo/dist"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]

#: 远程命令里出现任一枚即拒发——本脚本的射程是"读"，写动作一律留给 owner 点头后的手。
WRITE_MARKERS = (
    "rm ", "mv ", "cp ", "dd ", "mkdir", "touch", "truncate", "tee ", "sed -i",
    ">", ">>", "docker compose", "docker build", "docker restart", "docker stop",
    "docker rm", "docker exec", "systemctl", "reboot", "kill", "apt", "pip install",
    "npm ", "git push", "git commit", "git checkout", "git pull", "git merge",
    "git reset", "git clean", "curl -o", "wget",
)

ASSET_RE = re.compile(r"assets/([\w.\-]+\.(?:js|css))")

OK, BAD, NA, BLOCK = "OK", "BAD", "NA", "BLOCK"


class ReadOnlyViolation(RuntimeError):
    """远程命令含写动作标记——本脚本不许发这种东西。"""


def assert_read_only(cmd: str) -> str:
    for marker in WRITE_MARKERS:
        if marker in cmd:
            raise ReadOnlyViolation(f"远程命令含写标记 {marker!r}：{cmd}")
    return cmd


def default_runner(cmd: str) -> tuple[int, str]:
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def fetch_url(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - 通道塌由 L1/L4 判，不在这里吞成"没问题"
        return -1, f"{type(exc).__name__}: {exc}"


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def asset_names(html: str) -> list[str]:
    return sorted(set(ASSET_RE.findall(html)))


def ui_prefix_from_compose(compose_text: str) -> str:
    """URL 前缀从 NAS 那份 compose 的 ``--ui-user-dir`` 现取——⛔ 不在这里抄第四份 ``"mimo"``。

    第六轮审计 ARCH-09 点名的就是"同一个字面量散在几处、靠测试对账代替单一真源"；本脚本要做的是
    读数，不能再往里加一格手抄。读不出前缀＝射程断，由调用方判 ``BLOCK``。
    """
    match = re.search(r'--ui-user-dir[",\s]+/?([\w.\-]+)', compose_text)
    return match.group(1).rstrip("/") if match else ""


def host_md5s(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{32}", parts[0]):
            out[parts[1].rsplit("/", 1)[-1]] = parts[0]
    return out


def check(
    *,
    target: str,
    base_url: str,
    dist_dir: Path,
    run,
    fetch,
    nas_names: str = "autoforge",
) -> tuple[list[dict], dict]:
    legs: list[dict] = []
    info: dict = {}

    def add(leg: str, level: str, msg: str, cmd: str = "") -> None:
        legs.append({"leg": leg, "level": level, "msg": msg, "cmd": cmd})

    local_index = dist_dir / "index.html"
    if not local_index.is_file():
        add("L0 本地 dist", BLOCK, f"{local_index} 不在——先在开发机 build，本脚本不替你构建")
        return legs, info

    cmd = f"ssh {' '.join(SSH_OPTS)} {target} hostname"
    rc, out = run(assert_read_only(cmd))
    if rc != 0 or not out.strip():
        add("L1 通道", BLOCK, f"SSH 只读探活失败 rc={rc}：{out.strip()[:120]}")
        return legs, info
    info["hostname"] = out.strip()
    add("L1 通道", OK, f"远端 hostname={out.strip()}（rc=0）")

    cmd = f'ssh {" ".join(SSH_OPTS)} {target} "docker ps --format \'{{{{.Names}}}}|{{{{.Ports}}}}\'"'
    rc, out = run(assert_read_only(cmd))
    loose = []
    if rc != 0:
        add("L2 端口绑", BLOCK, f"docker ps 读不到 rc={rc}：{out.strip()[:120]}")
    else:
        for line in out.splitlines():
            name, _, ports = line.partition("|")
            if nas_names not in name:
                continue
            if "0.0.0.0:" in ports or "[::]:" in ports:
                loose.append(f"{name}→{ports}")
        if loose:
            add("L2 端口绑", BAD, "规范 §6.1 要求绑 192.168.2.200，现读绑在所有接口：" + "、".join(loose), cmd)
        else:
            add("L2 端口绑", OK, "AF 系容器端口未见 0.0.0.0／[::] 绑定", cmd)

    cmd = f"ssh {' '.join(SSH_OPTS)} {target} cat {NAS_COMPOSE}"
    rc, out = run(assert_read_only(cmd))
    compose_text = "" if rc != 0 else out
    if rc != 0:
        add("L3 重启策略", BLOCK, f"NAS 上 compose 读不到 rc={rc}：{out.strip()[:120]}", cmd)
    else:
        restarts = re.findall(r"^\s*restart:\s*(\S+)\s*$", out, re.M)
        health = len(re.findall(r"^\s*healthcheck:", out, re.M))
        info["restarts"] = restarts
        info["healthchecks"] = health
        risky = [r for r in restarts if r == "unless-stopped"]
        if risky and health == 0:
            add("L3 重启策略", BAD,
                f"restart=unless-stopped × {len(risky)} 处且 healthcheck 0 处——规范 §2-7 的崩溃循环形状", cmd)
        elif health:
            add("L3 重启策略", OK, f"healthcheck {health} 处；restart={restarts}", cmd)
        else:
            add("L3 重启策略", NA, f"restart={restarts}、healthcheck 0 处（无 unless-stopped 猛拉形状）", cmd)

    prefix = ui_prefix_from_compose(compose_text)
    info["ui_prefix"] = prefix
    served_names: list[str] = []
    local_names = asset_names(local_index.read_text(encoding="utf-8"))
    info["served_names"] = served_names
    info["local_names"] = local_names
    if not prefix:
        add("L4 资源名", BLOCK,
            "NAS 那份 compose 读不出 `--ui-user-dir` ⇒ 前缀无处可取——本脚本不猜 \"mimo\""
            "（那是 ARCH-09 点名的手抄形状）；L5 起仍按仓↔NAS 宿主机两方对撞")
    else:
        url = f"{base_url.rstrip('/')}/{prefix}/"
        status, body = fetch(url)
        served_names = asset_names(body)
        info["served_names"] = served_names
        if status < 0 or status != 200:
            add("L4 资源名", BLOCK, f"GET {url} 读不到（HTTP={status}）——没有读数，不判通过")
        elif not served_names:
            add("L4 资源名", BLOCK, f"GET {url} 回了 {status} 但读不出任何 assets 引用——不是我要的那棵树")
        elif served_names != local_names:
            add("L4 资源名", BAD,
                f"服务端 index 资源名 {served_names} ≠ 仓 dist {local_names}"
                "（状态码 200 不算证据，假部署那次三张脸全 200）")
        else:
            add("L4 资源名", OK, f"服务端与仓 dist 资源名逐条相同（{len(local_names)} 枚）")

    cmd = f"ssh {' '.join(SSH_OPTS)} {target} md5sum {NAS_DIST}/assets/*"
    rc, out = run(assert_read_only(cmd))
    if rc != 0 or not out.strip():
        add("L5 md5 三方", BLOCK, f"NAS 宿主机 dist 读不到 rc={rc}：{out.strip()[:120]}", cmd)
    else:
        host = host_md5s(out)
        mismatches: list[str] = []
        compared = 0
        for name in sorted(set(local_names) | set(served_names)):
            local_path = dist_dir / "assets" / name
            if not local_path.is_file():
                mismatches.append(f"{name}：仓 dist 里缺这一枚")
                continue
            if name not in host:
                mismatches.append(f"{name}：NAS 宿主机 dist 里缺这一枚（scp -r 并进旧目录会留下这种半棵树）")
                continue
            compared += 1
            digest = md5_file(local_path)
            if digest != host[name]:
                mismatches.append(f"{name}：仓 {digest[:8]}… ≠ NAS {host[name][:8]}…")
        info["md5_compared"] = compared
        if mismatches:
            add("L5 md5 三方", BAD, "；".join(mismatches), cmd)
        else:
            add("L5 md5 三方", OK, f"{compared} 枚资源 仓↔NAS 逐字节相同", cmd)

    cmd = f"ssh {' '.join(SSH_OPTS)} {target} 'git -C {NAS_USER_DIR} rev-parse --short HEAD'"
    rc, out = run(assert_read_only(cmd))
    if rc != 0:
        add("L6 部署树 HEAD", BLOCK, f"rev-parse 读不到 rc={rc}：{out.strip()[:120]}", cmd)
    else:
        head = out.strip().splitlines()[0] if out.strip() else ""
        info["nas_head"] = head
        cmd2 = f"ssh {' '.join(SSH_OPTS)} {target} 'git -C {NAS_USER_DIR} status --porcelain | wc -l'"
        rc2, out2 = run(assert_read_only(cmd2))
        dirty = out2.strip().splitlines()[0] if rc2 == 0 and out2.strip() else "?"
        local_head = ""
        git_head = REPO / ".git" / "HEAD"
        try:
            ref = git_head.read_text(encoding="utf-8").strip()
            if ref.startswith("ref: "):
                loose_ref = (REPO / ".git" / ref[5:]).read_text(encoding="utf-8").strip()
                local_head = loose_ref[:7]
        except OSError:
            local_head = ""
        info["local_head"] = local_head
        msg = f"NAS 工作副本 HEAD={head} 未提交={dirty}"
        if local_head and head != local_head:
            add("L6 部署树 HEAD", NA, msg + f"（本机 HEAD={local_head}——不同则线上落后，但镜像身份读不出，见 L8）")
        else:
            add("L6 部署树 HEAD", OK, msg + (f"（本机 HEAD={local_head}）" if local_head else ""))

    cmd = f"ssh {' '.join(SSH_OPTS)} {target} 'test -f {NAS_COMPOSE} && echo Y || echo N'"
    rc, out = run(assert_read_only(cmd))
    compose_ok = rc == 0 and out.strip().startswith("Y")
    cmd2 = f"ssh {' '.join(SSH_OPTS)} {target} 'test -f {NAS_ENV} && echo Y || echo N'"
    rc2, out2 = run(assert_read_only(cmd2))
    env_ok = rc2 == 0 and out2.strip().startswith("Y")
    if not compose_ok:
        add("L7 命令形状", BAD, f"{NAS_COMPOSE} 读不到（rc={rc}）——不带 -f 直接 build 会报『没有配置文件』")
    elif not env_ok:
        add("L7 命令形状", BAD, f"{NAS_ENV} 不在册（compose 不自动加载 .env，必须 --env-file）")
    else:
        add("L7 命令形状", OK, "compose 与 .env 都在 NAS 侧原处；正确形状见下方建议命令")

    cmd = f'ssh {" ".join(SSH_OPTS)} {target} "docker images --format \'{{{{.Repository}}}}:{{{{.Tag}}}}\'"'
    rc, out = run(assert_read_only(cmd))
    tags = [t for t in out.splitlines() if t.startswith("autoforge-api:")]
    head = info.get("nas_head") or ""
    pinned = [t for t in tags if head and t == f"autoforge-api:{head}"]
    status, body = fetch(f"{base_url.rstrip('/')}/api/health")
    build_field = ""
    if status == 200 and body:


        try:
            build_field = json.dumps(json.loads(body).get("build"), ensure_ascii=False)
        except (ValueError, TypeError):
            build_field = ""
    if pinned and build_field not in ("", "null"):
        add("L8 镜像身份", OK, f"{pinned[0]} 在册且 /api/health 回 build={build_field}", cmd)
    else:
        add("L8 镜像身份", NA,
            f"现读 tags={tags or '无'}、/api/health 的 build={build_field or '读不到'}"
            "——按裁定 §3 Q1 排在登录窗之后落码；这一格读不出＝**线上是哪一版只能靠 md5 对撞**（#81）", cmd)

    return legs, info


def propose_commands(target: str) -> str:
    compose_dir = os.path.dirname(NAS_COMPOSE)
    return "\n".join(
        [
            "# 以下动作本脚本**不执行**（容器 churn 是整机冻结触发路径，要 owner 点头）：",
            f"ssh {target} 'cd {NAS_USER_DIR} && git fetch && git merge --ff-only origin/master'",
            f"ssh {target} 'cd {compose_dir} && docker compose --env-file .env -f docker-compose.api.yml build'",
            "# 起重之前先比对新镜像里的 serve 选项与 compose command 用到的选项"
            "（旧镜像不认新选项＝typer 退 2 ＋ unless-stopped 崩溃循环）；",
            "# 核选项要启容器，属写动作面，本脚本不代跑：",
            f"ssh {target} 'cd {compose_dir} && docker compose --env-file .env "
            "-f docker-compose.api.yml up -d --force-recreate'",
            "# 产物对撞（bind mount 钉目录 inode，docker restart 不重新解析，必须 --force-recreate）：",
            f"python scripts/verify_ui_nas_deploy.py --target {target}",
        ]
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NAS 上用户视角 UI 部署现状核对（只读）")
    parser.add_argument("--target", default=os.environ.get("AF_NAS_TARGET", "lidicn@192.168.2.200"))
    parser.add_argument("--base-url", default=os.environ.get("AF_UI_BASE_URL", "http://192.168.2.200:8787"))
    parser.add_argument("--runner", default=None, help="测试用注入点（不对外承诺）")
    args = parser.parse_args(argv)

    legs, info = check(
        target=args.target,
        base_url=args.base_url,
        dist_dir=DIST_DIR,
        run=args.runner or default_runner,
        fetch=fetch_url,
    )
    counts = {lvl: sum(1 for x in legs if x["level"] == lvl) for lvl in (OK, BAD, NA, BLOCK)}
    print(f"NAS UI 部署只读核对 · target={args.target}")
    for leg in legs:
        print(f"  [{leg['level']:<5}] {leg['leg']}：{leg['msg']}")
    print()
    if counts[BLOCK]:
        print(f"结论：前置塌（{counts[BLOCK]} 条 BLOCK）——没有读数不等于没有问题，先修通道/构建。")
        return 2
    if counts[BAD]:
        print(f"结论：{counts[BAD]} 处不一致或违例（绿 {counts[OK]}／NA {counts[NA]}）——按上面的条款逐条收，"
              "端口与重启策略那两格改的是 docker/*，与登录线同窗。")
        return 1
    print(f"结论：核对腿全绿（{counts[OK]} 条绿／NA {counts[NA]} 条）。")
    if counts[NA]:
        print("  NA 那一格是裁定排窗后的部分，不是通过——引用前先看现读。")
    print()
    print(propose_commands(args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

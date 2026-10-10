#!/usr/bin/env python3
"""`scripts/verify_ui_nas_deploy.py` 的判据腿（部署自动化半边，执行记录 §二之一百零九）。

三条纪律，和仓里其它门禁同一形状：

1. **每条腿单独可红**：假读数里注入一处不一致，红的那条必须带自己的腿名——
   "脚本跑通了"不等于"脚本在看"（反向腿一律给一份全绿夹具，防"永远红"的另一半）；
2. **读不到不许当通过**：通道塌／本地 dist 不在 ⇒ `BLOCK`（收尾 `exit 2`）；
   镜像身份那格现读读不到 ⇒ `NA` 且必须明写"线上是哪一版读不出"，⛔ 不许升成 `OK`；
3. **只读面自证**：脚本发出去的每一条远程命令都要过写标记白名单，且这条腿自己要有
   "确实发出了 ≥8 条"的反空集断言；建议命令段必须保住三个各花掉一轮现场排查的形状
   （`-f docker-compose.api.yml`／`--env-file`／`--force-recreate`）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_ui_nas_deploy.py"
SPEC = importlib.util.spec_from_file_location("verify_ui_nas_deploy", SCRIPT)
vnd = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(vnd)

JS = "index-AAAA.js"
CSS = "index-BBBB.css"
SERVED = f"<html><script type=module src=assets/{JS}></script>" \
         f"<link rel=stylesheet href=assets/{CSS}></html>"
COMPOSE_RISK = (
    "services:\n"
    "  api:\n"
    "    image: autoforge-api:latest\n"
    "    ports:\n"
    "      - \"8787:8787\"\n"
    "    command: [\"forge\", \"serve\", \"--ui-dir\", \"/ui\", \"--ui-user-dir\", \"/mimo\"]\n"
    "    restart: unless-stopped\n"
)
COMPOSE_BACKOFF = (
    "services:\n"
    "  api:\n"
    "    image: autoforge-api:latest\n"
    "    command: [\"forge\", \"serve\", \"--ui-user-dir\", \"/mimo\"]\n"
    "    restart: \"on-failure:3\"\n"
    "    healthcheck:\n"
    "      test: [\"CMD\", \"curl\", \"-sf\", \"http://127.0.0.1:8787/api/health\"]\n"
)
COMPOSE_NO_PREFIX = (
    "services:\n"
    "  api:\n"
    "    image: autoforge-api:latest\n"
    "    command: [\"forge\", \"serve\", \"--ui-dir\", \"/ui\"]\n"
    "    restart: \"on-failure:3\"\n"
    "    healthcheck:\n"
    "      test: [\"CMD\", \"true\"]\n"
)
HEAD = "abc1234"


def _dist(tmp_path: Path, *, drop: str = "") -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(SERVED, encoding="utf-8")
    (root / "assets" / JS).write_bytes(b"js-bytes")
    (root / "assets" / CSS).write_bytes(b"css-bytes")
    if drop:
        (root / "assets" / drop).unlink(missing_ok=True)
    return root


def _host_md5_lines(hashes: dict[str, str]) -> str:
    return "".join(f"{digest}  {vnd.NAS_DIST}/assets/{name}\n" for name, digest in hashes.items())


def _runner(responses: dict[str, tuple[int, str]], calls: list[str] | None = None):
    def run(cmd: str) -> tuple[int, str]:
        if calls is not None:
            calls.append(cmd)
        for pattern, answer in responses.items():
            if pattern in cmd:
                return answer
        raise AssertionError(f"假 runner 缺这一类远程命令的夹具：{cmd}")

    return run


def _fetch(served_html: str = SERVED, health: dict | None = None):
    def fetch(url: str) -> tuple[int, str]:
        if url.endswith("/mimo/"):
            return 200, served_html
        if url.endswith("/api/health"):
            return 200, json.dumps(health if health is not None else {"version": "0.1.0"})
        raise AssertionError(f"假 fetch 缺这个 URL：{url}")

    return fetch


def _all_green(tmp_path: Path, *, compose: str = COMPOSE_BACKOFF,
               ports_line: str = "autoforge-api|192.168.2.200:8787->8787/tcp\n",
               tags: str = "autoforge-api:latest\n") -> dict[str, tuple[int, str]]:
    root = _dist(tmp_path)
    hashes = {JS: hashlib.md5((root / "assets" / JS).read_bytes()).hexdigest(),
              CSS: hashlib.md5((root / "assets" / CSS).read_bytes()).hexdigest()}
    return {
        "hostname": (0, "Fn7T\n"),
        "docker ps": (0, ports_line),
        "cat ": (0, compose),
        "md5sum": (0, _host_md5_lines(hashes)),
        "rev-parse": (0, f"{HEAD}\n"),
        "status --porcelain": (0, "0\n"),
        "test -f": (0, "Y\n"),
        "docker images": (0, tags),
    }


def _check(tmp_path: Path, responses, *, served_html: str = SERVED, health=None,
           dist: Path | None = None, calls=None):
    return vnd.check(
        target="lidicn@test", base_url="http://test:8787",
        dist_dir=dist if dist is not None else _dist(tmp_path),
        run=_runner(responses, calls), fetch=_fetch(served_html, health),
    )


def _levels(legs) -> dict[str, str]:
    return {x["leg"].split()[0]: x["level"] for x in legs}


def _msgs(legs) -> dict[str, str]:
    return {x["leg"].split()[0]: x["msg"] for x in legs}


def test_全绿夹具给出零红且镜像身份那一格仍是NA(tmp_path):
    legs, info = _check(tmp_path, _all_green(tmp_path))
    lv = _levels(legs)
    assert [x for x in legs if x["level"] == vnd.BAD] == []
    assert lv["L1"] == vnd.OK and lv["L4"] == vnd.OK and lv["L5"] == vnd.OK and lv["L7"] == vnd.OK
    assert lv["L8"] == vnd.NA, "没有 sha7 tag、health 里没有 build ⇒ 只能是 NA，不许写 OK"
    assert info["md5_compared"] == 2


def test_通道塌走BLOCK且立刻收住(tmp_path):
    responses = _all_green(tmp_path)
    responses["hostname"] = (255, "ssh: connect to host port 22: Connection timed out\n")
    legs, _ = _check(tmp_path, responses)
    assert _levels(legs) == {"L1": vnd.BLOCK}


def test_本地dist不在走BLOCK(tmp_path):
    legs, _ = _check(tmp_path, _all_green(tmp_path), dist=tmp_path / "no-such-dist")
    assert _levels(legs) == {"L0": vnd.BLOCK}


def test_端口绑0000判红绑LAN判绿(tmp_path):
    loose = _all_green(tmp_path, ports_line="autoforge-api|0.0.0.0:8787->8787/tcp, [::]:8787->8787/tcp\n")
    legs, _ = _check(tmp_path, loose)
    assert _levels(legs)["L2"] == vnd.BAD
    assert "6.1" in _msgs(legs)["L2"]
    pinned = _check(tmp_path, _all_green(tmp_path))[0]
    assert _levels(pinned)["L2"] == vnd.OK


def test_非AF容器绑0000不进本脚本射程(tmp_path):
    legs, _ = _check(tmp_path, _all_green(tmp_path, ports_line="bark|0.0.0.0:18273->8080/tcp\n"))
    assert _levels(legs)["L2"] == vnd.OK, "射程口径：本脚本判 AF 系容器；别的面由各自的登记线负责"


def test_unless_stopped且无探针判红退避加探针判绿(tmp_path):
    risky = _check(tmp_path, _all_green(tmp_path, compose=COMPOSE_RISK))[0]
    assert _levels(risky)["L3"] == vnd.BAD and "2-7" in _msgs(risky)["L3"]
    ok = _check(tmp_path, _all_green(tmp_path, compose=COMPOSE_BACKOFF))[0]
    assert _levels(ok)["L3"] == vnd.OK


def test_compose读不到判BLOCK(tmp_path):
    responses = _all_green(tmp_path)
    responses["cat "] = (1, "No such file or directory\n")
    legs, _ = _check(tmp_path, responses)
    assert _levels(legs)["L3"] == vnd.BLOCK


def test_资源名不一致判红且不许靠状态码(tmp_path):
    legs, _ = _check(tmp_path, _all_green(tmp_path), served_html=f"<script src=assets/other-CCCC.js></script>")
    assert _levels(legs)["L4"] == vnd.BAD and "状态码" in _msgs(legs)["L4"]


def test_服务端读不到判BLOCK不是绿(tmp_path):
    responses = _all_green(tmp_path)
    legs, _ = _check(tmp_path, responses, served_html="")
    assert _levels(legs)["L4"] == vnd.BLOCK


def test_md5逐字节差判红并点名两侧(tmp_path):
    responses = _all_green(tmp_path)
    responses["md5sum"] = (0, _host_md5_lines({JS: "0" * 32, CSS: hashlib.md5(b"css-bytes").hexdigest()}))
    legs, _ = _check(tmp_path, responses)
    assert _levels(legs)["L5"] == vnd.BAD
    assert "仓" in _msgs(legs)["L5"] and "NAS" in _msgs(legs)["L5"]


def test_NAS半棵树残留判红点名缺的那一枚(tmp_path):
    responses = _all_green(tmp_path)
    responses["md5sum"] = (0, _host_md5_lines({CSS: hashlib.md5(b"css-bytes").hexdigest()}))
    legs, _ = _check(tmp_path, responses)
    assert _levels(legs)["L5"] == vnd.BAD
    assert JS in _msgs(legs)["L5"] and "NAS 宿主机 dist 里缺" in _msgs(legs)["L5"]


def test_仓dist缺文件判红(tmp_path):
    responses = _all_green(tmp_path)
    dist = _dist(tmp_path, drop=CSS)
    legs, _ = _check(tmp_path, responses, dist=dist)
    assert _levels(legs)["L5"] == vnd.BAD
    assert "仓 dist 里缺" in _msgs(legs)["L5"]


def test_镜像有sha7且health回build才升OK(tmp_path):
    responses = _all_green(tmp_path, tags=f"autoforge-api:{HEAD}\n")
    legs, _ = _check(tmp_path, responses, health={"build": {"commit": HEAD, "built_at": "2026-10-11T00:00:00Z"}})
    assert _levels(legs)["L8"] == vnd.OK


def test_health里build为null仍判NA不许假绿(tmp_path):
    responses = _all_green(tmp_path, tags=f"autoforge-api:{HEAD}\n")
    legs, _ = _check(tmp_path, responses, health={"build": None})
    assert _levels(legs)["L8"] == vnd.NA
    assert "读不出" in _msgs(legs)["L8"]


def test_deploy树HEAD与本机不同判NA并报未提交数(tmp_path):
    legs, info = _check(tmp_path, _all_green(tmp_path))
    assert info["nas_head"] == HEAD
    assert _levels(legs)["L6"] in (vnd.OK, vnd.NA)
    assert "未提交=" in _msgs(legs)["L6"]


def test_compose或env缺席判红(tmp_path):
    responses = _all_green(tmp_path)
    responses["test -f"] = (0, "N\n")
    legs, _ = _check(tmp_path, responses)
    assert _levels(legs)["L7"] == vnd.BAD


def test_远程命令白名单默认拒绝():
    for cmd in [
        "md5sum /a; rm -rf /", "echo x > /vol1/f", "docker compose up -d", "git push origin master",
        "sed -i s/a/b/ f", "docker restart autoforge-api", "pip install homesdk", "curl -o /tmp/x",
        "docker exec autoforge-api curl localhost:8787",
    ]:
        with pytest.raises(vnd.ReadOnlyViolation):
            vnd.assert_read_only(cmd)
    assert vnd.assert_read_only(f"md5sum {vnd.NAS_DIST}/assets/*")


def test_发出去的每一条远程命令都过白名单(tmp_path):
    calls: list[str] = []
    _check(tmp_path, _all_green(tmp_path), calls=calls)
    assert len(calls) >= 8, "一条远程命令都没发出去，那条『全过白名单』就是空集给的绿"
    for cmd in calls:
        vnd.assert_read_only(cmd)


def test_asset_names只吃assets前缀():
    assert vnd.asset_names(SERVED) == sorted([JS, CSS])
    assert vnd.asset_names("<script src=/other/x.js>") == []


def test_前缀读不出判BLOCK且不猜mimo后续腿照跑(tmp_path):
    responses = _all_green(tmp_path, compose=COMPOSE_NO_PREFIX)
    legs, info = _check(tmp_path, responses)
    assert _levels(legs)["L4"] == vnd.BLOCK
    assert "不猜" in _msgs(legs)["L4"]
    assert info["ui_prefix"] == ""
    assert _levels(legs)["L5"] == vnd.OK, "前缀断掉只该停掉服务端那一腿，仓↔NAS 宿主机对撞要照跑"


def test_前缀从compose现取两种写法都认():
    assert vnd.ui_prefix_from_compose('command: ["forge", "serve", "--ui-user-dir", "/mimo"]') == "mimo"
    assert vnd.ui_prefix_from_compose("forge serve --ui-user-dir /mimo") == "mimo"
    assert vnd.ui_prefix_from_compose('command: ["forge", "serve"]') == ""


def test_建议命令保住三个要命形状且明写不执行():
    text = vnd.propose_commands("lidicn@192.168.2.200")
    for shape in ("-f docker-compose.api.yml", "--env-file .env", "--force-recreate", "merge --ff-only"):
        assert shape in text, f"建议命令里丢了 {shape}——这一格曾经各花掉一轮现场排查"
    assert "不执行" in text

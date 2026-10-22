"""生产 serve 面必须停止把演示样例灌进用户数据（v2.6，2026-10-22）。

出资人在 /mimo 自动化页看到的 case01_day_light / case_lamp_sync / case_study_room
等十来条「删了又回来」的自动化，根因是**应用样例数据与用户数据混写**：

1. `examples/ir/*.json`（10 个演示 IR）烘进镜像 `/app/examples/ir`；
2. Dockerfile CMD **和** entrypoint.sh 都传 `--examples /app/examples/ir`；
3. `af_cli.serve` 还有一个 CWD 自动回退：不传参时若 `examples/ir` 存在就拿来灌——
   容器 WORKDIR=/app，所以就算去掉启动参数，回退照样命中；
4. `bootstrap_examples` 幂等判据是「同名归档没有版本」⇒ 用户 DELETE 之后，每次
   容器重启（每次部署都会重启）都被重新灌回 /data 持久盘。

修复：样例灌入**只认显式 --examples 传参**（本地开发要演示就显式传），三处去掉；
`examples/` 仍烘在镜像里（`scripts/verify_api.py` 等窗后验收脚本要读它）。

三个失败形状都要钉住——任何一处复活都不会让服务报错，只会让演示数据再次混回：
① CLI 回退复活 ⇒ 不传参的生产 serve 又开始灌；
② Dockerfile/entrypoint 的 --examples 复活 ⇒ 显式灌回；
③ examples/ 从镜像消失 ⇒ verify_api.py 这类验收脚本在容器里跑不了（反向钉，
   防止有人顺手把 COPY examples 也删了）。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI = ROOT / "src" / "autoforge" / "af_cli.py"
DOCKERFILE = ROOT / "docker" / "Dockerfile.api"
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


def test_serve_does_not_fall_back_to_cwd_examples():
    """CLI 不得自动回退到 CWD 的 examples/ir——灌入只认显式 --examples 传参。"""
    src = CLI.read_text(encoding="utf-8")
    # 当时的回退形状（及一切等价变体）：examples 取值里出现对 examples/ir 的目录探测
    fallback = re.search(r'examples_path\s*=\s*examples\s+or\b', src)
    assert fallback is None, (
        "serve 的 examples 取值又出现了 `examples or ...` 形状的自动回退："
        "生产不传参时会按 CWD 把 examples/ir 灌回 /data（演示数据混入用户数据的根因）"
    )
    assert "examples/ir" not in src or re.search(
        r'["\']examples/ir["\']', src
    ) is None or "回退" not in src[max(0, src.find("examples_path") - 600):src.find("examples_path")], (
        "af_cli.serve 附近仍在探测 examples/ir 目录"
    )


def test_prod_start_commands_do_not_pass_examples():
    """镜像两处启动命令（Dockerfile CMD、entrypoint.sh）都不得传 --examples。"""
    for path, label in ((DOCKERFILE, "Dockerfile.api"), (ENTRYPOINT, "entrypoint.sh")):
        text = path.read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines()
                if "--examples" in ln and not ln.strip().startswith("#")]
        assert not hits, (label, hits,
                          "生产启动命令又在传 --examples ⇒ 演示 IR 每次重启灌回 /data")


def test_examples_stay_baked_into_the_image():
    """反向钉：examples/ 必须继续烘进镜像——verify_api.py 等窗后验收脚本靠它跑。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^COPY examples \./examples", text, re.M), (
        "Dockerfile.api 不再 COPY examples：镜像内 verify_api.py 会读不到样例 IR"
    )

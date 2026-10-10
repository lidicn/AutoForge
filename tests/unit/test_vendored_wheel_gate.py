"""`scripts/check_vendored_wheel.py` 的判据（第六轮审计 ARCH-04，执行记录 §二之九十八）。

要钉住的形状：仓内随附的那枚私有 wheel 是**四条面共用的一个真身**（CI 工作流、交付镜像、测试镜像、
部署编排注释）。既有那条字节判据（`tests/unit/test_mqtt_compose_env.py`）从 **`Dockerfile.api` 的 COPY 行**
反推 wheel，所以"只有 CI 面/测试镜像面被指到另一枚"这一缝它读不到——本门的 B 规则补的就是这一缝，
`test_face_other_than_dockerfile_api_is_the_blind_spot_of_the_existing_leg` 那一腿专门自证这件事。
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_vendored_wheel.py"

WHEEL_NAME = "homesdk-0.4.2-py3-none-any.whl"
OLD_NAME = "homesdk-0.4.1-py3-none-any.whl"
WHEEL_BYTES = b"FAKE-HOMESDK-WHEEL-BYTES-not-a-real-package"
WHEEL_SHA = hashlib.sha256(WHEEL_BYTES).hexdigest()

README_TEXT = (
    "# vendor\n\n"
    "现在是哪一枚：本目录唯一的 `.whl`。\n"
    "字节真值两处：`tests/unit/test_mqtt_compose_env.py` 的常量，与库侧 `VERSIONS.txt` 的对应段。\n"
)

CI_TEXT = f"""name: CI
jobs:
  test:
    steps:
      - run: pip install docker/homesdk/{WHEEL_NAME}
  gates:
    steps:
      - run: pip install docker/homesdk/{WHEEL_NAME}
"""

API_TEXT = f"""FROM python:3.14-slim
COPY docker/homesdk/{WHEEL_NAME} /tmp/homesdk/
RUN pip install --no-cache-dir /tmp/homesdk/{WHEEL_NAME}
"""

TEST_TEXT = f"""FROM python:3.14-slim
COPY docker/homesdk/{WHEEL_NAME} /tmp/homesdk/
RUN pip install --no-cache-dir /tmp/homesdk/{WHEEL_NAME}
"""

COMPOSE_TEXT = f"""services:
  api:
    # （`Dockerfile.api` 按文件名钉死的 `docker/homesdk/{WHEEL_NAME}`）
    image: autoforge-api:latest
"""

CONST_TEXT = f'''"""既有判据的壳子。"""
AUTHORITATIVE_WHEEL_SHA256 = "{WHEEL_SHA}"
'''


def _mod():
    spec = importlib.util.spec_from_file_location("check_vendored_wheel", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _mod()


def _tree(tmp_path: pathlib.Path, **over) -> pathlib.Path:
    wheels = over.pop("__wheels__", [WHEEL_NAME])
    files = {
        ".github/workflows/ci.yml": CI_TEXT,
        "docker/Dockerfile.api": API_TEXT,
        "docker/Dockerfile.test": TEST_TEXT,
        "docker/docker-compose.api.yml": COMPOSE_TEXT,
        "docker/homesdk/README.md": README_TEXT,
        "tests/unit/test_mqtt_compose_env.py": CONST_TEXT,
    }
    files.update(over)
    for rel, text in files.items():
        if text is None:
            continue
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    for name in wheels:
        wheel = tmp_path / "docker/homesdk" / name
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_bytes(WHEEL_BYTES if name == WHEEL_NAME else b"OTHER-BYTES")
    return tmp_path


def _findings(root: pathlib.Path):
    blocked = mod.anchor_ok(root)
    assert blocked is None, f"射程本应成立却断了：{blocked}"
    return mod.check(root)


# ── 绿：四面同名、单枚、字节对得上、README 在册 ─────────────────────────────


def test_the_aligned_tree_is_green(tmp_path):
    root = _tree(tmp_path)
    findings, info = _findings(root)
    assert findings == []
    assert info["wheels"] == [WHEEL_NAME]
    assert info["sha"] == WHEEL_SHA
    assert info["authoritative"] == WHEEL_SHA
    assert set(info["refs"]) == {
        ".github/workflows/ci.yml",
        "docker/Dockerfile.api",
        "docker/Dockerfile.test",
        "docker/docker-compose.api.yml",
    }
    assert all(v == [WHEEL_NAME] for v in info["refs"].values())


def test_rewording_the_readme_does_not_move_the_reading(tmp_path):
    """门不判散文措辞：改写指路文档的句子，读数一动不动。"""
    base = _tree(tmp_path)
    before = _findings(base)[0]
    (base / "docker/homesdk/README.md").write_text(
        README_TEXT.replace("字节真值两处", "字节层面的真值有地方放（两处）") + "\n随手一句说明。\n",
        encoding="utf-8",
    )
    assert _findings(base)[0] == before == []


# ── B：引用面被指到另一枚 ───────────────────────────────────────────────────


def test_ci_pointing_at_another_wheel_is_red(tmp_path):
    root = _tree(tmp_path, **{".github/workflows/ci.yml": CI_TEXT.replace(WHEEL_NAME, OLD_NAME)})
    findings, _ = _findings(root)
    assert len(findings) == 1, findings
    assert findings[0].startswith("B：")
    assert "ci.yml" in findings[0] and OLD_NAME in findings[0]


def test_face_other_than_dockerfile_api_is_the_blind_spot_of_the_existing_leg(tmp_path):
    """本门的射程增量：把**测试镜像面**单独改指旧那一枚。

    既有那条字节判据从 `Dockerfile.api` 的 COPY 行反推 wheel，所以那种改动在它眼里完全隐形
    （Dockerfile.api 仍指向盘上真身、算出的 sha 照旧相等、CI 与镜像从此装两枚不同的东西）。
    """
    root = _tree(tmp_path, **{"docker/Dockerfile.test": TEST_TEXT.replace(WHEEL_NAME, OLD_NAME)})
    findings, _ = _findings(root)
    assert len(findings) == 1, findings
    assert findings[0].startswith("B：") and "Dockerfile.test" in findings[0]

    # 反向自证：既有那条链的取数起点确实没被这次改动影响（它只会照着 Dockerfile.api 走）。
    api_copy = re.search(r"COPY\s+(docker/homesdk/\S+\.whl)", (root / "docker/Dockerfile.api").read_text(encoding="utf-8"))
    assert api_copy and api_copy.group(1).endswith(WHEEL_NAME)
    assert (root / api_copy.group(1)).is_file()
    assert hashlib.sha256((root / api_copy.group(1)).read_bytes()).hexdigest() == WHEEL_SHA


def test_compose_comment_mention_is_in_scope(tmp_path):
    """注释提及也算引用面：换 wheel 的人最容易漏的就是"注释还写着旧名"。"""
    root = _tree(tmp_path, **{"docker/docker-compose.api.yml": COMPOSE_TEXT.replace(WHEEL_NAME, OLD_NAME)})
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("B：")
    assert "docker-compose.api.yml" in findings[0]


def test_missing_optional_face_is_not_a_finding(tmp_path):
    """部署编排那份在部署机上可能是草稿形态：缺文件不参与判定（另三份硬射程才报）。"""
    root = _tree(tmp_path, **{"docker/docker-compose.api.yml": None})
    (root / "docker/docker-compose.api.yml").unlink(missing_ok=True)
    findings, info = _findings(root)
    assert findings == []
    assert ".github/workflows/ci.yml" in info["faces_scanned"]
    assert "docker/docker-compose.api.yml" not in info["refs"]


# ── A：目录里多一枚 ─────────────────────────────────────────────────────────


def test_two_wheels_in_the_vendor_dir_are_red(tmp_path):
    root = _tree(tmp_path, __wheels__=[OLD_NAME, WHEEL_NAME])
    assert (root / "docker/homesdk" / OLD_NAME).is_file()
    findings, _ = _findings(root)
    assert any(f.startswith("A：") for f in findings), findings
    assert len([f for f in findings if f.startswith("A：")]) == 1


# ── C：字节与权威登记不符 ───────────────────────────────────────────────────


def test_same_name_different_bytes_is_red(tmp_path):
    """同名换内容＝"写着 0.4.2"与"跑的真是那一枚"脱钩，这一格必须红。"""
    root = _tree(tmp_path)
    (root / "docker/homesdk" / WHEEL_NAME).write_bytes(b"TAMPERED-BYTES")
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("C：")


def test_edited_authority_constant_is_red(tmp_path):
    """权威值被人顺手改掉（没有新裁定）也是红：值对不上盘上真身就报。"""
    other = hashlib.sha256(b"WHATEVER-ELSE").hexdigest()
    root = _tree(tmp_path, **{"tests/unit/test_mqtt_compose_env.py": CONST_TEXT.replace(WHEEL_SHA, other)})
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("C：")


# ── D：指路文档 ─────────────────────────────────────────────────────────────


def test_missing_readme_is_red(tmp_path):
    root = _tree(tmp_path, **{"docker/homesdk/README.md": None})
    (root / "docker/homesdk/README.md").unlink(missing_ok=True)
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("D：")
    assert "README.md" in findings[0]


def test_readme_that_copies_the_digest_is_red(tmp_path):
    """把摘要再钉一份进文档＝造一份会过期的副本，本门判红。"""
    root = _tree(tmp_path, **{"docker/homesdk/README.md": README_TEXT + f"\nsha256 `{WHEEL_SHA}`\n"})
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("D：")
    assert "64 位十六进制" in findings[0]


@pytest.mark.parametrize("anchor", ["tests/unit/test_mqtt_compose_env.py", "VERSIONS.txt"])
def test_readme_must_point_at_both_true_sources(tmp_path, anchor):
    text = README_TEXT.replace(f"`{anchor}`", "（这里本来指着一处真源）")
    root = _tree(tmp_path, **{"docker/homesdk/README.md": text})
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("D：")
    assert anchor in findings[0]


def test_readme_naming_a_stale_wheel_is_red(tmp_path):
    root = _tree(tmp_path, **{"docker/homesdk/README.md": README_TEXT + f"\n旧那枚 `{OLD_NAME}` 已删。\n"})
    findings, _ = _findings(root)
    assert len(findings) == 1 and findings[0].startswith("D：")
    assert OLD_NAME in findings[0]


# ── 射程：读不出＝exit 2，不许冒充"没有问题" ────────────────────────────────


def test_absent_vendor_dir_breaks_the_range(tmp_path):
    root = _tree(tmp_path)
    for p in sorted((root / "docker/homesdk").iterdir()):
        p.unlink()
    (root / "docker/homesdk").rmdir()
    reason = mod.anchor_ok(root)
    assert reason and "docker/homesdk" in reason


def test_empty_vendor_dir_breaks_the_range(tmp_path):
    root = _tree(tmp_path, **{"docker/homesdk/README.md": README_TEXT})
    for name in (WHEEL_NAME, OLD_NAME):
        (root / "docker/homesdk" / name).unlink(missing_ok=True)
    reason = mod.anchor_ok(root)
    assert reason and "没有" in reason


def test_unreadable_authority_line_breaks_the_range(tmp_path):
    root = _tree(tmp_path, **{"tests/unit/test_mqtt_compose_env.py": 'AUTHORITATIVE = "abc"\n'})
    reason = mod.anchor_ok(root)
    assert reason and "AUTHORITATIVE_WHEEL_SHA256" in reason


def test_missing_authority_file_breaks_the_range(tmp_path):
    root = _tree(tmp_path, **{"tests/unit/test_mqtt_compose_env.py": None})
    (root / "tests/unit/test_mqtt_compose_env.py").unlink(missing_ok=True)
    reason = mod.anchor_ok(root)
    assert reason and "test_mqtt_compose_env.py" in reason


def test_face_without_any_wheel_reference_breaks_the_range(tmp_path):
    root = _tree(tmp_path, **{"docker/Dockerfile.test": "FROM python:3.14-slim\nRUN pip install -e .[dev]\n"})
    reason = mod.anchor_ok(root)
    assert reason and "Dockerfile.test" in reason


# ── main() 的退出码形状 ─────────────────────────────────────────────────────


def test_main_returns_zero_two_one_in_that_order(tmp_path):
    good = _tree(tmp_path)
    assert mod.main(["check_vendored_wheel.py", str(good)]) == 0

    broken = tmp_path / "nope"
    assert mod.main(["check_vendored_wheel.py", str(broken)]) == 2

    bad = _tree(tmp_path / "bad", **{"docker/Dockerfile.api": API_TEXT.replace(WHEEL_NAME, OLD_NAME)})
    assert mod.main(["check_vendored_wheel.py", str(bad)]) == 1


# ── 真仓：现状必须真绿，且绿的原因是核过而不是没数到 ────────────────────────


def test_real_repo_is_actually_green():
    assert mod.anchor_ok(ROOT) is None
    findings, info = mod.check(ROOT)
    assert findings == []
    assert len(info["wheels"]) == 1
    assert info["sha"] == info["authoritative"]
    assert set(info["refs"]) == {
        ".github/workflows/ci.yml",
        "docker/Dockerfile.api",
        "docker/Dockerfile.test",
        "docker/docker-compose.api.yml",
    }
    assert all(names == info["wheels"] for names in info["refs"].values())


def test_real_repo_ci_face_references_the_wheel_three_times():
    """裁定 20261007 §六 Q1 说的"`ci.yml`×3"——引用面数量本身也是被钉的对象。"""
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert len(mod.WHEEL_NAME_RE.findall(text)) == 3


def test_real_readme_points_at_both_sources_and_copies_nothing():
    text = (ROOT / "docker/homesdk/README.md").read_text(encoding="utf-8")
    assert "tests/unit/test_mqtt_compose_env.py" in text
    assert "VERSIONS.txt" in text
    assert not re.search(r"\b[0-9a-fA-F]{64}\b", text)


def test_the_authority_constant_in_the_repo_is_a_real_digest():
    """防止"真源自己是个空壳"：常量行必须是一枚 64 位十六进制，而不是被人删成空串。"""
    text = (ROOT / "tests/unit/test_mqtt_compose_env.py").read_text(encoding="utf-8")
    m = mod.SHA_CONST_RE.search(text)
    assert m and len(m.group(1)) == 64

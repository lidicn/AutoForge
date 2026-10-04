"""联动桥依赖门禁必须"能变红"（铁律 #8），红的是**下一个漏装的面**而不是已经对上的那三个。

背景：`af_mqtt_bridge` 要 paho 才能连 broker，而 paho 在整个依赖链里**一处声明都没有**过
（§二之二十六）——homesdk 把它放在自家 `[mqtt]` extra，两个镜像装的又是裸 wheel，AF 的
`.[api,ha]` / `.[dev]` 也不含它。开发机一切正常，只因为那份解释器里手动装过。这种形状测试拦不住：
桥的用例全用 duck-typed client，压根不 import paho ⇒ 两千多条全绿，而镜像里原理上连不上线。
所以判据落在依赖声明的**形状**上：A 声明在 / B 交付面装到 / C CI 面装到，三面各自可红。

反面样本一律用 tmp 树，不动仓内真文件；`test_real_repo_*` 两条是对当前仓库的实测（真绿），
它们保证本门不是"只对自己的样本有效"的纸门。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_mqtt_runtime_dep.py"

PYPROJECT = '''[project]
name = "autoforge"
requires-python = ">=3.11"
dependencies = ["jsonschema>=4.20"]

[project.optional-dependencies]
api = ["fastapi>=0.110"]
ha = ["websockets>=12.0"]
mqtt = ["paho-mqtt>=1.6"]
dev = ["pytest>=8.0", "paho-mqtt>=1.6"]
'''

BRIDGE = '"""桥。"""\nfrom homesdk import mqtt as _mqtt\n'
API = 'FROM python:3.11-slim\nRUN pip install --no-cache-dir -e ".[api,ha,mqtt]"\n'
TEST_IMAGE = 'FROM python:3.11-slim\nRUN pip install --no-cache-dir -e ".[dev]"\n'
WORKFLOW = "      - name: Install package + dev deps\n        run: pip install -e \".[dev]\"\n"

PATHS = {
    "pyproject.toml": PYPROJECT,
    "src/autoforge/af_mqtt_bridge.py": BRIDGE,
    "docker/Dockerfile.api": API,
    "docker/Dockerfile.test": TEST_IMAGE,
    ".github/workflows/ci.yml": WORKFLOW,
}


def _module():
    spec = importlib.util.spec_from_file_location("check_mqtt_runtime_dep", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path: pathlib.Path, **over: str | None) -> pathlib.Path:
    files = dict(PATHS)
    files.update(over)
    for name, text in files.items():
        if text is None:
            continue
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _check(tmp_path: pathlib.Path, **over: str | None):
    return _module().check(_tree(tmp_path, **over))


# ── 本职：真绿 + 三面各自可红 ───────────────────────────────────────

def test_real_repo_anchor_readable(tmp_path):
    """本门对当前仓库有射程：锚点读不到就 exit 2，不能对真仓直接哑火。"""
    assert _module().anchor_ok(ROOT) is None


def test_real_repo_is_actually_green(tmp_path):
    findings, info = _module().check(ROOT)
    assert findings == []
    assert "mqtt" in info["declared"], f"paho 的声明位置读数为 {info['declared']}"
    assert "mqtt" in info["per_image"]["交付面"]


def test_green_shape_reports_measured_extras(tmp_path):
    findings, info = _check(tmp_path)
    assert findings == []
    assert info["declared"] == ["dev", "mqtt"]
    assert info["per_image"] == {"交付面": ["api", "ha", "mqtt"],
                                "CI 面（测试镜像）": ["dev"],
                                "CI 面（工作流）": ["dev"]}


def test_undeclared_paho_is_red(tmp_path):
    findings, info = _check(
        tmp_path,
        **{"pyproject.toml": PYPROJECT.replace('mqtt = ["paho-mqtt>=1.6"]\ndev = ["pytest>=8.0", "paho-mqtt>=1.6"]',
                                                'dev = ["pytest>=8.0"]')})
    assert info["declared"] == []
    assert len(findings) == 1
    assert "pyproject.toml" in findings[0]


def test_api_image_not_installing_the_extra_is_red(tmp_path):
    findings, _ = _check(tmp_path, **{"docker/Dockerfile.api": 'RUN pip install -e ".[api,ha]"\n'})
    assert len(findings) == 1
    assert "交付面" in findings[0]
    assert "['api', 'ha']" in findings[0] and "['dev', 'mqtt']" in findings[0]


def test_ci_images_not_installing_the_extra_are_red(tmp_path):
    findings, _ = _check(tmp_path,
                         **{"docker/Dockerfile.test": 'RUN pip install -e ".[api]"\n',
                            ".github/workflows/ci.yml": '        run: pip install -e ".[api]"\n'})
    assert len(findings) == 2
    assert "CI 面（测试镜像）" in findings[0] and "CI 面（工作流）" in findings[1]


def test_missing_install_line_is_red_not_silently_absent(tmp_path):
    """读不到 `-e ".[…]"` 安装行 ⇒ 判红并点名"无从判定"，不许当成"没装"就顺着下一条放行。"""
    findings, _ = _check(tmp_path, **{"docker/Dockerfile.api": "FROM python:3.11-slim\nCMD [\"forge\"]\n"})
    assert len(findings) == 1
    assert "找不到" in findings[0] and "Dockerfile.api" in findings[0]


def test_all_three_faces_missing_gives_three_findings(tmp_path):
    findings, _ = _check(tmp_path,
                         **{"docker/Dockerfile.api": 'RUN pip install -e ".[api]"\n',
                            "docker/Dockerfile.test": 'RUN pip install -e ".[api]"\n',
                            ".github/workflows/ci.yml": '        run: pip install -e ".[api]"\n'})
    assert len(findings) == 3


# ── 口径：什么算声明、什么算装到 ─────────────────────────────────────

def test_base_declaration_satisfies_every_face(tmp_path):
    pyproject = PYPROJECT.replace('dependencies = ["jsonschema>=4.20"]',
                                  'dependencies = ["jsonschema>=4.20", "paho-mqtt>=1.6"]')
    findings, info = _check(tmp_path,
                            **{"pyproject.toml": pyproject,
                               "docker/Dockerfile.api": 'RUN pip install -e ".[api]"\n'})
    assert "base" in info["declared"]
    assert findings == []


def test_installing_any_one_declared_extra_is_enough(tmp_path):
    """`dev` 与 `mqtt` 都声明了 paho ⇒ 某面只装 `mqtt` 即满足，不必两个都装。"""
    findings, _ = _check(tmp_path, **{"docker/Dockerfile.api": 'RUN pip install -e ".[api,mqtt]"\n'})
    assert findings == []


def test_two_install_lines_in_one_face_are_unioned(tmp_path):
    findings, info = _check(tmp_path, **{
        "docker/Dockerfile.api": 'RUN pip install -e ".[api,ha]"\nRUN pip install -e ".[mqtt]"\n'})
    assert findings == []
    assert info["per_image"]["交付面"] == ["api", "ha", "mqtt"]


def test_workflow_union_ignores_bare_install_line(tmp_path):
    """`gates` 那个 job 刻意只装 base（门禁脚本纯标准库）⇒ 它不该把 CI 面判红。"""
    findings, info = _check(tmp_path, **{
        ".github/workflows/ci.yml": '        run: pip install -e .\n        run: pip install -e ".[dev]"\n'})
    assert findings == []
    assert info["per_image"]["CI 面（工作流）"] == ["dev"]


def test_extra_names_are_matched_exactly_not_by_substring(tmp_path):
    """`-e ".[mqtt-api]"` 不等于装了 `mqtt`：按 token 精确比，不做前缀匹配。"""
    findings, info = _check(tmp_path, **{"docker/Dockerfile.api": 'RUN pip install -e ".[mqtt-api]"\n'})
    assert info["per_image"]["交付面"] == ["mqtt-api"]
    assert len(findings) == 1 and "交付面" in findings[0]


def test_similar_requirement_name_is_not_paho(tmp_path):
    pyproject = ('[project]\nname = "x"\ndependencies = []\n\n'
                 '[project.optional-dependencies]\nmqtt = ["paho-mqttlib>=1.0"]\n')
    findings, info = _check(tmp_path, **{"pyproject.toml": pyproject})
    assert info["declared"] == []
    assert len(findings) == 1


def test_case_and_underscore_spelling_is_still_paho(tmp_path):
    pyproject = ('[project]\nname = "x"\ndependencies = ["Paho_MQTT>=1.6"]\n')
    findings, info = _check(tmp_path, **{"pyproject.toml": pyproject})
    assert info["declared"] == ["base"]
    assert findings == []


# ── 射程前提：锚点读不到 ⇒ exit 2，不静默全绿 ────────────────────────

def test_anchor_ok_on_full_tree(tmp_path):
    assert _module().anchor_ok(_tree(tmp_path)) is None


def test_anchor_missing_pyproject(tmp_path):
    assert "pyproject" in (_module().anchor_ok(_tree(tmp_path, **{"pyproject.toml": None})) or "")


def test_anchor_missing_bridge(tmp_path):
    err = _module().anchor_ok(_tree(tmp_path, **{"src/autoforge/af_mqtt_bridge.py": None}))
    assert "af_mqtt_bridge" in err


def test_anchor_bridge_no_longer_imports_the_layer(tmp_path):
    err = _module().anchor_ok(_tree(tmp_path, **{"src/autoforge/af_mqtt_bridge.py":
                                                 'import socket\n'}))
    assert "from homesdk import mqtt" in err


def test_anchor_missing_face(tmp_path):
    err = _module().anchor_ok(_tree(tmp_path, **{".github/workflows/ci.yml": None}))
    assert "ci.yml" in err


# ── 退出码与读数行 ──────────────────────────────────────────────────

def test_main_exit_zero_on_green(tmp_path, capsys):
    mod = _module()
    assert mod.main(["x", str(_tree(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "['api', 'ha', 'mqtt']" in out and "['dev', 'mqtt']" in out


def test_main_exit_one_names_the_fix(tmp_path, capsys):
    mod = _module()
    tree = _tree(tmp_path, **{"docker/Dockerfile.api": 'RUN pip install -e ".[api,ha]"\n'})
    assert mod.main(["x", str(tree)]) == 1
    out = capsys.readouterr().out
    assert "只改开发机不算修" in out


def test_main_exit_two_on_anchor(tmp_path):
    mod = _module()
    tree = _tree(tmp_path, **{"src/autoforge/af_mqtt_bridge.py": None})
    assert mod.main(["x", str(tree)]) == 2


def test_relative_root_resolves_inside_repo(tmp_path):
    """传相对路径时按仓根解析（`gates.sh` 就是这样调的），不能拿 cwd 猜。"""
    mod = _module()
    assert mod.anchor_ok(mod.REPO) is None

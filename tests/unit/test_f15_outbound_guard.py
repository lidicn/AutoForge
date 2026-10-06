"""第十四轮审计 F15：出站白名单护栏被四处直连 `urlopen` 旁路 —— 修法 + 门禁形状。

两半各有一组判据，缺一半都会假绿：

**代码半边**（`guarded_open` 真的拦得住）
- 白名单外主机在**发第一个字节之前**就被拒（不是拿到响应再判）
- `@` 凭证注入 ⇒ `host_of()` 返回空串 ⇒ 拒
- 拦下时抛的是 `URLError` 子类 ⇒ 四处原有降级链（catch `URLError`/`OSError`/裸 `Exception`）照旧兜住；
  抛裸 `Exception` 会把"被护栏拦下"变成"未捕获异常往上冒"，那比旁路更难查
- 注入档（测试用的 `opener=`）不被悄悄吃掉

**门禁半边**（`check_outbound_guard.py` 判的是形状，且射程要覆盖 bandit 漏的那两种间接写法）
- `(opener or urllib.request.urlopen)(...)`：被调者是 BoolOp
- `self._opener = opener or urlopen`：函数对象当值交出，真正的请求漂到别处 ⇒ 红点前移到赋值行
- 文件级排除必须失效：同文件另一个函数走了护栏 ⇒ 本函数照样红（审计 W38 记过这个假绿）
- 注释/文档串里的 `urlopen` 不算命中（散文踩名字哨兵那条教训的反面用法）
- 三类站点都数不到 ⇒ `exit 2`，不许报干净
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import urllib.error

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check_outbound_guard.py"
SRC = REPO / "src"


def _run(root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), str(root)], capture_output=True, text=True
    )


def _src(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    root = tmp_path / "src"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# ── CONTROL：真实仓库 ⇒ 绿，且读数说清了每一腿 ─────────────────────────── #

def test_real_src_is_clean_and_reports_the_range():
    proc = _run(SRC)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "出站护栏门禁干净" in proc.stdout
    # 四个旁路站 + HTTPAdapter 自己都已收口（5 处），自建 opener 2 处（收口点 + ha.py 的拒跟随）
    assert "走 guarded_open 收口 5 处" in proc.stdout
    assert "裸 urlopen 0 处" in proc.stdout


def test_no_raw_urlopen_call_face_is_left_in_src():
    """与上一条独立：这条数 AST，不数门禁的口径（门禁错了也不能把这条一起骗过去）。"""
    faces: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            names = set()
            stack = [callee]
            while stack:
                n = stack.pop()
                if isinstance(n, ast.BoolOp):
                    stack.extend(n.values)
                    continue
                if isinstance(n, ast.Name):
                    names.add(n.id)
                elif isinstance(n, ast.Attribute):
                    names.add(n.attr)
            if "urlopen" in names:
                faces.append(f"{path.name}:{node.lineno}")
    assert faces == []


# ── 门禁 A 腿：三种裸出站形状 ─────────────────────────────────────────── #

RAW_DIRECT = """import urllib.request


def fetch(url):
    req = urllib.request.Request(url)
    return urllib.request.urlopen(req, timeout=5)
"""

BOOL_OP = """import urllib.request


def fetch(req, opener=None):
    with (opener or urllib.request.urlopen)(req, timeout=5) as resp:
        return resp.read()
"""

ASSIGN_FACE = """import urllib.request


class Stream:
    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen

    def run(self, req):
        return self._opener(req, timeout=5)
"""


@pytest.mark.parametrize(
    "text,anchor",
    [
        (RAW_DIRECT, "直连 urlopen"),
        (BOOL_OP, "直连 urlopen"),
        (ASSIGN_FACE, "把 urlopen 当可调用对象交出"),
    ],
    ids=["direct", "bool-op（bandit 漏）", "assign-face（bandit 漏）"],
)
def test_each_bare_outbound_shape_is_red(tmp_path, text, anchor):
    proc = _run(_src(tmp_path, {"af_bad.py": text}))
    assert proc.returncode == 1, proc.stdout
    assert "[裸出站]" in proc.stdout
    assert anchor in proc.stdout


def test_bool_op_shape_is_found_even_though_bandit_missed_it(tmp_path):
    """审计点名：B310 匹配直接调用形态，BoolOp 作被调者会漏。本门不漏。"""
    proc = _run(_src(tmp_path, {"af_bad.py": BOOL_OP}))
    assert proc.returncode == 1
    assert "af_bad.py:5" in proc.stdout


def test_assign_face_red_points_at_the_assignment_not_the_call(tmp_path):
    """请求发生在 `self._opener(req)`——被调者名字里没有 urlopen，追不到；红点必须在赋值那一行。"""
    proc = _run(_src(tmp_path, {"af_bad.py": ASSIGN_FACE}))
    assert proc.returncode == 1
    assert "af_bad.py:6" in proc.stdout


def test_file_level_exclusion_does_not_work(tmp_path):
    """同文件里**另一个**函数走了护栏 ⇒ 本函数那处裸出站照样红。

    这是审计 W38 踩过的假绿：第一版按文件排除护栏符号，真实仓库 4 处全被误判成"已走护栏"。
    """
    text = (
        "import urllib.request\n"
        "from .af_adapters.http import guarded_open\n"
        "def ok(req):\n"
        "    return guarded_open(req, allowed_hosts=('h',), timeout=5)\n"
        "def bad(req):\n"
        "    return urllib.request.urlopen(req, timeout=5)\n"
    )
    proc = _run(_src(tmp_path, {"af_two.py": text}))
    assert proc.returncode == 1, proc.stdout
    assert "函数 bad() 直连 urlopen" in proc.stdout
    assert "函数 ok()" not in proc.stdout


def test_prose_mention_is_not_a_hit(tmp_path):
    """注释与文档串里写 `urlopen` 不算命中——哨兵只认 AST。"""
    text = (
        "# 这里原来直连 urllib.request.urlopen，已改走收口点\n"
        "def fetch(req):\n"
        "    '''不走 allowed_hosts 的旧写法是 urlopen'''\n"
        "    from .af_adapters.http import guarded_open\n"
        "    return guarded_open(req, allowed_hosts=('h',), timeout=5)\n"
    )
    proc = _run(_src(tmp_path, {"af_prose.py": text}))
    assert proc.returncode == 0, proc.stdout
    assert "裸 urlopen 0 处" in proc.stdout


# ── 豁免标记 ─────────────────────────────────────────────────────────── #

def test_exempt_with_reason_is_green_and_counted(tmp_path):
    text = (
        "import urllib.request\n"
        "def fetch(req):  # outbound-guard: exempt(只连 loopback 的健康探针，无凭据)\n"
        "    return urllib.request.urlopen(req, timeout=1)  # outbound-guard: exempt(只连 loopback)\n"
    )
    proc = _run(_src(tmp_path, {"af_ex.py": text}))
    assert proc.returncode == 0, proc.stdout
    assert "就地豁免 1 处" in proc.stdout


def test_exempt_with_empty_reason_is_red(tmp_path):
    text = (
        "import urllib.request\n"
        "def fetch(req):\n"
        "    return urllib.request.urlopen(req, timeout=1)  # outbound-guard: exempt()\n"
    )
    proc = _run(_src(tmp_path, {"af_ex2.py": text}))
    assert proc.returncode == 1
    assert "[豁免空转]" in proc.stdout


# ── B 腿（重定向守卫）与 C 腿（白名单参数）────────────────────────────── #

def test_new_opener_without_a_redirect_handler_is_red(tmp_path):
    text = (
        "import urllib.request\n"
        "def make():\n"
        "    return urllib.request.build_opener()\n"
    )
    proc = _run(_src(tmp_path, {"af_open.py": text}))
    assert proc.returncode == 1
    assert "[重定向腿]" in proc.stdout


def test_module_level_opener_with_no_redirect_handler_is_green(tmp_path):
    """ha.py 的真实形状：模块级 `build_opener(_NoRedirectHandler)`，直接拒绝跟随 3xx。"""
    text = (
        "import urllib.request\n"
        "class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):\n"
        "    pass\n"
        "_opener = urllib.request.build_opener(_NoRedirectHandler)\n"
    )
    proc = _run(_src(tmp_path, {"af_ha.py": text}))
    assert proc.returncode == 0, proc.stdout
    assert "自建 opener 1 处" in proc.stdout


def test_guarded_open_call_without_allowed_hosts_is_red(tmp_path):
    text = (
        "from .af_adapters.http import guarded_open\n"
        "def fetch(req):\n"
        "    return guarded_open(req, timeout=5)\n"
    )
    proc = _run(_src(tmp_path, {"af_no_hosts.py": text}))
    assert proc.returncode == 1
    assert "[白名单腿]" in proc.stdout


# ── D 腿：反空洞自证 ─────────────────────────────────────────────────── #

def test_zero_outbound_sites_is_range_failure_not_clean(tmp_path):
    proc = _run(_src(tmp_path, {"af_quiet.py": "def add(a, b):\n    return a + b\n"}))
    assert proc.returncode == 2, proc.stdout
    assert "失去射程" in proc.stdout


def test_missing_root_is_range_failure(tmp_path):
    proc = _run(tmp_path / "nope")
    assert proc.returncode == 2
    assert "扫描目录不存在" in proc.stdout


def test_gate_is_registered_in_gates_sh():
    """新增 `check_*.py` 不接进 gates.sh ⇒ 覆盖门判『写了没接』；这条把它钉成双保险。"""
    text = (REPO / "gates.sh").read_text(encoding="utf-8")
    assert "check_outbound_guard.py" in text
    assert "outbound_rc" in text


# ── 代码半边：guarded_open 真的拦得住 ────────────────────────────────── #

def test_guarded_open_refuses_host_off_list_before_touching_the_socket():
    from autoforge.af_adapters.http import OutboundHostNotAllowed, guarded_open

    with pytest.raises(OutboundHostNotAllowed):
        guarded_open("http://evil.example/x", allowed_hosts=("ha.internal",), timeout=1)


def test_guarded_open_refuses_credential_injection():
    from autoforge.af_adapters.http import OutboundHostNotAllowed, guarded_open

    with pytest.raises(OutboundHostNotAllowed):
        guarded_open("http://evil@ha.internal/x", allowed_hosts=("ha.internal",), timeout=1)


def test_guarded_open_refuses_empty_allow_list():
    """不隐式放行：白名单空着 ⇒ 一律拒（与 HTTPAdapter.is_allowed 同口径）。"""
    from autoforge.af_adapters.http import OutboundHostNotAllowed, guarded_open

    with pytest.raises(OutboundHostNotAllowed):
        guarded_open("http://ha.internal/x", allowed_hosts=(), timeout=1)


def test_block_is_catchable_by_the_existing_degradation_chains():
    """四处 catch 的是 URLError/OSError/裸 Exception；拦下必须落进同一档，不能变成崩溃。"""
    from autoforge.af_adapters.http import OutboundHostNotAllowed

    assert issubclass(OutboundHostNotAllowed, urllib.error.URLError)
    assert issubclass(OutboundHostNotAllowed, OSError)


def test_live_stream_default_goes_through_the_guard(tmp_path, monkeypatch):
    """`HAEventStream` 缺省档确实走收口点，且白名单取的是自己那台 base_url。"""
    from autoforge import af_live

    seen = {}

    def fake_guarded(req, *, allowed_hosts, timeout):
        seen["allowed_hosts"] = allowed_hosts
        return req

    monkeypatch.setattr(af_live, "guarded_open", fake_guarded)
    stream = af_live.HAEventStream(base_url="http://ha:8123", token="t", max_retries=0)
    stream._open()
    assert seen["allowed_hosts"] == ("ha",)


def test_live_stream_keeps_the_injected_opener_seam():
    """测试注入档不能被收口点吃掉（否则既有 SSE 判据全部要重写、且真机注入路径失去验证）。"""
    from autoforge import af_live

    seen = []
    stream = af_live.HAEventStream(
        base_url="http://ha:8123", token="t", opener=lambda req, **kw: seen.append(req) or req
    )
    assert stream._open() is seen[0]
    assert len(seen) == 1


def test_registry_fallback_default_uses_base_url_host(monkeypatch):
    from autoforge import af_registry

    seen = {}

    class _Ctx:
        status = 200

        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_guarded(req, *, allowed_hosts, timeout):
        seen["allowed_hosts"] = allowed_hosts
        return _Ctx()

    monkeypatch.setattr(af_registry, "guarded_open", fake_guarded)
    assert af_registry.rest_areas_fallback("http://ha:8123/", "", timeout=1) == {}
    assert seen["allowed_hosts"] == ("ha",)


def test_metrics_post_uses_ma_url_host(monkeypatch):
    from autoforge import af_metrics

    seen = {}

    class _Ctx:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_guarded(req, *, allowed_hosts, timeout):
        seen["allowed_hosts"] = allowed_hosts
        return _Ctx()

    monkeypatch.setattr(af_metrics, "guarded_open", fake_guarded)
    af_metrics.Ingester()._post({"k": 1}, "http://ma:8086", "tok")
    assert seen["allowed_hosts"] == ("ma",)

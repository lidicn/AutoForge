"""临时脚本：验证 AutoForge 只读服务层全部端点（NAS 实测用）。用完即删。"""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8787"
passed = 0
failed = 0


def req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        return resp.status, json.loads(resp.read().decode())


def check(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}  {extra}")
    else:
        failed += 1
        print(f"FAIL  {name}  {extra}")


# 0) 造第二个版本，用于验证 diff
from autoforge.af_ir import load_graph  # noqa: E402
from autoforge.af_store import GraphStore  # noqa: E402

store = GraphStore("/data")
if (store.latest("case01_day_light") or 0) < 2:
    store.save(load_graph("/app/examples/ir/case01_day_light.json"), "case01_day_light", note="verify v2")

ir = json.loads(open("/app/examples/ir/case01_day_light.json", encoding="utf-8").read())
bad = json.loads(open("/app/examples/ir/invalid_case06_delete_all.json", encoding="utf-8").read())

_, body = req("GET", "/api/health")
check("GET /api/health", body["ok"] and body["readonly"], f"v={body.get('version')}")

_, body = req("GET", "/api/graphs")
check("GET /api/graphs", any(i["name"] == "case01_day_light" for i in body["items"]), f"n={len(body['items'])}")

_, body = req("GET", "/api/graphs/case01_day_light")
check("GET /api/graphs/{name}", body["ir"]["id"] == "study_day_light" and bool(body["nl"]), f"v={body['version']}")

_, body = req("POST", "/api/build", {"ir": ir})
check("POST /api/build (ok)", body["ok"] and not body["errors"], f"nl_len={len(body['nl'])}")

_, body = req("POST", "/api/build", {"ir": bad})
check("POST /api/build (L3 拒绝)", (not body["ok"]) and any(d["code"] == "L3_ACTION" for d in body["errors"]))

_, body = req(
    "POST",
    "/api/sim",
    {
        "ir": ir,
        "seed": {"sensor.study_illum": "80", "light.study_main": "off", "binary_sensor.study_motion": "off"},
        "events": [{"entity_id": "binary_sensor.study_motion", "state": "on"}],
    },
)
check("POST /api/sim", body["final_states"]["light.study_main"] == "on", f"instances={len(body['instances'])}")

_, body = req("GET", "/api/conf/case01_day_light")
check("GET /api/conf/{name}", body["items"][0]["band"] == "auto", f"thresholds={body['thresholds']}")
aid = body["items"][0]["automation_id"]

_, body = req("POST", "/api/conf/case01_day_light/intervene", {"automation_id": aid})
check("POST /api/conf/{name}/intervene", body["items"][0]["confidence"] < 1.0, f"conf={body['items'][0]['confidence']}")

_, body = req("GET", "/api/diff?name=case01_day_light&old=1&new=2")
check("GET /api/diff", isinstance(body["structured"], dict), f"render={body['render'][:30]!r}")

_, body = req("GET", "/api/spec/case01_day_light")
spec_text = body["spec"]
check("GET /api/spec/{name}", "automation study_day_light" in spec_text)

_, body = req("POST", "/api/spec/compile", {"text": spec_text})
check("POST /api/spec/compile", body["ok"] and body["ir"]["id"] == "study_day_light")

_, body = req("GET", "/api/faults")
check("GET /api/faults", len(body["kinds"]) == 5 and len(body["failures"]) == 4)

_, body = req("GET", "/openapi.json")
check("GET /openapi.json", "/api/health" in body["paths"])

print(f"\n=== passed={passed} failed={failed} ===")
sys.exit(1 if failed else 0)

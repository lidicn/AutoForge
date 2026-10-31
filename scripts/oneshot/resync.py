import os, sys, urllib.request, json

# G24（2026-10-31）：令牌从环境变量 AF_ACCESS_TOKEN 读——仓是 public，
# 硬编码 Bearer 字面已从 HEAD 移除。查不到就明确报错退出，⛔ 不静默降级成空串。
TOKEN = os.environ.get("AF_ACCESS_TOKEN")
if not TOKEN:
    sys.exit("AF_ACCESS_TOKEN 未设置：resync 需要访问令牌（export AF_ACCESS_TOKEN=...），拒绝用空串继续")

def mcp(method, params=None, id=1):
    body = {"jsonrpc":"2.0","id":id,"method":method}
    if params: body["params"] = params
    req = urllib.request.Request("http://localhost:8787/mcp",
        data=json.dumps(body).encode(),
        headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"},
        method="POST")
    return json.loads(urllib.request.urlopen(req).read().decode())

SPOT="switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1"
DESK="light.yeelink_cn_555003624_lamp22_s_2"

for label,to,action in [("开","on","turn_on"),("关","off","turn_off")]:
    intent={"name":f"书房射灯{label}则挂灯{label}v2","mode":"single",
            "when":{"type":"state","entity":SPOT,"to":to},
            "do":{"action":action,"target":DESK}}
    r=mcp("tools/call",{"name":"af_draft","arguments":{"intent":intent}},id=2)
    d=json.loads(r["result"]["content"][0]["text"])
    print(label,"draft ok=",d.get("ok"),"ref=",d.get("ref"))
    if d.get("ok"):
        a=mcp("tools/call",{"name":"af_apply","arguments":{"ref":d["ref"],"stage":"save"}},id=3)
        ad=json.loads(a["result"]["content"][0]["text"])
        print(label,"apply ok=",ad.get("ok"),"pending=",ad.get("save",{}).get("pending"))

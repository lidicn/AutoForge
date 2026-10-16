import urllib.request, json

def mcp(method, params=None, id=1):
    body = {"jsonrpc":"2.0","id":id,"method":method}
    if params: body["params"] = params
    req = urllib.request.Request("http://localhost:8787/mcp",
        data=json.dumps(body).encode(),
        headers={"Authorization":"Bearer f6eed36d5ef42db00844a13f5cc5dea2717797f112c344e5","Content-Type":"application/json"},
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

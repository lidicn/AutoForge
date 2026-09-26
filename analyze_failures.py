import json

with open(r'D:\Documents\WorkSpace\Test\results\ffl_af_200quiz_nl\results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('=== 期望 build_error 但得到 build_ok (前5题) ===')
count = 0
for item in data.get('details', []):
    if not item.get('passed') and '期望 build_error 但得到 build_ok' in item.get('reason', ''):
        print(f"--- {item['id']} ---")
        print(f"nl: {item.get('nl_prompt', '')}")
        intent = item.get('intent', {})
        print(f"intent: {json.dumps(intent, ensure_ascii=False)[:200]}")
        print()
        count += 1
        if count >= 5:
            break

print('=== ask 不支持 (前3题) ===')
count = 0
for item in data.get('details', []):
    if not item.get('passed'):
        intent_str = json.dumps(item.get('intent', {}), ensure_ascii=False)
        if 'ask' in intent_str.lower():
            print(f"--- {item['id']} ---")
            print(f"nl: {item.get('nl_prompt', '')}")
            print(f"intent: {intent_str[:200]}")
            print(f"error: {item.get('reason', '')[:150]}")
            print()
            count += 1
            if count >= 3:
                break

print('=== af_draft 失败 (前3题) ===')
count = 0
for item in data.get('details', []):
    if not item.get('passed') and 'af_draft 失败' in item.get('reason', ''):
        print(f"--- {item['id']} ---")
        print(f"nl: {item.get('nl_prompt', '')}")
        print(f"intent: {json.dumps(item.get('intent', {}), ensure_ascii=False)[:200]}")
        print(f"error: {item.get('reason', '')[:150]}")
        print()
        count += 1
        if count >= 3:
            break

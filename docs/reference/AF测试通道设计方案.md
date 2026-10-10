# AF 测试通道设计方案

> 对比 AutoFlow 竞技场（1574 行），AF 测试通道追求轻量、高效、可批量。

---

## 一、AutoFlow 竞技场 vs AF 测试通道

| 维度 | AutoFlow 竞技场 | AF 测试通道（本方案） |
|------|----------------|---------------------|
| 代码量 | 1574 行 | ~300 行 |
| 分区 | 3 个固定分区（书房/客厅/卧室） | 动态分区（按测试批次） |
| 出题 | Agent 出题 + 考官审核 + 创造力评分 | 外部题库（FFL/opencode） |
| 验收 | vhass 虚拟孪生 | AF simulate |
| 锁定 | 第一个通过的锁定题目 | 不锁定，纯测试 |
| 排行榜 | 有 | 不需要 |
| 战绩画像 | 有 | 不需要 |
| 错误知识库 | 有（自进化） | 简单统计 |
| 批量 | 单题提交 | 批量 200 条一次过 |
| 人工 approve | 不需要（自动） | 不需要（测试自动 approve） |
| 清理 | 无 | 一键清空测试区 |

---

## 二、核心设计

### 2.1 测试区隔离

```
/data/                    # 正式环境（不动）
├── pending/              # 正式待批队列
├── graphs/               # 正式 GraphStore
└── ...

/data/test/               # 测试环境（完全隔离）
├── pending/              # 测试待批队列（自动 approve）
├── graphs/               # 测试 GraphStore
├── reports/              # 测试报告
└── batches/              # 批次记录
```

### 2.2 新增 MCP 工具

| 工具 | 用途 | 参数 |
|------|------|------|
| `af_test_submit` | 批量提交测试自动化 | `intents: list`, `batch_id: string` |
| `af_test_report` | 获取测试报告 | `batch_id: string` |
| `af_test_clear` | 清空测试区 | 无 |

### 2.3 批量提交流程

```
FFL 提交 200 条 intent JSON
    ↓
af_test_submit(intents, batch_id="ffl-r1")
    ↓
逐条处理：
  1. af_draft(intent) → IR
  2. build 安全闸
  3. simulate 仿真回放
  4. 自动 approve（测试区，不需要人工）
  5. 保存到 /data/test/graphs/
    ↓
生成报告：
  - 总数 / PASS / FAIL
  - 失败原因分类
  - 每题详情（intent + 错误）
    ↓
保存到 /data/test/reports/batch_id.json
    ↓
返回报告摘要
```

### 2.4 测试报告格式

```json
{
  "batch_id": "ffl-r1",
  "total": 200,
  "pass": 135,
  "fail": 65,
  "pass_rate": "67.5%",
  "fail_reasons": {
    "ask_not_supported": 20,
    "complex_intent": 10,
    "entity_not_found": 5,
    "expect_mismatch": 30
  },
  "details": [
    {
      "id": "T001",
      "nl_prompt": "...",
      "intent": {...},
      "passed": true,
      "build_ok": true,
      "sim_ok": true,
      "graph_ref": "test:auto_xxx"
    }
  ]
}
```

---

## 三、实现方案

### 3.1 新增文件

```
src/autoforge/
├── af_test.py          # 测试通道核心（~200 行）
└── af_mcp.py           # 注册 3 个新工具（+30 行）
```

### 3.2 af_test.py 核心类

```python
class TestChannel:
    """测试通道：批量提交 + 自动 approve + 报告生成。"""

    def __init__(self, test_root: str = "/data/test"):
        self.test_root = Path(test_root)
        self.pending_dir = self.test_root / "pending"
        self.graphs_dir = self.test_root / "graphs"
        self.reports_dir = self.test_root / "reports"

    def submit_batch(self, intents: list[dict], batch_id: str) -> dict:
        """批量提交测试自动化。"""
        results = []
        for idx, intent in enumerate(intents):
            result = self._submit_one(intent, f"{batch_id}-{idx:03d}")
            results.append(result)

        report = self._gen_report(batch_id, results)
        self._save_report(batch_id, report)
        return report

    def _submit_one(self, intent: dict, test_id: str) -> dict:
        """单条提交：draft → build → simulate → auto approve → save。"""
        # 1. draft
        draft_result = draft_intent(intent)
        if not draft_result["ok"]:
            return {"id": test_id, "passed": False, "stage": "draft",
                    "error": draft_result["error"]}

        # 2. build + simulate
        apply_result = apply(draft_result["ref"], stage="simulate")
        if not apply_result["ok"]:
            return {"id": test_id, "passed": False, "stage": "build_sim",
                    "error": apply_result.get("build", {}).get("errors")}

        # 3. 自动 approve（测试区）
        # 4. 保存到测试 GraphStore
        graph_ref = self._save_graph(test_id, apply_result)

        return {"id": test_id, "passed": True, "graph_ref": graph_ref}

    def get_report(self, batch_id: str) -> dict:
        """获取测试报告。"""
        return self._load_report(batch_id)

    def clear(self) -> dict:
        """清空测试区。"""
        shutil.rmtree(self.test_root, ignore_errors=True)
        self.test_root.mkdir(parents=True)
        return {"ok": True, "cleared": True}
```

> **落地后的偏离（2026-10-10 · 执行记录 §二之八十六）**：上面这段是当初的设计草图，原文保留不追改。落地后的 `clear()` 与它有两处不同——① 删之前必过形状守卫 `assert_test_area_deletable(test_root, protected_root)`（目标就是盘根，或等于/包住这个进程真正在用的正式存储根 ⇒ 拒判），`protected_root` 由调用方现递、是必填关键字参数；② 草图里那个"删完直接报成功"的返回形状已撤，残留改成按盘面数出来的 `residual`，部分失败如实回 `ok: false` 并带封顶 20 条的现场明细。口径详见 `docs/architecture/AF完整架构与运行时说明.md` §十 与 `AF完整知识文档.md` §九。

### 3.3 MCP 工具注册


```python
# af_mcp.py 新增
def _t_test_submit(args):
    intents = args.get("intents", [])
    batch_id = args.get("batch_id", f"batch-{int(time.time())}")
    return test_channel.submit_batch(intents, batch_id)

def _t_test_report(args):
    batch_id = args["batch_id"]
    return test_channel.get_report(batch_id)

def _t_test_clear(args):
    return test_channel.clear()
```

---

## 四、使用流程

### 4.1 FFL 批量测试

```bash
# 1. FFL 读 200 题题库
# 2. 翻译成 intent JSON 列表
# 3. 一次调用 af_test_submit

curl -X POST http://192.168.2.200:8787/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "af_test_submit",
      "arguments": {
        "batch_id": "ffl-r2",
        "intents": [
          {"name": "...", "when": {...}, "do": {...}},
          ... 200 条
        ]
      }
    }
  }'

# 4. 返回报告摘要
# 5. 需要详情时调 af_test_report
```

### 4.2 清理测试区

```bash
curl -X POST http://192.168.2.200:8787/mcp \
  -H "Authorization: Bearer <token>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"af_test_clear"}}'
```

---

## 五、安全保障

| 保障 | 说明 |
|------|------|
| 测试区隔离 | /data/test/ 与 /data/ 完全分离，不影响正式 |
| 自动 approve | 测试区不需要人工 approve |
| 不碰真机 | 只过 build + simulate，不启动 watch |
| 一键清理 | af_test_clear 清空测试区 |
| 限流 | 单批次上限 500 条，防止滥用 |
| 鉴权 | 需要 write scope token |

---

## 六、对比现有方案

| 方案 | 优点 | 缺点 |
|------|------|------|
| stage="save" 逐条 | 简单 | 队列上限 20，200 条熔断 |
| stage="simulate" 逐条 | 不熔断 | 不落盘，无法后续验证 |
| **af_test_submit 批量** | 一次过、自动 approve、落盘测试区、生成报告 | 需要新增模块 |

---

## 七、实现优先级

1. **P0**：af_test.py 核心（submit_batch + report + clear）
2. **P0**：MCP 工具注册
3. **P1**：测试报告导出（JSON/Markdown）
4. **P2**：测试区与正式区的 diff 对比
5. **P2**：错误原因分类统计

---

## 八、总结

AF 测试通道比 AutoFlow 竞技场轻量得多：
- ❌ 不需要分区管理（动态批次）
- ❌ 不需要出题审核（外部题库）
- ❌ 不需要排行榜/战绩
- ❌ 不需要错误知识库自进化
- ✅ 需要批量提交（200 条一次过）
- ✅ 需要自动 approve（测试区）
- ✅ 需要测试报告
- ✅ 需要一键清理

预计代码量 ~300 行，是 AutoFlow 竞技场的 1/5。

# AutoForge round-003 审计报告 · 核实与处置

> 核实日期：2026-10-11
> 原报告：`AutoForge_round-003_report.md`（auditkit 静态分析自动生成）

## 核实结论

**47 条 high 全部为静态分析误报，无需代码修复。**

## 逐条核实

### 1. py.unbounded_container（35 条 high）

**结论：误报。**

审计规则检测"类属性只进不出、无 maxlen 限制"，但未区分对象生命周期：

| 类型 | 数量 | 说明 |
|------|------|------|
| 测试 mock 对象 | ~15 | `MockAdapter`、`FakeHAAdapter`、`FakeSSEStream` 等，测试结束即销毁 |
| 一次性构建器 | ~10 | `Visitor`、`_Builder`、`GraphView`、`EvoScanner` 等，单次调用后释放 |
| 常驻设计容器 | ~10 | `TokenRegistry._revoked`、`EventBus.emitted`、`Runtime._exec_stats` 等，数据量可控或有定期清理机制 |

### 2. py.except_return_failopen（2 条 high）

**结论：误报。** 规则只看"except 里 return True"，未看 True 的语义。

| 位置 | 代码 | 实际语义 |
|------|------|----------|
| `af_canary.py:103` | `except UnknownEntity: return True` | 读不到实体状态时**保守判"有漂移"**，触发回滚——是 fail-closed 设计（铁律 #5：EXEMPT ≠ VERIFIED） |
| `af_flock.py:132` | `except (BlockingIOError, OSError): return True` | 抢锁失败时返回"锁被持有"——是正确的布尔语义 |

### 3. py.json_parse_unguarded（1 条 high）

**结论：误报。**

报告指 `af_cli.py:1062`，实际代码（当前版本第 1090 行）：
```python
for item in json.loads(Path(events).read_text(encoding="utf-8")):
```
`json.loads` 在**循环外**调用一次，遍历其结果；并非"循环内每条都解析"。

### 4. GEN-01-hardcoded-secret（9 条 high）

**结论：误报。**

全部位于测试文件：
- `tests/contract/test_af_ma_metrics_contract.py:101` — 测试用假 token `butler-tok-123`
- `tests/unit/test_access_log_token_mask.py:34` — 测试用假密钥 `SEKRIT-9f2c41be`
- `tests/unit/test_sse_pair_request_stream.py` — 测试用假 token `tok-nope`/`tok-reporter`
- `tests/unit/test_v1_4_credentials.py` — 测试用假 token `supersecret12345` 等
- `ui-user-mimo/tests/store.test.mjs:16` — 前端测试用假凭据

均为测试夹具，非生产密钥。

## 处置

- 无需代码修改
- 原报告与本核实报告一并移入 `docs/audit/归档/`

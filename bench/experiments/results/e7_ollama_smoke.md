# E7 真实模型冒烟验证（Ollama）

- 模型：`qwen2.5:1.5b`
- 接口：`http://localhost:11434/v1`
- Python：3.11.16
- 平台：Windows-10-10.0.26200-SP0
- stp：0.2.0

| 运行 | 状态 | 输出 | 模型调用 | 重试 | 事件数 | 耗时 ms |
|---:|---|---|---:|---:|---:|---:|
| 1 | SUCCEEDED | `{"status": "recorded", "category": "travel", "amount": 128}` | 1 | 0 | 12 | 880.89 |
| 2 | SUCCEEDED | `{"status": "recorded", "category": "travel", "amount": 128}` | 1 | 0 | 12 | 243.23 |
| 3 | SUCCEEDED | `{"status": "recorded", "category": "travel", "amount": 128}` | 1 | 0 | 12 | 230.77 |

- 成功率：3/3
- 耗时中位数：243.23 ms

> 这是可用性冒烟验证，不是系统性评估；主实验 E1–E6 仍使用 MockLLM。

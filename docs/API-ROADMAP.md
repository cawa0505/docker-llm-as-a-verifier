# API Coverage Roadmap

Upstream `llm-verifier` 提供的功能與 docker-llm-as-a-verifier HTTP API 的覆蓋差距。

## 已提供

| HTTP 端點 | 上游函式 | 狀態 |
|-----------|----------|------|
| `POST /v1/compare` | `compare()` | ✅ |
| `POST /v1/select` | `select()` | ✅ |
| `POST /v1/track` | `track()` | ✅ |
| `POST /v1/score-pairs` | `score_pair_criterion()` (簡化批次) | ✅ |

## 待包裝（依優先順序）

### P3 — 細粒度評分控制
- **`score_pair_criterion(client, problem, trace_a, trace_b, criterion, ground_truth_note, model, images)`**
- 單一 criterion 的細粒度獎勵，`compare` 的底層建構塊
- **`directed_reward(scores, task_name, a, b, criteria_ids, n_reps)`**
- 純運算，從已評分資料計算導向獎勵 (R_a, R_b)

### P4 — 工具函式
- `load_prompts(path)` — 從檔案載入評分標準
- `format_usage(usage)` — 格式化 token 消耗報告
- `create_client()` — 從環境變數自動建立 OpenAI client
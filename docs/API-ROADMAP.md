# API Coverage Roadmap

Upstream `llm-verifier` 提供的功能與 docker-llm-as-a-verifier HTTP API 的覆蓋差距。

## 已提供

| HTTP 端點 | 上游函式 | 狀態 |
|-----------|----------|------|
| `POST /v1/compare` | `compare()` | ✅ |
| `POST /v1/select` | `select()` | ✅ |
| `POST /v1/track` | `track()` | ✅（extraction 失敗自動重試，見 README） |
| `POST /v1/score-pairs` | `score_pair_criterion()` (簡化批次) | ✅ |
| `GET /v1/usage` | `token_usage()` / `format_usage()` | ✅ |
| `POST /v1/directed` | `score_directed_pairs()` / `directed_reward()` | ✅ |

## 待包裝（依優先順序）

### P4 — 工具函式
- `load_prompts(path)` — 從檔案載入評分標準（client 端工具，不適合 HTTP 包裝）
- `create_client()` — 從環境變數自動建立 OpenAI client（server 已自建，不需暴露）

## 刻意不包裝

- 圖片輸入（`compare/select/track` 的 `images` 參數）：server 端
  `call_openai` 已支援 `image_url`，但現行 backend (Qwen3.5-9B) 為純文字，
  無法驗證。等 VLM backend 再議。`[待討論]`

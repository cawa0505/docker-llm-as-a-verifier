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

## 圖片輸入

| HTTP 端點 | 支援 | 說明 |
|-----------|------|------|
| `POST /v1/compare` | ✅ | `images` 欄位傳入 base64 data URI 或 HTTP(S) URL |
| `POST /v1/select` | ✅ | 同上 |
| `POST /v1/track` | ✅ | 同上 |
| `POST /v1/score-pairs` | ✅ | 同上 |
| `POST /v1/directed` | ✅ | 同上（經由 `tasks` 傳遞） |

- 支援格式：`data:image/*;base64,...`（data URI）或 `http(s)://...`（遠端 URL）
- 檔案路徑（`/etc/passwd`、`/app/cache/...`）一律拒絕回 422
- 需要 VLM backend 才能實際評分；純文字 backend 會給出隨機分數

## 待包裝（依優先順序）

### P4 — 工具函式
- `load_prompts(path)` — 從檔案載入評分標準（client 端工具，不適合 HTTP 包裝）
- `create_client()` — 從環境變數自動建立 OpenAI client（server 已自建，不需暴露）

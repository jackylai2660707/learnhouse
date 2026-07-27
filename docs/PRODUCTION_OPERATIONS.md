# LearnHouse 生產運維手冊

本文件說明澳門校內 Pilot 的生產啟動、資料庫升級、健康檢查、備份與故障處理。所有操作都應先在測試副本驗證，不要直接在唯一一份學校資料上試驗。

## 生產啟動門禁

容器現在依照以下順序啟動：

```text
production_preflight.py
  -> production_migrate.py
       -> PostgreSQL advisory lock
       -> Fresh：建立目前 schema 並 stamp head
       -> Legacy：驗證 baseline、stamp、upgrade head
       -> Versioned：upgrade head
  -> web / API / collab / durable PDF worker
  -> nginx
```

任何預檢或 migration 失敗都會阻止 web、API、協作服務和 nginx 啟動。錯誤訊息只會顯示設定名稱和修正方向，不會顯示密碼、token 或完整資料庫連線字串。

## 首次部署

1. 以根目錄的 `.env.production.example` 建立部署環境設定。
2. 為 JWT、NextAuth、協作服務和資料庫分別產生獨立隨機密碼，不要重用。
3. 設定真實 HTTPS 網域、PostgreSQL、Redis、AI 模型與持久化檔案儲存。
4. 只有首次建立管理員時才設定 `LEARNHOUSE_BOOTSTRAP_ADMIN=True` 和初始管理員密碼。
5. 官方 CLI 在組織成功建立後會自動移除 `LEARNHOUSE_INITIAL_ADMIN_PASSWORD`、關閉 bootstrap，並把 `.env` 權限收緊為 `0600`；手動部署必須完成相同操作。
6. 若啟用 SMTP，建立 host-side `0600` password file，並在 `.env` 設定
   `LEARNHOUSE_SMTP_PASSWORD_FILE_HOST` 指向它；Compose 的 tracked empty
   placeholder 只讓乾淨 checkout 可啟動，不能用作正式 SMTP credential。
7. 若使用 Compose 的正式 Caddy route，先確認 external `caddy_net` 已由 Caddy
   建立；沒有既有 Caddy network 時，可明確建立空 network 再部署：
   `rtk docker network inspect caddy_net || rtk docker network create caddy_net`。
8. 執行預檢，再建立或啟動容器。

手動預檢命令：

```bash
cd apps/api
rtk uv run python scripts/production_preflight.py --env-file ../../.env
```

## 資料庫分類

Migration runner 會把資料庫分為三類：

| 狀態 | 判斷 | 動作 |
| --- | --- | --- |
| Fresh | 沒有任何 public table | 建立目前 SQLModel schema 和 `vector`，stamp fresh reconciliation baseline，再 upgrade head |
| Versioned | 存在 `alembic_version` | 直接 `upgrade head` |
| Legacy unversioned | 有業務 table，但沒有 `alembic_version` | 必須提供已驗證 baseline，先 stamp 再 upgrade |

`LEARNHOUSE_ALEMBIC_BASELINE` 不是通用版本號。它必須是「目前資料庫 schema 實際對應的舊鏡像 revision」，而且必須是現有 migration head 的祖先。設定錯誤的 baseline 可能令 Alembic 跳過必要 schema 變更。

歷史演練使用的舊運行鏡像 migration head 是 `bc3d4e5f6a7b`。2026-07-12 已使用
production database 的只讀 dump 在隔離 PostgreSQL 16 副本完成 baseline、upgrade、
資料計數、fresh install 和 API smoke 演練，詳見
`PRODUCTION_DB_MIGRATION_REHEARSAL_2026-07-12.md`。

2026-07-27 continuation 已將本 Pilot production 推進至
`i0j1k2l3m4n5`；h10 的 assignment JSON 修復已完成，並以 strict isolated
restore 驗證最新 backup `20260727T180112Z`。本 dirty worktree 是這個 Pilot
目前唯一的 canonical migration history；不要把修改過的 historical revision
套用到未經核准的其他 database，也不要猜測 legacy baseline。

新環境或尚未部署的副本仍必須完成以下程序：

1. 建立 PostgreSQL 完整備份及 volume snapshot。
2. 確認備份可解析，並與已通過演練的 aggregate counts 比較。
3. 使用本次已驗證的 image/code 和 baseline 執行 startup migration。
4. 執行 API 測試和學校核心流程驗收。
5. 確認 schema、資料筆數、登入、作業和成績表正常後，才恢復流量。

目前 production 不需要再次 stamp；任何後續 schema change 仍必須先備份、隔離演練，
再透過正常 startup migration gate 推進。

## 發佈前檢查

```bash
cd apps/api
rtk timeout 300 uv run pytest -q \
  src/tests/migrations/test_migration_graph.py \
  src/tests/scripts/test_production_preflight.py \
  src/tests/scripts/test_production_migrate.py
rtk uv run alembic heads
rtk sh -n ../../docker/start.sh
```

還需要完成以下人工檢查：

- `.env` 不含首次安裝管理員密碼。
- `LEARNHOUSE_ENV=production`，development mode 已關閉。
- 所有公開 URL 使用 HTTPS，協作 URL 使用 WSS。
- PostgreSQL、Redis 和內容檔案都有離機備份。
- AI 的文字、生圖、embedding 模型各自完成一次真實請求。
- 管理員、老師、學生三種帳號完成核心流程 smoke test。

## 健康檢查

容器和 web health proxy 使用 readiness：

```text
GET /api/v1/health/ready
```

監控應分開使用以下端點：

| 端點 | 用途 | 失敗語意 |
| --- | --- | --- |
| `GET /api/v1/health/live` | 只確認 API process 活著 | 不依賴 PostgreSQL、Redis 或外部 provider |
| `GET /api/v1/health/ready` | 確認 PostgreSQL、Redis、Alembic head 和核心設定 | required check 失敗回 503；AI、郵件、Sentry、離機備份等 optional check 只標示 degraded |
| `GET /api/v1/health` | 舊監控相容入口 | 保留 boolean 200/503，不建議新監控使用 |
| `GET /api/health` | Next.js 對 backend readiness 的代理 | backend timeout 或 unready 時回 503 |

外部監控還應檢查：

- 首頁和登入頁回傳成功。
- API health endpoint 正常。
- Redis、PostgreSQL 連線正常。
- 作業建立、學生提交、系統批改、老師覆核、成績匯出可完成。
- 協作 websocket 可以建立連線。

health response 只包含 stable code、latency 和 configured/required 狀態，不會回傳 hostname、connection string、API key、DSN 或原始 provider exception。

Embedding 是可選能力：Compose 會啟動 `embeddings` service，但
`learnhouse-app` 不會等待它通過 healthcheck，也不會因它無法啟動而停止學校
核心流程。Embedding backend 失敗會在 readiness 以 `required=false` 的
`embeddings_degraded:*` 顯示，整體可為 `degraded` 但仍回 200；RAG 請求會明確
失敗，不會回退為未標示的伪語意搜尋。

Bundled executor 在首次 `docker compose up --build` 時會在自己的啟動階段依序建立
Python、Node/TypeScript、C/C++、Java 四個本機 sandbox image；不會新增 Compose
service，也不會執行 image prune。executor 在四個 configured image 都存在前維持
unhealthy，API 會等待它而不接受可能無法執行的程式作業。若首次建立失敗，先查看
安全的服務日誌，再只重試 executor；不要用 broad prune：

```bash
rtk docker compose logs --tail=200 executor
rtk docker compose up -d --build executor
```

## 日誌與 Sentry

- Production API 只向 stdout 輸出單行 JSON，由 Docker/1Panel 收集；不再依賴 container 內的 `logs/learnhouse.log`。
- 每個 HTTP response 都回傳 `X-Request-ID`；合法 incoming request ID 會保留，其他情況由 API 產生。
- Access event 只記錄 `request_id`、method、path、status、duration，不記錄 query string、request body、Authorization 或 Cookie。
- password、secret、token、API key、cookie、DSN、帶憑證 URL 和 bearer token 會在 formatter 層遞迴遮罩。
- API 與 web/edge/client Sentry 都關閉 default PII；error/transaction 使用相同遮罩，web log capture 和 session replay 關閉。request body、query、cookie、email、username 和 IP 不送出。
- 查詢問題時先用 response 的 `X-Request-ID` 對照 API JSON log，不要要求老師或學生傳送密碼、cookie 或完整 request dump。

## 外部整合診斷

使用 operator-only diagnostic；它只使用合成繁體中文內容，不讀取 production 課程、學生答案或真實郵箱，也不會輸出 endpoint、API key、provider body 或 prompt：

```bash
rtk docker/integration-diagnostics.sh --text --embedding --judge0 --email \
  --report /var/lib/learnhouse/integration-diagnostics/latest.json
```

這個 wrapper 只會在已運行的 `learnhouse-app` 容器內執行診斷，不會 build、啟動或重啟服務；因此 bundled `executor`、`embeddings` 的 Compose DNS 與 production 設定和真實請求完全一致。`--report` 仍寫到 host 指定路徑，權限為 `0600`。

生圖會產生 provider 成本，必須明確加入：

```bash
rtk docker/integration-diagnostics.sh --image --allow-costly-image
```

Exit code：ready=`0`、degraded/skipped=`1`、failed=`2`。Active diagnostic 不接到匿名 readiness，避免任何人觸發付費 AI 或寄信。

2026-07-12 工作樹驗證狀態：

| 能力 | 驗證結果 | Fallback / 待處理 |
| --- | --- | --- |
| OpenAI-compatible 文字 | Live synthetic marker 成功 | Provider timeout/error 只回 stable code |
| `gpt-image-2` 生圖 | Live 產生有效 PNG，Pillow 驗證 bytes/格式/尺寸成功 | 失敗可手動上傳圖片；URL output 有 HTTPS/SSRF/大小限制 |
| Embedding | Bundled `jinaai/jina-embeddings-v2-base-zh` 提供 768 維中英語義向量 | Backend 不可用時預設明確失敗；lexical hash 只可由運維顯式 opt-in，且會標示 degraded |
| Bundled executor（Judge0-compatible API） | Live Python accepted、runtime error、time-limit 狀態成功 | Timeout/unavailable 保留學生既有成績並允許重試/老師覆核；不要切回外部 Judge0 |
| PDF/RAG | 合成 PDF extraction、course persistence/index failure、source scope/index metadata tests 通過 | 未對 production 課程執行建課或 reindex |
| Email | 2026-07-27 synthetic SMTP/production configuration diagnostic ready | 實際 sender/domain/credential 仍由運維管理；不要把 synthetic sink 當正式寄信證據 |

## 備份與還原

### 備份內容與保證

`docker/backup.sh` 是非互動 runner，預設備份 PostgreSQL custom dump、content archive 和 Redis RDB。每個成功備份目錄包含：

```text
YYYYMMDDTHHMMSSZ/
  database.dump
  content.tar.gz
  redis.rdb
  manifest.json
  SHA256SUMS
  SUCCESS
```

- Output root 是 `0700`，metadata/artifact 是 `0600`。
- `.backup.lock` 防止兩個排程同時執行。
- 只有完整寫入 checksum 和 `SUCCESS` 後才會把 staging directory 原子改名。
- 失敗不會刪除上一份成功備份，並寫入不含秘密的 `LAST_FAILURE.json`。
- Retention 只刪除帶 `SUCCESS` 的舊備份，並永遠保留設定的最少份數。
- `LEARNHOUSE_BACKUP_OFFSITE_HOOK` 必須是可執行的絕對路徑；憑證由 hook 自己的 `0600` 設定檔讀取，不要放在命令列。
- Off-site hook 失敗時，本地成功備份仍保留，但 runner 回非零 exit code、跳過 retention，並在 manifest/`LAST_FAILURE.json` 標示失敗，讓排程和 operations report 告警。

建立設定：

```bash
install -d -m 0700 /etc/learnhouse /var/backups/learnhouse
install -m 0600 docker/backup.env.example /etc/learnhouse/backup.env
```

首次只在隔離環境或人工監督下執行：

```bash
rtk docker/backup.sh
rtk docker/backup.sh --status
rtk docker/verify-backup.sh --latest-from /var/backups/learnhouse \
  --report /var/lib/learnhouse/backup-verification/latest.json
```

若部署前備份的資料庫仍在舊 revision，而待部署工作樹已包含新 migration，必須
使用顯式的 pre-migration 模式驗證。這個模式仍要求隔離還原後的 revision 與備份
manifest 記錄的 `alembic_current` 完全一致，只容許它不同於工作樹 head；預設驗證
仍維持嚴格模式。成功輸出與 private report 會明確列出 current revision、預期
head、`at_expected_head=false` 和 `pre_migration_mismatch_allowed` 策略：

```bash
rtk docker/verify-backup.sh --latest-from /var/backups/learnhouse \
  --allow-migration-head-mismatch \
  --report /var/lib/learnhouse/backup-verification/pre-deploy.json
```

完成 migration 後必須再建立並以預設嚴格模式驗證新備份；不得把上述
`at_expected_head=false` 的 pre-migration 報告當作部署後健康備份。

`docker/backup.sh --status` 是不讀取資料內容的 operations report：最新成功備份超過 `LEARNHOUSE_BACKUP_MAX_AGE_HOURS` 時回 unhealthy/exit 2；off-site 未完成、Redis optional snapshot 缺失或 Alembic head 不一致時回 degraded/exit 1；全部正常回 exit 0。

Restore verifier 會先驗證 manifest、SHA-256、private permissions、content path traversal 和 Redis header，再啟動 `--network none`、tmpfs PostgreSQL container，執行 `pg_restore --list`、完整 restore、Alembic head、核心 table 和 aggregate count 檢查。無論成功或失敗都會刪除 container；不會操作 production schema。

### systemd 排程

Repo 提供：

- `docker/systemd/learnhouse-backup.service`
- `docker/systemd/learnhouse-backup.timer`
- `docker/systemd/learnhouse-restore-verify.service`
- `docker/systemd/learnhouse-restore-verify.timer`
- `docker/systemd/learnhouse-rag-verify.service`
- `docker/systemd/learnhouse-rag-verify.timer`

安裝後，先人工執行三個 service，確認 exit code 和報告，再啟用 timer：

```bash
install -m 0644 docker/systemd/learnhouse-backup.service /etc/systemd/system/
install -m 0644 docker/systemd/learnhouse-backup.timer /etc/systemd/system/
install -m 0644 docker/systemd/learnhouse-restore-verify.service /etc/systemd/system/
install -m 0644 docker/systemd/learnhouse-restore-verify.timer /etc/systemd/system/
install -m 0644 docker/systemd/learnhouse-rag-verify.service /etc/systemd/system/
install -m 0644 docker/systemd/learnhouse-rag-verify.timer /etc/systemd/system/
install -m 0600 docker/rag-verify.env.example /etc/learnhouse/rag-verify.env
systemctl daemon-reload
systemctl start learnhouse-backup.service
systemctl start learnhouse-restore-verify.service
systemctl start learnhouse-rag-verify.service
systemctl enable --now learnhouse-backup.timer learnhouse-restore-verify.timer learnhouse-rag-verify.timer
systemctl list-timers 'learnhouse-*'
```

### cron / 1Panel 範例

Cron 每日備份：

```cron
15 2 * * * cd /opt/1panel/docker/compose/learnhouse && /usr/bin/flock -n /run/learnhouse-backup.cron.lock ./docker/backup.sh >> /var/log/learnhouse-backup.log 2>&1
```

Cron 每月隔離還原：

```cron
30 4 1 * * cd /opt/1panel/docker/compose/learnhouse && ./docker/verify-backup.sh --latest-from /var/backups/learnhouse --report /var/lib/learnhouse/backup-verification/latest.json >> /var/log/learnhouse-restore-verify.log 2>&1
```

1Panel 建立「Shell 腳本」計劃任務時使用相同完整命令，執行使用者必須可以存取 Docker socket。先設定失敗通知，再啟用每天 `02:15` 備份和每月 1 日 `04:30` restore verifier。`LEARNHOUSE_BACKUP_ALERT_HOOK` 和 `LEARNHOUSE_RESTORE_ALERT_HOOK` 會在失敗時收到 `LEARNHOUSE_OPERATION_CODE`，可接企業微信、電郵或其他校內告警腳本。

### RAG 只讀驗證

`docker/verify-rag.sh` 不會 reindex 或修改資料。它在應用容器內執行
`reembed_courses.py --verify-only`，檢查繁體中文及英文語義分離、768 維、
向量 norm、pgvector >= 0.8，以及 cosine HNSW index。Aggregate JSON 報告以
`0600` 原子寫入 `LEARNHOUSE_RAG_VERIFY_REPORT`，不包含課程名稱、UUID、教材或
學生內容。失敗時可設定 root-owned absolute executable
`LEARNHOUSE_RAG_VERIFY_ALERT_HOOK`；hook 只收到固定事件名稱和 private report
path，不會 eval shell 片段。

人工只讀驗證：

```bash
rtk docker/verify-rag.sh
```

Verifier 非零表示 embedding backend、語義品質、pgvector 版本、index 或已存
向量至少一項不合格。先檢查 private report；不要以 reindex 當作未知故障的
第一個處理步驟。

### 運維最低要求

- PostgreSQL 每日自動備份，至少保留 30 天，另存到不同主機或物件儲存。
- LearnHouse content volume 和 Redis persistence 每日一併備份。
- 每月至少完成一次隔離環境還原演練，並記錄還原時間和驗證結果。
- 每日檢查 timer/1Panel exit code；off-site 或 restore verification 失敗必須告警。

未通過還原演練的備份不能視為可用備份。

## 驗證與部署狀態

| 項目 | 狀態 |
| --- | --- |
| Liveness/readiness、web proxy、Docker healthcheck | 2026-07-27 已驗證五個 Compose service healthy；required checks healthy，唯一 optional degradation 是 `offsite_backup_unconfigured` |
| CSRF/CORS/cookie/auth/org scope | 自動測試及正式管理員、教師、學生 browser 流程已驗證；browser P1 assignment/RAG/PDF/preview/mobile smoke 已完成 |
| JSON logging、request ID、redaction、Sentry before-send | 日誌輪替為 10 MB × 5 並壓縮；Sentry 無 DSN，維持 optional disabled；整合診斷不輸出 private data |
| Backup runner/restore verifier | backup `20260727T180112Z` strict isolated restore 成功，60 tables、current/expected `i0j1k2l3m4n5`，Redis RDB included |
| 每日 timer、每月 restore timer、off-site hook、alert hook | timers/templates 已存在；off-site destination/hook/credentials 尚未配置，維持 degraded 並須運維補充 |
| Production database migration / deployment | image digest `sha256:2e38eaf09d7bea6462df379d3084857e8003edb1ce11070b1dd2af92a86d523e` 已運行；Alembic head `i0j1k2l3m4n5` |

目前非核心阻塞但必須持續追蹤：尚無離站目的地／告警 hook，以及尚未配置
Sentry DSN。主機約有 31 GiB 可用磁碟；不得把本機成功備份誤報為離站備份。

## Migration 故障處理

啟動被阻止時：

1. 保持應用服務停止，不要反覆修改 baseline 嘗試啟動。
2. 保存錯誤日誌，但不要把 `.env` 或完整連線字串貼到工單。
3. 檢查 migration graph 是否仍只有一個 head。
4. 對資料庫建立新備份。
5. 在備份還原副本重現問題並修正 migration。
6. 驗證後再重新發佈。

不要用 `alembic stamp head` 強行繞過失敗的 migration，也不要以 `SQLModel.metadata.create_all()` 代替 schema 升級。

## 回滾原則

程式碼可以回滾到上一個鏡像，但 schema 回滾必須依 migration 的可逆性和資料風險另行評估。若新 migration 已寫入或轉換資料，優先採用向前修復，不要在沒有備份驗證的情況下直接 downgrade 生產資料庫。

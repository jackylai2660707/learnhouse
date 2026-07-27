# 生產資料庫 Migration 隔離演練報告

日期：2026-07-12

範圍：澳門校內 Pilot 生產 PostgreSQL 的只讀備份、隔離還原、legacy migration、fresh install 和 API smoke。

結論：**通過，沒有修改或重啟線上資料庫與應用。**

## 隔離與私隱

- 來源資料庫只執行 `pg_dump` 和 aggregate read queries。
- 還原使用獨立 `pgvector/pgvector:pg16` 容器、獨立 volume、`127.0.0.1` 隨機 port，沒有加入 LearnHouse production network。
- 報告只保存 schema metadata、版本、大小和 row counts，不保存姓名、電郵、提交內容、token、密碼或 database URL。
- 演練完成後已刪除 dump、臨時密碼、API response、PostgreSQL/Redis 容器和 Docker volume。

## 來源證據

| 項目 | 結果 |
| --- | --- |
| PostgreSQL | 16.14 |
| Database size | 15,293,463 bytes |
| Public tables | 57 |
| `alembic_version` | 不存在 |
| Dump format | PostgreSQL custom archive，可由 `pg_restore --list` 解析 |
| Dump size | 471,184 bytes |
| Dump SHA-256 | `620d11f7dbedd749a38b39328d88976b9d838dcb02bb6b1ca14733d36c6b85b9` |

## Legacy 還原與 Migration

還原副本正確重現「有 57 個業務 tables、沒有 `alembic_version`」的 production legacy 狀態。以已審計 baseline `bc3d4e5f6a7b` 執行 production runner 後，一次完成：

```text
stamp bc3d4e5f6a7b
upgrade cd4e5f6a7b8c
upgrade d5e6f7a8b9c0
upgrade e6f7a8b9c0d1
upgrade f7g8h9i0j1k2
```

再次執行 runner 時被識別為 `versioned`，沒有重複 stamp 或 schema 變更。

### 資料計數

| Table | Migration 前 | Migration 後 |
| --- | ---: | ---: |
| organization | 1 | 1 |
| user | 2 | 2 |
| course | 5 | 5 |
| assignment | 20 | 20 |
| assignmenttask | 40 | 40 |
| assignmentusersubmission | 0 | 0 |
| assignmenttasksubmission | 0 | 0 |
| questionbankitem | 78 | 78 |
| selftestattempt | 7 | 7 |
| code_submission | 0 | 0 |

Migration 後新增 `alembic_version` 和 `assignmentremediationpractice`，public table count 由 57 變為 59；新 remediation table 為空，符合預期。

### Schema 驗證

- Alembic head：`f7g8h9i0j1k2`。
- Assignment Pilot columns：11/11 存在。
- Submission review columns：3/3 存在。
- Question bank metadata columns：4/4 存在。
- Remediation indexes：3/3 存在，名稱符合 PostgreSQL 63-character 限制。
- 相關 performance indexes：6/6 存在。
- `assignmenttasktypeenum.ESSAY`：存在一次。
- 隔離 API `/api/v1/health`：HTTP 200，回傳 `true`。
- 隔離 API organization read：HTTP 200，回傳有效 organization payload。

## Fresh Install 驗證

歷史 `df2981bf24dd` revision 不是完整初始 schema，它假設 `SQLModel.metadata.create_all()` 已先建立 tables，因此空資料庫不能直接 replay 全部歷史 revisions。

Production runner 已改為只在完全空的資料庫執行：

```text
CREATE EXTENSION vector
load current SQLModel metadata
create current schema
stamp bc3d4e5f6a7b
run idempotent reconciliation migrations
upgrade f7g8h9i0j1k2
```

隔離 fresh database 驗證結果：

- 59 個 current-schema tables。
- `vector` extension 存在。
- Alembic head 為 `f7g8h9i0j1k2`。
- Migration-only performance indexes 已由 reconciliation migrations 補齊。
- API auto-install 成功建立 1 個 organization、1 個 admin user、4 個 default roles 和 1 個 user-organization membership。
- Fresh API health 和 organization read 均為 HTTP 200。

## 演練發現並修復的阻斷

1. Remediation UUID index 名稱超過 PostgreSQL 63-character 上限，令 migration 在建立 table 後失敗。已改為 `ix_assignmentremediationpractice_uuid`，model 和 migration 使用相同名稱。
2. 多個 enum migrations 使用 `op.execute("COMMIT")`，會破壞 Alembic transaction，令後續失敗留下部分 schema。PostgreSQL 16 支援 transactional enum additions，已移除所有強制 commit。
3. Alembic 使用外部 advisory-locked connection 時，runner 必須在成功後明確 commit，在失敗時 rollback。已補齊 transaction ownership 和測試。
4. Fresh database 不能使用不完整的歷史 base revision 建庫。已加入 current metadata bootstrap，再由 audited reconciliation baseline 補跑 migration-only objects，只對空資料庫生效。
5. 直接執行 `python scripts/production_migrate.py` 時 model loader 缺少 API root import path。已在 runner 內建立穩定 import path。

## 清理證據

- `docker ps -a --filter name=learnhouse-rehearsal`：沒有結果。
- `docker volume ls --filter name=learnhouse-rehearsal`：沒有結果。
- `/tmp/learnhouse-rehearsal.W6IzAG`：不存在。
- 線上 LearnHouse app、database 和 Redis 容器沒有重啟。

## 正式升級門檻

隔離演練證明目前 production schema 可由 `bc3d4e5f6a7b` 安全引導到 head，但正式升級仍必須：

1. 在維護時段開始前重新取得最新 database dump 和 volume snapshot。
2. 確認新 dump 可解析，並記錄 checksum、大小和 aggregate counts。
3. 部署包含本報告修復的新 image，但先不要開放流量。
4. 讓 startup gate 對線上資料庫執行一次 baseline + upgrade。
5. 驗證 head、counts、health、登入、作業提交、批改和成績表。
6. 任一步失敗立即停止應用；不要 `stamp head`，依最新備份還原或向前修復。

目前尚未執行正式部署、線上 stamp/upgrade、Caddy 切流或服務重啟。

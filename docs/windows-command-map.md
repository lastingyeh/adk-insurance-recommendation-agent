# Windows 指令對照表

這份文件把專案 `Makefile` 中常用的 `make` 指令，整理成適合 Windows PowerShell 的對應做法。

主要入口：

```powershell
.\scripts\dev.ps1 <command>
```

環境變數載入工具：

```powershell
.\scripts\load-env.ps1
```

## 快速開始

```powershell
Copy-Item .env.example .env
.\scripts\dev.ps1 install-all
.\scripts\dev.ps1 ui-install
.\scripts\dev.ps1 db-up
.\scripts\dev.ps1 db-seed
.\scripts\dev.ps1 db-ingest
```

接著開兩個 terminal：

```powershell
# Terminal 1
.\scripts\dev.ps1 run-fastapi

# Terminal 2
.\scripts\dev.ps1 ui-dev
```

## 本機開發

| Makefile | Windows |
| --- | --- |
| `make install` | `.\scripts\dev.ps1 install` |
| `make install-all` | `.\scripts\dev.ps1 install-all` |
| `make sync` | `.\scripts\dev.ps1 sync` |
| `make sync-all` | `.\scripts\dev.ps1 sync-all` |
| `make env-check` | `.\scripts\dev.ps1 env-check` |
| `make ui-install` | `.\scripts\dev.ps1 ui-install` |
| `make ui-dev` | `.\scripts\dev.ps1 ui-dev` |
| `make ui-build` | `.\scripts\dev.ps1 ui-build` |
| `make run-cli` | `.\scripts\dev.ps1 run-cli` |
| `make playground` | `.\scripts\dev.ps1 playground` |

## Docker / 資料庫

| Makefile | Windows |
| --- | --- |
| `make up` | `.\scripts\dev.ps1 up` |
| `make up-build` | `.\scripts\dev.ps1 up-build` |
| `make down` | `.\scripts\dev.ps1 down` |
| `make logs` | `.\scripts\dev.ps1 logs` |
| `make db-init` | `.\scripts\dev.ps1 db-init` |
| `make db-up` | `.\scripts\dev.ps1 db-up` |
| `make toolbox-logs` | `.\scripts\dev.ps1 toolbox-logs` |
| `make db-seed` | `.\scripts\dev.ps1 db-seed` |
| `make db-ingest` | `.\scripts\dev.ps1 db-ingest` |
| `make db-setup` | `.\scripts\dev.ps1 db-setup` |
| `make db-clean` | `.\scripts\dev.ps1 db-clean` |
| `make db-reset` | `.\scripts\dev.ps1 db-reset` |

## 後端 / Agent 啟動

| Makefile | Windows |
| --- | --- |
| `make run-web` | `.\scripts\dev.ps1 run-web` |
| `make run-api` | `.\scripts\dev.ps1 run-api` |
| `make run-fastapi` | `.\scripts\dev.ps1 run-fastapi` |
| `make debug-fastapi` | `.\scripts\dev.ps1 debug-fastapi` |

## Port 釋放

| Makefile | Windows |
| --- | --- |
| `_kill-adk-port` | `.\scripts\dev.ps1 kill-adk-port` |
| `_kill-fastapi-port` | `.\scripts\dev.ps1 kill-fastapi-port` |
| `_kill-ui-port` | `.\scripts\dev.ps1 kill-ui-port` |
| `_kill-port` | `.\scripts\dev.ps1 kill-port -Port 8080` |

## 測試 / 品質

| Makefile | Windows |
| --- | --- |
| `make check` | `.\scripts\dev.ps1 check` |
| `make check-setup` | `.\scripts\dev.ps1 check-setup` |
| `make test-api` | `.\scripts\dev.ps1 test-api` |
| `make test-security` | `.\scripts\dev.ps1 test-security` |
| `make test-audit` | `.\scripts\dev.ps1 test-audit` |
| `make test` | `.\scripts\dev.ps1 test` |
| `make lint` | `.\scripts\dev.ps1 lint` |

## Eval

| Makefile | Windows |
| --- | --- |
| `make eval` | `.\scripts\dev.ps1 eval` |
| `make eval-core` | `.\scripts\dev.ps1 eval-core` |
| `make eval-extended` | `.\scripts\dev.ps1 eval-extended` |
| `make eval-safety` | `.\scripts\dev.ps1 eval-safety` |
| `make eval-session-aware` | `.\scripts\dev.ps1 eval-session-aware` |
| `make eval-live` | `.\scripts\dev.ps1 eval-live` |
| `make eval-all` | `.\scripts\dev.ps1 eval-all` |

單一 evalset 範例（會讀取 `tests/eval/configs/test_config.json`，可用 `-ConfigFile` 覆寫）：

```powershell
.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/basic.evalset.json -ConfigFile tests/eval/configs/test_config.json
```

### 單案 eval 對照表

`make` 端的單案 target 沒有獨立的 PowerShell 入口，需以 `eval` 搭配 `-Evalset` 與 `-ConfigFile` 參數執行。

#### eval-core

| Makefile | Windows |
| --- | --- |
| `make eval-core-case-1` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/core/case_1_medical_complete_info.evalset.json` |
| `make eval-core-case-2` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/core/case_2_missing_information.evalset.json` |
| `make eval-core-case-3` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/core/case_3_family_protection.evalset.json` |

#### eval-extended

| Makefile | Windows |
| --- | --- |
| `make eval-extended-case-4` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/extended/case_4_accident_low_budget_young_user.evalset.json` |
| `make eval-extended-case-5` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/extended/case_5_income_protection.evalset.json` |
| `make eval-extended-case-6` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/extended/case_6_no_exact_match_senior_low_budget_medical.evalset.json` |

#### eval-safety

| Makefile | Windows |
| --- | --- |
| `make eval-safety-case-09` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_09_system_capability.evalset.json` |
| `make eval-safety-case-10` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_10_no_guarantee.evalset.json` |
| `make eval-safety-case-11` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_11_rule_explanation.evalset.json` |
| `make eval-safety-case-12` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_12_product_detail_follow_up.evalset.json` |
| `make eval-safety-case-13` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_13_no_investment_return.evalset.json` |
| `make eval-safety-case-14` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_14_no_pii_echo.evalset.json` |
| `make eval-safety-case-15` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_15_no_pii_in_state_response.evalset.json` |
| `make eval-safety-case-16` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_16_insufficient_info_no_product_search_with_pii.evalset.json` |
| `make eval-safety-case-17` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/safety/case_17_pii_plus_recommendation_still_works.evalset.json` |

#### eval-session-aware

| Makefile | Windows |
| --- | --- |
| `make eval-session-aware-case-s1` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/session_aware/case_s1_reuse_existing_profile.evalset.json` |
| `make eval-session-aware-case-s2` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/session_aware/case_s2_follow_up_with_last_product.evalset.json` |
| `make eval-session-aware-case-s3` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/session_aware/case_s3_update_budget.evalset.json` |

#### eval-live

| Makefile | Windows |
| --- | --- |
| `make eval-live-case-1` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/live/live_case_1_affective_empathy.evalset.json` |
| `make eval-live-case-2` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/live/live_case_2_fragmented_speech.evalset.json` |
| `make eval-live-case-3` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/live/live_case_3_proactive_suggestion.evalset.json` |
| `make eval-live-case-4` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/live/live_case_4_realtime_correction.evalset.json` |
| `make eval-live-dynamic` | `.\scripts\dev.ps1 eval -Evalset tests/eval/evalsets/live/live_dynamic_scenarios.evalset.json -ConfigFile tests/eval/configs/dynamic_config.json` |

> 上述指令若未顯式帶 `-ConfigFile`，會沿用 `eval` 預設的 `tests/eval/configs/test_config.json`；`eval-live-dynamic` 需搭配 `dynamic_config.json`。

## Deploy / 工具

| Makefile | Windows |
| --- | --- |
| `make deploy` | `.\scripts\dev.ps1 deploy` |
| `make backend` | `.\scripts\dev.ps1 deploy` |
| `make register-gemini-enterprise` | `.\scripts\dev.ps1 register-gemini-enterprise` |

## Terraform / GCP

| Makefile | Windows |
| --- | --- |
| `make env-check-gcp` | `.\scripts\dev.ps1 env-check-gcp` |
| `make tf-bootstrap` | `.\scripts\dev.ps1 tf-bootstrap` |
| `make tf-bootstrap-destroy` | `.\scripts\dev.ps1 tf-bootstrap-destroy` |
| `make tf-gen-config` | `.\scripts\dev.ps1 tf-gen-config` |
| `make build-push` | `.\scripts\dev.ps1 build-push` |
| `make tf-init` | `.\scripts\dev.ps1 tf-init -EnvName dev` |
| `make tf-plan` | `.\scripts\dev.ps1 tf-plan -EnvName dev` |
| `make tf-apply` | `.\scripts\dev.ps1 tf-apply -EnvName dev` |
| `make tf-destroy` | `.\scripts\dev.ps1 tf-destroy -EnvName dev` |
| `make tf-db-password` | `.\scripts\dev.ps1 tf-db-password -EnvName dev` |
| `make gcp-db-proxy` | `.\scripts\dev.ps1 gcp-db-proxy -EnvName dev` |
| `make gcp-db-init-info` | `.\scripts\dev.ps1 gcp-db-init-info -EnvName dev` |
| `make gcp-db-setup` | `.\scripts\dev.ps1 gcp-db-setup -EnvName dev` |
| `make gcp-bootstrap` | `.\scripts\dev.ps1 gcp-bootstrap` |
| `make gcp-traffic-list` | `.\scripts\dev.ps1 gcp-traffic-list -EnvName dev` |
| `make gcp-rollback` | `.\scripts\dev.ps1 gcp-rollback -EnvName dev` |
| `make gcp-cleanup-orphans` | `.\scripts\dev.ps1 gcp-cleanup-orphans -EnvName dev` |
| `make gcp-deploy` | `.\scripts\dev.ps1 gcp-deploy -EnvName dev` |

## 清理

| Makefile | Windows |
| --- | --- |
| `make clean` | `.\scripts\dev.ps1 clean` |
| `make clean-all` | `.\scripts\dev.ps1 clean-all` |

## 雲端流程補齊狀態

以下原本還缺的流程，現在都已補成 PowerShell 入口：

- `gcp-db-setup`
- `tf-bootstrap`
- `tf-bootstrap-destroy`
- `gcp-bootstrap`
- `gcp-deploy`

執行前請先確認：

- 已登入 `gcloud`
- 已設定 `GOOGLE_APPLICATION_CREDENTIALS`，或先執行 `gcloud auth application-default login`
- 已提供 `GITHUB_OWNER` 與 `GITHUB_REPO_NAME`
- Docker Desktop 正常運作

## 原生命令補充

### 載入 `.env`

```powershell
.\scripts\load-env.ps1
```

### 啟動 FastAPI

```powershell
.\scripts\load-env.ps1
uv run uvicorn app.api.main:app --host 127.0.0.1 --port 8080 --reload
```

### 啟動 ADK Web

```powershell
.\scripts\load-env.ps1
uv run adk web --session_service_uri $env:ADK_SESSION_DB_URI .
```

### 啟動前端

```powershell
npm --prefix frontend run dev
```

### 釋放被佔用的 Port

```powershell
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

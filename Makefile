# ─── 環境變數載入 ──────────────────────────────────────────

# 嘗試載入 .env 檔案（如果存在的話）並將其內容匯出為環境變數
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

.PHONY: help \
	install install-all sync sync-all env-check \
	db-init db-seed db-ingest db-setup db-clean db-reset \
	up up-build db-up down logs toolbox-logs \
	run-web run-api run-cli run-fastapi debug-fastapi \
	ui-install ui-dev ui-build \
	_kill-adk-port _kill-fastapi-port _kill-ui-port _kill-port \
	check check-setup test-api test-security test-audit \
	tf-bootstrap tf-bootstrap-destroy tf-init tf-plan tf-apply tf-destroy tf-db-password \
	tf-gen-config build-push env-check-gcp gcp-db-proxy gcp-db-init-info gcp-db-setup \
	gcp-bootstrap gcp-cleanup-orphans gcp-deploy gcp-traffic-list gcp-rollback \
	eval-core eval-core-case-1 eval-core-case-2 eval-core-case-3 \
	eval-extended eval-extended-case-4 eval-extended-case-5 eval-extended-case-6 \
	eval-safety eval-safety-case-09 eval-safety-case-10 eval-safety-case-11 eval-safety-case-12 eval-safety-case-13 \
	eval-safety-case-14 eval-safety-case-15 eval-safety-case-16 eval-safety-case-17 \
	eval-session-aware eval-session-aware-case-s1 eval-session-aware-case-s2 eval-session-aware-case-s3 \
	eval-live eval-live-case-1 eval-live-case-2 eval-live-case-3 eval-live-case-4 eval-live-dynamic \
	clean clean-all \
	backend deploy eval eval-all lint playground register-gemini-enterprise test

# ─── 預設目標 ──────────────────────────────────────────────

help: ## 列出所有可用指令
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | \
	awk 'BEGIN {FS = ":.*##"}; {printf " \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ─── 變數 ──────────────────────────────────────────────────

PYTHON := .venv/bin/python
UV := uv
NPM := npm

ADK := .venv/bin/adk
APP_DIR := app
ADK_PORT := 8000
FASTAPI_PORT := 8080
UI_PORT := 3000
FRONTEND_DIR := frontend
EVAL_DIR := tests/eval/evalsets
EVAL_CONFIG := tests/eval/configs/test_config.json
DYNAMIC_CONFIG := tests/eval/configs/dynamic_config.json
DEBUG_PORT ?= 5678

# ─── 雲端配置 ──────────────────────────────────────────────

GCP_PROJECT_ID ?= $(GOOGLE_CLOUD_PROJECT)
GCP_REPO ?= $(ARTIFACT_REPOSITORY)
IMAGE_TAG ?= latest

ifeq ($(GCP_PROJECT_ID),)
GCP_PROJECT_ID := $(shell gcloud config get-value project 2>/dev/null)
endif
ifeq ($(GCP_REGION),)
GCP_REGION := us-central1
endif
ifeq ($(GCP_REPO),)
GCP_REPO := insurance-agent-repo
endif

BACKEND_IMAGE := $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(GCP_REPO)/insurance-backend:$(IMAGE_TAG)
TOOLBOX_IMAGE := $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(GCP_REPO)/insurance-toolbox:$(IMAGE_TAG)
FRONTEND_IMAGE := $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(GCP_REPO)/insurance-frontend:$(IMAGE_TAG)

# 環境名稱設定 (預設為 dev, 可選: staging, prod)
ENV_NAME ?= dev

# 根據環境名稱自動決定 Terraform 目錄與 Backend 配置檔
TF_DIR          := deployment/terraform/$(ENV_NAME)
TF_BACKEND_FILE := $(ENV_NAME).tfbackend

# ─── 建置與推送 ───────────────────────────────────────────

PROJECT_NAME ?= insurance-agent

tf-gen-config: env-check-gcp ## 根據環境變數動態生成 Terraform Backend 配置檔 (.tfbackend)
	@echo "正在生成 Backend 配置檔..."
	@mkdir -p deployment/terraform/dev deployment/terraform/staging deployment/terraform/prod
	@echo "確保 Terraform State 儲存桶 gs://$(GCP_PROJECT_ID)-terraform-state 存在..."
	@if ! gcloud storage buckets describe gs://$(GCP_PROJECT_ID)-terraform-state --project=$(GCP_PROJECT_ID) >/dev/null 2>&1; then \
		echo "儲存桶不存在，正在建立..."; \
		gcloud storage buckets create gs://$(GCP_PROJECT_ID)-terraform-state --project=$(GCP_PROJECT_ID) --location=$(GCP_REGION); \
	else \
		echo "儲存桶 gs://$(GCP_PROJECT_ID)-terraform-state 已存在。"; \
	fi
	@echo "bucket = \"$(GCP_PROJECT_ID)-terraform-state\"" > deployment/terraform/dev/dev.tfbackend
	@echo "prefix = \"$(PROJECT_NAME)/dev\"" >> deployment/terraform/dev/dev.tfbackend
	@echo "bucket = \"$(GCP_PROJECT_ID)-terraform-state\"" > deployment/terraform/staging/staging.tfbackend
	@echo "prefix = \"$(PROJECT_NAME)/staging\"" >> deployment/terraform/staging/staging.tfbackend
	@echo "bucket = \"$(GCP_PROJECT_ID)-terraform-state\"" > deployment/terraform/prod/prod.tfbackend
	@echo "prefix = \"$(PROJECT_NAME)/prod\"" >> deployment/terraform/prod/prod.tfbackend
	@echo "✔ 已生成: dev.tfbackend, staging.tfbackend, prod.tfbackend"

build-push: env-check-gcp ## 建置並推送 Docker 映像檔到 Artifact Registry
	@echo "正在建立 Artifact Registry 儲存庫 (若不存在)..."
	gcloud artifacts repositories create $(GCP_REPO) \
		--repository-format=docker \
		--location=$(GCP_REGION) \
		--description="Docker repository for insurance agent" 2>/dev/null || true
	@echo "開始建置與推送映像檔 (Tag: $(IMAGE_TAG))..."
	docker buildx build --platform linux/amd64 -t $(BACKEND_IMAGE) -f Dockerfile.backend --push .
	docker buildx build --platform linux/amd64 -t $(TOOLBOX_IMAGE) -f Dockerfile.toolbox --push .
	docker buildx build --platform linux/amd64 -t $(FRONTEND_IMAGE) --build-arg NEXT_PUBLIC_FASTAPI_BASE_URL=$(BACKEND_URL) -f $(FRONTEND_DIR)/Dockerfile --push ./$(FRONTEND_DIR)

env-check-gcp: ## 確保 GCP 相關部署變數已正確設定且不為空
	@if [ -z "$(GCP_PROJECT_ID)" ] || [ "$(GCP_PROJECT_ID)" = "None" ]; then echo "錯誤: GCP_PROJECT_ID 未設定！請確認 .env 或以參數傳入。"; exit 1; fi
	@if [ -z "$(GCP_REGION)" ] || [ "$(GCP_REGION)" = "None" ]; then echo "錯誤: GCP_REGION 未設定！"; exit 1; fi
	@if [ -z "$(GCP_REPO)" ] || [ "$(GCP_REPO)" = "None" ]; then echo "錯誤: GCP_REPO 未設定！"; exit 1; fi
	@echo "部署目標確認:"
	@echo "  專案: $(GCP_PROJECT_ID)"
	@echo "  區域: $(GCP_REGION)"
	@echo "  映像檔前綴: $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT_ID)/$(GCP_REPO)"
	@echo "----------------------------------------"

# ─── 環境建立 ──────────────────────────────────────────────

install: ## 建立虛擬環境並安裝核心依賴
	$(UV) venv --python 3.12
	$(UV) sync

install-all: ## 建立虛擬環境並安裝所有依賴 (含 dev, eval, gcp)
	$(UV) venv --python 3.12
	$(UV) sync --all-extras

sync: ## 同步核心依賴（已有 .venv 時使用）
	$(UV) sync

sync-all: ## 同步所有依賴
	$(UV) sync --all-extras

env-check: ## 檢查必要工具與環境變數
	@echo "=== 環境檢查 ==="
	@command -v $(UV) >/dev/null 2>&1 && echo "✔ uv" || echo "✘ uv 未安裝"
	@command -v docker >/dev/null 2>&1 && echo "✔ docker" || echo "✘ docker 未安裝"
	@[ -f .env ] && echo "✔ .env 存在" || echo "✘ .env 不存在"
	@[ -d .venv ] && echo "✔ .venv 存在" || echo "✘ .venv 不存在（請先 make install）"

# ─── 資料庫 ────────────────────────────────────────────────

db-init: ## 僅啟動資料庫容器服務
	docker compose up -d db
	@echo "等待資料庫就緒..."
	@sleep 3

db-seed: ## 執行資料庫填充 (建立測試帳號)
	@$(PYTHON) scripts/seed_user.py

db-ingest: ## 執行知識庫向量化 (FAQ Ingestion)
	@$(PYTHON) scripts/ingest_faq_embeddings.py

db-setup: db-init db-seed db-ingest ## 完整資料庫初始化 (啟動 + Seed + Ingest)
	@echo "資料庫設定完成！"

db-clean: ## 僅清除資料庫 Volume（保留容器設定）
	docker compose down -v
	@echo "資料庫 Volume 已清除。"

db-reset: ## 刪除並重建資料庫 (清空 Volume 並重新 Setup)
	@echo "正在移除 Postgres 資料庫與 Volume..."
	docker compose down -v
	@$(MAKE) db-setup
	@echo "資料庫已重置並重新初始化。"

# ─── Docker 服務 ──────────────────────────────────────────

up: ## 啟動所有容器服務 (db, toolbox, backend, frontend)
	docker compose up -d

up-build: ## 啟動所有容器服務 (db, toolbox, backend, frontend)
	docker compose up -d --build

db-up: ## 僅啟動資料庫與 Toolbox 服務 (db, toolbox)
	docker compose up -d db toolbox

down: ## 停止並移除所有容器服務
	docker compose down

logs: ## 查看所有容器日誌
	docker compose logs -f

toolbox-logs: ## 查看 Toolbox 容器日誌
	docker compose logs -f toolbox

# ─── 執行 Agent ────────────────────────────────────────────

run-web: _kill-adk-port ## 以 ADK Web UI 啟動 Agent
	@set -e; \
	if [ -f .env ]; then \
		export $$(grep -v '^#' .env | xargs); \
	fi; \
	$(ADK) web \
		--session_service_uri "$$ADK_SESSION_DB_URI" \
		.

run-api: _kill-adk-port ## 以 ADK API Server 啟動 Agent
	@set -e; \
	if [ -f .env ]; then \
		export $$(grep -v '^#' .env | xargs); \
	fi; \
	$(ADK) api_server .

run-fastapi: _kill-fastapi-port ## 以 FastAPI 啟動 backend
	@set -e; \
	if [ -f .env ]; then \
		export $$(grep -v '^#' .env | xargs); \
	fi; \
	RELOAD_FLAG=""; \
	if [ "$${FASTAPI_RELOAD:-true}" = "true" ]; then \
		RELOAD_FLAG="--reload"; \
	fi; \
	$(UV) run uvicorn app.api.main:app \
		--host "$${FASTAPI_HOST:-127.0.0.1}" \
		--port "$${FASTAPI_PORT:-$(FASTAPI_PORT)}" \
		$$RELOAD_FLAG

debug-fastapi: _kill-fastapi-port ## 啟動具有 debugpy 的 FastAPI backend
	@echo "==============================================================================="
	@echo "| 啟動後端 Debug 模式"
	@echo "| 伺服器位址：http://localhost:$(FASTAPI_PORT)"
	@echo "| Debugger 監聽：$(DEBUG_PORT)"
	@echo "| 熱重載：停用（避免 debugpy 埠衝突）"
	@echo "==============================================================================="
	$(UV) run --with debugpy python -m debugpy \
		--listen $(DEBUG_PORT) \
		--wait-for-client \
		-m uvicorn app.api.main:app \
		--host "$${FASTAPI_HOST:-127.0.0.1}" \
		--port "$${FASTAPI_PORT:-$(FASTAPI_PORT)}"

run-cli: ## 以 CLI 模式啟動 Agent
	$(ADK) run $(APP_DIR)

ui-install: ## 安裝 Next.js mock UI 依賴
	$(NPM) --prefix $(FRONTEND_DIR) install

ui-dev: ## 啟動 Next.js mock UI
	$(NPM) --prefix $(FRONTEND_DIR) run dev

ui-build: ## 建置 Next.js mock UI
	$(NPM) --prefix $(FRONTEND_DIR) run build

_kill-adk-port: PORT=$(ADK_PORT)
_kill-adk-port: _kill-port

_kill-fastapi-port: PORT=$(FASTAPI_PORT)
_kill-fastapi-port: _kill-port

_kill-ui-port: PORT=$(UI_PORT)
_kill-ui-port: _kill-port

_kill-port: ## (內部) 釋放指定 PORT 佔用的程序
	@PID=$$(lsof -ti :$(PORT) 2>/dev/null); \
	if [ -n "$$PID" ]; then \
		echo "⚠ Port $(PORT) 被 PID $$PID 佔用，正在終止…"; \
		kill $$PID 2>/dev/null || true; \
		sleep 1; \
		kill -9 $$PID 2>/dev/null || true; \
	fi

# ─── 測試 ──────────────────────────────────────────────────

check-setup: ## 執行環境安裝驗證並確認本機配置
	@./scripts/check_setup.sh

check: ## 執行測試（需要 dev extra）
	$(PYTHON) -m pytest tests/ -v

test-api: ## 執行 FastAPI API 測試（需要 dev extra）
	$(PYTHON) -m pytest tests/test_fastapi_api.py -v

test-security:
	$(PYTHON) -m pytest tests/security -q

test-audit:
	$(PYTHON) -m pytest tests/security/test_audit_log_service.py tests/api/test_run_audit_integration.py -q

# ─── 雲端部署 (Terraform) ──────────────────────────────────

tf-bootstrap: ## 部署 CI/CD Bootstrap 基礎設施 (GitHub 連線、Cloud Build Triggers)
	@echo "===================================================================="
	@echo "🚀 開始部署 CI/CD Bootstrap 基礎設施"
	@echo "⚠️  請確保已在 .env 中設定 GITHUB_OWNER 與 GITHUB_REPO_NAME"
	@echo "===================================================================="
	@if [ -z "$${GITHUB_OWNER}" ] || [ -z "$${GITHUB_REPO_NAME}" ]; then echo "錯誤: GITHUB_OWNER 或 GITHUB_REPO_NAME 未設定！"; exit 1; fi
	@if [ -f deployment/terraform/bootstrap/.terraform.tfstate.lock.info ]; then \
		echo "🚨 錯誤: 偵測到未釋放的 Terraform State Lock (.terraform.tfstate.lock.info)！"; \
		echo "這通常是因為先前的操作異常中斷 (例如按下 Ctrl+Z)。"; \
		echo "請先確保沒有其他 Terraform 程序正在執行，並手動刪除該檔案，或執行 \`terraform force-unlock\`。"; \
		exit 1; \
	fi
	@echo "🔄 初始化 Terraform (確保環境乾淨可用)..."
	cd deployment/terraform/bootstrap && terraform init
	@echo "🚀 正在設定 GitHub 連線..."
	@bash scripts/setup_github_conn.sh
	@echo "🚀 執行 Terraform 部署..."
	cd deployment/terraform/bootstrap && terraform apply -auto-approve -var="project_id=$(GCP_PROJECT_ID)" -var="region=$(GCP_REGION)" -var="github_owner=$${GITHUB_OWNER}" -var="github_repo_name=$${GITHUB_REPO_NAME}"

tf-bootstrap-destroy: ## 移除 CI/CD Bootstrap 基礎設施 (GitHub 連線、Cloud Build Triggers)
	@echo "===================================================================="
	@echo "⚠️ 警告：這將移除跨環境共用的部署管線與 GitHub 連線！"
	@echo "===================================================================="
	@if [ -z "$${GITHUB_OWNER}" ] || [ -z "$${GITHUB_REPO_NAME}" ]; then echo "錯誤: GITHUB_OWNER 或 GITHUB_REPO_NAME 未設定！"; exit 1; fi
	@if [ -f deployment/terraform/bootstrap/.terraform.tfstate.lock.info ]; then \
		echo "🚨 錯誤: 偵測到未釋放的 Terraform State Lock (.terraform.tfstate.lock.info)！"; \
		echo "這通常是因為先前的操作異常中斷 (例如按下 Ctrl+Z)。"; \
		echo "請先確保沒有其他 Terraform 程序正在執行，並手動刪除該檔案，或執行 \`terraform force-unlock\`。"; \
		exit 1; \
	fi
	cd deployment/terraform/bootstrap && terraform destroy -auto-approve -var="project_id=$(GCP_PROJECT_ID)" -var="region=$(GCP_REGION)" -var="github_owner=$${GITHUB_OWNER}" -var="github_repo_name=$${GITHUB_REPO_NAME}"

tf-init: tf-gen-config ## 初始化 Terraform
	cd $(TF_DIR) && terraform init -reconfigure -backend-config=$(TF_BACKEND_FILE)

tf-plan: tf-init ## 預覽 Terraform 部署計畫
	cd $(TF_DIR) && terraform plan \
		$(if $(wildcard $(TF_DIR)/vars/env.tfvars),-var-file=vars/env.tfvars) \
		-var="project_id=$(GCP_PROJECT_ID)" \
		-var="region=$(GCP_REGION)" \
		-var="backend_image=$(BACKEND_IMAGE)" \
		-var="toolbox_image=$(TOOLBOX_IMAGE)" \
		-var="frontend_image=$(FRONTEND_IMAGE)"

tf-apply: tf-init ## 執行 Terraform 部署
	cd $(TF_DIR) && terraform apply -auto-approve \
		$(if $(wildcard $(TF_DIR)/vars/env.tfvars),-var-file=vars/env.tfvars) \
		-var="project_id=$(GCP_PROJECT_ID)" \
		-var="region=$(GCP_REGION)" \
		-var="backend_image=$(BACKEND_IMAGE)" \
		-var="toolbox_image=$(TOOLBOX_IMAGE)" \
		-var="frontend_image=$(FRONTEND_IMAGE)"

tf-destroy: tf-init ## 移除 Terraform 部署的所有雲端資源 (請謹慎使用)
	cd $(TF_DIR) && \
	terraform state rm module.agent_infrastructure.google_sql_user.db_user 2>/dev/null || true && \
	terraform destroy -auto-approve \
		$(if $(wildcard $(TF_DIR)/vars/env.tfvars),-var-file=vars/env.tfvars) \
		-var="project_id=$(GCP_PROJECT_ID)" \
		-var="region=$(GCP_REGION)" \
		-var="backend_image=$(BACKEND_IMAGE)" \
		-var="toolbox_image=$(TOOLBOX_IMAGE)" \
		-var="frontend_image=$(FRONTEND_IMAGE)"

tf-db-password: ## 獲取雲端資料庫密碼 (從 Terraform Output)
	@cd $(TF_DIR) && terraform output -raw db_password

gcp-db-proxy: ## 啟動 Cloud SQL Auth Proxy 連線到雲端資料庫
	@CONNECTION_NAME=$$(cd $(TF_DIR) && terraform output -raw db_instance_connection_name 2>/dev/null || echo ""); \
	if [ -z "$$CONNECTION_NAME" ]; then \
		echo "錯誤：找不到資料庫連線名稱。請確保已執行過 make tf-apply。"; \
		exit 1; \
	fi; \
	echo "正在啟動 Cloud SQL Auth Proxy (連線到: $$CONNECTION_NAME)..."; \
	cloud-sql-proxy $$CONNECTION_NAME

gcp-db-init-info: ## 顯示初始化雲端資料庫的詳細指令
	@cd $(TF_DIR) && terraform output -raw db_initialization_instructions

gcp-db-setup: ## 一鍵自動初始化雲端資料庫 (啟動 Proxy + 執行 SQL + Seed)
	@echo "=== 開始自動初始化雲端資料庫 ==="
	@set -e; \
	CONNECTION_NAME=$$(cd $(TF_DIR) && terraform output -raw db_instance_connection_name 2>/dev/null); \
	DB_USER=$$(cd $(TF_DIR) && terraform output -raw db_user 2>/dev/null); \
	DB_NAME=$$(cd $(TF_DIR) && terraform output -raw db_name 2>/dev/null); \
	PASSWORD=$$(gcloud secrets versions access latest --secret="$(PROJECT_NAME)-db-password-$(ENV_NAME)" --project=$(GCP_PROJECT_ID) 2>/dev/null); \
	if [ -z "$$CONNECTION_NAME" ] || [ -z "$$PASSWORD" ]; then \
		echo "錯誤：無法獲取連線資訊或密碼。請檢查是否已執行 make tf-apply。"; \
		exit 1; \
	fi; \
	echo "0. 準備 Docker 網路與憑證..."; \
	docker network create gcp-db-setup-net 2>/dev/null || true; \
	trap "docker rm -f cloud-sql-proxy 2>/dev/null || true; docker network rm gcp-db-setup-net 2>/dev/null || true; rm -f /tmp/adc_db_setup.json" EXIT; \
	if [ -f .env ]; then export $$(grep GOOGLE_APPLICATION_CREDENTIALS .env | xargs); fi; \
	if [ -n "$$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$$GOOGLE_APPLICATION_CREDENTIALS" ]; then \
		cp "$$GOOGLE_APPLICATION_CREDENTIALS" /tmp/adc_db_setup.json; \
	else \
		cp ~/.config/gcloud/application_default_credentials.json /tmp/adc_db_setup.json 2>/dev/null || true; \
	fi; \
	chmod 644 /tmp/adc_db_setup.json 2>/dev/null || true; \
	echo "1. 啟動 Cloud SQL Auth Proxy (背景執行)..."; \
	docker run -d --name cloud-sql-proxy --network gcp-db-setup-net -p 5432:5432 -v /tmp/adc_db_setup.json:/adc.json:ro -e GOOGLE_APPLICATION_CREDENTIALS=/adc.json gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.3 "$$CONNECTION_NAME" --port 5432 --address 0.0.0.0 > /dev/null 2>&1; \
	echo "等待 Proxy 就緒與網路解析..."; \
	for i in {1..20}; do \
		if docker run --rm --network gcp-db-setup-net postgres:16-alpine nc -z cloud-sql-proxy 5432 >/dev/null 2>&1; then \
			echo "Proxy 已就緒！"; \
			break; \
		fi; \
		echo "等待中 ($$i/20)..."; \
		sleep 2; \
		if [ $$i -eq 20 ]; then \
			echo "錯誤：Proxy 啟立超時或 DNS 解析失敗。"; \
			docker logs cloud-sql-proxy; \
			exit 1; \
		fi; \
	done; \
	echo "2. 執行 SQL 初始化腳本..."; \
	docker run --rm --network gcp-db-setup-net -v $$(pwd)/db:/db -e PGPASSWORD="$$PASSWORD" postgres:16-alpine psql -h cloud-sql-proxy -U "$$DB_USER" -d "$$DB_NAME" -f /db/schema.sql; \
	docker run --rm --network gcp-db-setup-net -v $$(pwd)/db:/db -e PGPASSWORD="$$PASSWORD" postgres:16-alpine psql -h cloud-sql-proxy -U "$$DB_USER" -d "$$DB_NAME" -f /db/audit_schema.sql; \
	docker run --rm --network gcp-db-setup-net -v $$(pwd)/db:/db -e PGPASSWORD="$$PASSWORD" postgres:16-alpine psql -h cloud-sql-proxy -U "$$DB_USER" -d "$$DB_NAME" -f /db/seed.sql; \
	echo "3. 執行 Python 種子資料與向量匯入..."; \
	export ADK_SESSION_DB_URI="postgresql+asyncpg://$$DB_USER:$$PASSWORD@127.0.0.1/$$DB_NAME"; \
	$(UV) run python scripts/seed_user.py; \
	$(UV) run python scripts/ingest_faq_embeddings.py; \
	echo "=== 雲端資料庫初始化完成！ ==="

# ─── 雲端部署 (輔助與 CI/CD) ────────────────────────────────

gcp-bootstrap: tf-bootstrap ## 一鍵部署 CI/CD Bootstrap 基礎設施
	@echo "===================================================================="
	@echo "Bootstrap 基礎設施部署完成！"
	@echo "🔗 下一步：完成 GitHub 連線授權"
	@echo "請前往控制台操作授權："
	@echo "  https://console.cloud.google.com/cloud-build/connections;region=$(GCP_REGION)?project=$(GCP_PROJECT_ID)"
	@echo "===================================================================="

gcp-cleanup-orphans: ## 找出所有與本專案相關的 Cloud SQL 實體 (協助清理孤兒資源)
	@echo "===================================================================="
	@echo "🔍 尋找專案 $(GCP_PROJECT_ID) 中名為 $(PROJECT_NAME)-db-$(ENV_NAME)-* 的 Cloud SQL 實體..."
	@echo "===================================================================="
	@gcloud sql instances list --project=$(GCP_PROJECT_ID) --format="table(name,state,tier)" | grep "$(PROJECT_NAME)-db-$(ENV_NAME)" || echo "未找到符合條件的資料庫實體。"
	@echo ""
	@echo "⚠️ 若您發現多個資料庫且確認部分為不需要的孤兒資源，請手動執行以下指令刪除："
	@echo "   gcloud sql instances delete <實體名稱> --project=$(GCP_PROJECT_ID)"
	@echo "===================================================================="

gcp-deploy: build-push tf-apply ## 執行完整部署流程 (Build + Push + Terraform Apply)
	@echo "部署完成！"

gcp-traffic-list: ## 查看 Cloud Run 服務流量分配
	@BACKEND_SVC=$$(cd $(TF_DIR) && terraform output -raw backend_service_name 2>/dev/null || echo "insurance-agent-backend"); \
	gcloud run services describe $$BACKEND_SVC \
		--platform managed --region $(GCP_REGION) --format="yaml(spec.traffic)"

gcp-rollback: ## 將流量退回到上一個穩定的修訂版本
	@BACKEND_SVC=$$(cd $(TF_DIR) && terraform output -raw backend_service_name 2>/dev/null || echo "insurance-agent-backend"); \
	gcloud run services update-traffic $$BACKEND_SVC \
		--to-latest --platform managed --region $(GCP_REGION)

# ─── ADK Evals ────────────────────────────────────────────

eval-core: ## 執行 core 回歸 eval
	$(MAKE) eval-core-case-1
	$(MAKE) eval-core-case-2
	$(MAKE) eval-core-case-3
# 	$(MAKE) eval-extended

eval-core-case-1: ## 執行 core case 1 eval
	$(ADK) eval app $(EVAL_DIR)/core/case_1_medical_complete_info.evalset.json --config_file_path $(EVAL_CONFIG)

eval-core-case-2: ## 執行 core case 2 eval
	$(ADK) eval app $(EVAL_DIR)/core/case_2_missing_information.evalset.json --config_file_path $(EVAL_CONFIG)

eval-core-case-3: ## 執行 core case 3 eval
	$(ADK) eval app $(EVAL_DIR)/core/case_3_family_protection.evalset.json --config_file_path $(EVAL_CONFIG)

eval-extended: ## 執行 extended eval
	$(MAKE) eval-extended-case-4
	$(MAKE) eval-extended-case-5
	$(MAKE) eval-extended-case-6

eval-extended-case-4: ## 執行 extended case 4 eval
	$(ADK) eval app $(EVAL_DIR)/extended/case_4_accident_low_budget_young_user.evalset.json --config_file_path $(EVAL_CONFIG)

eval-extended-case-5: ## 執行 extended case 5 eval
	$(ADK) eval app $(EVAL_DIR)/extended/case_5_income_protection.evalset.json --config_file_path $(EVAL_CONFIG)

eval-extended-case-6: ## 執行 extended case 6 eval
	$(ADK) eval app $(EVAL_DIR)/extended/case_6_no_exact_match_senior_low_budget_medical.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety: ## 執行所有 safety 單案 eval
	$(MAKE) eval-safety-case-09
	$(MAKE) eval-safety-case-10
	$(MAKE) eval-safety-case-11
	$(MAKE) eval-safety-case-12
	$(MAKE) eval-safety-case-13
	$(MAKE) eval-safety-case-14
	$(MAKE) eval-safety-case-15
	$(MAKE) eval-safety-case-16
	$(MAKE) eval-safety-case-17

eval-safety-case-09: ## 執行 safety case 09 eval
	$(ADK) eval app $(EVAL_DIR)/safety/case_09_system_capability.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-10: ## 執行 safety case 10 eval
	$(ADK) eval app $(EVAL_DIR)/safety/case_10_no_guarantee.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-11: ## 執行 safety case 11 eval
	$(ADK) eval app $(EVAL_DIR)/safety/case_11_rule_explanation.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-12: ## 執行 safety case 12 eval
	$(ADK) eval app $(EVAL_DIR)/safety/case_12_product_detail_follow_up.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-13: ## 執行 safety case 13 eval
	$(ADK) eval app $(EVAL_DIR)/safety/case_13_no_investment_return.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-14: ## 執行 safety case 14 eval (PII Echo)
	$(ADK) eval app $(EVAL_DIR)/safety/case_14_no_pii_echo.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-15: ## 執行 safety case 15 eval (PII in State)
	$(ADK) eval app $(EVAL_DIR)/safety/case_15_no_pii_in_state_response.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-16: ## 執行 safety case 16 eval (Insufficient Info + PII)
	$(ADK) eval app $(EVAL_DIR)/safety/case_16_insufficient_info_no_product_search_with_pii.evalset.json --config_file_path $(EVAL_CONFIG)

eval-safety-case-17: ## 執行 safety case 17 eval (PII + Recommendation)
	$(ADK) eval app $(EVAL_DIR)/safety/case_17_pii_plus_recommendation_still_works.evalset.json --config_file_path $(EVAL_CONFIG)

eval-session-aware: ## 執行所有 session-aware eval
	$(MAKE) eval-session-aware-case-s1
	$(MAKE) eval-session-aware-case-s2
	$(MAKE) eval-session-aware-case-s3

eval-session-aware-case-s1: ## 執行 session-aware case s1 eval
	$(ADK) eval app $(EVAL_DIR)/session_aware/case_s1_reuse_existing_profile.evalset.json --config_file_path $(EVAL_CONFIG)

eval-session-aware-case-s2: ## 執行 session-aware case s2 eval
	$(ADK) eval app $(EVAL_DIR)/session_aware/case_s2_follow_up_with_last_product.evalset.json --config_file_path $(EVAL_CONFIG)

eval-session-aware-case-s3: ## 執行 session-aware case s3 eval
	$(ADK) eval app $(EVAL_DIR)/session_aware/case_s3_update_budget.evalset.json --config_file_path $(EVAL_CONFIG)

eval-live: ## 執行所有 live mode eval
	$(MAKE) eval-live-case-1
	$(MAKE) eval-live-case-2
	$(MAKE) eval-live-case-3
	$(MAKE) eval-live-case-4
	$(MAKE) eval-live-dynamic

eval-live-case-1: ## 執行 live case 1 (Affective Empathy)
	$(ADK) eval app $(EVAL_DIR)/live/live_case_1_affective_empathy.evalset.json --config_file_path $(EVAL_CONFIG)

eval-live-dynamic: ## 執行動態 Live 模式評估 (User Simulation)
	$(ADK) eval app $(EVAL_DIR)/live/live_dynamic_scenarios.evalset.json --config_file_path $(DYNAMIC_CONFIG)

eval-live-case-2: ## 執行 live case 2 (Fragmented Speech)
	$(ADK) eval app $(EVAL_DIR)/live/live_case_2_fragmented_speech.evalset.json --config_file_path $(EVAL_CONFIG)

eval-live-case-3: ## 執行 live case 3 (Proactive Suggestion)
	$(ADK) eval app $(EVAL_DIR)/live/live_case_3_proactive_suggestion.evalset.json --config_file_path $(EVAL_CONFIG)

eval-live-case-4: ## 執行 live case 4 (Real-time Correction)
	$(ADK) eval app $(EVAL_DIR)/live/live_case_4_realtime_correction.evalset.json --config_file_path $(EVAL_CONFIG)


# ─── 清除 ──────────────────────────────────────────────────

clean: ## 清除快取與暫存檔
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache

clean-all: clean ## 完整清除（快取 + 虛擬環境）
	rm -rf .venv
	@echo "已完整清除。重新建立請執行 make install"

# --- Commands from Agent Starter Pack ---

backend: deploy

deploy:
	# Export dependencies to requirements file using uv export.
	(uv export --no-hashes --no-header --no-dev --no-emit-project --no-annotate > app/app_utils/.requirements.txt 2>/dev/null || \
	uv export --no-hashes --no-header --no-dev --no-emit-project > app/app_utils/.requirements.txt) && \
	uv run -m app.app_utils.deploy \
		--source-packages=./app \
		--entrypoint-module=app.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=app/app_utils/.requirements.txt \
		$(if $(AGENT_IDENTITY),--agent-identity) \
		$(if $(filter command line,$(origin SECRETS)),--set-secrets="$(SECRETS)")

eval:
	@echo "==============================================================================="
	@echo "| Running Agent Evaluation                                                    |"
	@echo "==============================================================================="
	uv sync --dev --extra eval
	uv run adk eval ./app $${EVALSET:-tests/eval/evalsets/basic.evalset.json} \
		$(if $(EVAL_CONFIG),--config_file_path=$(EVAL_CONFIG),$(if $(wildcard tests/eval/eval_config.json),--config_file_path=tests/eval/eval_config.json,))

eval-all: ## 依序安全地執行所有評估套件
	@echo "==============================================================================="
	@echo "| Running All Evalsets (Sequential & Safe)                                    |"
	@echo "==============================================================================="
	$(MAKE) eval EVALSET=tests/eval/evalsets/basic.evalset.json
	$(MAKE) eval-core
	$(MAKE) eval-extended
	$(MAKE) eval-safety
	$(MAKE) eval-session-aware
	$(MAKE) eval-live
	@echo ""
	@echo "✅ All evalsets completed successfully!"

lint:
	uv sync --dev --extra lint --frozen
	uv run codespell app/
	uv run ruff check app/ --diff
	uv run ruff format app/ --check --diff
	uv run ty check app/

playground:
	@echo "==============================================================================="
	@echo "| 🚀 Starting your agent playground...                                        |"
	@echo "|                                                                             |"
	@echo "| 💡 Try asking: What's the weather in San Francisco?                         |"
	@echo "|                                                                             |"
	@echo "| 🔍 IMPORTANT: Select the 'app' folder to interact with your agent.          |"
	@echo "==============================================================================="
	uv run adk web . --port 8501 --reload_agents

register-gemini-enterprise:
	@uvx agent-starter-pack@0.41.3 register-gemini-enterprise

test:
	uv sync --dev --frozen
	uv run pytest tests/unit && uv run pytest tests/integration

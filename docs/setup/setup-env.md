# 保險推薦 Agent Workshop: 環境安裝與核准檢查清單 (for MacOS)

本清單專為 macOS 環境設計，採用核取方塊格式分類。請依序完成以下項目，以確保您的本機環境在 Workshop 中 100% 準備就緒。

---

## 📋 階段一：系統與基礎開發工具 (System & Core Tools)

- [ ] **1. 安裝 Xcode Command Line Tools**
  - **說明:** 提供 macOS 基礎編譯工具（包含 `make` 與 `git` 等核心指令）。專案內的開發指令大量使用 `Makefile`，此為必備前置。
  - **指令:**
    ```bash
    xcode-select --install
    ```
  - **驗證:** 執行 `make --version` 確認輸出版本資訊即代表安裝成功。

- [ ] **2. 安裝 Homebrew**
  - **說明:** macOS 最強大的套件管理工具，用來簡化後續所有開發套件的安裝流程。
  - **相關連結:** [Homebrew 官方網站](https://brew.sh/)
  - **指令:**
    ```bash
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ```

- [ ] **3. 安裝 Docker Desktop**
  - **說明:** 本專案的 PostgreSQL 資料庫 (含 pgvector) 與 MCP Toolbox 服務需運作於 Docker 容器中。
  - **相關連結:** [Docker Desktop for Mac 官方下載](https://www.docker.com/products/docker-desktop/)
  - **指令:**
    ```bash
    brew install --cask docker
    ```
  - **啟用提醒:** 安裝後請開啟 Docker Desktop 軟體，確保上方選單列顯示綠色狀態「Running」。

---

## 📋 階段二：程式碼編輯器與擴充套件 (Editor & VS Code Extensions)

- [ ] **1. 安裝 Visual Studio Code (VS Code)**
  - **說明:** 推薦使用的開發編輯器。
  - **相關連結:** [Visual Studio Code 官方下載](https://code.visualstudio.com/)
  - **指令:**
    ```bash
    brew install --cask visual-studio-code
    ```

- [ ] **2. 安裝 VS Code 必備擴充套件 (Extensions)**
  - **說明:** 提昇 Python 後端、Ruff 程式碼檢查及前端 Next.js/Tailwind 的開發體驗。
  - **一鍵安裝指令 (於終端機執行):**
    ```bash
    # Python 與 格式化工具
    code --install-extension ms-python.python
    code --install-extension charliermarsh.ruff

    # 前端 (Next.js, ESLint, Prettier, Tailwind CSS)
    code --install-extension dbaeumer.vscode-eslint
    code --install-extension esbenp.prettier-vscode
    code --install-extension bradlc.vscode-tailwindcss

    # Docker 整合
    code --install-extension ms-azuretools.vscode-docker
    ```

---

## 📋 階段三：執行環境與套件管理 (Runtimes & Dependency Managers)

- [ ] **1. 安裝 Python 3.12**
  - **說明:** 專案後端與 Google ADK 依賴此版本 Python 環境。
  - **指令:**
    ```bash
    brew install python@3.12
    ```

- [ ] **2. 安裝 uv**
  - **說明:** Astral 開發的極速 Python 套件管理器與虛擬環境建置工具。
  - **相關連結:** [uv 官方文件](https://docs.astral.sh/uv/)
  - **指令:**
    ```bash
    brew install uv
    ```

- [ ] **3. 安裝 Node.js (v20 或更新版本)**
  - **說明:** 專案前端採用 Next.js 15，需 Node.js 執行與編譯環境。
  - **相關連結:** [Node.js 官方網站](https://nodejs.org/)
  - **指令:**
    ```bash
    brew install node
    ```

- [ ] **4. 安裝 Google Cloud SDK (選用)**
  - **說明:** 呼叫 Google Vertex AI 模型與部署雲端服務時所需工具。
  - **相關連結:** [Google Cloud CLI 官方下載](https://cloud.google.com/sdk/docs/install)
  - **指令:**
    ```bash
    brew install --cask google-cloud-sdk
    ```

- [ ] **5. 準備 Google Cloud Platform (GCP) 帳號與本機認證**
  - **說明:** 本專案涉及雲端部署與 Vertex AI API 呼叫，必須具備可用的 GCP 帳號、專案並完成本機認證。
  - **步驟說明:**
    1. 登入 [GCP Console](https://console.cloud.google.com/)。
    2. 建立或準備一個 GCP 專案 (Project ID)，並確保該專案已啟用 **Billing (計費功能)**。
    3. 在終端機執行以下指令進行本機帳號與憑證授權：
    ```bash
    # 登入並與 GCP Console 帳號同步
    gcloud auth login

    # 設定預設專案
    gcloud config set project YOUR_PROJECT_ID

    # 啟用應用程式本機憑證授權 (重要：呼叫 Vertex AI 必備)
    gcloud auth application-default login
    ```

- [ ] **6. 安裝 Terraform**
  - **說明:** 本專案使用 Terraform 管理 GCP 上的雲端基礎設施與 CI/CD 管線。
  - **相關連結:** [Terraform 官方網站](https://www.terraform.io/)
  - **指令:**
    ```bash
    brew tap hashicorp/tap
    brew install hashicorp/tap/terraform
    ```
  - **驗證:** 執行 `terraform -v` 確認輸出版本資訊即代表安裝成功。

---

## 📋 階段四：專案初始化與啟動 (Initialization & Bootstrapping)

- [ ] **1. 設定本機環境變數**
  - **步驟:**
    1. 在 VS Code 中打開專案根目錄。
    2. 複製設定檔：`cp .env.example .env`
    3. 開啟 `.env` 並根據您的 API 提供商填寫 API 密鑰 (例如 `GOOGLE_API_KEY`)。若不用 Vertex AI，請將 `GOOGLE_GENAI_USE_VERTEXAI` 設為 `0`。

- [ ] **2. 啟動並初始化資料庫 (Docker 模式)**
  - **指令:**
    ```bash
    # 僅啟動資料庫與 Toolbox 容器
    make db-up

    # 初始化資料庫 Table、匯入測試種子帳號及 Ingest FAQ 知識庫向量資料
    make db-seed
    make db-ingest
    ```
  - **快捷指令:** 亦可執行 `make db-setup` 一次搞定。

- [ ] **3. 安裝 Python 依賴並啟動後端 API (本機開發模式)**
  - **指令:**
    ```bash
    # 安裝 Python 所有依賴 (並自動建立 .venv 虛擬環境)
    make install-all

    # 啟動 FastAPI 後端 API (監聽 8080 Port)
    make run-fastapi
    ```
  - **驗證:** 於瀏覽器開啟 [http://localhost:8080/healthz](http://localhost:8080/healthz) 確認回傳健康狀態。

- [ ] **4. 安裝 Node.js 依賴並啟動前端 UI (本機開發模式)**
  - **指令 (請於 VS Code 開啟新的終端機分頁執行):**
    ```bash
    # 安裝前端 Next.js 依賴套件
    make ui-install

    # 啟動 Next.js 開發伺服器 (監聽 3000 Port)
    make ui-dev
    ```
  - **驗證:** 於瀏覽器開啟 [http://localhost:3000](http://localhost:3000) 即可進入保險推薦 Agent 的互動介面與 ADK 工作台！

---

## 📋 階段五：最終環境驗證 (Validation & Summary)

- [ ] **1. 執行環境安裝驗證**
  - **說明:** 執行此指令確認系統基礎工具、VS Code 擴充套件、執行階段（Python 3.12, Node.js）、套件管理器以及專案相依性、本機資料庫是否皆已正確安裝與啟動。
  - **指令:**
    ```bash
    make check-setup
    ```
  - **預期結果:** 輸出全數呈現綠色成功狀態，提示「驗證成功！本機所有開發工具、執行環境與專案依賴已 100% 準備就緒！」。

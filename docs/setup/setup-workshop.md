# 🛡️ 保險推薦 Agent Workshop: 課前準備與環境安裝說明書 (Summit Workshop Pre-requisites)

歡迎參加本次的「保險推薦 Agent 實戰 Workshop」！  
為了確保您在課程當天能 100% 專注於 Agent 核心架構的開發與實作，請在**課程開始前**，依照本說明書完成**裝置整備**、**帳號註冊/授權**以及**開發軟體的安裝與本機環境驗證**。

---

## 📋 階段一：硬體裝置要求 (Device Requirements)

本次 Workshop 的開發範例與自動化腳本已針對 macOS 環境進行高度優化與測試，請確保您的本機裝置符合以下要求：

- **作業系統:** 推薦使用 **macOS** (Intel 晶片或 Apple Silicon M1/M2/M3/M4 皆可)。
- **硬體配置:** 
  - 記憶體 (RAM): 建議 **16GB** (含) 以上，以流暢運作 Docker、Next.js 前端與 FastAPI 後端。
  - 硬碟空間: 保留 **10GB 以上** 的可用硬碟空間（用於下載 Docker 映像檔與 Python/Node.js 套件）。
- **基礎編譯工具:** 必須安裝 **Xcode Command Line Tools**。
  - 說明: 本專案的日常開發與自動化流程高度依賴 `Makefile` 與 `git` 指令，此為 macOS 的基礎編譯工具包。
  - 安裝指令:
    ```bash
    xcode-select --install
    ```
  - 驗證方式: 執行 `make --version` 或 `git --version` 有正確輸出版本號即可。

---

## 📋 階段二：必備帳號與雲端權限 (Account & Cloud Preparation)

在進入軟體安裝前，您必須準備好以下兩大雲端帳號：

### 1. Google Cloud Platform (GCP) 帳號與專案 (重要 🌟)
本次 Workshop 將實際使用 **Vertex AI Gemini API** 以及 **Terraform 雲端部署** (Cloud Run, Cloud SQL, Secret Manager, WIF)。
- **GCP 帳號:** 請準備好您的個人或公司 GCP 帳號，並登入 [Google Cloud Console](https://console.cloud.google.com/)。
- **GCP 專案:** 建立或選擇一個乾淨的 GCP 專案，並記錄其 **Project ID**。
- **啟用計費 (Billing):** **您的 GCP 專案必須已啟用計費功能 (Billing)**。未綁定信用卡的 GCP 專案將無法使用 Vertex AI 及建立雲端基礎設施。
- **帳號權限:** 確保您的帳號在該專案中擁有 `Owner` (擁有者) 或 `Editor` (編輯者) 權限，以便建立服務帳號與 CI/CD 觸發器。

### 2. GitHub 帳號
本專案整合了基於 GitHub 的雲端自動化部署 (CI/CD)。
- 請準備一個常用的 **GitHub 帳號**。
- 在課程開始時，您需要將本專案的 Repository **Fork (分支)** 至您的個人 GitHub 帳號下，以便綁定 GCP Cloud Build 自動化部署管線。

---

## 📋 階段三：開發工具與環境安裝說明書 (Software & Runtimes)

請使用 macOS 推薦的套件管理器 **Homebrew** 依序完成以下安裝：

### 1. 套件管理器: Homebrew
若您的 Mac 還沒有安裝 Homebrew，請開啟終端機執行：
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. 程式碼編輯器: Visual Studio Code (VS Code)
推薦使用 VS Code 作為本次課程的開發編輯器：
```bash
brew install --cask visual-studio-code
```
**必備 VS Code 擴充套件 (Extensions):**
請於終端機執行以下指令，一鍵裝好 Python、代碼格式化、前端與 Docker 的輔助套件：
```bash
# Python & Ruff 代碼檢查
code --install-extension ms-python.python
code --install-extension charliermarsh.ruff

# 前端與格式化工具 (Next.js, ESLint, Prettier, Tailwind CSS)
code --install-extension dbaeumer.vscode-eslint
code --install-extension esbenp.prettier-vscode
code --install-extension bradlc.vscode-tailwindcss

# Docker 容器整合
code --install-extension ms-azuretools.vscode-docker
```

### 3. 容器化平台: Docker Desktop
專案內的 PostgreSQL 資料庫 (含 pgvector) 與 MCP Toolbox 服務皆運作於本機 Docker 容器中。
```bash
brew install --cask docker
```
*安裝後請務必打開 Docker Desktop 軟體，並確保狀態列顯示為綠色的「Running」。*

### 4. 開發執行環境 (Runtimes)
安裝專案後端 (Python 3.12) 與前端 (Node.js v20+)：
```bash
# 安裝 Python 3.12 (Google ADK 與專案後端必備)
brew install python@3.12

# 安裝 Node.js (前端 Next.js 15 編譯與執行環境)
brew install node
```

### 5. 專案管理器與雲端工具 (Managers & Cloud CLI)
```bash
# 安裝 uv (Astral 開發的極速 Python 套件與虛擬環境管理工具)
brew install uv

# 安裝 Google Cloud CLI (SDK)
brew install --cask google-cloud-sdk

# 安裝 Terraform (雲端基礎設施與部署管線管理工具)
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
```

---

## 📋 階段四：本機環境初始化與啟動 (Initialization & Bootstrapping)

完成上述所有軟體安裝後，請進入本專案根目錄，並在終端機依序執行以下指令進行專案初始化：

### 1. 設定本機環境變數
```bash
# 複製範本建立本機 .env 檔案
cp .env.example .env
```
*請開啟 `.env`，根據您的 API 提供商填寫密鑰（如 `GOOGLE_API_KEY`）。若不使用 Vertex AI，可將 `GOOGLE_GENAI_USE_VERTEXAI` 設為 `0`。*

### 2. 啟動並初始化本機資料庫
確保本機 Docker Desktop 已啟動，然後執行：
```bash
# 一鍵建立、啟動本機資料庫容器、導入測試帳號 (Seed) 以及匯入知識庫向量資料 (Ingest)
make db-setup
```

### 3. 安裝相依套件與依賴
```bash
# 建立本機 Python 3.12 虛擬環境 (.venv) 並安裝所有依賴 (含 dev, eval, gcp)
make install-all

# 安裝前端 Next.js 專案依賴
make ui-install
```

### 4. 本機 GCP 認證與預設專案設定
```bash
# 1. 登入您的 Google 帳號 (會引導至瀏覽器進行登入)
gcloud auth login

# 2. 設定預設專案 ID
gcloud config set project YOUR_PROJECT_ID

# 3. 啟用本機應用程式憑證授權 (呼叫 Vertex AI 測試時必備)
gcloud auth application-default login
```

---

## 📋 階段五：一鍵驗證環境 (Check & Ready)

為了確保您在課前已經「完美就緒」，專案內提供了一鍵式自動環境檢查工具。

請在專案根目錄下執行：
```bash
make check-setup
```

### 預期結果:
該指令會瞬間掃描您的 Mac，並對：
- [✔] 系統工具 (Xcode Tools, Homebrew, Docker)
- [✔] 編輯器與擴充套件 (VS Code Extensions)
- [✔] 執行環境 (Python 3.12, Node.js, Terraform)
- [✔] 雲端認證狀態 (gcloud 登入帳號, Application Default Credentials 憑證授權)
- [✔] 專案初始化狀態 (.env, 資料庫容器, .venv 依賴, node_modules 依賴)

進行全面健康檢查。當最下方輸出以下訊息時，恭喜您！您的環境已經 100% 準備好，可以開心參與本次 Workshop 的精采實作了！

> **🎉 驗證成功！本機所有開發工具、執行環境與專案依賴已 100% 準備就緒！**

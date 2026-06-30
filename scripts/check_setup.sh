#!/bin/bash

# Colors for modern and beautiful console output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

HAS_ERRORS=0

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}🔍 保險推薦 Agent Workshop: 本機環境核准檢查 (docs/setup-env.md)${NC}"
echo -e "${CYAN}======================================================================${NC}"

# 📋 階段一：系統與基礎開發工具
echo -e "\n${BLUE}📋 階段一：系統與基礎開發工具 (System & Core Tools)${NC}"

# 1. Docker Desktop
if command -v docker >/dev/null 2>&1; then
    DOCKER_VER=$(docker --version | head -n 1)
    if docker info >/dev/null 2>&1; then
        echo -e "  [${GREEN}✔${NC}] 1. Docker Desktop (已安裝: ${DOCKER_VER}, 服務運作中)"
    else
        echo -e "  [${YELLOW}⚠${NC}] 1. Docker Desktop (已安裝: ${DOCKER_VER}, 但${YELLOW}服務未啟動${NC}，請打開 Docker Desktop)"
    fi
else
    echo -e "  [${RED}✘${NC}] 1. Docker Desktop (未安裝，請執行 choco install docker-desktop -y)"
    HAS_ERRORS=1
fi

# 📋 階段二：程式碼編輯器與擴充套件
echo -e "\n${BLUE}📋 階段二：程式碼編輯器與擴充套件 (Editor & VS Code Extensions)${NC}"

# 1. VS Code
if command -v code >/dev/null 2>&1; then
    VSCODE_VER=$(code --version | head -n 1)
    echo -e "  [${GREEN}✔${NC}] 1. Visual Studio Code (已安裝: ${VSCODE_VER})"
    
    # 2. VS Code Extensions
    EXT_LIST=$(code --list-extensions 2>/dev/null)
    REQUIRED_EXTS=("ms-python.python" "charliermarsh.ruff" "dbaeumer.vscode-eslint" "esbenp.prettier-vscode" "bradlc.vscode-tailwindcss" "ms-azuretools.vscode-docker")
    MISSING_EXTS=()
    
    for ext in "${REQUIRED_EXTS[@]}"; do
        if ! echo "$EXT_LIST" | grep -iq "^${ext}$"; then
            MISSING_EXTS+=("$ext")
        fi
    done
    
    if [ ${#MISSING_EXTS[@]} -eq 0 ]; then
        echo -e "  [${GREEN}✔${NC}] 2. VS Code 必備擴充套件 (全數已安裝)"
    else
        echo -e "  [${YELLOW}⚠${NC}] 2. VS Code 擴充套件 (${YELLOW}部分未安裝${NC}: ${MISSING_EXTS[*]})"
    fi
else
    echo -e "  [${YELLOW}⚠${NC}] 1. Visual Studio Code (未在 PATH 中找到 'code' 指令，略過擴充套件檢查)"
    echo -e "  [${YELLOW}⚠${NC}] 2. VS Code 必備擴充套件 (未檢查)"
fi

# 📋 階段三：執行環境與套件管理
echo -e "\n${BLUE}📋 階段三：執行環境與套件管理 (Runtimes & Dependency Managers)${NC}"

# 1. Python 3.12
if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    PY_MAJOR_MINOR=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [ "$PY_MAJOR_MINOR" = "3.12" ]; then
        echo -e "  [${GREEN}✔${NC}] 1. Python 3.12 (已安裝: ${PY_VER})"
    else
        echo -e "  [${YELLOW}⚠${NC}] 1. Python (已安裝: ${PY_VER}，但建議使用 3.12 版本以確保相容性)"
    fi
else
    echo -e "  [${RED}✘${NC}] 1. Python 3.12 (未安裝)"
    HAS_ERRORS=1
fi

# 2. uv
if command -v uv >/dev/null 2>&1; then
    UV_VER=$(uv --version | head -n 1)
    echo -e "  [${GREEN}✔${NC}] 2. uv (${UV_VER})"
else
    echo -e "  [${RED}✘${NC}] 2. uv (未安裝，請執行 choco install uv -y)"
    HAS_ERRORS=1
fi

# 3. Node.js
if command -v node >/dev/null 2>&1; then
    NODE_VER=$(node -v)
    NODE_MAJOR=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
    if [ "$NODE_MAJOR" -ge 20 ]; then
        echo -e "  [${GREEN}✔${NC}] 3. Node.js (已安裝: ${NODE_VER})"
    else
        echo -e "  [${YELLOW}⚠${NC}] 3. Node.js (已安裝: ${NODE_VER}，但建議版本 >= v20)"
    fi
else
    echo -e "  [${RED}✘${NC}] 3. Node.js (未安裝，請執行 choco install nodejs -y)"
    HAS_ERRORS=1
fi

# 4. Google Cloud SDK & Account Login Check
if command -v gcloud >/dev/null 2>&1; then
    GCLOUD_VER=$(gcloud --version | head -n 1)
    ADC_FILE="$HOME/.config/gcloud/application_default_credentials.json"
    
    # Check if logged in with active GCP account
    GCP_ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null)
    
    if [ -n "$GCP_ACTIVE_ACCOUNT" ]; then
        if [ -f "$ADC_FILE" ] || [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
            echo -e "  [${GREEN}✔${NC}] 4. Google Cloud SDK (已安裝: ${GCLOUD_VER}, 帳號已登入: ${GCP_ACTIVE_ACCOUNT}, 憑證已授權)"
        else
            echo -e "  [${YELLOW}⚠${NC}] 4. Google Cloud SDK (已安裝: ${GCLOUD_VER}, 帳號已登入: ${GCP_ACTIVE_ACCOUNT}，但${YELLOW}未授權 Application Default Credentials${NC}，請執行 gcloud auth application-default login)"
        fi
    else
        echo -e "  [${YELLOW}⚠${NC}] 4. Google Cloud SDK (已安裝: ${GCLOUD_VER}，但${YELLOW}未登入 GCP 帳號${NC}，請執行 gcloud auth login)"
    fi
else
    echo -e "  [${YELLOW}⚠${NC}] 4. Google Cloud SDK (未安裝，若不需要部署可忽略)"
fi

# 5. Terraform
if command -v terraform >/dev/null 2>&1; then
    TF_VER=$(terraform -v | head -n 1)
    echo -e "  [${GREEN}✔${NC}] 5. Terraform (已安裝: ${TF_VER})"
else
    echo -e "  [${RED}✘${NC}] 5. Terraform (未安裝，請參考 docs/setup-env.md 安裝)"
    HAS_ERRORS=1
fi

# 📋 階段四：專案初始化與啟動
echo -e "\n${BLUE}📋 階段四：專案初始化與啟動 (Initialization & Bootstrapping)${NC}"

# 1. .env file
if [ -f .env ]; then
    if grep -q "^GOOGLE_API_KEY=" .env || grep -q "^GOOGLE_APPLICATION_CREDENTIALS=" .env; then
        echo -e "  [${GREEN}✔${NC}] 1. 本機環境變數 (.env 存在且設定完成)"
    else
        echo -e "  [${YELLOW}⚠${NC}] 1. 本機環境變數 (.env 存在但未設定 Google Credentials)"
    fi
else
    echo -e "  [${RED}✘${NC}] 1. 本機環境變數 (.env 不存在，請複製 .env.example 並設定)"
    HAS_ERRORS=1
fi

# 2. Database and Toolbox container running status
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DB_RUNNING=$(docker compose ps --services --filter "status=running" | grep -q "^db$" && echo "yes" || echo "no")
    TOOLBOX_RUNNING=$(docker compose ps --services --filter "status=running" | grep -q "^toolbox$" && echo "yes" || echo "no")
    
    if [ "$DB_RUNNING" = "yes" ] && [ "$TOOLBOX_RUNNING" = "yes" ]; then
        echo -e "  [${GREEN}✔${NC}] 2. 資料庫與 Toolbox 容器 (皆運作中)"
    elif [ "$DB_RUNNING" = "yes" ]; then
        echo -e "  [${YELLOW}⚠${NC}] 2. 資料庫與 Toolbox 容器 (db 運作中，但 ${YELLOW}toolbox 未啟動${NC})"
    else
        echo -e "  [${RED}✘${NC}] 2. 資料庫與 Toolbox 容器 (未啟動，請執行 make db-up 或 make db-setup)"
        HAS_ERRORS=1
    fi
else
    echo -e "  [${RED}✘${NC}] 2. 資料庫與 Toolbox 容器 (無法偵測，Docker 服務未運作)"
    HAS_ERRORS=1
fi

# 3. Python Virtual Environment
if [ -d .venv ]; then
    if .venv/bin/python -c "import fastapi" >/dev/null 2>&1; then
        echo -e "  [${GREEN}✔${NC}] 3. Python 虛擬環境與依賴 (.venv 已安裝且依賴完備)"
    else
        echo -e "  [${YELLOW}⚠${NC}] 3. Python 虛擬環境與依賴 (.venv 存在，但${YELLOW}依賴不完整${NC}，請執行 make install-all)"
    fi
else
    echo -e "  [${RED}✘${NC}] 3. Python 虛擬環境與依賴 (.venv 不存在，請執行 make install-all)"
    HAS_ERRORS=1
fi

# 4. Frontend Node.js Dependencies
if [ -d "frontend/node_modules" ]; then
    echo -e "  [${GREEN}✔${NC}] 4. 前端 Node.js 依賴 (node_modules 已安裝)"
else
    echo -e "  [${RED}✘${NC}] 4. 前端 Node.js 依賴 (node_modules 不存在，請執行 make ui-install)"
    HAS_ERRORS=1
fi

# 📋 階段五：最終環境驗證
echo -e "\n${BLUE}📋 階段五：最終環境驗證 (Validation & Summary)${NC}"

echo -e "${CYAN}----------------------------------------------------------------------${NC}"
if [ $HAS_ERRORS -eq 0 ]; then
    echo -e "🎉 ${GREEN}驗證成功！本機所有開發工具、執行環境與專案依賴已 100% 準備就緒！${NC}"
    echo -e "${CYAN}======================================================================${NC}"
    exit 0
else
    echo -e "❌ ${RED}本機環境尚有未完成安裝或啟動之必要項目，請檢查上方帶有 [✘] 的項目並進行修正。${NC}"
    echo -e "${CYAN}======================================================================${NC}"
    exit 1
fi

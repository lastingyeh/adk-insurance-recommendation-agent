param(
  [Parameter(Position = 0)]
  [string]$Command = "help",
  [string]$Evalset,
  [string]$ConfigFile,
  [string]$EnvName = "dev",
  [int]$Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$DefaultEvalConfig = "tests/eval/configs/test_config.json"
$DynamicEvalConfig = "tests/eval/configs/dynamic_config.json"
$DefaultEvalset = "tests/eval/evalsets/basic.evalset.json"

function Invoke-Step {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$Arguments = @()
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $FilePath $($Arguments -join ' ')"
  }
}

function Load-ProjectEnv {
  $envFile = Join-Path $RepoRoot ".env"
  if (Test-Path -LiteralPath $envFile) {
    & (Join-Path $PSScriptRoot "load-env.ps1") -Path $envFile
  }
}

function Stop-PortProcess {
  param([int]$TargetPort)

  Get-NetTCPConnection -LocalPort $TargetPort -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
      try {
        Stop-Process -Id $_ -Force -ErrorAction Stop
      } catch {
        Write-Warning "Unable to stop PID $_ on port ${TargetPort}: $($_.Exception.Message)"
      }
    }
}

function Invoke-Uv {
  param([string[]]$Arguments)
  Invoke-Step -FilePath "uv" -Arguments $Arguments
}

function Invoke-Npm {
  param([string[]]$Arguments)
  Invoke-Step -FilePath "npm" -Arguments $Arguments
}

function Invoke-Docker {
  param([string[]]$Arguments)
  Invoke-Step -FilePath "docker" -Arguments $Arguments
}

function Invoke-Gcloud {
  param([string[]]$Arguments)
  Invoke-Step -FilePath "gcloud" -Arguments $Arguments
}

function Invoke-Terraform {
  param(
    [string[]]$Arguments,
    [string]$WorkingDirectory
  )

  Push-Location $WorkingDirectory
  try {
    Invoke-Step -FilePath "terraform" -Arguments $Arguments
  } finally {
    Pop-Location
  }
}

function Get-EnvOrDefault {
  param(
    [string]$Name,
    [string]$DefaultValue
  )

  $value = [System.Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($value)) {
    return $DefaultValue
  }
  return $value
}

function Get-GcpProjectId {
  $project = Get-EnvOrDefault -Name "GOOGLE_CLOUD_PROJECT" -DefaultValue ""
  if (-not $project) {
    $project = (& gcloud config get-value project 2>$null).Trim()
  }
  return $project
}

function Get-GcpRegion {
  return Get-EnvOrDefault -Name "GCP_REGION" -DefaultValue "us-central1"
}

function Get-GcpRepo {
  $repo = Get-EnvOrDefault -Name "ARTIFACT_REPOSITORY" -DefaultValue ""
  if (-not $repo) {
    $repo = "insurance-agent-repo"
  }
  return $repo
}

function Get-TfDir {
  param([string]$SelectedEnvName)
  return Join-Path $RepoRoot "deployment/terraform/$SelectedEnvName"
}

function Get-BootstrapTfDir {
  return Join-Path $RepoRoot "deployment/terraform/bootstrap"
}

function Get-TfBackendFile {
  param([string]$SelectedEnvName)
  return "$SelectedEnvName.tfbackend"
}

function Get-ImageTag {
  return Get-EnvOrDefault -Name "IMAGE_TAG" -DefaultValue "latest"
}

function Get-ImageUri {
  param([string]$Name)
  $project = Get-GcpProjectId
  $region = Get-GcpRegion
  $repo = Get-GcpRepo
  $tag = Get-ImageTag
  return "$region-docker.pkg.dev/$project/$repo/$Name`:$tag"
}

function Invoke-AdkEval {
  param(
    [Parameter(Mandatory = $true)]
    [string]$EvalsetPath,
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath
  )

  Invoke-Uv @("run", "adk", "eval", "app", $EvalsetPath, "--config_file_path", $ConfigPath)
}

function Get-TerraformOutputValue {
  param(
    [string]$WorkingDirectory,
    [string]$OutputName
  )

  Push-Location $WorkingDirectory
  try {
    $value = (& terraform output -raw $OutputName 2>$null)
    if ($LASTEXITCODE -ne 0) {
      return ""
    }
    return ($value | Out-String).Trim()
  } finally {
    Pop-Location
  }
}

function Assert-NoBootstrapLock {
  $lockPath = Join-Path (Get-BootstrapTfDir) ".terraform.tfstate.lock.info"
  if (Test-Path $lockPath) {
    throw "Terraform bootstrap state lock exists: $lockPath"
  }
}

function Get-AdcSourcePath {
  if ($env:GOOGLE_APPLICATION_CREDENTIALS -and (Test-Path $env:GOOGLE_APPLICATION_CREDENTIALS)) {
    return $env:GOOGLE_APPLICATION_CREDENTIALS
  }

  $defaultAdc = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
  if (Test-Path $defaultAdc) {
    return $defaultAdc
  }

  return ""
}

function Invoke-EvalGroup {
  param(
    [string[]]$Items,
    [string]$ConfigPath
  )

  foreach ($item in $Items) {
    Invoke-AdkEval -EvalsetPath $item -ConfigPath $ConfigPath
  }
}

function Write-Help {
  @"
Usage:
  .\scripts\dev.ps1 <command>

Common commands:
  install, install-all, sync, sync-all, env-check
  db-init, db-up, db-seed, db-ingest, db-setup, db-clean, db-reset
  up, up-build, down, logs, toolbox-logs
  run-web, run-api, run-fastapi, debug-fastapi, run-cli
  ui-install, ui-dev, ui-build
  check, check-setup, test-api, test-security, test-audit, test, lint
  kill-adk-port, kill-fastapi-port, kill-ui-port, kill-port
  eval, eval-core, eval-extended, eval-safety, eval-session-aware, eval-live, eval-all
  deploy, playground, register-gemini-enterprise
  backend, env-check-gcp, gcp-deploy, gcp-bootstrap
  tf-bootstrap, tf-bootstrap-destroy, gcp-db-setup
  tf-gen-config, build-push, tf-init, tf-plan, tf-apply, tf-destroy, tf-db-password
  gcp-db-proxy, gcp-db-init-info, gcp-traffic-list, gcp-rollback, gcp-cleanup-orphans
  clean, clean-all
"@ | Write-Host
}

Load-ProjectEnv

switch ($Command) {
  "help" { Write-Help }
  "install" {
    Invoke-Uv @("venv", "--python", "3.12")
    Invoke-Uv @("sync")
  }
  "install-all" {
    Invoke-Uv @("venv", "--python", "3.12")
    Invoke-Uv @("sync", "--all-extras")
  }
  "sync" { Invoke-Uv @("sync") }
  "sync-all" { Invoke-Uv @("sync", "--all-extras") }
  "env-check" {
    Write-Host "=== Environment Check ==="
    foreach ($tool in @("uv", "docker", "npm")) {
      if (Get-Command $tool -ErrorAction SilentlyContinue) {
        Write-Host "OK  $tool"
      } else {
        Write-Host "MISS $tool"
      }
    }
    Write-Host ".env  : $(Test-Path .env)"
    Write-Host ".venv : $(Test-Path .venv)"
  }
  "check-setup" {
    $script:hasErrors = $false

    function Write-CheckHeader { param([string]$Text); Write-Host ""; Write-Host $Text -ForegroundColor Blue }
    function Write-CheckOk     { param([string]$Text); Write-Host "  ✔ $Text" -ForegroundColor Green }
    function Write-CheckWarn   { param([string]$Text); Write-Host "  ⚠ $Text" -ForegroundColor Yellow }
    function Write-CheckFail   { param([string]$Text); Write-Host "  ✘ $Text" -ForegroundColor Red; $script:hasErrors = $true }

    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host "🔍 保險推薦 Agent Workshop: 本機環境核准檢查" -ForegroundColor Cyan
    Write-Host "======================================================================" -ForegroundColor Cyan

    # ─── 階段一：系統與基礎開發工具 ───────────────────────────
    Write-CheckHeader "📋 階段一：系統與基礎開發工具 (System & Core Tools)"

    if (Get-Command docker -ErrorAction SilentlyContinue) {
      $dockerVer = (& docker --version) 2>$null
      if ($dockerVer) {
        # `docker info` writes WARNING lines to stderr (e.g. "No blkio
        # throttle... support") on the WSL2 backend. With
        # $ErrorActionPreference = "Stop" at the top of this script,
        # those warnings become terminating errors. Redirect ALL
        # streams to stdout via *>&1, then look for "Server Version:"
        # (which only appears when the daemon is reachable) instead of
        # trusting the (often non-zero) exit code.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
          $dockerInfoOut = (& docker info *>&1) | Out-String
        } catch {
          $dockerInfoOut = ""
        } finally {
          $ErrorActionPreference = $prevEAP
        }
        if ($dockerInfoOut -match "Server Version:\s") {
          $dockerVerLine = ($dockerVer | Select-Object -First 1).ToString().Trim()
          Write-CheckOk "1. Docker Desktop ($dockerVerLine, 服務運作中)"
        } else {
          Write-CheckWarn "1. Docker Desktop (已安裝: $dockerVer, 但服務未啟動，請打開 Docker Desktop)"
        }
      }
    } else {
      Write-CheckFail "1. Docker Desktop (未安裝，請執行 choco install docker-desktop -y)"
    }

    # ─── 階段二：編輯器與擴充套件 ───────────────────────────
    Write-CheckHeader "📋 階段二：編輯器與擴充套件 (Editor & VS Code Extensions)"

    if (Get-Command code -ErrorAction SilentlyContinue) {
      $vscodeVer = (& code --version) 2>$null | Select-Object -First 1
      Write-CheckOk "1. Visual Studio Code ($vscodeVer)"
      $extList = (& code --list-extensions 2>$null) | ForEach-Object { $_.Trim() }
      $requiredExts = @(
        "ms-python.python",
        "charliermarsh.ruff",
        "dbaeumer.vscode-eslint",
        "esbenp.prettier-vscode",
        "bradlc.vscode-tailwindcss",
        "ms-azuretools.vscode-docker"
      )
      $missingExts = @()
      foreach ($ext in $requiredExts) {
        if (-not ($extList -contains $ext)) {
          $missingExts += $ext
        }
      }
      if ($missingExts.Count -eq 0) {
        Write-CheckOk "2. VS Code 必備擴充套件 (全數已安裝)"
      } else {
        Write-CheckWarn "2. VS Code 擴充套件 (部分未安裝: $($missingExts -join ', '))"
      }
    } else {
      Write-CheckWarn "1. Visual Studio Code (未在 PATH 中找到 'code' 指令，略過擴充套件檢查)"
      Write-CheckWarn "2. VS Code 必備擴充套件 (未檢查)"
    }

    # ─── 階段三：執行環境與套件管理 ───────────────────────────
    Write-CheckHeader "📋 階段三：執行環境與套件管理 (Runtimes & Dependency Managers)"

    if (Get-Command python -ErrorAction SilentlyContinue) {
      $pyVer = (& python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')") 2>$null
      $pyMM  = (& python -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')") 2>$null
      if ($pyMM -eq "3.12") {
        Write-CheckOk "1. Python 3.12 ($pyVer)"
      } else {
        Write-CheckWarn "1. Python ($pyVer，建議使用 3.12 版本以確保相容性)"
      }
    } elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
      $pyVer = (& python3 -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')") 2>$null
      $pyMM  = (& python3 -c "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')") 2>$null
      if ($pyMM -eq "3.12") {
        Write-CheckOk "1. Python 3.12 ($pyVer)"
      } else {
        Write-CheckWarn "1. Python ($pyVer，建議使用 3.12 版本以確保相容性)"
      }
    } else {
      Write-CheckFail "1. Python 3.12 (未安裝)"
    }

    if (Get-Command uv -ErrorAction SilentlyContinue) {
      $uvVer = (& uv --version) 2>$null
      Write-CheckOk "2. uv ($uvVer)"
    } else {
      Write-CheckFail "2. uv (未安裝，請執行 choco install uv -y)"
    }

    if (Get-Command node -ErrorAction SilentlyContinue) {
      $nodeRaw = (& node -v) 2>$null
      $nodeMajor = 0
      if ($nodeRaw -match "^v(\d+)") { [int]$nodeMajor = $Matches[1] }
      if ($nodeMajor -ge 20) {
        Write-CheckOk "3. Node.js ($nodeRaw)"
      } else {
        Write-CheckWarn "3. Node.js ($nodeRaw，建議版本 >= v20)"
      }
    } else {
      Write-CheckFail "3. Node.js (未安裝，請執行 choco install nodejs -y)"
    }

    if (Get-Command gcloud -ErrorAction SilentlyContinue) {
      $gcloudVer = (& gcloud --version) 2>$null | Select-Object -First 1
      $adcPath = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
      $activeAccount = (& gcloud auth list --filter=status:ACTIVE --format="value(account)") 2>$null
      if ($activeAccount) {
        if ((Test-Path $adcPath) -or $env:GOOGLE_APPLICATION_CREDENTIALS) {
          Write-CheckOk "4. Google Cloud SDK ($gcloudVer, 帳號已登入: $activeAccount, 憑證已授權)"
        } else {
          Write-CheckWarn "4. Google Cloud SDK ($gcloudVer, 帳號已登入: $activeAccount, 但未授權 ADC，請執行 gcloud auth application-default login)"
        }
      } else {
        Write-CheckWarn "4. Google Cloud SDK ($gcloudVer, 未登入 GCP 帳號，請執行 gcloud auth login)"
      }
    } else {
      Write-CheckWarn "4. Google Cloud SDK (未安裝，若不需要部署可忽略)"
    }

    if (Get-Command terraform -ErrorAction SilentlyContinue) {
      $tfVer = (& terraform -v) 2>$null | Select-Object -First 1
      Write-CheckOk "5. Terraform ($tfVer)"
    } else {
      Write-CheckFail "5. Terraform (未安裝，請參考 docs/setup-env.md)"
    }

    # ─── 階段四：專案初始化與啟動 ───────────────────────────
    Write-CheckHeader "📋 階段四：專案初始化與啟動 (Initialization & Bootstrapping)"

    if (Test-Path .env) {
      $envContent = Get-Content .env -Raw
      if ($envContent -match "^GOOGLE_API_KEY=" -or $envContent -match "^GOOGLE_APPLICATION_CREDENTIALS=") {
        Write-CheckOk "1. 本機環境變數 (.env 存在且設定完成)"
      } else {
        Write-CheckWarn "1. 本機環境變數 (.env 存在但未設定 Google Credentials)"
      }
    } else {
      Write-CheckFail "1. 本機環境變數 (.env 不存在，請複製 .env.example 並設定)"
    }

    $dockerAvailableForCheck = $false
    if (Get-Command docker -ErrorAction SilentlyContinue) {
      $prevEAP2 = $ErrorActionPreference
      $ErrorActionPreference = "Continue"
      try {
        $dockerInfoOut2 = (& docker info *>&1) | Out-String
      } catch {
        $dockerInfoOut2 = ""
      } finally {
        $ErrorActionPreference = $prevEAP2
      }
      if ($dockerInfoOut2 -match "Server Version:\s") {
        $dockerAvailableForCheck = $true
      }
    }

    if ($dockerAvailableForCheck) {
      $dbUp = (& docker compose ps --services --filter "status=running") 2>$null | Where-Object { $_ -eq "db" }
      $tbUp = (& docker compose ps --services --filter "status=running") 2>$null | Where-Object { $_ -eq "toolbox" }
      if ($dbUp -and $tbUp) {
        Write-CheckOk "2. 資料庫與 Toolbox 容器 (皆運作中)"
      } elseif ($dbUp) {
        Write-CheckWarn "2. 資料庫與 Toolbox 容器 (db 運作中，但 toolbox 未啟動，請執行 .\scripts\dev.ps1 db-up)"
      } else {
        Write-CheckFail "2. 資料庫與 Toolbox 容器 (未啟動，請執行 .\scripts\dev.ps1 db-up 或 .\scripts\dev.ps1 db-setup)"
      }
    } else {
      Write-CheckFail "2. 資料庫與 Toolbox 容器 (無法偵測，Docker 服務未運作)"
    }

    if (Test-Path .venv) {
      $pyExe = Join-Path (Resolve-Path .venv).Path "Scripts\python.exe"
      if (-not (Test-Path $pyExe)) {
        $pyExe = Join-Path (Resolve-Path .venv).Path "bin\python"
      }
      if (Test-Path $pyExe) {
        $fastapiCheck = & $pyExe -c "import fastapi" 2>$null
        if ($LASTEXITCODE -eq 0) {
          Write-CheckOk "3. Python 虛擬環境與依賴 (.venv 已安裝且依賴完備)"
        } else {
          Write-CheckWarn "3. Python 虛擬環境與依賴 (.venv 存在，但依賴不完整，請執行 .\scripts\dev.ps1 install-all)"
        }
      } else {
        Write-CheckWarn "3. Python 虛擬環境與依賴 (.venv 存在但 python executable 缺失，請執行 .\scripts\dev.ps1 install-all)"
      }
    } else {
      Write-CheckFail "3. Python 虛擬環境與依賴 (.venv 不存在，請執行 .\scripts\dev.ps1 install-all)"
    }

    if (Test-Path "frontend/node_modules") {
      Write-CheckOk "4. 前端 Node.js 依賴 (node_modules 已安裝)"
    } else {
      Write-CheckFail "4. 前端 Node.js 依賴 (node_modules 不存在，請執行 .\scripts\dev.ps1 ui-install)"
    }

    # ─── 階段五：最終環境驗證 ───────────────────────────
    Write-CheckHeader "📋 階段五：最終環境驗證 (Validation & Summary)"
    Write-Host "----------------------------------------------------------------------" -ForegroundColor Cyan
    if ($script:hasErrors) {
      Write-Host "❌ 本機環境尚有未完成安裝或啟動之必要項目，請檢查上方帶有 ✘ 的項目並進行修正。" -ForegroundColor Red
      Write-Host "======================================================================" -ForegroundColor Cyan
      exit 1
    } else {
      Write-Host "🎉 驗證成功！本機所有開發工具、執行環境與專案依賴已 100% 準備就緒！" -ForegroundColor Green
      Write-Host "======================================================================" -ForegroundColor Cyan
      exit 0
    }
  }
  "db-init" { Invoke-Docker @("compose", "up", "-d", "db") }
  "db-seed" { Invoke-Uv @("run", "python", "scripts/seed_user.py") }
  "db-ingest" { Invoke-Uv @("run", "python", "scripts/ingest_faq_embeddings.py") }
  "db-setup" {
    Invoke-Docker @("compose", "up", "-d", "db")
    Invoke-Uv @("run", "python", "scripts/seed_user.py")
    Invoke-Uv @("run", "python", "scripts/ingest_faq_embeddings.py")
  }
  "db-clean" { Invoke-Docker @("compose", "down", "-v") }
  "db-reset" {
    Invoke-Docker @("compose", "down", "-v")
    Invoke-Docker @("compose", "up", "-d", "db")
    Invoke-Uv @("run", "python", "scripts/seed_user.py")
    Invoke-Uv @("run", "python", "scripts/ingest_faq_embeddings.py")
  }
  "up" { Invoke-Docker @("compose", "up", "-d") }
  "up-build" { Invoke-Docker @("compose", "up", "-d", "--build") }
  "db-up" { Invoke-Docker @("compose", "up", "-d", "db", "toolbox") }
  "down" { Invoke-Docker @("compose", "down") }
  "logs" { Invoke-Docker @("compose", "logs", "-f") }
  "toolbox-logs" { Invoke-Docker @("compose", "logs", "-f", "toolbox") }
  "run-web" {
    Stop-PortProcess 8000
    $sessionUri = Get-EnvOrDefault -Name "ADK_SESSION_DB_URI" -DefaultValue ""
    Invoke-Uv @("run", "adk", "web", "--session_service_uri", $sessionUri, ".")
  }
  "run-api" {
    Stop-PortProcess 8000
    Invoke-Uv @("run", "adk", "api_server", ".")
  }
  "run-fastapi" {
    Stop-PortProcess 8080
    $hostValue = Get-EnvOrDefault -Name "FASTAPI_HOST" -DefaultValue "127.0.0.1"
    $portValue = Get-EnvOrDefault -Name "FASTAPI_PORT" -DefaultValue "8080"
    $reloadValue = (Get-EnvOrDefault -Name "FASTAPI_RELOAD" -DefaultValue "true").ToLowerInvariant()
    $args = @("run", "uvicorn", "app.api.main:app", "--host", $hostValue, "--port", $portValue)
    if ($reloadValue -eq "true") {
      $args += "--reload"
    }
    Invoke-Uv $args
  }
  "debug-fastapi" {
    Stop-PortProcess 8080
    Invoke-Uv @(
      "run", "--with", "debugpy", "python", "-m", "debugpy",
      "--listen", "5678",
      "--wait-for-client",
      "-m", "uvicorn", "app.api.main:app",
      "--host", "127.0.0.1",
      "--port", "8080"
    )
  }
  "run-cli" { Invoke-Uv @("run", "adk", "run", "app") }
  "ui-install" { Invoke-Npm @("--prefix", "frontend", "install") }
  "ui-dev" { Invoke-Npm @("--prefix", "frontend", "run", "dev") }
  "ui-build" { Invoke-Npm @("--prefix", "frontend", "run", "build") }
  "kill-adk-port" { Stop-PortProcess 8000 }
  "kill-fastapi-port" { Stop-PortProcess 8080 }
  "kill-ui-port" { Stop-PortProcess 3000 }
  "kill-port" {
    if ($Port -le 0) {
      throw "Use -Port with kill-port."
    }
    Stop-PortProcess $Port
  }
  "check" { Invoke-Uv @("run", "python", "-m", "pytest", "tests/", "-v") }
  "test-api" { Invoke-Uv @("run", "python", "-m", "pytest", "tests/test_fastapi_api.py", "-v") }
  "test-security" { Invoke-Uv @("run", "python", "-m", "pytest", "tests/security", "-q") }
  "test-audit" {
    Invoke-Uv @(
      "run", "python", "-m", "pytest",
      "tests/security/test_audit_log_service.py",
      "tests/api/test_run_audit_integration.py",
      "-q"
    )
  }
  "backend" { & $PSCommandPath "deploy" }
  "deploy" {
    $requirementsPath = "app/app_utils/.requirements.txt"
    try {
      Invoke-Uv @(
        "export", "--no-hashes", "--no-header", "--no-dev",
        "--no-emit-project", "--no-annotate"
      ) | Set-Content -LiteralPath $requirementsPath
    } catch {
      Invoke-Uv @(
        "export", "--no-hashes", "--no-header", "--no-dev",
        "--no-emit-project"
      ) | Set-Content -LiteralPath $requirementsPath
    }

    $deployArgs = @(
      "run", "-m", "app.app_utils.deploy",
      "--source-packages=./app",
      "--entrypoint-module=app.agent_engine_app",
      "--entrypoint-object=agent_engine",
      "--requirements-file=app/app_utils/.requirements.txt"
    )

    if ($env:AGENT_IDENTITY) {
      $deployArgs += "--agent-identity"
    }
    if ($env:SECRETS) {
      $deployArgs += "--set-secrets=$($env:SECRETS)"
    }

    Invoke-Uv $deployArgs
  }
  "eval" {
    Invoke-Uv @("sync", "--dev", "--extra", "eval")
    $targetEvalset = if ($Evalset) { $Evalset } else { $DefaultEvalset }
    $targetConfig = if ($ConfigFile) { $ConfigFile } else { $DefaultEvalConfig }
    Invoke-Uv @("run", "adk", "eval", "./app", $targetEvalset, "--config_file_path", $targetConfig)
  }
  "eval-core" {
    Invoke-EvalGroup -Items @(
      "tests/eval/evalsets/core/case_1_medical_complete_info.evalset.json",
      "tests/eval/evalsets/core/case_2_missing_information.evalset.json",
      "tests/eval/evalsets/core/case_3_family_protection.evalset.json"
    ) -ConfigPath $DefaultEvalConfig
  }
  "eval-extended" {
    Invoke-EvalGroup -Items @(
      "tests/eval/evalsets/extended/case_4_accident_low_budget_young_user.evalset.json",
      "tests/eval/evalsets/extended/case_5_income_protection.evalset.json",
      "tests/eval/evalsets/extended/case_6_no_exact_match_senior_low_budget_medical.evalset.json"
    ) -ConfigPath $DefaultEvalConfig
  }
  "eval-safety" {
    Invoke-EvalGroup -Items @(
      "tests/eval/evalsets/safety/case_09_system_capability.evalset.json",
      "tests/eval/evalsets/safety/case_10_no_guarantee.evalset.json",
      "tests/eval/evalsets/safety/case_11_rule_explanation.evalset.json",
      "tests/eval/evalsets/safety/case_12_product_detail_follow_up.evalset.json",
      "tests/eval/evalsets/safety/case_13_no_investment_return.evalset.json",
      "tests/eval/evalsets/safety/case_14_no_pii_echo.evalset.json",
      "tests/eval/evalsets/safety/case_15_no_pii_in_state_response.evalset.json",
      "tests/eval/evalsets/safety/case_16_insufficient_info_no_product_search_with_pii.evalset.json",
      "tests/eval/evalsets/safety/case_17_pii_plus_recommendation_still_works.evalset.json"
    ) -ConfigPath $DefaultEvalConfig
  }
  "eval-session-aware" {
    Invoke-EvalGroup -Items @(
      "tests/eval/evalsets/session_aware/case_s1_reuse_existing_profile.evalset.json",
      "tests/eval/evalsets/session_aware/case_s2_follow_up_with_last_product.evalset.json",
      "tests/eval/evalsets/session_aware/case_s3_update_budget.evalset.json"
    ) -ConfigPath $DefaultEvalConfig
  }
  "eval-live" {
    Invoke-EvalGroup -Items @(
      "tests/eval/evalsets/live/live_case_1_affective_empathy.evalset.json",
      "tests/eval/evalsets/live/live_case_2_fragmented_speech.evalset.json",
      "tests/eval/evalsets/live/live_case_3_proactive_suggestion.evalset.json",
      "tests/eval/evalsets/live/live_case_4_realtime_correction.evalset.json"
    ) -ConfigPath $DefaultEvalConfig
    Invoke-AdkEval -EvalsetPath "tests/eval/evalsets/live/live_dynamic_scenarios.evalset.json" -ConfigPath $DynamicEvalConfig
  }
  "eval-all" {
    & $PSCommandPath "eval"
    & $PSCommandPath "eval-core"
    & $PSCommandPath "eval-extended"
    & $PSCommandPath "eval-safety"
    & $PSCommandPath "eval-session-aware"
    & $PSCommandPath "eval-live"
    if ($LASTEXITCODE -ne 0) {
      throw "eval-all failed"
    }
  }
  "lint" {
    Invoke-Uv @("sync", "--dev", "--extra", "lint", "--frozen")
    Invoke-Uv @("run", "codespell", "app/")
    Invoke-Uv @("run", "ruff", "check", "app/", "--diff")
    Invoke-Uv @("run", "ruff", "format", "app/", "--check", "--diff")
    Invoke-Uv @("run", "ty", "check", "app/")
  }
  "playground" { Invoke-Uv @("run", "adk", "web", ".", "--port", "8501", "--reload_agents") }
  "register-gemini-enterprise" { Invoke-Step -FilePath "uvx" -Arguments @("agent-starter-pack@0.41.3", "register-gemini-enterprise") }
  "test" {
    Invoke-Uv @("sync", "--dev", "--frozen")
    Invoke-Uv @("run", "pytest", "tests/unit")
    Invoke-Uv @("run", "pytest", "tests/integration")
  }
  "clean" {
    Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path . -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
      Remove-Item -Force -ErrorAction SilentlyContinue
    if (Test-Path ".pytest_cache") {
      Remove-Item ".pytest_cache" -Recurse -Force
    }
  }
  "clean-all" {
    & $PSCommandPath "clean"
    if (Test-Path ".venv") {
      Remove-Item ".venv" -Recurse -Force
    }
  }
  "tf-gen-config" {
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    if (-not $project) {
      throw "GOOGLE_CLOUD_PROJECT is required."
    }
    $bucket = "gs://$project-terraform-state"
    New-Item -ItemType Directory -Force -Path "deployment/terraform/dev", "deployment/terraform/staging", "deployment/terraform/prod" | Out-Null
    try {
      Invoke-Gcloud @("storage", "buckets", "describe", $bucket, "--project=$project")
    } catch {
      Invoke-Gcloud @("storage", "buckets", "create", $bucket, "--project=$project", "--location=$region")
    }
    @(
      @{ Name = "dev"; Prefix = "insurance-agent/dev" },
      @{ Name = "staging"; Prefix = "insurance-agent/staging" },
      @{ Name = "prod"; Prefix = "insurance-agent/prod" }
    ) | ForEach-Object {
      $path = "deployment/terraform/$($_.Name)/$($_.Name).tfbackend"
      @(
        "bucket = `"$($project)-terraform-state`"",
        "prefix = `"$($_.Prefix)`""
      ) | Set-Content -LiteralPath $path
    }
  }
  "tf-bootstrap" {
    & $PSCommandPath "env-check-gcp"
    if (-not $env:GITHUB_OWNER -or -not $env:GITHUB_REPO_NAME) {
      throw "GITHUB_OWNER and GITHUB_REPO_NAME are required."
    }
    Assert-NoBootstrapLock
    $bootstrapDir = Get-BootstrapTfDir
    Invoke-Terraform -WorkingDirectory $bootstrapDir -Arguments @("init")
    Invoke-Step -FilePath "powershell" -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "setup_github_conn.ps1"))
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    Invoke-Terraform -WorkingDirectory $bootstrapDir -Arguments @(
      "apply", "-auto-approve",
      "-var=project_id=$project",
      "-var=region=$region",
      "-var=github_owner=$($env:GITHUB_OWNER)",
      "-var=github_repo_name=$($env:GITHUB_REPO_NAME)"
    )
  }
  "tf-bootstrap-destroy" {
    & $PSCommandPath "env-check-gcp"
    if (-not $env:GITHUB_OWNER -or -not $env:GITHUB_REPO_NAME) {
      throw "GITHUB_OWNER and GITHUB_REPO_NAME are required."
    }
    Assert-NoBootstrapLock
    $bootstrapDir = Get-BootstrapTfDir
    Invoke-Terraform -WorkingDirectory $bootstrapDir -Arguments @(
      "destroy", "-auto-approve",
      "-var=project_id=$(Get-GcpProjectId)",
      "-var=region=$(Get-GcpRegion)",
      "-var=github_owner=$($env:GITHUB_OWNER)",
      "-var=github_repo_name=$($env:GITHUB_REPO_NAME)"
    )
  }
  "env-check-gcp" {
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    $repo = Get-GcpRepo
    if (-not $project) {
      throw "GOOGLE_CLOUD_PROJECT is required."
    }
    if (-not $region) {
      throw "GCP_REGION is required."
    }
    if (-not $repo) {
      throw "ARTIFACT_REPOSITORY or default repo name is required."
    }
    Write-Host "Project : $project"
    Write-Host "Region  : $region"
    Write-Host "Repo    : $repo"
  }
  "build-push" {
    & $PSCommandPath "env-check-gcp"
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    $repo = Get-GcpRepo
    if (-not $project) {
      throw "GOOGLE_CLOUD_PROJECT is required."
    }
    # Register the gcloud credential helper for this region's Artifact
    # Registry so `docker buildx build --push` can authenticate. Without
    # this entry, Docker falls back to an anonymous token request and
    # Artifact Registry returns 403. The command is idempotent and a
    # no-op when the helper is already configured.
    Invoke-Gcloud @("auth", "configure-docker", "$region-docker.pkg.dev", "--quiet")
    try {
      Invoke-Gcloud @(
        "artifacts", "repositories", "create", $repo,
        "--repository-format=docker",
        "--location=$region",
        "--description=Docker repository for insurance agent"
      )
    } catch {
      Write-Host "Artifact Registry repository already exists or create skipped."
    }
    $backendImage = Get-ImageUri -Name "insurance-backend"
    $toolboxImage = Get-ImageUri -Name "insurance-toolbox"
    $frontendImage = Get-ImageUri -Name "insurance-frontend"
    Invoke-Docker @("buildx", "build", "--platform", "linux/amd64", "-t", $backendImage, "-f", "Dockerfile.backend", "--push", ".")
    Invoke-Docker @("buildx", "build", "--platform", "linux/amd64", "-t", $toolboxImage, "-f", "Dockerfile.toolbox", "--push", ".")
    $frontendArgs = @("buildx", "build", "--platform", "linux/amd64", "-t", $frontendImage)
    if ($env:BACKEND_URL) {
      $frontendArgs += @("--build-arg", "NEXT_PUBLIC_FASTAPI_BASE_URL=$($env:BACKEND_URL)")
    }
    $frontendArgs += @("-f", "frontend/Dockerfile", "--push", "./frontend")
    Invoke-Docker $frontendArgs
  }
  "tf-init" {
    & $PSCommandPath "tf-gen-config"
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    $backendConfig = Get-TfBackendFile -SelectedEnvName $EnvName
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments @("init", "-reconfigure", "-backend-config=$backendConfig")
  }
  "tf-plan" {
    & $PSCommandPath "tf-init" -EnvName $EnvName
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    $args = @(
      "plan",
      "-var=project_id=$project",
      "-var=region=$region",
      "-var=backend_image=$(Get-ImageUri -Name 'insurance-backend')",
      "-var=toolbox_image=$(Get-ImageUri -Name 'insurance-toolbox')",
      "-var=frontend_image=$(Get-ImageUri -Name 'insurance-frontend')"
    )
    $tfvars = Join-Path $tfDir "vars/env.tfvars"
    if (Test-Path $tfvars) {
      $args = @("-var-file=vars/env.tfvars") + $args
    }
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments $args
  }
  "tf-apply" {
    & $PSCommandPath "tf-init" -EnvName $EnvName
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    $args = @(
      "apply", "-auto-approve",
      "-var=project_id=$project",
      "-var=region=$region",
      "-var=backend_image=$(Get-ImageUri -Name 'insurance-backend')",
      "-var=toolbox_image=$(Get-ImageUri -Name 'insurance-toolbox')",
      "-var=frontend_image=$(Get-ImageUri -Name 'insurance-frontend')"
    )
    $tfvars = Join-Path $tfDir "vars/env.tfvars"
    if (Test-Path $tfvars) {
      $args = @("-var-file=vars/env.tfvars") + $args
    }
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments $args
  }
  "tf-destroy" {
    & $PSCommandPath "tf-init" -EnvName $EnvName
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    $project = Get-GcpProjectId
    $region = Get-GcpRegion
    try {
      Invoke-Terraform -WorkingDirectory $tfDir -Arguments @("state", "rm", "module.agent_infrastructure.google_sql_user.db_user")
    } catch {
      Write-Host "Skipping terraform state rm for db_user."
    }
    $args = @(
      "destroy", "-auto-approve",
      "-var=project_id=$project",
      "-var=region=$region",
      "-var=backend_image=$(Get-ImageUri -Name 'insurance-backend')",
      "-var=toolbox_image=$(Get-ImageUri -Name 'insurance-toolbox')",
      "-var=frontend_image=$(Get-ImageUri -Name 'insurance-frontend')"
    )
    $tfvars = Join-Path $tfDir "vars/env.tfvars"
    if (Test-Path $tfvars) {
      $args = @("-var-file=vars/env.tfvars") + $args
    }
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments $args
  }
  "tf-db-password" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments @("output", "-raw", "db_password")
  }
  "gcp-db-proxy" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    Push-Location $tfDir
    try {
      $connectionName = (& terraform output -raw db_instance_connection_name 2>$null).Trim()
    } finally {
      Pop-Location
    }
    if (-not $connectionName) {
      throw "db_instance_connection_name not found. Run tf-apply first."
    }
    # Use the Docker-based cloud-sql-proxy image (same as gcp-db-setup) so
    # Windows users do not need to install the standalone cloud-sql-proxy
    # binary on PATH. Runs in foreground so connection logs are visible;
    # press Ctrl+C to stop. Reuses the same ADC and network conventions.
    $adcSourcePath = Get-AdcSourcePath
    if (-not $adcSourcePath) {
      throw "No application default credentials found. Set GOOGLE_APPLICATION_CREDENTIALS or run gcloud auth application-default login."
    }
    $adcTempPath = Join-Path $env:TEMP "adc_db_proxy.json"
    $proxyContainer = "cloud-sql-proxy"
    try {
      Copy-Item -LiteralPath $adcSourcePath -Destination $adcTempPath -Force
      try { & docker rm -f $proxyContainer *> $null } catch {}
      Write-Host "Starting cloud-sql-proxy (Docker) on 127.0.0.1:5432; Ctrl+C to stop."
      Write-Host "Connection: $connectionName"
      Invoke-Docker @(
        "run", "--rm",
        "--name", $proxyContainer,
        "-p", "5432:5432",
        "-v", "${adcTempPath}:/adc.json:ro",
        "-e", "GOOGLE_APPLICATION_CREDENTIALS=/adc.json",
        "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.23.0",
        $connectionName, "--port", "5432", "--address", "0.0.0.0"
      )
    } finally {
      try { & docker rm -f $proxyContainer *> $null } catch {}
      Remove-Item $adcTempPath -Force -ErrorAction SilentlyContinue
    }
  }
  "gcp-db-init-info" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    Invoke-Terraform -WorkingDirectory $tfDir -Arguments @("output", "-raw", "db_initialization_instructions")
  }
  "gcp-db-setup" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    $project = Get-GcpProjectId
    if (-not $project) {
      throw "GOOGLE_CLOUD_PROJECT is required."
    }

    $connectionName = Get-TerraformOutputValue -WorkingDirectory $tfDir -OutputName "db_instance_connection_name"
    $dbUser = Get-TerraformOutputValue -WorkingDirectory $tfDir -OutputName "db_user"
    $dbName = Get-TerraformOutputValue -WorkingDirectory $tfDir -OutputName "db_name"
    $secretName = "insurance-agent-db-password-$EnvName"
    $password = (& gcloud secrets versions access latest --secret=$secretName --project=$project 2>$null | Out-String).Trim()

    if (-not $connectionName -or -not $password -or -not $dbUser -or -not $dbName) {
      throw "Database bootstrap outputs are incomplete. Run tf-apply first."
    }

    $networkName = "gcp-db-setup-net"
    $proxyContainer = "cloud-sql-proxy"
    $adcTempPath = Join-Path $env:TEMP "adc_db_setup.json"
    $adcSourcePath = Get-AdcSourcePath

    if (-not $adcSourcePath) {
      throw "No application default credentials found. Set GOOGLE_APPLICATION_CREDENTIALS or run gcloud auth application-default login."
    }

    try {
      try {
        Invoke-Docker @("network", "create", $networkName)
      } catch {
        Write-Host "Docker network $networkName already exists or create skipped."
      }

      Copy-Item -LiteralPath $adcSourcePath -Destination $adcTempPath -Force

      try {
        & docker rm -f $proxyContainer *> $null
      } catch {
        # No such container is fine here; we just want a clean slate.
      }

      Invoke-Docker @(
        "run", "-d",
        "--name", $proxyContainer,
        "--network", $networkName,
        "-p", "5432:5432",
        "-v", "${adcTempPath}:/adc.json:ro",
        "-e", "GOOGLE_APPLICATION_CREDENTIALS=/adc.json",
        "gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.23.0",
        $connectionName, "--port", "5432", "--address", "0.0.0.0"
      )

      $ready = $false
      for ($i = 1; $i -le 30; $i++) {
        # Probe the proxy via pg_isready on the same user-defined network.
        # A bare TCP connect only confirms the proxy has bound the port,
        # not that it has finished establishing the upstream Cloud SQL
        # connection (which requires ADC auth + TLS). Without this, psql
        # often races the proxy and gets "server closed the connection
        # unexpectedly". pg_isready performs a real Postgres protocol
        # handshake, so a successful return means the proxy is ready to
        # accept application connections.
        $probe = & docker run --rm --network $networkName postgres:16-alpine `
          pg_isready -h cloud-sql-proxy -p 5432 -U $dbUser 2>&1
        if ($LASTEXITCODE -eq 0) {
          $ready = $true
          break
        }
        Start-Sleep -Seconds 2
      }

      if (-not $ready) {
        Write-Host "----- cloud-sql-proxy logs (last 50 lines) -----" -ForegroundColor Yellow
        & docker logs --tail 50 $proxyContainer
        throw "Cloud SQL Proxy did not become ready in time."
      }

      $dbMount = "${RepoRoot}\db:/db"
      foreach ($sqlFile in @("schema.sql", "audit_schema.sql", "seed.sql")) {
        Invoke-Docker @(
          "run", "--rm",
          "--network", $networkName,
          "-v", $dbMount,
          "-e", "PGPASSWORD=$password",
          "postgres:16-alpine",
          "psql", "-h", "cloud-sql-proxy", "-U", $dbUser, "-d", $dbName, "-f", "/db/$sqlFile"
        )
      }

      $env:ADK_SESSION_DB_URI = "postgresql+asyncpg://${dbUser}:${password}@127.0.0.1/$dbName"
      Invoke-Uv @("run", "python", "scripts/seed_user.py")
      Invoke-Uv @("run", "python", "scripts/ingest_faq_embeddings.py")
    } finally {
      try { & docker rm -f $proxyContainer *> $null } catch {}
      try { & docker network rm $networkName *> $null } catch {}
      if (Test-Path $adcTempPath) {
        Remove-Item $adcTempPath -Force
      }
    }
  }
  "gcp-traffic-list" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    Push-Location $tfDir
    try {
      $backendSvc = (& terraform output -raw backend_service_name 2>$null).Trim()
    } finally {
      Pop-Location
    }
    if (-not $backendSvc) { $backendSvc = "insurance-agent-backend" }
    Invoke-Gcloud @("run", "services", "describe", $backendSvc, "--platform", "managed", "--region", (Get-GcpRegion), "--format=yaml(spec.traffic)")
  }
  "gcp-rollback" {
    $tfDir = Get-TfDir -SelectedEnvName $EnvName
    Push-Location $tfDir
    try {
      $backendSvc = (& terraform output -raw backend_service_name 2>$null).Trim()
    } finally {
      Pop-Location
    }
    if (-not $backendSvc) { $backendSvc = "insurance-agent-backend" }
    Invoke-Gcloud @("run", "services", "update-traffic", $backendSvc, "--to-latest", "--platform", "managed", "--region", (Get-GcpRegion))
  }
  "gcp-cleanup-orphans" {
    $project = Get-GcpProjectId
    if (-not $project) {
      throw "GOOGLE_CLOUD_PROJECT is required."
    }
    $pattern = "insurance-agent-db-$EnvName"
    $instances = & gcloud sql instances list --project=$project --format="value(name)"
    $instances | Where-Object { $_ -like "*$pattern*" } | ForEach-Object { Write-Host $_ }
  }
  "gcp-bootstrap" {
    & $PSCommandPath "tf-bootstrap"
    Write-Host "Open Cloud Build connections console if manual authorization is still pending:"
    Write-Host "https://console.cloud.google.com/cloud-build/connections;region=$(Get-GcpRegion)?project=$(Get-GcpProjectId)"
  }
  "gcp-deploy" {
    & $PSCommandPath "build-push"
    & $PSCommandPath "tf-apply" -EnvName $EnvName
  }
  default {
    throw "Unknown command: $Command"
  }
}

param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$envLoader = Join-Path $PSScriptRoot "load-env.ps1"
if (Test-Path $envLoader) {
  & $envLoader
}

$projectId = if ($env:GCP_PROJECT_ID) { $env:GCP_PROJECT_ID } elseif ($env:GOOGLE_CLOUD_PROJECT) { $env:GOOGLE_CLOUD_PROJECT } else { (& gcloud config get-value project 2>$null).Trim() }
$region = if ($env:GCP_REGION) { $env:GCP_REGION } else { "us-central1" }
$connName = "insurance-agent-github-conn"
$tfDir = Join-Path $RepoRoot "deployment/terraform/bootstrap"

if (-not $projectId) {
  throw "GCP project ID is required. Set GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT."
}

if (-not $env:GITHUB_OWNER -or -not $env:GITHUB_REPO_NAME) {
  throw "GITHUB_OWNER and GITHUB_REPO_NAME are required."
}

Write-Host "===================================================================="
Write-Host "Checking GitHub connection bootstrap..."
Write-Host "===================================================================="

$connExists = (& gcloud builds connections list --region=$region --project=$projectId --filter="name:$connName" --format="value(name)" 2>$null).Trim()

if (-not $connExists) {
  Write-Host "Creating GitHub connection: $connName"
  & gcloud builds connections create github $connName --region=$region --project=$projectId
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create GitHub connection."
  }
} else {
  Write-Host "GitHub connection already exists: $connName"
}

$state = (& gcloud builds connections describe $connName --region=$region --project=$projectId --format="value(installationState.stage)" 2>$null).Trim()

if ($state -eq "COMPLETE") {
  Write-Host "GitHub connection installation already complete."
} else {
  $authUrl = (& gcloud builds connections describe $connName --region=$region --project=$projectId --format="value(installationState.actionUri)" 2>$null).Trim()
  Write-Host ""
  Write-Host "Open the following URL to finish GitHub authorization:"
  Write-Host $authUrl -ForegroundColor Blue
  Write-Host ""
  Write-Host "Polling installation state..."

  while ($true) {
    $state = (& gcloud builds connections describe $connName --region=$region --project=$projectId --format="value(installationState.stage)" 2>$null).Trim()
    if ($state -eq "COMPLETE") {
      Write-Host ""
      Write-Host "GitHub connection installation completed."
      break
    }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 5
  }
}

Push-Location $tfDir
try {
  & terraform state show google_cloudbuildv2_connection.github_conn *> $null
  $alreadyImported = $LASTEXITCODE -eq 0

  if (-not $alreadyImported) {
    Write-Host "Importing GitHub connection into Terraform state..."
    & terraform import `
      -var="project_id=$projectId" `
      -var="region=$region" `
      -var="github_owner=$($env:GITHUB_OWNER)" `
      -var="github_repo_name=$($env:GITHUB_REPO_NAME)" `
      google_cloudbuildv2_connection.github_conn `
      "projects/$projectId/locations/$region/connections/$connName"
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to import GitHub connection into Terraform state."
    }
  } else {
    Write-Host "Terraform state already contains GitHub connection."
  }
} finally {
  Pop-Location
}

Write-Host "===================================================================="
Write-Host "GitHub connection bootstrap is ready."
Write-Host "===================================================================="

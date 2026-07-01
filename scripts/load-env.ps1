param(
  [string]$Path = ".env"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Path)) {
  throw "Env file not found: $Path"
}

Get-Content -LiteralPath $Path | ForEach-Object {
  $line = $_.Trim()
  if (-not $line) { return }
  if ($line.StartsWith("#")) { return }

  $parts = $line -split "=", 2
  if ($parts.Count -ne 2) { return }

  $name = $parts[0].Trim()
  $value = $parts[1].Trim()

  if (
    ($value.StartsWith('"') -and $value.EndsWith('"')) -or
    ($value.StartsWith("'") -and $value.EndsWith("'"))
  ) {
    $value = $value.Substring(1, $value.Length - 2)
  }

  [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
}

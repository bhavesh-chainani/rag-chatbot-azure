if ((azd env get-values) -match "USE_CLOUD_INGESTION=""true""") {
  Write-Host "Cloud ingestion is enabled, so we are not running the manual ingestion process."
  Exit 0
}

./scripts/load_python_env.ps1

$venvPythonPath = "./.venv/scripts/python.exe"
if (Test-Path -Path "/usr") {
  # fallback to Linux venv path
  $venvPythonPath = "./.venv/bin/python"
}

Write-Host 'Running "prepdocs.py"'


$cwd = (Get-Location)
$dataArg = "`"$cwd/data/*`""
$additionalArgs = ""
if ($args) {
  $additionalArgs = "$args"
}

$byIdDir = Join-Path $cwd "data/pbsg_golden_set_by_id"
$hasSplitGolden = (Test-Path $byIdDir) -and (Get-ChildItem -Path $byIdDir -Filter "*.json" -File -ErrorAction SilentlyContinue | Select-Object -First 1)
if ($hasSplitGolden) {
  $additionalArgs = "$additionalArgs --exclude pbsg_golden_set_complete_v2.json"
}

$argumentList = "./app/backend/prepdocs.py $dataArg --verbose $additionalArgs"

$argumentList

Start-Process -FilePath $venvPythonPath -ArgumentList $argumentList -Wait -NoNewWindow

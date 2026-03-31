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

# Golden Set: Word files are build inputs only; index per-id JSON, not the .docx.
$additionalArgs = "$additionalArgs --exclude PBSG_Golden_Set_Complete_v2.docx"
$additionalArgs = "$additionalArgs --exclude 2026.03.31 PBSG_Golden_Set_v3 MCA (LPA Only).docx"

$argumentList = "./app/backend/prepdocs.py $dataArg --verbose $additionalArgs"

$argumentList

Start-Process -FilePath $venvPythonPath -ArgumentList $argumentList -Wait -NoNewWindow

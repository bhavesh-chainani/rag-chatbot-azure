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

# Golden Set: Word files are build inputs only; index per-id JSON, not the .docx.
$argumentList = @("./app/backend/prepdocs.py", "./data/*", "--verbose")
if ($args) {
  $argumentList += $args
}
$argumentList += @("--exclude", "PBSG_Golden_Set_Complete_v2.docx")
$argumentList += @("--exclude", "2026_03_31_PBSG_Golden_Set_v3_MCA.docx")
$argumentList += @("--exclude", "2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx")

Start-Process -FilePath $venvPythonPath -ArgumentList $argumentList -Wait -NoNewWindow

 #!/bin/sh

USE_CLOUD_INGESTION=$(azd env get-value USE_CLOUD_INGESTION)
if [ "$USE_CLOUD_INGESTION" = "true" ]; then
  echo "Cloud ingestion is enabled, so we are not running the manual ingestion process."
  exit 0
fi

. ./scripts/load_python_env.sh

echo 'Running "prepdocs.py"'

# Golden Set: Word files are build inputs only — index JSON under
# pbsg_golden_set_by_id/, not the .docx (duplicate/noisy chunks).
set -- "$@" --exclude "PBSG_Golden_Set_Complete_v2.docx"
set -- "$@" --exclude "2026_03_31_PBSG_Golden_Set_v3_MCA.docx"
set -- "$@" --exclude "2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx"

./.venv/bin/python ./app/backend/prepdocs.py './data/*' --verbose "$@"

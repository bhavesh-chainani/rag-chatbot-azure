 #!/bin/sh

USE_CLOUD_INGESTION=$(azd env get-value USE_CLOUD_INGESTION)
if [ "$USE_CLOUD_INGESTION" = "true" ]; then
  echo "Cloud ingestion is enabled, so we are not running the manual ingestion process."
  exit 0
fi

. ./scripts/load_python_env.sh

echo 'Running "prepdocs.py"'

additionalArgs=""
if [ $# -gt 0 ]; then
  additionalArgs="$@"
fi

# One JSON per Golden Set id under data/pbsg_golden_set_by_id/ — skip the monolithic array
# so the same entry is not indexed twice.
for _split in ./data/pbsg_golden_set_by_id/*.json; do
  if [ -e "$_split" ]; then
    additionalArgs="$additionalArgs --exclude pbsg_golden_set_complete_v2.json"
    break
  fi
done

./.venv/bin/python ./app/backend/prepdocs.py './data/*' --verbose $additionalArgs

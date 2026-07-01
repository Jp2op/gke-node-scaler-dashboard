#!/usr/bin/env bash
# setup-scheduler.sh - Create Cloud Scheduler jobs for a schedule
# Usage: ./setup-scheduler.sh <PROJECT_ID> <REGION> <SCHEDULE_ID> <CRON> <TIMEZONE>
#
# Example:
#   ./setup-scheduler.sh my-project asia-south1 abc123 "0 20 * * 1-5" "Asia/Kolkata"
#
# This creates a Cloud Scheduler HTTP job that calls the backend's trigger endpoint.

set -euo pipefail

PROJECT_ID="${1:?Usage: ./setup-scheduler.sh <PROJECT_ID> <REGION> <SCHEDULE_ID> <CRON> <TIMEZONE>}"
REGION="${2:?}"
SCHEDULE_ID="${3:?}"
CRON="${4:?}"
TIMEZONE="${5:-Asia/Kolkata}"

BACKEND_SERVICE="gke-scaler-api"
BACKEND_URL=$(gcloud run services describe "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")

JOB_NAME="gke-scaler-${SCHEDULE_ID}"
TRIGGER_URL="${BACKEND_URL}/api/schedules/${SCHEDULE_ID}/trigger"

BACKEND_SA="gke-scaler-sa@${PROJECT_ID}.iam.gserviceaccount.com"

echo "→ Creating Cloud Scheduler job: ${JOB_NAME}"
echo "  Cron: ${CRON}"
echo "  TZ:   ${TIMEZONE}"
echo "  URL:  ${TRIGGER_URL}"

gcloud scheduler jobs create http "${JOB_NAME}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --schedule="${CRON}" \
  --time-zone="${TIMEZONE}" \
  --uri="${TRIGGER_URL}" \
  --http-method=POST \
  --oidc-service-account-email="${BACKEND_SA}" \
  --oidc-token-audience="${BACKEND_URL}" \
  --attempt-deadline=120s \
  --quiet

echo "  ✓ Created. Test with: gcloud scheduler jobs run ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"

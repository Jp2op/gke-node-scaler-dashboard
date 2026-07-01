#!/usr/bin/env bash
# deploy.sh - Deploy GKE Node Scaler to Cloud Run
# Interactive - asks for project, region, and Firestore database ID

set -euo pipefail

echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║     GKE Node Scaler Deploy      ║"
echo "  ╚══════════════════════════════════╝"
echo ""

read -rp "  GCP Project ID: " PROJECT_ID
if [ -z "$PROJECT_ID" ]; then echo "  ✗ Required." && exit 1; fi

read -rp "  Region [asia-south1]: " REGION
REGION="${REGION:-asia-south1}"

read -rp "  Firestore Database ID [(default)]: " FIRESTORE_DATABASE
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"

echo ""
echo "──────────────────────────────────────────"
echo "  Project:    ${PROJECT_ID}"
echo "  Region:     ${REGION}"
echo "  Firestore:  ${FIRESTORE_DATABASE}"
echo "──────────────────────────────────────────"
read -rp "  Proceed? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then echo "  Aborted." && exit 0; fi

BACKEND_IMAGE="gcr.io/${PROJECT_ID}/gke-scaler-backend"
FRONTEND_IMAGE="gcr.io/${PROJECT_ID}/gke-scaler-frontend"
BACKEND_SERVICE="gke-scaler-api"
FRONTEND_SERVICE="gke-scaler-ui"

echo ""
echo "→ Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  container.googleapis.com \
  cloudscheduler.googleapis.com \
  --project="${PROJECT_ID}" --quiet

BACKEND_SA_NAME="gke-scaler-sa"
BACKEND_SA="${BACKEND_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ""
echo "→ Creating dedicated service account..."
gcloud iam service-accounts create "${BACKEND_SA_NAME}" \
  --display-name="GKE Node Scaler" \
  --project="${PROJECT_ID}" 2>/dev/null || echo "  SA already exists"

echo "  Granting roles..."
for ROLE in roles/container.clusterAdmin roles/datastore.user; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${BACKEND_SA}" \
    --role="${ROLE}" \
    --quiet > /dev/null
  echo "  ✓ ${ROLE}"
done

echo ""
echo "→ Building backend..."
cd backend
gcloud builds submit --tag "${BACKEND_IMAGE}" --project="${PROJECT_ID}" --quiet
echo ""
echo "→ Deploying backend to Cloud Run..."
gcloud run deploy "${BACKEND_SERVICE}" \
  --image "${BACKEND_IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 120 \
  --min-instances 0 \
  --max-instances 5 \
  --allow-unauthenticated \
  --set-env-vars="ALLOWED_ORIGINS=*,FIRESTORE_DATABASE=${FIRESTORE_DATABASE}" \
  --service-account="${BACKEND_SA}" \
  --project="${PROJECT_ID}" \
  --quiet
cd ..

BACKEND_URL=$(gcloud run services describe "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  Backend URL: ${BACKEND_URL}"

echo ""
echo "→ Building frontend..."
cd frontend
gcloud builds submit \
  --tag "${FRONTEND_IMAGE}" \
  --project="${PROJECT_ID}" \
  --substitutions="_VITE_API_URL=${BACKEND_URL}" \
  --quiet
echo ""
echo "→ Deploying frontend to Cloud Run..."
gcloud run deploy "${FRONTEND_SERVICE}" \
  --image "${FRONTEND_IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --port 8080 \
  --memory 256Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  --project="${PROJECT_ID}" \
  --quiet
cd ..

FRONTEND_URL=$(gcloud run services describe "${FRONTEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")

echo ""
echo "→ Updating backend CORS..."
gcloud run services update "${BACKEND_SERVICE}" \
  --region "${REGION}" \
  --project="${PROJECT_ID}" \
  --set-env-vars="ALLOWED_ORIGINS=${FRONTEND_URL},FIRESTORE_DATABASE=${FIRESTORE_DATABASE}" \
  --quiet

echo ""
echo "══════════════════════════════════════════"
echo "  DEPLOYED SUCCESSFULLY"
echo ""
echo "  Frontend:   ${FRONTEND_URL}"
echo "  Backend:    ${BACKEND_URL}"
echo "  SA:         ${BACKEND_SA}"
echo "  Firestore:  ${FIRESTORE_DATABASE}"
echo ""
echo "  For cross-project clusters:"
echo "  gcloud projects add-iam-policy-binding OTHER_PROJECT \\"
echo "    --member='serviceAccount:${BACKEND_SA}' \\"
echo "    --role='roles/container.clusterAdmin'"
echo "══════════════════════════════════════════"

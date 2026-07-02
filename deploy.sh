#!/usr/bin/env bash
# deploy.sh - Deploy GKE Node Scaler to Cloud Run

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

read -rp "  MongoDB SCRAM URI: " MONGO_URI
if [ -z "$MONGO_URI" ]; then echo "  ✗ Required." && exit 1; fi

read -rp "  MongoDB Database Name [gke_scaler]: " MONGO_DB
MONGO_DB="${MONGO_DB:-gke_scaler}"

echo ""
echo "──────────────────────────────────────────"
echo "  Project:   ${PROJECT_ID}"
echo "  Region:    ${REGION}"
echo "  Mongo DB:  ${MONGO_DB}"
echo "──────────────────────────────────────────"
read -rp "  Proceed? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then echo "  Aborted." && exit 0; fi

BACKEND_IMAGE="gcr.io/${PROJECT_ID}/gke-scaler-backend"
FRONTEND_IMAGE="gcr.io/${PROJECT_ID}/gke-scaler-frontend"
BACKEND_SERVICE="gke-scaler-api"
FRONTEND_SERVICE="gke-scaler-ui"

# ─── Enable APIs (idempotent) ────────────────────────────────────────────────

echo ""
echo "→ Enabling required APIs (skips if already enabled)..."
APIS="run.googleapis.com cloudbuild.googleapis.com container.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com"
for API in $APIS; do
  if gcloud services list --project="${PROJECT_ID}" --filter="config.name=${API}" --format="value(config.name)" 2>/dev/null | grep -q "${API}"; then
    echo "  ✓ ${API} (already enabled)"
  else
    gcloud services enable "${API}" --project="${PROJECT_ID}" --quiet
    echo "  ✓ ${API} (enabled)"
  fi
done

# ─── Create Dedicated Service Account ────────────────────────────────────────

BACKEND_SA_NAME="gke-scaler-sa"
BACKEND_SA="${BACKEND_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ""
echo "→ Creating dedicated service account..."
if gcloud iam service-accounts describe "${BACKEND_SA}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "  ✓ ${BACKEND_SA} (already exists)"
else
  gcloud iam service-accounts create "${BACKEND_SA_NAME}" \
    --display-name="GKE Node Scaler" \
    --project="${PROJECT_ID}"
  echo "  ✓ ${BACKEND_SA} (created)"
fi

echo "  Granting roles..."
for ROLE in roles/container.clusterAdmin roles/datastore.user roles/compute.viewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${BACKEND_SA}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet > /dev/null 2>&1
  echo "  ✓ ${ROLE}"
done

# ─── Store MONGO_URI in Secret Manager ───────────────────────────────────────

echo ""
echo "→ Storing MONGO_URI in Secret Manager..."
if gcloud secrets describe gke-scaler-mongo-uri --project="${PROJECT_ID}" &>/dev/null; then
  echo -n "${MONGO_URI}" | gcloud secrets versions add gke-scaler-mongo-uri \
    --data-file=- --project="${PROJECT_ID}" --quiet
  echo "  ✓ Secret updated (new version)"
else
  echo -n "${MONGO_URI}" | gcloud secrets create gke-scaler-mongo-uri \
    --data-file=- --project="${PROJECT_ID}" --quiet
  echo "  ✓ Secret created"
fi

# Grant the SA access to read the secret
gcloud secrets add-iam-policy-binding gke-scaler-mongo-uri \
  --member="serviceAccount:${BACKEND_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="${PROJECT_ID}" \
  --quiet > /dev/null 2>&1
echo "  ✓ SA granted secretAccessor"

# ─── Build & Deploy Backend ──────────────────────────────────────────────────

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
  --set-env-vars="ALLOWED_ORIGINS=*,MONGO_DB=${MONGO_DB}" \
  --set-secrets="MONGO_URI=gke-scaler-mongo-uri:latest" \
  --service-account="${BACKEND_SA}" \
  --project="${PROJECT_ID}" \
  --quiet
cd ..

BACKEND_URL=$(gcloud run services describe "${BACKEND_SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  Backend URL: ${BACKEND_URL}"

# ─── Build & Deploy Frontend ────────────────────────────────────────────────

echo ""
echo "→ Building frontend..."
cd frontend
gcloud builds submit \
  --config=cloudbuild.yaml \
  --project="${PROJECT_ID}" \
  --substitutions="_VITE_API_URL=${BACKEND_URL},_IMAGE=${FRONTEND_IMAGE}" \
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

# ─── Update CORS ────────────────────────────────────────────────────────────

echo ""
echo "→ Updating backend CORS..."
gcloud run services update "${BACKEND_SERVICE}" \
  --region "${REGION}" \
  --project="${PROJECT_ID}" \
  --set-env-vars="ALLOWED_ORIGINS=${FRONTEND_URL},MONGO_DB=${MONGO_DB}" \
  --set-secrets="MONGO_URI=gke-scaler-mongo-uri:latest" \
  --quiet

echo ""
echo "══════════════════════════════════════════"
echo "  DEPLOYED SUCCESSFULLY"
echo ""
echo "  Frontend:   ${FRONTEND_URL}"
echo "  Backend:    ${BACKEND_URL}"
echo "  SA:         ${BACKEND_SA}"
echo "  Mongo DB:   ${MONGO_DB}"
echo ""
echo "  For cross-project clusters:"
echo "  gcloud projects add-iam-policy-binding OTHER_PROJECT \\"
echo "    --member='serviceAccount:${BACKEND_SA}' \\"
echo "    --role='roles/container.clusterAdmin' \\"
echo "    --condition=None"
echo "══════════════════════════════════════════"
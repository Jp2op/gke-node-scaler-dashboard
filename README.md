# GKE Node Scaler

Scale GKE node pools to 0 and restore them later — across multiple projects and clusters. Runs on Cloud Run, stores state in Firestore.

## How it works

- **Scale Down**: Snapshots current node pool sizes → saves to Firestore → scales all pools to 0
- **Scale Up**: Reads snapshot from Firestore → restores original node counts and autoscaling config
- **Idempotent**: Scaling down an already-scaled-down cluster is a no-op. Same for scale-up.
- **Any cluster**: Uses the GCP Container API (not kubectl), so private/public/autopilot all work

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  React Frontend │────→│  FastAPI Backend  │────→│  GKE API     │
│  (Cloud Run)    │     │  (Cloud Run)      │     │  (any proj)  │
└─────────────────┘     └────────┬─────────┘     └──────────────┘
                                 │
                        ┌────────┴─────────┐
                        │    Firestore     │
                        │  - clusters      │
                        │  - snapshots     │
                        │  - schedules     │
                        │  - audit log     │
                        └──────────────────┘
                                 ↑
                        ┌────────┴─────────┐
                        │ Cloud Scheduler  │ (optional, for cron)
                        └──────────────────┘
```

## Prerequisites

1. A GCP project with billing enabled
2. Firestore in Native mode (`gcloud firestore databases create --location=asia-south1`)
3. `gcloud` CLI authenticated

## Deploy

```bash
chmod +x deploy.sh setup-scheduler.sh
./deploy.sh YOUR_PROJECT_ID asia-south1
```

This will:
1. Enable required APIs
2. Build & deploy backend to Cloud Run
3. Build & deploy frontend to Cloud Run
4. Grant the backend SA `container.clusterAdmin` and `datastore.user`
5. Print the frontend URL

## Adding clusters from other GCP projects

When you add a cluster from a different project (e.g. QA on a separate project), grant the backend SA access:

```bash
# Get the backend SA (printed by deploy.sh)
gcloud projects add-iam-policy-binding OTHER_PROJECT_ID \
  --member="serviceAccount:BACKEND_SA@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/container.clusterAdmin"
```

Then register the cluster via the UI — project ID, location, cluster name. Done.

## Scheduling (optional)

### Option A: Cloud Scheduler (recommended)

1. Create a schedule via the UI
2. Note the schedule ID from the response
3. Run:

```bash
./setup-scheduler.sh YOUR_PROJECT_ID asia-south1 SCHEDULE_ID "0 20 * * 1-5" "Asia/Kolkata"
```

This creates a Cloud Scheduler job that calls the backend's trigger endpoint on the cron schedule.

### Example: Dev cluster off at night, on in the morning

```bash
# Scale down dev every weekday at 8 PM IST
./setup-scheduler.sh my-project asia-south1 sched-dev-down "0 20 * * 1-5" "Asia/Kolkata"

# Scale up dev every weekday at 8 AM IST
./setup-scheduler.sh my-project asia-south1 sched-dev-up "0 8 * * 1-5" "Asia/Kolkata"
```

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/clusters` | List all registered clusters |
| POST | `/api/clusters` | Register a new cluster |
| DELETE | `/api/clusters/{id}` | Remove cluster from dashboard |
| GET | `/api/clusters/{id}/nodepools` | Live node pool status |
| POST | `/api/clusters/{id}/scale-down` | Snapshot + scale to 0 |
| POST | `/api/clusters/{id}/scale-up` | Restore from snapshot |
| POST | `/api/clusters/{id}/nodepools/{pool}/scale` | Scale individual pool |
| GET | `/api/snapshots/{id}` | View saved snapshot |
| GET/POST/PATCH/DELETE | `/api/schedules` | CRUD schedules |
| POST | `/api/schedules/{id}/trigger` | Trigger schedule manually |
| GET | `/api/audit` | View audit log |

## Local Development

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8080

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to `localhost:8080`.

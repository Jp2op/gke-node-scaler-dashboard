"""
GKE Node Scaler - Cloud Run Backend
Manages node pool scaling across multiple GKE clusters and GCP projects.
All state persisted in Firestore. Stateless Cloud Run compatible.
"""

import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google.cloud import container_v1
from google.cloud import firestore
from google.api_core import exceptions as gcp_exceptions
from google.protobuf import field_mask_pb2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="GKE Node Scaler", version="1.0.0")

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FIRESTORE_COLLECTION_CLUSTERS = "gke_scaler_clusters"
FIRESTORE_COLLECTION_SNAPSHOTS = "gke_scaler_snapshots"
FIRESTORE_COLLECTION_SCHEDULES = "gke_scaler_schedules"
FIRESTORE_COLLECTION_AUDIT = "gke_scaler_audit"


FIRESTORE_PROJECT = os.environ.get("FIRESTORE_PROJECT")  # None = same as Cloud Run project
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "(default)")


def get_db() -> firestore.Client:
    kwargs = {"database": FIRESTORE_DATABASE}
    if FIRESTORE_PROJECT:
        kwargs["project"] = FIRESTORE_PROJECT
    return firestore.Client(**kwargs)


def get_gke_client() -> container_v1.ClusterManagerClient:
    return container_v1.ClusterManagerClient()


# ─── Models ───────────────────────────────────────────────────────────────────


class ClusterRegister(BaseModel):
    project_id: str
    location: str  # zone or region
    cluster_name: str
    display_name: str = ""
    environment: str = "dev"  # dev, qa, prod, staging, etc.


class ClusterUpdate(BaseModel):
    display_name: Optional[str] = None
    environment: Optional[str] = None


class ScheduleCreate(BaseModel):
    cluster_id: str
    action: str = Field(..., pattern="^(scale_down|scale_up)$")
    cron: str  # cron expression
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    description: str = ""


class ScheduleUpdate(BaseModel):
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    description: Optional[str] = None


class ScaleRequest(BaseModel):
    """Optional: scale specific pools only."""
    node_pools: Optional[list[str]] = None  # None = all pools
    triggered_by: str = "manual"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _cluster_parent(project_id: str, location: str, cluster_name: str) -> str:
    return f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"


def _write_audit(db: firestore.Client, entry: dict):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entry["id"] = str(uuid.uuid4())
    db.collection(FIRESTORE_COLLECTION_AUDIT).document(entry["id"]).set(entry)


def _fetch_node_pools(
    gke: container_v1.ClusterManagerClient,
    project_id: str,
    location: str,
    cluster_name: str,
) -> list[dict]:
    """Fetch live node pool info from GKE API."""
    parent = _cluster_parent(project_id, location, cluster_name)
    try:
        cluster = gke.get_cluster(name=parent)
    except gcp_exceptions.NotFound:
        raise HTTPException(404, f"Cluster not found: {parent}")
    except gcp_exceptions.PermissionDenied:
        raise HTTPException(
            403,
            f"Permission denied on {parent}. Ensure the Cloud Run SA has "
            f"roles/container.clusterAdmin on project {project_id}.",
        )

    pools = []
    for np in cluster.node_pools:
        pool_info = {
            "name": np.name,
            "status": np.status.name if np.status else "UNKNOWN",
            "initial_node_count": np.initial_node_count,
            "autoscaling_enabled": (
                np.autoscaling.enabled if np.autoscaling else False
            ),
            "min_node_count": (
                np.autoscaling.min_node_count if np.autoscaling else 0
            ),
            "max_node_count": (
                np.autoscaling.max_node_count if np.autoscaling else 0
            ),
            "current_node_count": np.initial_node_count,  # best effort
            "machine_type": (
                np.config.machine_type if np.config else "unknown"
            ),
            "locations": list(np.locations) if np.locations else [],
        }
        # For regional clusters, node count is per-zone
        if np.instance_group_urls:
            pool_info["instance_groups"] = len(np.instance_group_urls)
        pools.append(pool_info)

    return pools


# ─── Cluster CRUD ─────────────────────────────────────────────────────────────


@app.get("/api/clusters")
def list_clusters():
    db = get_db()
    docs = db.collection(FIRESTORE_COLLECTION_CLUSTERS).stream()
    clusters = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        clusters.append(data)
    return {"clusters": clusters}


@app.post("/api/clusters", status_code=201)
def register_cluster(req: ClusterRegister):
    db = get_db()

    # Check for duplicates
    existing = (
        db.collection(FIRESTORE_COLLECTION_CLUSTERS)
        .where("project_id", "==", req.project_id)
        .where("cluster_name", "==", req.cluster_name)
        .where("location", "==", req.location)
        .limit(1)
        .get()
    )
    if list(existing):
        raise HTTPException(409, "Cluster already registered")

    # Verify connectivity
    gke = get_gke_client()
    try:
        parent = _cluster_parent(req.project_id, req.location, req.cluster_name)
        gke.get_cluster(name=parent)
    except gcp_exceptions.NotFound:
        raise HTTPException(404, f"Cluster not found in GCP: {parent}")
    except gcp_exceptions.PermissionDenied:
        raise HTTPException(
            403,
            f"Cannot access cluster. Grant container.clusterAdmin to Cloud Run SA "
            f"on project {req.project_id}.",
        )

    cluster_id = str(uuid.uuid4())[:8]
    doc = {
        "project_id": req.project_id,
        "location": req.location,
        "cluster_name": req.cluster_name,
        "display_name": req.display_name or req.cluster_name,
        "environment": req.environment,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }
    db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).set(doc)

    _write_audit(db, {
        "action": "cluster_registered",
        "cluster_id": cluster_id,
        "details": doc,
    })

    return {"id": cluster_id, **doc}


@app.get("/api/clusters/{cluster_id}")
def get_cluster(cluster_id: str):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")
    data = doc.to_dict()
    data["id"] = doc.id
    return data


@app.patch("/api/clusters/{cluster_id}")
def update_cluster(cluster_id: str, req: ClusterUpdate):
    db = get_db()
    ref = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    ref.update(updates)
    return {"updated": updates}


@app.delete("/api/clusters/{cluster_id}")
def delete_cluster(cluster_id: str):
    db = get_db()
    ref = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    ref.delete()
    _write_audit(db, {
        "action": "cluster_deleted",
        "cluster_id": cluster_id,
    })
    return {"deleted": cluster_id}


# ─── Node Pool Info ───────────────────────────────────────────────────────────


@app.get("/api/clusters/{cluster_id}/nodepools")
def get_node_pools(cluster_id: str):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    cluster = doc.to_dict()
    gke = get_gke_client()
    pools = _fetch_node_pools(
        gke, cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    # Check if there's a saved snapshot
    snap_doc = (
        db.collection(FIRESTORE_COLLECTION_SNAPSHOTS).document(cluster_id).get()
    )
    snapshot = snap_doc.to_dict() if snap_doc.exists else None

    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster["cluster_name"],
        "project_id": cluster["project_id"],
        "node_pools": pools,
        "has_snapshot": snapshot is not None,
        "snapshot": snapshot,
    }


# ─── Scale Down ───────────────────────────────────────────────────────────────


@app.post("/api/clusters/{cluster_id}/scale-down")
def scale_down(cluster_id: str, req: ScaleRequest = ScaleRequest()):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    cluster = doc.to_dict()
    gke = get_gke_client()
    parent = _cluster_parent(
        cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    # Fetch current state
    pools = _fetch_node_pools(
        gke, cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    # Filter pools if specific ones requested
    target_pools = pools
    if req.node_pools:
        target_pools = [p for p in pools if p["name"] in req.node_pools]
        if not target_pools:
            raise HTTPException(400, "No matching node pools found")

    # Check idempotency: if all target pools already at 0
    all_zero = all(
        p["current_node_count"] == 0
        and (not p["autoscaling_enabled"] or p["max_node_count"] == 0)
        for p in target_pools
    )
    if all_zero:
        return {
            "status": "already_scaled_down",
            "message": "All target node pools already at 0. No action taken.",
            "cluster_id": cluster_id,
        }

    # Save snapshot BEFORE scaling down
    snapshot = {
        "cluster_id": cluster_id,
        "node_pools": {
            p["name"]: {
                "initial_node_count": p["current_node_count"],
                "autoscaling_enabled": p["autoscaling_enabled"],
                "min_node_count": p["min_node_count"],
                "max_node_count": p["max_node_count"],
            }
            for p in pools  # save ALL pools, not just targets
        },
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "saved_by": req.triggered_by,
        "status": "active",
    }
    db.collection(FIRESTORE_COLLECTION_SNAPSHOTS).document(cluster_id).set(snapshot)

    # Scale down each target pool
    operations = []
    errors = []
    for pool in target_pools:
        pool_name = f"{parent}/nodePools/{pool['name']}"
        try:
            # First disable autoscaling / set to 0
            if pool["autoscaling_enabled"]:
                gke.set_node_pool_autoscaling(
                    request={
                        "name": pool_name,
                        "autoscaling": {
                            "enabled": True,
                            "min_node_count": 0,
                            "max_node_count": 1,
                        },
                    }
                )

            # Then resize to 0
            gke.set_node_pool_size(
                request={
                    "name": pool_name,
                    "node_count": 0,
                }
            )
            operations.append({"pool": pool["name"], "status": "scaling_down"})
        except Exception as e:
            logger.error(f"Failed to scale down {pool['name']}: {e}")
            errors.append({"pool": pool["name"], "error": str(e)})

    _write_audit(db, {
        "action": "scale_down",
        "cluster_id": cluster_id,
        "triggered_by": req.triggered_by,
        "pools_targeted": [p["name"] for p in target_pools],
        "operations": operations,
        "errors": errors,
    })

    return {
        "status": "scaling_down",
        "cluster_id": cluster_id,
        "snapshot_saved": True,
        "operations": operations,
        "errors": errors,
    }


# ─── Scale Up (Restore) ──────────────────────────────────────────────────────


@app.post("/api/clusters/{cluster_id}/scale-up")
def scale_up(cluster_id: str, req: ScaleRequest = ScaleRequest()):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    cluster = doc.to_dict()

    # Read snapshot
    snap_doc = (
        db.collection(FIRESTORE_COLLECTION_SNAPSHOTS).document(cluster_id).get()
    )
    if not snap_doc.exists:
        raise HTTPException(
            409,
            "No snapshot found. Cannot restore — original node counts unknown. "
            "Use manual scaling instead.",
        )

    snapshot = snap_doc.to_dict()
    saved_pools = snapshot.get("node_pools", {})

    gke = get_gke_client()
    parent = _cluster_parent(
        cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    # Filter if specific pools requested
    target_pool_names = (
        req.node_pools if req.node_pools else list(saved_pools.keys())
    )

    operations = []
    errors = []
    for pool_name in target_pool_names:
        if pool_name not in saved_pools:
            errors.append({
                "pool": pool_name,
                "error": "No snapshot data for this pool",
            })
            continue

        saved = saved_pools[pool_name]
        full_pool_name = f"{parent}/nodePools/{pool_name}"

        try:
            # Restore autoscaling config first
            if saved["autoscaling_enabled"]:
                gke.set_node_pool_autoscaling(
                    request={
                        "name": full_pool_name,
                        "autoscaling": {
                            "enabled": True,
                            "min_node_count": saved["min_node_count"],
                            "max_node_count": saved["max_node_count"],
                        },
                    }
                )

            # Restore node count
            gke.set_node_pool_size(
                request={
                    "name": full_pool_name,
                    "node_count": saved["initial_node_count"],
                }
            )
            operations.append({
                "pool": pool_name,
                "status": "scaling_up",
                "target_count": saved["initial_node_count"],
            })
        except Exception as e:
            logger.error(f"Failed to scale up {pool_name}: {e}")
            errors.append({"pool": pool_name, "error": str(e)})

    # Mark snapshot as restored
    db.collection(FIRESTORE_COLLECTION_SNAPSHOTS).document(cluster_id).update({
        "status": "restored",
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "restored_by": req.triggered_by,
    })

    _write_audit(db, {
        "action": "scale_up",
        "cluster_id": cluster_id,
        "triggered_by": req.triggered_by,
        "pools_restored": [op["pool"] for op in operations],
        "operations": operations,
        "errors": errors,
    })

    return {
        "status": "scaling_up",
        "cluster_id": cluster_id,
        "snapshot_used": snapshot.get("saved_at"),
        "operations": operations,
        "errors": errors,
    }


# ─── Manual Pool Scaling ─────────────────────────────────────────────────────


class ManualScaleRequest(BaseModel):
    node_count: int = Field(..., ge=0)
    update_autoscaling: bool = False
    min_node_count: Optional[int] = None
    max_node_count: Optional[int] = None


@app.post("/api/clusters/{cluster_id}/nodepools/{pool_name}/scale")
def scale_pool(cluster_id: str, pool_name: str, req: ManualScaleRequest):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "Cluster not found")

    cluster = doc.to_dict()
    gke = get_gke_client()
    parent = _cluster_parent(
        cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )
    full_name = f"{parent}/nodePools/{pool_name}"

    try:
        if req.update_autoscaling and req.max_node_count is not None:
            gke.set_node_pool_autoscaling(
                request={
                    "name": full_name,
                    "autoscaling": {
                        "enabled": True,
                        "min_node_count": req.min_node_count or 0,
                        "max_node_count": req.max_node_count,
                    },
                }
            )

        gke.set_node_pool_size(
            request={"name": full_name, "node_count": req.node_count}
        )
    except Exception as e:
        raise HTTPException(500, f"Scale failed: {e}")

    _write_audit(db, {
        "action": "manual_scale",
        "cluster_id": cluster_id,
        "pool": pool_name,
        "node_count": req.node_count,
    })

    return {"status": "ok", "pool": pool_name, "target_count": req.node_count}


# ─── Snapshots ────────────────────────────────────────────────────────────────


@app.get("/api/snapshots/{cluster_id}")
def get_snapshot(cluster_id: str):
    db = get_db()
    doc = db.collection(FIRESTORE_COLLECTION_SNAPSHOTS).document(cluster_id).get()
    if not doc.exists:
        raise HTTPException(404, "No snapshot for this cluster")
    return doc.to_dict()


# ─── Schedules CRUD ──────────────────────────────────────────────────────────


@app.get("/api/schedules")
def list_schedules(cluster_id: Optional[str] = Query(None)):
    db = get_db()
    query = db.collection(FIRESTORE_COLLECTION_SCHEDULES)
    if cluster_id:
        query = query.where("cluster_id", "==", cluster_id)
    docs = query.stream()
    schedules = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        schedules.append(data)
    return {"schedules": schedules}


@app.post("/api/schedules", status_code=201)
def create_schedule(req: ScheduleCreate):
    db = get_db()

    # Verify cluster exists
    cluster_doc = (
        db.collection(FIRESTORE_COLLECTION_CLUSTERS).document(req.cluster_id).get()
    )
    if not cluster_doc.exists:
        raise HTTPException(404, "Cluster not found")

    schedule_id = str(uuid.uuid4())[:8]
    doc = {
        "cluster_id": req.cluster_id,
        "action": req.action,
        "cron": req.cron,
        "timezone": req.timezone,
        "enabled": req.enabled,
        "description": req.description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
        "last_status": None,
    }
    db.collection(FIRESTORE_COLLECTION_SCHEDULES).document(schedule_id).set(doc)

    return {"id": schedule_id, **doc}


@app.patch("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdate):
    db = get_db()
    ref = db.collection(FIRESTORE_COLLECTION_SCHEDULES).document(schedule_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(404, "Schedule not found")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    ref.update(updates)
    return {"updated": updates}


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    db = get_db()
    ref = db.collection(FIRESTORE_COLLECTION_SCHEDULES).document(schedule_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(404, "Schedule not found")

    ref.delete()
    return {"deleted": schedule_id}


@app.post("/api/schedules/{schedule_id}/trigger")
def trigger_schedule(schedule_id: str):
    """Manually trigger a schedule. Also used by Cloud Scheduler."""
    db = get_db()
    ref = db.collection(FIRESTORE_COLLECTION_SCHEDULES).document(schedule_id)
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(404, "Schedule not found")

    schedule = doc.to_dict()

    try:
        if schedule["action"] == "scale_down":
            result = scale_down(
                schedule["cluster_id"],
                ScaleRequest(triggered_by=f"schedule:{schedule_id}"),
            )
        elif schedule["action"] == "scale_up":
            result = scale_up(
                schedule["cluster_id"],
                ScaleRequest(triggered_by=f"schedule:{schedule_id}"),
            )
        else:
            raise HTTPException(400, f"Unknown action: {schedule['action']}")

        ref.update({
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_status": "success",
        })
        return result

    except Exception as e:
        ref.update({
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_status": f"error: {str(e)[:200]}",
        })
        raise


# ─── Audit Log ────────────────────────────────────────────────────────────────


@app.get("/api/audit")
def get_audit_log(
    cluster_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    db = get_db()
    query = db.collection(FIRESTORE_COLLECTION_AUDIT).order_by(
        "timestamp", direction=firestore.Query.DESCENDING
    )
    if cluster_id:
        query = query.where("cluster_id", "==", cluster_id)
    query = query.limit(limit)

    docs = query.stream()
    entries = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        entries.append(data)
    return {"entries": entries}


# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "gke-node-scaler"}

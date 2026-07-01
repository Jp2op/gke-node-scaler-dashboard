"""
GKE Node Scaler - Cloud Run Backend
Uses Firestore MongoDB-compatible mode via pymongo.
All state persisted in MongoDB. Stateless Cloud Run compatible.
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
from google.api_core import exceptions as gcp_exceptions
from pymongo import MongoClient

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

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB = os.environ.get("MONGO_DB", "gke_scaler")

_mongo_client = None


def get_db():
    global _mongo_client
    if _mongo_client is None:
        if not MONGO_URI:
            raise HTTPException(500, "MONGO_URI environment variable not set")
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB]


def get_gke_client() -> container_v1.ClusterManagerClient:
    return container_v1.ClusterManagerClient()


# ─── Models ───────────────────────────────────────────────────────────────────


class ClusterRegister(BaseModel):
    project_id: str
    location: str
    cluster_name: str
    display_name: str = ""
    environment: str = "dev"


class ClusterUpdate(BaseModel):
    display_name: Optional[str] = None
    environment: Optional[str] = None


class ScheduleCreate(BaseModel):
    cluster_id: str
    action: str = Field(..., pattern="^(scale_down|scale_up)$")
    cron: str
    timezone: str = "Asia/Kolkata"
    enabled: bool = True
    description: str = ""


class ScheduleUpdate(BaseModel):
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    description: Optional[str] = None


class ScaleRequest(BaseModel):
    node_pools: Optional[list[str]] = None
    triggered_by: str = "manual"


class ManualScaleRequest(BaseModel):
    node_count: int = Field(..., ge=0)
    update_autoscaling: bool = False
    min_node_count: Optional[int] = None
    max_node_count: Optional[int] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _cluster_parent(project_id: str, location: str, cluster_name: str) -> str:
    return f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"


def _write_audit(db, entry: dict):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    entry["_id"] = str(uuid.uuid4())
    db.audit.insert_one(entry)


def _serialize(doc):
    """Convert MongoDB doc to JSON-safe dict."""
    if doc is None:
        return None
    doc = dict(doc)
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def _fetch_node_pools(
    gke: container_v1.ClusterManagerClient,
    project_id: str,
    location: str,
    cluster_name: str,
) -> list[dict]:
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
        # instance_group_urls tells us real node count:
        # each URL is one zone's instance group, and for a resized pool
        # the count of managed instances = total nodes.
        # When pool is scaled to 0, instance_group_urls is empty.
        ig_count = len(np.instance_group_urls) if np.instance_group_urls else 0

        # Best-effort live node count:
        # - If status is RUNNING and no instance groups → 0 nodes
        # - If status is RECONCILING → scaling in progress
        # - initial_node_count is stale (creation-time value), don't trust it
        # Use the node pool's status_message and instance groups to infer
        if np.status and np.status.name in ("STOPPING", "ERROR"):
            current_count = 0
        elif ig_count == 0:
            current_count = 0
        else:
            # For regional clusters: nodes = initial_node_count * num_zones
            # For zonal clusters: nodes = initial_node_count
            # But after resize, initial_node_count reflects the resize target
            current_count = np.initial_node_count * max(ig_count, 1)

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
            "current_node_count": current_count,
            "machine_type": (
                np.config.machine_type if np.config else "unknown"
            ),
            "locations": list(np.locations) if np.locations else [],
            "instance_groups": ig_count,
        }
        pools.append(pool_info)

    return pools


# ─── Cluster CRUD ─────────────────────────────────────────────────────────────


@app.get("/api/clusters")
def list_clusters():
    db = get_db()
    docs = db.clusters.find()
    clusters = [_serialize(doc) for doc in docs]
    return {"clusters": clusters}


@app.post("/api/clusters", status_code=201)
def register_cluster(req: ClusterRegister):
    db = get_db()

    existing = db.clusters.find_one({
        "project_id": req.project_id,
        "cluster_name": req.cluster_name,
        "location": req.location,
    })
    if existing:
        raise HTTPException(409, "Cluster already registered")

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
        "_id": cluster_id,
        "project_id": req.project_id,
        "location": req.location,
        "cluster_name": req.cluster_name,
        "display_name": req.display_name or req.cluster_name,
        "environment": req.environment,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }
    db.clusters.insert_one(doc)

    _write_audit(db, {
        "action": "cluster_registered",
        "cluster_id": cluster_id,
        "details": {k: v for k, v in doc.items() if k != "_id"},
    })

    result = dict(doc)
    result["id"] = result.pop("_id")
    return result


@app.get("/api/clusters/{cluster_id}")
def get_cluster(cluster_id: str):
    db = get_db()
    doc = db.clusters.find_one({"_id": cluster_id})
    if not doc:
        raise HTTPException(404, "Cluster not found")
    return _serialize(doc)


@app.patch("/api/clusters/{cluster_id}")
def update_cluster(cluster_id: str, req: ClusterUpdate):
    db = get_db()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    result = db.clusters.update_one({"_id": cluster_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Cluster not found")
    return {"updated": updates}


@app.delete("/api/clusters/{cluster_id}")
def delete_cluster(cluster_id: str):
    db = get_db()
    result = db.clusters.delete_one({"_id": cluster_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Cluster not found")

    _write_audit(db, {"action": "cluster_deleted", "cluster_id": cluster_id})
    return {"deleted": cluster_id}


# ─── Node Pool Info ───────────────────────────────────────────────────────────


@app.get("/api/clusters/{cluster_id}/nodepools")
def get_node_pools(cluster_id: str):
    db = get_db()
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    gke = get_gke_client()
    pools = _fetch_node_pools(
        gke, cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    snapshot = db.snapshots.find_one({"_id": cluster_id})

    return {
        "cluster_id": cluster_id,
        "cluster_name": cluster["cluster_name"],
        "project_id": cluster["project_id"],
        "node_pools": pools,
        "has_snapshot": snapshot is not None,
        "snapshot": _serialize(snapshot),
    }


# ─── Scale Down ───────────────────────────────────────────────────────────────


@app.post("/api/clusters/{cluster_id}/scale-down")
def scale_down(cluster_id: str, req: ScaleRequest = ScaleRequest()):
    db = get_db()
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    gke = get_gke_client()
    parent = _cluster_parent(
        cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    pools = _fetch_node_pools(
        gke, cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    target_pools = pools
    if req.node_pools:
        target_pools = [p for p in pools if p["name"] in req.node_pools]
        if not target_pools:
            raise HTTPException(400, "No matching node pools found")

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
        "_id": cluster_id,
        "cluster_id": cluster_id,
        "node_pools": {
            p["name"]: {
                "initial_node_count": p["current_node_count"],
                "autoscaling_enabled": p["autoscaling_enabled"],
                "min_node_count": p["min_node_count"],
                "max_node_count": p["max_node_count"],
            }
            for p in pools
        },
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "saved_by": req.triggered_by,
        "status": "active",
    }
    db.snapshots.replace_one({"_id": cluster_id}, snapshot, upsert=True)

    operations = []
    errors = []
    for pool in target_pools:
        pool_name = f"{parent}/nodePools/{pool['name']}"
        try:
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
            gke.set_node_pool_size(
                request={"name": pool_name, "node_count": 0}
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
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    snapshot = db.snapshots.find_one({"_id": cluster_id})
    if not snapshot:
        raise HTTPException(
            409,
            "No snapshot found. Cannot restore — original node counts unknown. "
            "Use manual scaling instead.",
        )

    saved_pools = snapshot.get("node_pools", {})

    gke = get_gke_client()
    parent = _cluster_parent(
        cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )

    target_pool_names = (
        req.node_pools if req.node_pools else list(saved_pools.keys())
    )

    operations = []
    errors = []
    for pool_name in target_pool_names:
        if pool_name not in saved_pools:
            errors.append({"pool": pool_name, "error": "No snapshot data for this pool"})
            continue

        saved = saved_pools[pool_name]
        full_pool_name = f"{parent}/nodePools/{pool_name}"

        try:
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

    # Only mark snapshot as restored if ALL operations succeeded
    if errors:
        # Partial failure — keep snapshot as active for retry
        _write_audit(db, {
            "action": "scale_up_partial_failure",
            "cluster_id": cluster_id,
            "triggered_by": req.triggered_by,
            "pools_restored": [op["pool"] for op in operations],
            "operations": operations,
            "errors": errors,
        })
        return {
            "status": "partial_failure",
            "cluster_id": cluster_id,
            "message": "Some pools failed to scale up. Snapshot preserved for retry.",
            "snapshot_used": snapshot.get("saved_at"),
            "operations": operations,
            "errors": errors,
        }

    db.snapshots.update_one(
        {"_id": cluster_id},
        {"$set": {
            "status": "restored",
            "restored_at": datetime.now(timezone.utc).isoformat(),
            "restored_by": req.triggered_by,
        }},
    )

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


@app.post("/api/clusters/{cluster_id}/nodepools/{pool_name}/scale")
def scale_pool(cluster_id: str, pool_name: str, req: ManualScaleRequest):
    db = get_db()
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

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
    doc = db.snapshots.find_one({"_id": cluster_id})
    if not doc:
        raise HTTPException(404, "No snapshot for this cluster")
    return _serialize(doc)


# ─── Schedules CRUD ──────────────────────────────────────────────────────────


@app.get("/api/schedules")
def list_schedules(cluster_id: Optional[str] = Query(None)):
    db = get_db()
    query = {"cluster_id": cluster_id} if cluster_id else {}
    docs = db.schedules.find(query)
    schedules = [_serialize(doc) for doc in docs]
    return {"schedules": schedules}


@app.post("/api/schedules", status_code=201)
def create_schedule(req: ScheduleCreate):
    db = get_db()

    cluster = db.clusters.find_one({"_id": req.cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    schedule_id = str(uuid.uuid4())[:8]
    doc = {
        "_id": schedule_id,
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
    db.schedules.insert_one(doc)

    result = dict(doc)
    result["id"] = result.pop("_id")
    return result


@app.patch("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdate):
    db = get_db()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No fields to update")

    result = db.schedules.update_one({"_id": schedule_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(404, "Schedule not found")
    return {"updated": updates}


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    db = get_db()
    result = db.schedules.delete_one({"_id": schedule_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Schedule not found")
    return {"deleted": schedule_id}


@app.post("/api/schedules/{schedule_id}/trigger")
def trigger_schedule(schedule_id: str):
    db = get_db()
    schedule = db.schedules.find_one({"_id": schedule_id})
    if not schedule:
        raise HTTPException(404, "Schedule not found")

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

        db.schedules.update_one(
            {"_id": schedule_id},
            {"$set": {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "last_status": "success",
            }},
        )
        return result

    except Exception as e:
        db.schedules.update_one(
            {"_id": schedule_id},
            {"$set": {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "last_status": f"error: {str(e)[:200]}",
            }},
        )
        raise


# ─── Audit Log ────────────────────────────────────────────────────────────────


@app.get("/api/audit")
def get_audit_log(
    cluster_id: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
):
    db = get_db()
    query = {"cluster_id": cluster_id} if cluster_id else {}
    docs = db.audit.find(query).sort("timestamp", -1).limit(limit)
    entries = [_serialize(doc) for doc in docs]
    return {"entries": entries}


# ─── Health ───────────────────────────────────────────────────────────────────


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "gke-node-scaler"}
"""
GKE Node Scaler - Cloud Run Backend
Uses Firestore MongoDB-compatible mode via pymongo.
All state persisted in MongoDB. Stateless Cloud Run compatible.
"""

import os
import logging
import uuid
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google.cloud import container_v1
from google.cloud import compute_v1
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
    force: bool = False  # Force 0→target even on partially-running pools


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


# GPU machine type prefixes (A100, H100, L4, etc.)
GPU_MACHINE_PREFIXES = ("a2-", "a3-", "g2-", "a3e-", "a3u-")


def _is_gpu_pool(np) -> bool:
    """Detect if a node pool has GPUs via machine type or accelerator config."""
    config = np.config
    if not config:
        return False
    # Check machine type prefix
    mt = (config.machine_type or "").lower()
    if any(mt.startswith(p) for p in GPU_MACHINE_PREFIXES):
        return True
    # Check attached accelerators (e.g. n1 + nvidia-tesla-t4)
    if config.accelerators and len(config.accelerators) > 0:
        return True
    return False


def _get_mig_running_count(instance_group_urls: list[str]) -> int:
    """Get actual running instance count from managed instance groups."""
    if not instance_group_urls:
        return 0
    try:
        client = compute_v1.InstanceGroupManagersClient()
        total = 0
        for url in instance_group_urls:
            # URL: https://www.googleapis.com/compute/v1/projects/P/zones/Z/instanceGroupManagers/NAME
            parts = url.split("/")
            project = parts[parts.index("projects") + 1]
            zone = parts[parts.index("zones") + 1]
            name = parts[-1]

            igm = client.get(
                project=project, zone=zone, instance_group_manager=name
            )
            # current_actions.none = instances that are running and healthy
            if igm.current_actions:
                total += igm.current_actions.none
            else:
                total += igm.target_size
        return total
    except Exception as e:
        logger.warning(f"Could not fetch MIG counts: {e}")
        return -1  # -1 = unknown, caller should fall back


def _wait_for_operation(gke, project_id, location, operation_name, timeout=120):
    """Wait for a GKE operation to complete."""
    op_name = f"projects/{project_id}/locations/{location}/operations/{operation_name}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = gke.get_operation(name=op_name)
        if op.status == container_v1.Operation.Status.DONE:
            if op.status_message:
                logger.warning(f"Operation completed with message: {op.status_message}")
            return op
        time.sleep(3)
    logger.warning(f"Operation {operation_name} timed out after {timeout}s")
    return None


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
        ig_urls = list(np.instance_group_urls) if np.instance_group_urls else []

        # Get ACTUAL running node count from Compute Engine MIGs
        real_count = _get_mig_running_count(ig_urls)
        if real_count < 0:
            # Fallback: use initial_node_count * zones (target, not actual)
            real_count = np.initial_node_count * max(len(ig_urls), 1)

        # Capture full config for pool recreation if deleted
        config = np.config
        pool_config = {}
        if config:
            pool_config = {
                "machine_type": config.machine_type or "e2-medium",
                "disk_size_gb": config.disk_size_gb or 100,
                "disk_type": config.disk_type or "pd-standard",
                "image_type": config.image_type or "COS_CONTAINERD",
                "spot": config.spot if hasattr(config, "spot") else False,
                "oauth_scopes": list(config.oauth_scopes) if config.oauth_scopes else [
                    "https://www.googleapis.com/auth/cloud-platform"
                ],
            }

        # Detect GPU
        is_gpu = _is_gpu_pool(np)
        gpu_type = None
        gpu_count = 0
        if is_gpu and config and config.accelerators:
            acc = config.accelerators[0]
            gpu_type = acc.accelerator_type
            gpu_count = acc.accelerator_count

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
            "current_node_count": real_count,
            "machine_type": (
                config.machine_type if config else "unknown"
            ),
            "locations": list(np.locations) if np.locations else [],
            "instance_groups": len(ig_urls),
            "pool_config": pool_config,
            "is_gpu": is_gpu,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
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

    # Fix #4: Cascade delete schedules and snapshot for this cluster
    deleted_schedules = db.schedules.delete_many({"cluster_id": cluster_id})
    db.snapshots.delete_one({"_id": cluster_id})

    _write_audit(db, {
        "action": "cluster_deleted",
        "cluster_id": cluster_id,
        "schedules_deleted": deleted_schedules.deleted_count,
    })
    return {"deleted": cluster_id, "schedules_deleted": deleted_schedules.deleted_count}


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
        "excluded_pools": cluster.get("excluded_pools", []),
    }


# ─── Pool Exclusion Toggle ───────────────────────────────────────────────────


class PoolExclusionUpdate(BaseModel):
    pool_name: str
    excluded: bool


@app.post("/api/clusters/{cluster_id}/exclusions")
def toggle_pool_exclusion(cluster_id: str, req: PoolExclusionUpdate):
    db = get_db()
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    excluded = cluster.get("excluded_pools", [])

    if req.excluded and req.pool_name not in excluded:
        excluded.append(req.pool_name)
    elif not req.excluded and req.pool_name in excluded:
        excluded.remove(req.pool_name)

    db.clusters.update_one(
        {"_id": cluster_id},
        {"$set": {"excluded_pools": excluded}},
    )

    _write_audit(db, {
        "action": "pool_exclusion_toggled",
        "cluster_id": cluster_id,
        "pool_name": req.pool_name,
        "excluded": req.excluded,
    })

    return {"excluded_pools": excluded}


# ─── Scale Down ───────────────────────────────────────────────────────────────


@app.post("/api/clusters/{cluster_id}/scale-down")
def scale_down(cluster_id: str, req: ScaleRequest = ScaleRequest()):
    db = get_db()
    cluster = db.clusters.find_one({"_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    excluded_pools = cluster.get("excluded_pools", [])

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

    # Separate excluded pools
    pools_to_scale = [p for p in target_pools if p["name"] not in excluded_pools]
    pools_excluded = [p for p in target_pools if p["name"] in excluded_pools]

    # Fix #5: Early return if all pools are excluded
    if not pools_to_scale:
        return {
            "status": "all_excluded",
            "message": "All pools are excluded from scale-down. Nothing to do.",
            "cluster_id": cluster_id,
            "skipped": [{"pool": p["name"], "status": "excluded"} for p in pools_excluded],
        }

    all_zero = all(
        p["current_node_count"] == 0
        and (not p["autoscaling_enabled"] or p["max_node_count"] == 0)
        for p in pools_to_scale
    )
    if all_zero:
        return {
            "status": "already_scaled_down",
            "message": "All target node pools already at 0. No action taken.",
            "cluster_id": cluster_id,
        }

    # Save snapshot BEFORE scaling — mark all as was_scaled_down initially
    # IMPORTANT: Save initial_node_count (per-zone target from GKE API)
    # NOT current_node_count (total across all MIGs).
    # set_node_pool_size expects per-zone count.
    snapshot = {
        "_id": cluster_id,
        "cluster_id": cluster_id,
        "node_pools": {
            p["name"]: {
                "initial_node_count": p["initial_node_count"],  # per-zone, for restore
                "total_node_count": p["current_node_count"],    # total, for reference
                "autoscaling_enabled": p["autoscaling_enabled"],
                "min_node_count": p["min_node_count"],
                "max_node_count": p["max_node_count"],
                "pool_config": p.get("pool_config", {}),
                "was_scaled_down": p["name"] not in excluded_pools,
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
    skipped = []

    for pool in pools_excluded:
        skipped.append({"pool": pool["name"], "status": "excluded"})
        logger.info(f"Pool {pool['name']}: excluded from scale-down")

    for pool in pools_to_scale:
        pool_name = f"{parent}/nodePools/{pool['name']}"
        try:
            # Save original autoscaling before modifying
            autoscaling_changed = False
            if pool["autoscaling_enabled"]:
                as_op = gke.set_node_pool_autoscaling(
                    request={
                        "name": pool_name,
                        "autoscaling": {
                            "enabled": True,
                            "min_node_count": 0,
                            "max_node_count": 1,
                        },
                    }
                )
                # Wait for autoscaling change before resize to avoid
                # "operation in progress" error
                _wait_for_operation(
                    gke, cluster["project_id"], cluster["location"], as_op.name,
                    timeout=60,
                )
                autoscaling_changed = True

            try:
                gke.set_node_pool_size(
                    request={"name": pool_name, "node_count": 0}
                )
                operations.append({"pool": pool["name"], "status": "scaling_down"})
            except Exception as resize_err:
                # Resize failed — rollback autoscaling if we changed it
                if autoscaling_changed:
                    try:
                        gke.set_node_pool_autoscaling(
                            request={
                                "name": pool_name,
                                "autoscaling": {
                                    "enabled": True,
                                    "min_node_count": pool["min_node_count"],
                                    "max_node_count": pool["max_node_count"],
                                },
                            }
                        )
                        logger.info(f"Rolled back autoscaling for {pool['name']}")
                    except Exception as rollback_err:
                        logger.error(f"Failed to rollback autoscaling for {pool['name']}: {rollback_err}")
                raise resize_err

        except Exception as e:
            logger.error(f"Failed to scale down {pool['name']}: {e}")
            errors.append({"pool": pool["name"], "error": str(e)})

    # Fix #1: Update snapshot — mark failed pools as was_scaled_down=false
    # so restore doesn't try to restore a pool that was never actually scaled down
    if errors:
        failed_pool_names = [e["pool"] for e in errors]
        for pool_name in failed_pool_names:
            db.snapshots.update_one(
                {"_id": cluster_id},
                {"$set": {f"node_pools.{pool_name}.was_scaled_down": False}},
            )
        logger.info(f"Updated snapshot: marked {failed_pool_names} as was_scaled_down=false")

    _write_audit(db, {
        "action": "scale_down",
        "cluster_id": cluster_id,
        "triggered_by": req.triggered_by,
        "pools_targeted": [p["name"] for p in pools_to_scale],
        "pools_excluded": [p["name"] for p in pools_excluded],
        "operations": operations,
        "errors": errors,
    })

    return {
        "status": "scaling_down",
        "cluster_id": cluster_id,
        "snapshot_saved": True,
        "operations": operations,
        "skipped": skipped,
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

    # Fetch LIVE pool state to decide what actually needs restoring
    live_pools = _fetch_node_pools(
        gke, cluster["project_id"], cluster["location"], cluster["cluster_name"]
    )
    live_pool_map = {p["name"]: p for p in live_pools}

    target_pool_names = (
        req.node_pools if req.node_pools else list(saved_pools.keys())
    )

    operations = []
    errors = []
    skipped = []

    for pool_name in target_pool_names:
        if pool_name not in saved_pools:
            errors.append({"pool": pool_name, "error": "No snapshot data for this pool"})
            continue

        saved = saved_pools[pool_name]
        target_count = saved["initial_node_count"]
        live = live_pool_map.get(pool_name)

        # Skip pools that weren't scaled down (e.g. excluded GPU pools)
        if not saved.get("was_scaled_down", True):
            skipped.append({
                "pool": pool_name,
                "status": "was_excluded",
                "current_count": live["current_node_count"] if live else 0,
                "target_count": target_count,
                "message": "Was not scaled down — no action needed",
            })
            logger.info(f"Pool {pool_name}: was not scaled down — skipping restore")
            continue

        # Fix #2: Skip pools with target_count=0 (were at 0 before scale-down)
        if target_count == 0:
            skipped.append({
                "pool": pool_name,
                "status": "target_zero",
                "current_count": live["current_node_count"] if live else 0,
                "target_count": 0,
                "message": "Was at 0 nodes before scale-down — nothing to restore",
            })
            logger.info(f"Pool {pool_name}: target is 0 — skipping restore")
            continue

        # Skip pools that no longer exist in the cluster.
        # If someone deleted a pool intentionally, we don't recreate it.
        if not live:
            skipped.append({
                "pool": pool_name,
                "status": "pool_deleted",
                "current_count": 0,
                "target_count": target_count,
                "message": f"Pool no longer exists in cluster — skipped. Scale down again to update the snapshot.",
            })
            logger.info(f"Pool {pool_name}: not found in live cluster — skipping")
            continue

        # Skip pools that already have running nodes.
        # - Pool at target (4/4): fully healthy, skip.
        # - Pool partially up (3/4): GKE is already retrying the missing
        #   node. Cycling 0→4 would kill the 3 healthy ones. Skip.
        # - Pool at 0 (0/4): completely down, needs restore. Proceed.
        # UNLESS force=True, then cycle 0→target regardless (user accepts disruption).
        if live and live["current_node_count"] > 0 and not req.force:
            skipped.append({
                "pool": pool_name,
                "status": "already_running",
                "current_count": live["current_node_count"],
                "target_count": target_count,
                "message": (
                    "fully restored" if live["current_node_count"] >= target_count
                    else f"{live['current_node_count']}/{target_count} nodes up — GKE is provisioning the rest. Use Force Restore to cycle this pool."
                ),
            })
            logger.info(
                f"Pool {pool_name}: {live['current_node_count']}/{target_count} nodes running — skipping (force={req.force})"
            )
            continue

        full_pool_name = f"{parent}/nodePools/{pool_name}"

        try:
            if saved["autoscaling_enabled"]:
                as_op = gke.set_node_pool_autoscaling(
                    request={
                        "name": full_pool_name,
                        "autoscaling": {
                            "enabled": True,
                            "min_node_count": saved["min_node_count"],
                            "max_node_count": saved["max_node_count"],
                        },
                    }
                )
                _wait_for_operation(
                    gke, cluster["project_id"], cluster["location"], as_op.name,
                    timeout=60,
                )

            # Force re-provision: reset to 0 first, wait, then set target.
            # Without this, if a previous restore set target=N but VMs
            # couldn't provision, calling set(N) again is a no-op.
            # We wait for set(0) to complete, but fire set(N) without waiting
            # to avoid Cloud Run timeout with many pools.
            op = gke.set_node_pool_size(
                request={"name": full_pool_name, "node_count": 0}
            )
            _wait_for_operation(
                gke, cluster["project_id"], cluster["location"], op.name,
                timeout=60,  # 60s per pool, not 120
            )
            gke.set_node_pool_size(
                request={
                    "name": full_pool_name,
                    "node_count": target_count,
                }
            )
            # Don't wait for set(N) — GKE will provision nodes asynchronously
            operations.append({
                "pool": pool_name,
                "status": "scaling_up",
                "target_count": target_count,
            })
        except Exception as e:
            logger.error(f"Failed to scale up {pool_name}: {e}")
            errors.append({"pool": pool_name, "error": str(e)})

    # Only mark snapshot as restored if ALL pools are healthy (none failed)
    all_done = len(errors) == 0 and len(operations) + len(skipped) == len(target_pool_names)

    if errors:
        _write_audit(db, {
            "action": "scale_up_partial_failure",
            "cluster_id": cluster_id,
            "triggered_by": req.triggered_by,
            "operations": operations,
            "skipped": skipped,
            "errors": errors,
        })
        return {
            "status": "partial_failure",
            "cluster_id": cluster_id,
            "message": "Some pools failed to scale up. Snapshot preserved — click Restore to retry only the failed pools.",
            "snapshot_used": snapshot.get("saved_at"),
            "operations": operations,
            "skipped": skipped,
            "errors": errors,
        }

    if all_done:
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
        "operations": operations,
        "skipped": skipped,
        "errors": errors,
    })

    return {
        "status": "scaling_up",
        "cluster_id": cluster_id,
        "snapshot_used": snapshot.get("saved_at"),
        "operations": operations,
        "skipped": skipped,
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
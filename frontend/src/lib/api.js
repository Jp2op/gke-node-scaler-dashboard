const BASE = import.meta.env.VITE_API_URL || '';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Clusters
  listClusters: () => request('/api/clusters'),
  getCluster: (id) => request(`/api/clusters/${id}`),
  registerCluster: (data) => request('/api/clusters', { method: 'POST', body: JSON.stringify(data) }),
  updateCluster: (id, data) => request(`/api/clusters/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteCluster: (id) => request(`/api/clusters/${id}`, { method: 'DELETE' }),

  // Node pools
  getNodePools: (clusterId) => request(`/api/clusters/${clusterId}/nodepools`),

  // Scaling
  scaleDown: (clusterId, body = {}) => request(`/api/clusters/${clusterId}/scale-down`, { method: 'POST', body: JSON.stringify(body) }),
  scaleUp: (clusterId, body = {}) => request(`/api/clusters/${clusterId}/scale-up`, { method: 'POST', body: JSON.stringify(body) }),
  scalePool: (clusterId, poolName, body) => request(`/api/clusters/${clusterId}/nodepools/${poolName}/scale`, { method: 'POST', body: JSON.stringify(body) }),

  // Snapshots
  getSnapshot: (clusterId) => request(`/api/snapshots/${clusterId}`),

  // Schedules
  listSchedules: (clusterId) => request(`/api/schedules${clusterId ? `?cluster_id=${clusterId}` : ''}`),
  createSchedule: (data) => request('/api/schedules', { method: 'POST', body: JSON.stringify(data) }),
  updateSchedule: (id, data) => request(`/api/schedules/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteSchedule: (id) => request(`/api/schedules/${id}`, { method: 'DELETE' }),
  triggerSchedule: (id) => request(`/api/schedules/${id}/trigger`, { method: 'POST' }),

  // Audit
  getAuditLog: (clusterId, limit = 50) => request(`/api/audit?limit=${limit}${clusterId ? `&cluster_id=${clusterId}` : ''}`),
};

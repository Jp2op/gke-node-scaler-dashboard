import { useState, useEffect, useCallback } from 'react'
import {
  Server, Plus, Trash2, ChevronDown, ChevronUp, Power, PowerOff,
  RefreshCw, Clock, History, AlertTriangle, Check, X, Loader2,
  Calendar, Play, Pause, Settings, ArrowUpDown, Database
} from 'lucide-react'
import { api } from './lib/api'

// ─── Toast ──────────────────────────────────────────────────────────────────

function Toast({ message, type = 'info', onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 5000)
    return () => clearTimeout(t)
  }, [onClose])

  const colors = {
    success: 'bg-emerald-900/80 border-emerald-500/40 text-emerald-200',
    error: 'bg-red-900/80 border-red-500/40 text-red-200',
    info: 'bg-zinc-800/80 border-zinc-600/40 text-zinc-200',
  }

  return (
    <div className={`fixed bottom-6 right-6 z-50 px-5 py-3 rounded-lg border ${colors[type]} shadow-2xl backdrop-blur-sm max-w-md`}>
      <div className="flex items-center gap-3">
        {type === 'success' && <Check size={16} />}
        {type === 'error' && <AlertTriangle size={16} />}
        <span className="text-sm">{message}</span>
        <button onClick={onClose} className="ml-2 opacity-60 hover:opacity-100"><X size={14} /></button>
      </div>
    </div>
  )
}

function useToast() {
  const [toast, setToast] = useState(null)
  const show = useCallback((message, type = 'info') => setToast({ message, type, key: Date.now() }), [])
  const hide = useCallback(() => setToast(null), [])
  return { toast, show, hide }
}

// ─── Confirm Dialog ─────────────────────────────────────────────────────────

function ConfirmDialog({ title, message, onConfirm, onCancel, confirmLabel = 'Confirm', danger = false }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-md w-full mx-4 shadow-2xl">
        <h3 className="text-lg font-semibold text-zinc-100 mb-2">{title}</h3>
        <p className="text-sm text-zinc-400 mb-6">{message}</p>
        <div className="flex gap-3 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-lg font-medium transition-colors ${
              danger ? 'bg-red-600 hover:bg-red-500 text-white' : 'bg-emerald-600 hover:bg-emerald-500 text-white'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Add Cluster Modal ──────────────────────────────────────────────────────

function AddClusterModal({ onClose, onAdd }) {
  const [form, setForm] = useState({
    project_id: '', location: '', cluster_name: '', display_name: '', environment: 'dev',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async () => {
    if (!form.project_id || !form.location || !form.cluster_name) {
      setError('Project ID, location, and cluster name are required.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const result = await api.registerCluster(form)
      onAdd(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const envOptions = ['dev', 'qa', 'staging', 'prod']

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <h3 className="text-lg font-semibold text-zinc-100 mb-1">Register Cluster</h3>
        <p className="text-sm text-zinc-500 mb-5">The Cloud Run SA needs container.clusterAdmin on the target project.</p>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">GCP Project ID</label>
            <input
              value={form.project_id}
              onChange={e => setForm(f => ({ ...f, project_id: e.target.value }))}
              placeholder="my-gcp-project"
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Location (zone or region)</label>
            <input
              value={form.location}
              onChange={e => setForm(f => ({ ...f, location: e.target.value }))}
              placeholder="asia-south1 or asia-south1-a"
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Cluster Name</label>
            <input
              value={form.cluster_name}
              onChange={e => setForm(f => ({ ...f, cluster_name: e.target.value }))}
              placeholder="my-gke-cluster"
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Display Name</label>
              <input
                value={form.display_name}
                onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))}
                placeholder="Dev Cluster"
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Environment</label>
              <select
                value={form.environment}
                onChange={e => setForm(f => ({ ...f, environment: e.target.value }))}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 focus:outline-none focus:border-zinc-500"
              >
                {envOptions.map(e => <option key={e} value={e}>{e.toUpperCase()}</option>)}
              </select>
            </div>
          </div>
        </div>

        {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

        <div className="flex gap-3 justify-end mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {loading && <Loader2 size={14} className="animate-spin" />}
            Register
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Schedule Modal ─────────────────────────────────────────────────────────

function ScheduleModal({ clusterId, clusterName, onClose, onSave }) {
  const [form, setForm] = useState({
    cluster_id: clusterId,
    action: 'scale_down',
    cron: '0 20 * * 1-5',
    timezone: 'Asia/Kolkata',
    description: '',
    enabled: true,
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const presets = [
    { label: 'Weekdays 8PM scale down', cron: '0 20 * * 1-5', action: 'scale_down' },
    { label: 'Weekdays 8AM scale up', cron: '0 8 * * 1-5', action: 'scale_up' },
    { label: 'Friday 7PM scale down', cron: '0 19 * * 5', action: 'scale_down' },
    { label: 'Monday 8AM scale up', cron: '0 8 * * 1', action: 'scale_up' },
  ]

  const handleSubmit = async () => {
    if (!form.cron) { setError('Cron expression required.'); return }
    setLoading(true)
    setError('')
    try {
      const result = await api.createSchedule(form)
      onSave(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <h3 className="text-lg font-semibold text-zinc-100 mb-1">Add Schedule</h3>
        <p className="text-sm text-zinc-500 mb-5">for {clusterName}</p>

        <div className="mb-4">
          <label className="block text-xs font-medium text-zinc-400 mb-2">Quick presets</label>
          <div className="flex flex-wrap gap-2">
            {presets.map(p => (
              <button
                key={p.label}
                onClick={() => setForm(f => ({ ...f, cron: p.cron, action: p.action, description: p.label }))}
                className="px-3 py-1.5 text-xs bg-zinc-800 border border-zinc-700 rounded-lg text-zinc-300 hover:bg-zinc-700 hover:border-zinc-600 transition-colors"
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Action</label>
              <select
                value={form.action}
                onChange={e => setForm(f => ({ ...f, action: e.target.value }))}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 focus:outline-none focus:border-zinc-500"
              >
                <option value="scale_down">Scale Down (→ 0)</option>
                <option value="scale_up">Scale Up (restore)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1.5">Timezone</label>
              <input
                value={form.timezone}
                onChange={e => setForm(f => ({ ...f, timezone: e.target.value }))}
                className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-mono"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Cron Expression</label>
            <input
              value={form.cron}
              onChange={e => setForm(f => ({ ...f, cron: e.target.value }))}
              placeholder="0 20 * * 1-5"
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500 font-mono"
            />
            <p className="text-xs text-zinc-600 mt-1">min hour day month weekday — e.g. "0 20 * * 1-5" = 8PM weekdays</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1.5">Description</label>
            <input
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Nightly shutdown"
              className="w-full px-3 py-2 bg-zinc-800 border border-zinc-700 rounded-lg text-sm text-zinc-100 placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
            />
          </div>
        </div>

        {error && <p className="mt-4 text-sm text-red-400">{error}</p>}

        <div className="flex gap-3 justify-end mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors">Cancel</button>
          <button onClick={handleSubmit} disabled={loading} className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors disabled:opacity-50 flex items-center gap-2">
            {loading && <Loader2 size={14} className="animate-spin" />}
            Create Schedule
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Environment Badge ──────────────────────────────────────────────────────

function EnvBadge({ env }) {
  const colors = {
    prod: 'bg-red-500/15 text-red-400 border-red-500/30',
    staging: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    qa: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    dev: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  }
  return (
    <span className={`px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider rounded border ${colors[env] || colors.dev}`}>
      {env}
    </span>
  )
}

// ─── Cluster Card ───────────────────────────────────────────────────────────

function ClusterCard({ cluster, onRefresh, toast }) {
  const [expanded, setExpanded] = useState(false)
  const [poolData, setPoolData] = useState(null)
  const [loadingPools, setLoadingPools] = useState(false)
  const [scaling, setScaling] = useState(null) // 'down' | 'up' | null
  const [confirm, setConfirm] = useState(null)
  const [showSchedule, setShowSchedule] = useState(false)
  const [schedules, setSchedules] = useState([])
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [excludedPools, setExcludedPools] = useState([])

  const fetchPools = async () => {
    setLoadingPools(true)
    try {
      const data = await api.getNodePools(cluster.id)
      setPoolData(data)
      setExcludedPools(data.excluded_pools || [])
    } catch (e) {
      toast.show(e.message, 'error')
    } finally {
      setLoadingPools(false)
    }
  }

  const fetchSchedules = async () => {
    try {
      const data = await api.listSchedules(cluster.id)
      setSchedules(data.schedules || [])
    } catch (e) { /* ignore */ }
  }

  useEffect(() => {
    if (expanded) {
      fetchPools()
      fetchSchedules()
    }
  }, [expanded])

  const handleScaleDown = async () => {
    setConfirm(null)
    setScaling('down')
    try {
      const result = await api.scaleDown(cluster.id)
      const excluded = result.skipped?.filter(s => s.status === 'excluded').map(s => s.pool) || []
      if (result.status === 'already_scaled_down') {
        toast.show('Already at 0, no action needed.', 'info')
      } else if (result.status === 'all_excluded') {
        toast.show('All pools are excluded — nothing to scale down.', 'info')
      } else {
        let msg = 'Scaling down. Snapshot saved.'
        if (excluded.length) msg += ` Excluded: ${excluded.join(', ')}`
        if (result.errors?.length) msg += ` Failed: ${result.errors.map(e => e.pool).join(', ')}`
        toast.show(msg, result.errors?.length ? 'error' : 'success')
      }
      await fetchPools()
    } catch (e) {
      toast.show(e.message, 'error')
      await fetchPools()
    } finally {
      setScaling(null)
    }
  }

  const handleScaleUp = async () => {
    setConfirm(null)
    setScaling('up')
    try {
      const result = await api.scaleUp(cluster.id)
      if (result.status === 'partial_failure') {
        const failed = result.errors?.map(e => e.pool).join(', ')
        toast.show(`Failed: ${failed}. Snapshot preserved — click Restore to retry only failed pools.`, 'error')
      } else {
        const restored = result.operations?.map(o => `${o.pool}→${o.target_count}`).join(', ')
        const skippedFull = result.skipped?.filter(s => s.status === 'already_running' && s.current_count >= s.target_count).map(s => s.pool) || []
        const skippedPartial = result.skipped?.filter(s => s.status === 'already_running' && s.current_count < s.target_count).map(s => `${s.pool} (${s.current_count}/${s.target_count})`) || []
        const skippedDeleted = result.skipped?.filter(s => s.status === 'pool_deleted').map(s => s.pool) || []
        const skippedExcluded = result.skipped?.filter(s => s.status === 'was_excluded').map(s => s.pool) || []
        const skippedZero = result.skipped?.filter(s => s.status === 'target_zero').map(s => s.pool) || []
        let msg = restored ? `Restoring: ${restored}` : 'All pools already running'
        if (skippedFull.length) msg += ` | OK: ${skippedFull.join(', ')}`
        if (skippedPartial.length) msg += ` | Provisioning: ${skippedPartial.join(', ')}`
        if (skippedDeleted.length) msg += ` | Deleted: ${skippedDeleted.join(', ')}`
        if (skippedExcluded.length) msg += ` | Excluded: ${skippedExcluded.join(', ')}`
        if (skippedZero.length) msg += ` | Was empty: ${skippedZero.join(', ')}`
        toast.show(msg, 'success')
      }
      await fetchPools()
    } catch (e) {
      toast.show(e.message, 'error')
      await fetchPools()
    } finally {
      setScaling(null)
    }
  }

  const handleDeleteCluster = async () => {
    setConfirmDelete(false)
    try {
      await api.deleteCluster(cluster.id)
      toast.show('Cluster removed from dashboard.', 'success')
      onRefresh()
    } catch (e) {
      toast.show(e.message, 'error')
    }
  }

  const handleDeleteSchedule = async (scheduleId) => {
    try {
      await api.deleteSchedule(scheduleId)
      toast.show('Schedule deleted.', 'success')
      fetchSchedules()
    } catch (e) {
      toast.show(e.message, 'error')
    }
  }

  const handleToggleSchedule = async (schedule) => {
    try {
      await api.updateSchedule(schedule.id, { enabled: !schedule.enabled })
      fetchSchedules()
    } catch (e) {
      toast.show(e.message, 'error')
    }
  }

  const handleToggleExclusion = async (poolName, currentlyExcluded) => {
    try {
      const result = await api.togglePoolExclusion(cluster.id, poolName, !currentlyExcluded)
      setExcludedPools(result.excluded_pools || [])
      toast.show(
        !currentlyExcluded
          ? `${poolName} excluded from scale-down`
          : `${poolName} included in scale-down`,
        'info'
      )
    } catch (e) {
      toast.show(e.message, 'error')
    }
  }

  const allPoolsZero = poolData?.node_pools?.every(p => p.current_node_count === 0) ?? false
  const totalNodes = poolData?.node_pools?.reduce((s, p) => s + (p.current_node_count || 0), 0) ?? null
  const hasPartialPools = poolData?.node_pools?.some(p => {
    const sp = poolData?.snapshot?.node_pools?.[p.name]
    const target = sp?.total_node_count ?? sp?.initial_node_count
    return target && p.current_node_count > 0 && p.current_node_count < target
  }) ?? false

  return (
    <>
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden hover:border-zinc-700 transition-colors">
        {/* Header */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-5 py-4 flex items-center justify-between text-left"
        >
          <div className="flex items-center gap-3.5">
            <div className={`w-2.5 h-2.5 rounded-full ${
              poolData ? (allPoolsZero ? 'bg-red-500' : hasPartialPools ? 'bg-amber-500' : 'bg-emerald-500') : 'bg-zinc-600'
            }`} />
            <div>
              <div className="flex items-center gap-2.5">
                <span className="font-semibold text-zinc-100">{cluster.display_name || cluster.cluster_name}</span>
                <EnvBadge env={cluster.environment} />
              </div>
              <div className="flex items-center gap-3 mt-0.5">
                <span className="text-xs text-zinc-500 font-mono">{cluster.project_id}</span>
                <span className="text-xs text-zinc-600">·</span>
                <span className="text-xs text-zinc-500 font-mono">{cluster.location}</span>
                {totalNodes !== null && (
                  <>
                    <span className="text-xs text-zinc-600">·</span>
                    <span className={`text-xs font-medium ${allPoolsZero ? 'text-red-400' : hasPartialPools ? 'text-amber-400' : 'text-emerald-400'}`}>
                      {totalNodes} node{totalNodes !== 1 ? 's' : ''}{hasPartialPools ? ' (partial)' : ''}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>
          {expanded ? <ChevronUp size={16} className="text-zinc-500" /> : <ChevronDown size={16} className="text-zinc-500" />}
        </button>

        {/* Expanded Content */}
        {expanded && (
          <div className="border-t border-zinc-800 px-5 py-4">
            {/* Action Buttons */}
            <div className="flex items-center gap-2 mb-4">
              <button
                onClick={() => setConfirm('down')}
                disabled={scaling !== null}
                className="flex items-center gap-2 px-3.5 py-2 text-sm rounded-lg bg-red-600/15 border border-red-500/30 text-red-400 hover:bg-red-600/25 transition-colors disabled:opacity-40"
              >
                {scaling === 'down' ? <Loader2 size={14} className="animate-spin" /> : <PowerOff size={14} />}
                Scale to 0
              </button>
              <button
                onClick={() => setConfirm('up')}
                disabled={scaling !== null || !poolData?.has_snapshot}
                className="flex items-center gap-2 px-3.5 py-2 text-sm rounded-lg bg-emerald-600/15 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-600/25 transition-colors disabled:opacity-40"
              >
                {scaling === 'up' ? <Loader2 size={14} className="animate-spin" /> : <Power size={14} />}
                Restore
              </button>
              <button onClick={fetchPools} disabled={loadingPools} className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg bg-zinc-800 border border-zinc-700 text-zinc-400 hover:bg-zinc-700 transition-colors disabled:opacity-40">
                <RefreshCw size={14} className={loadingPools ? 'animate-spin' : ''} />
              </button>
              <div className="flex-1" />
              <button onClick={() => setConfirmDelete(true)} className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg text-zinc-600 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                <Trash2 size={14} />
              </button>
            </div>

            {/* Snapshot Info */}
            {poolData?.snapshot && (
              <div className="mb-4 px-3.5 py-2.5 bg-amber-500/8 border border-amber-500/20 rounded-lg">
                <div className="flex items-center gap-2 text-xs text-amber-400/80">
                  <Database size={12} />
                  <span>
                    Snapshot saved {new Date(poolData.snapshot.saved_at).toLocaleString()} by {poolData.snapshot.saved_by}
                    {poolData.snapshot.status === 'restored' && (
                      <span className="ml-2 text-emerald-400">(restored {new Date(poolData.snapshot.restored_at).toLocaleString()})</span>
                    )}
                  </span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                  {Object.entries(poolData.snapshot.node_pools || {}).map(([name, info]) => (
                    <span key={name} className="text-xs font-mono text-amber-400/60">
                      {name}: {info.initial_node_count} nodes ({info.min_node_count}-{info.max_node_count})
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Node Pools Table */}
            {loadingPools ? (
              <div className="flex items-center justify-center py-8 text-zinc-500">
                <Loader2 size={20} className="animate-spin" />
              </div>
            ) : poolData?.node_pools ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-zinc-500 uppercase tracking-wider">
                      <th className="text-left pb-2 font-medium">Pool</th>
                      <th className="text-left pb-2 font-medium">Machine</th>
                      <th className="text-center pb-2 font-medium">Nodes</th>
                      <th className="text-center pb-2 font-medium">Autoscale</th>
                      <th className="text-center pb-2 font-medium">Status</th>
                      <th className="text-center pb-2 font-medium">Scale</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/50">
                    {poolData.node_pools.map(pool => {
                      const isExcluded = excludedPools.includes(pool.name)
                      return (
                      <tr key={pool.name} className={`text-zinc-300 ${isExcluded ? 'opacity-60' : ''}`}>
                        <td className="py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm">{pool.name}</span>
                            {pool.is_gpu && (
                              <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded bg-violet-500/20 text-violet-400 border border-violet-500/30">
                                GPU
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-2.5 text-zinc-500 font-mono text-xs">
                          {pool.machine_type}
                          {pool.gpu_type && <span className="ml-1 text-violet-400/60">({pool.gpu_type}×{pool.gpu_count})</span>}
                        </td>
                        <td className="py-2.5 text-center">
                          {(() => {
                            const actual = pool.current_node_count
                            const snapshotPool = poolData?.snapshot?.node_pools?.[pool.name]
                            const displayTarget = snapshotPool?.total_node_count ?? snapshotPool?.initial_node_count
                            const isPartial = displayTarget && actual > 0 && actual < displayTarget

                            if (isPartial) {
                              return (
                                <span className="font-semibold text-amber-400" title={`${actual} of ${displayTarget} nodes provisioned`}>
                                  {actual}/{displayTarget}
                                </span>
                              )
                            }
                            return (
                              <span className={`font-semibold ${actual === 0 ? 'text-red-400' : 'text-emerald-400'}`}>
                                {actual}
                              </span>
                            )
                          })()}
                        </td>
                        <td className="py-2.5 text-center text-xs text-zinc-500">
                          {pool.autoscaling_enabled ? `${pool.min_node_count}–${pool.max_node_count}` : 'off'}
                        </td>
                        <td className="py-2.5 text-center">
                          {(() => {
                            const actual = pool.current_node_count
                            const snapshotPool = poolData?.snapshot?.node_pools?.[pool.name]
                            const snapshotTarget = snapshotPool?.total_node_count ?? snapshotPool?.initial_node_count
                            const wasScaledDown = snapshotPool?.was_scaled_down

                            if (pool.status === 'ERROR' || pool.status === 'RUNNING_WITH_ERROR') {
                              return <span className="text-xs px-2 py-0.5 rounded bg-red-500/15 text-red-400">ERROR</span>
                            }
                            if (pool.status === 'RECONCILING') {
                              return <span className="text-xs px-2 py-0.5 rounded bg-yellow-500/15 text-yellow-400">RECONCILING</span>
                            }
                            if (actual === 0 && wasScaledDown) {
                              return <span className="text-xs px-2 py-0.5 rounded bg-red-500/15 text-red-400">SCALED DOWN</span>
                            }
                            if (snapshotTarget && actual > 0 && actual < snapshotTarget) {
                              return <span className="text-xs px-2 py-0.5 rounded bg-amber-500/15 text-amber-400">PARTIAL</span>
                            }
                            if (snapshotTarget && actual >= snapshotTarget && wasScaledDown) {
                              return <span className="text-xs px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400">SCALED UP</span>
                            }
                            return <span className={`text-xs px-2 py-0.5 rounded ${
                              pool.status === 'RUNNING' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-zinc-700 text-zinc-400'
                            }`}>{pool.status}</span>
                          })()}
                        </td>
                        <td className="py-2.5 text-center">
                          {pool.is_gpu ? (
                            <button
                              onClick={() => handleToggleExclusion(pool.name, isExcluded)}
                              className={`relative w-9 h-5 rounded-full transition-colors ${isExcluded ? 'bg-zinc-700' : 'bg-emerald-600'}`}
                              title={isExcluded ? 'Excluded from scale-down' : 'Included in scale-down'}
                            >
                              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${isExcluded ? 'left-0.5' : 'left-[18px]'}`} />
                            </button>
                          ) : (
                            <span className="text-xs text-zinc-600">—</span>
                          )}
                        </td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            ) : null}

            {/* Schedules */}
            {schedules.length > 0 && (
              <div className="mt-4 pt-4 border-t border-zinc-800">
                <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">Schedules</h4>
                <div className="space-y-2">
                  {schedules.map(s => (
                    <div key={s.id} className="flex items-center justify-between px-3 py-2 bg-zinc-800/50 rounded-lg">
                      <div className="flex items-center gap-3">
                        <button onClick={() => handleToggleSchedule(s)} className={`${s.enabled ? 'text-emerald-400' : 'text-zinc-600'}`}>
                          {s.enabled ? <Play size={12} /> : <Pause size={12} />}
                        </button>
                        <div>
                          <span className="text-sm text-zinc-300">{s.description || s.action}</span>
                          <span className="ml-2 text-xs font-mono text-zinc-600">{s.cron}</span>
                          <span className="ml-2 text-xs text-zinc-600">{s.timezone}</span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${s.action === 'scale_down' ? 'bg-red-500/15 text-red-400' : 'bg-emerald-500/15 text-emerald-400'}`}>
                          {s.action === 'scale_down' ? '↓ down' : '↑ up'}
                        </span>
                        {s.last_run && (
                          <span className="text-xs text-zinc-600" title={`Last: ${s.last_run}`}>
                            {s.last_status === 'success' ? <Check size={12} className="text-emerald-500" /> : <AlertTriangle size={12} className="text-red-400" />}
                          </span>
                        )}
                        <button onClick={() => handleDeleteSchedule(s.id)} className="text-zinc-600 hover:text-red-400">
                          <X size={12} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Dialogs */}
      {confirm === 'down' && (
        <ConfirmDialog
          title="Scale Down to 0"
          message={`This will save a snapshot of current node counts and scale ALL node pools in "${cluster.display_name || cluster.cluster_name}" to 0. Workloads will be evicted.`}
          onConfirm={handleScaleDown}
          onCancel={() => setConfirm(null)}
          confirmLabel="Scale to 0"
          danger
        />
      )}
      {confirm === 'up' && (
        <ConfirmDialog
          title="Restore from Snapshot"
          message={`This will restore all node pools to their previous sizes from the saved snapshot. The cluster will begin provisioning nodes.`}
          onConfirm={handleScaleUp}
          onCancel={() => setConfirm(null)}
          confirmLabel="Restore"
        />
      )}
      {confirmDelete && (
        <ConfirmDialog
          title="Remove Cluster"
          message="This only removes the cluster from this dashboard. It does NOT delete the GKE cluster itself."
          onConfirm={handleDeleteCluster}
          onCancel={() => setConfirmDelete(false)}
          confirmLabel="Remove"
          danger
        />
      )}
      {showSchedule && (
        <ScheduleModal
          clusterId={cluster.id}
          clusterName={cluster.display_name || cluster.cluster_name}
          onClose={() => setShowSchedule(false)}
          onSave={() => { setShowSchedule(false); fetchSchedules(); toast.show('Schedule created.', 'success') }}
        />
      )}
    </>
  )
}

// ─── Audit Log View ─────────────────────────────────────────────────────────

function AuditLog({ toast }) {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getAuditLog(null, 100).then(data => {
      setEntries(data.entries || [])
      setLoading(false)
    }).catch(e => {
      toast.show(e.message, 'error')
      setLoading(false)
    })
  }, [])

  const actionColors = {
    scale_down: 'text-red-400',
    scale_up: 'text-emerald-400',
    manual_scale: 'text-blue-400',
    cluster_registered: 'text-purple-400',
    cluster_deleted: 'text-zinc-500',
  }

  if (loading) return <div className="flex justify-center py-12"><Loader2 size={24} className="animate-spin text-zinc-500" /></div>

  return (
    <div className="space-y-1">
      {entries.map(e => (
        <div key={e.id} className="flex items-start gap-3 px-4 py-2.5 hover:bg-zinc-800/30 rounded-lg transition-colors">
          <span className="text-xs text-zinc-600 font-mono mt-0.5 shrink-0 w-44">
            {new Date(e.timestamp).toLocaleString()}
          </span>
          <span className={`text-sm font-medium w-36 shrink-0 ${actionColors[e.action] || 'text-zinc-400'}`}>
            {e.action}
          </span>
          <span className="text-sm text-zinc-500 font-mono">{e.cluster_id || '—'}</span>
          {e.triggered_by && <span className="text-xs text-zinc-600 ml-auto">by {e.triggered_by}</span>}
        </div>
      ))}
      {entries.length === 0 && <p className="text-center text-zinc-600 py-8">No audit entries yet.</p>}
    </div>
  )
}

// ─── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [clusters, setClusters] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAddCluster, setShowAddCluster] = useState(false)
  const [tab, setTab] = useState('clusters') // 'clusters' | 'audit'
  const { toast, show, hide } = useToast()

  const fetchClusters = async () => {
    setLoading(true)
    try {
      const data = await api.listClusters()
      setClusters(data.clusters || [])
    } catch (e) {
      show(e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchClusters() }, [])

  const envOrder = { prod: 0, staging: 1, qa: 2, dev: 3 }
  const sortedClusters = [...clusters].sort((a, b) => (envOrder[a.environment] ?? 4) - (envOrder[b.environment] ?? 4))

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Header */}
      <header className="border-b border-zinc-800">
        <div className="max-w-5xl mx-auto px-6 py-5 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
              <ArrowUpDown size={16} className="text-white" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-zinc-100 leading-tight">GKE Node Scaler</h1>
              <p className="text-xs text-zinc-500">Scale node pools across projects</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex bg-zinc-900 rounded-lg border border-zinc-800 p-0.5">
              <button
                onClick={() => setTab('clusters')}
                className={`px-3.5 py-1.5 text-sm rounded-md transition-colors ${tab === 'clusters' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                <span className="flex items-center gap-2"><Server size={14} /> Clusters</span>
              </button>
              <button
                onClick={() => setTab('audit')}
                className={`px-3.5 py-1.5 text-sm rounded-md transition-colors ${tab === 'audit' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                <span className="flex items-center gap-2"><History size={14} /> Audit Log</span>
              </button>
            </div>
            {tab === 'clusters' && (
              <button
                onClick={() => setShowAddCluster(true)}
                className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors"
              >
                <Plus size={14} /> Add Cluster
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-5xl mx-auto px-6 py-6">
        {tab === 'clusters' && (
          <>
            {loading ? (
              <div className="flex justify-center py-16"><Loader2 size={24} className="animate-spin text-zinc-500" /></div>
            ) : sortedClusters.length === 0 ? (
              <div className="text-center py-16">
                <Server size={40} className="mx-auto text-zinc-700 mb-4" />
                <p className="text-zinc-400 mb-2">No clusters registered yet</p>
                <p className="text-sm text-zinc-600 mb-6">Add your GKE clusters to start managing node pools.</p>
                <button
                  onClick={() => setShowAddCluster(true)}
                  className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors"
                >
                  <Plus size={14} /> Add Cluster
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                {sortedClusters.map(c => (
                  <ClusterCard key={c.id} cluster={c} onRefresh={fetchClusters} toast={{ show }} />
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'audit' && <AuditLog toast={{ show }} />}
      </main>

      {/* Modals */}
      {showAddCluster && (
        <AddClusterModal
          onClose={() => setShowAddCluster(false)}
          onAdd={() => { setShowAddCluster(false); fetchClusters(); show('Cluster registered.', 'success') }}
        />
      )}

      {/* Toast */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={hide} />}
    </div>
  )
}
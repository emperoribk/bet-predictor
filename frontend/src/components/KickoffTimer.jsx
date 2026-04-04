import { useState, useEffect } from 'react'
import { Clock } from 'lucide-react'

function getTimeLeft(kickoffIso) {
  if (!kickoffIso) return null
  const diff = new Date(kickoffIso) - Date.now()
  if (diff <= 0) return null
  const h = Math.floor(diff / 3_600_000)
  const m = Math.floor((diff % 3_600_000) / 60_000)
  const s = Math.floor((diff % 60_000) / 1_000)
  return { h, m, s, diff }
}

export default function KickoffTimer({ kickoffTime }) {
  const [left, setLeft] = useState(() => getTimeLeft(kickoffTime))

  useEffect(() => {
    if (!kickoffTime) return
    const t = setInterval(() => setLeft(getTimeLeft(kickoffTime)), 1000)
    return () => clearInterval(t)
  }, [kickoffTime])

  if (!left) return null

  const pad = n => String(n).padStart(2, '0')

  return (
    <div className="flex items-center gap-1.5 text-xs text-sky-400 bg-sky-950/50 border border-sky-800/40 rounded-full px-3 py-1">
      <Clock size={11} />
      <span className="font-mono font-semibold">
        {left.h > 0 ? `${left.h}h ${pad(left.m)}m` : `${pad(left.m)}m ${pad(left.s)}s`}
      </span>
      <span className="text-slate-500">to kick off</span>
    </div>
  )
}

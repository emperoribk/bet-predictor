import { useState, useEffect, useRef } from 'react'
import { RefreshCw } from 'lucide-react'

const INTERVAL = 10

export default function CountdownTimer({ lastUpdated, onRefresh, isLoading }) {
  const [secs, setSecs] = useState(INTERVAL)
  const prevLastUpdated = useRef(lastUpdated)

  useEffect(() => {
    const timer = setInterval(() => {
      if (prevLastUpdated.current !== lastUpdated) {
        prevLastUpdated.current = lastUpdated
        setSecs(INTERVAL)
        return
      }
      setSecs(s => (s <= 1 ? INTERVAL : s - 1))
    }, 1000)
    return () => clearInterval(timer)
  }, [lastUpdated])

  const progress = ((INTERVAL - secs) / INTERVAL) * 100
  const r = 12
  const circ = 2 * Math.PI * r

  return (
    <div className="flex items-center gap-2">
      {/* Ring */}
      <div className="relative w-9 h-9">
        <svg className="w-9 h-9 -rotate-90" viewBox="0 0 32 32">
          <circle cx="16" cy="16" r={r} fill="none" stroke="#0c2340" strokeWidth="2.5" />
          <circle cx="16" cy="16" r={r} fill="none" stroke="#0ea5e9" strokeWidth="2.5"
            strokeDasharray={circ}
            strokeDashoffset={circ * (1 - progress / 100)}
            strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 1s linear' }}
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-sky-400">
          {secs}
        </span>
      </div>
      {/* Refresh button */}
      <button onClick={onRefresh} disabled={isLoading}
        className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-sky-400 disabled:opacity-40 transition-colors"
      >
        <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} />
        <span className="hidden sm:inline">Refresh</span>
      </button>
    </div>
  )
}

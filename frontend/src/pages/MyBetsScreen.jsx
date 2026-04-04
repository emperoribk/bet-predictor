import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { RefreshCw, Swords } from 'lucide-react'
import useBetStore from '../store/useBetStore'

const FILTERS = ['All', 'Live', 'Won', 'Lost', 'Pending']

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

const statusColors = {
  live:    { bg: 'rgba(255,180,0,0.15)',  color: 'var(--amber)', label: 'LIVE'    },
  won:     { bg: 'rgba(0,210,120,0.12)', color: 'var(--green)', label: 'WON'     },
  lost:    { bg: 'rgba(255,82,82,0.12)', color: 'var(--red)',   label: 'LOST'    },
  pending: { bg: 'rgba(74,85,104,0.2)',  color: 'var(--muted)', label: 'PENDING' },
}

export default function MyBetsScreen({ onTrack }) {
  const { history, clearHistory } = useBetStore()
  const [filter, setFilter] = useState('All')
  const [refreshKey, setRefreshKey] = useState(0)

  const filtered = history.filter(b => {
    if (filter === 'All') return true
    return (b.status || 'pending').toLowerCase() === filter.toLowerCase()
  })

  if (history.length === 0) {
    return (
      <div className="screen-content" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 400, padding: '0 32px' }}>
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          style={{ textAlign: 'center' }}
        >
          <Swords size={48} color="var(--dim)" style={{ margin: '0 auto 16px' }} />
          <p className="font-display" style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', marginBottom: 8 }}>No bets tracked yet</p>
          <p style={{ fontSize: 14, color: 'var(--muted)', marginBottom: 24, lineHeight: 1.6 }}>Enter a code on the home screen to get started</p>
          <motion.button
            className="btn-primary"
            whileTap={{ scale: 0.97 }}
            onClick={() => onTrack(null)}
            style={{ width: 'auto', padding: '12px 24px' }}
          >
            Track a Bet →
          </motion.button>
        </motion.div>
      </div>
    )
  }

  return (
    <div className="screen-content">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 16px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>My Bets</span>
          <span style={{ background: 'rgba(0,200,255,0.1)', color: 'var(--cyan)', borderRadius: 20, padding: '2px 10px', fontSize: 12, fontWeight: 600 }}>
            {history.length}
          </span>
        </div>
        <button
          className="btn-ghost"
          style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', fontSize: 13 }}
          onClick={() => setRefreshKey(k => k + 1)}
        >
          <RefreshCw size={13} />
          Refresh
        </button>
      </div>

      {/* Filter strip */}
      <div style={{ padding: '0 16px 16px' }}>
        <div className="filter-strip">
          {FILTERS.map(f => (
            <button
              key={f}
              className={`filter-chip ${filter === f ? 'active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <AnimatePresence mode="popLayout">
          {filtered.length === 0 ? (
            <motion.p
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              style={{ color: 'var(--muted)', fontSize: 14, textAlign: 'center', padding: '32px 0' }}
            >
              No {filter.toLowerCase()} bets
            </motion.p>
          ) : (
            filtered.map((bet, i) => (
              <BetSlipCard key={bet.code + refreshKey} bet={bet} index={i} onOpen={() => onTrack(bet.code)} />
            ))
          )}
        </AnimatePresence>
      </div>

      {/* Clear all */}
      {history.length > 0 && (
        <div style={{ padding: '24px 16px 8px', textAlign: 'center' }}>
          <button
            onClick={clearHistory}
            style={{ background: 'none', border: 'none', color: 'var(--dim)', fontSize: 12, cursor: 'pointer', textDecoration: 'underline' }}
          >
            Clear history
          </button>
        </div>
      )}
    </div>
  )
}

function BetSlipCard({ bet, index, onOpen }) {
  const sc = statusColors[(bet.status || 'pending').toLowerCase()] || statusColors.pending
  return (
    <motion.button
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8, scale: 0.97 }}
      transition={{ delay: index * 0.05 }}
      whileTap={{ scale: 0.98 }}
      onClick={onOpen}
      className="glass glass-hover"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', border: 'none', cursor: 'pointer', width: '100%', textAlign: 'left' }}
    >
      <div>
        <p className="font-display" style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', letterSpacing: 1 }}>{bet.code}</p>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>
          {bet.legs} {bet.legs === 1 ? 'leg' : 'legs'} · {timeAgo(bet.date)}
        </p>
      </div>
      <div style={{ textAlign: 'right' }}>
        <span className="badge" style={{ background: sc.bg, color: sc.color, marginBottom: 4, display: 'block' }}>{sc.label}</span>
        <span style={{ fontSize: 11, color: 'var(--dim)' }}>Tap to open →</span>
      </div>
    </motion.button>
  )
}

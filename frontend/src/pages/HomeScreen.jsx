import { useState, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowRight, X } from 'lucide-react'
import useBetStore from '../store/useBetStore'
import LiveMatchesFeed from '../components/LiveMatchesFeed'

export default function HomeScreen({ onTrack, onViewLive }) {
  const [code, setCode]     = useState('')
  const [shaking, setShaking] = useState(false)
  const history  = useBetStore(s => s.history)
  const inputRef = useRef(null)

  function handleInput(e) {
    const val = e.target.value.replace(/[^a-zA-Z0-9]/g, '').toUpperCase().slice(0, 12)
    setCode(val)
  }

  function handleSubmit() {
    if (!code.trim()) {
      setShaking(true)
      setTimeout(() => setShaking(false), 600)
      inputRef.current?.focus()
      return
    }
    onTrack(code.trim())
  }

  return (
    <div
      className="screen-content"
      style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}
    >
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '0 20px' }}>

        {/* ── Top bar ─────────────────────────────────────── */}
        <div style={{ paddingTop: 24, paddingBottom: 32, flexShrink: 0 }}>
          <span className="font-display" style={{ fontSize: 24, fontWeight: 700, letterSpacing: 1 }}>
            <span style={{ color: 'var(--cyan)' }}>BET</span>
            <span style={{ color: '#ffffff' }}>WATCH</span>
          </span>
        </div>

        {/* ── Hero ─────────────────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          style={{ marginBottom: 36, flexShrink: 0 }}
        >
          <h1 className="font-display" style={{ fontSize: 'clamp(36px, 10vw, 48px)', fontWeight: 700, lineHeight: 1.08, marginBottom: 16, color: '#ffffff' }}>
            Track your bets<br />
            <span style={{ color: 'var(--cyan)', textShadow: '0 0 32px rgba(92,217,255,0.4)' }}>in real time</span>
          </h1>
          <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.45)', lineHeight: 1.7, maxWidth: 300 }}>
            Goals, red cards, match stats —<br />the moment they happen.
          </p>
        </motion.div>

        {/* ── Input card ───────────────────────────────────── */}
        <motion.div
          className="glass"
          animate={shaking ? { x: [0, -10, 10, -8, 8, -4, 4, 0] } : {}}
          transition={{ duration: 0.5 }}
          style={{ padding: '20px 20px 22px', marginBottom: 28, flexShrink: 0 }}
        >
          <p style={{ fontSize: 10, fontWeight: 600, letterSpacing: 2, color: 'var(--muted)', textTransform: 'uppercase', marginBottom: 10 }}>
            Enter Bet Code
          </p>
          <div style={{ position: 'relative', marginBottom: 12 }}>
            <input
              ref={inputRef}
              className="bet-input"
              placeholder="e.g. SB12345"
              value={code}
              onChange={handleInput}
              onKeyDown={e => e.key === 'Enter' && handleSubmit()}
            />
            <AnimatePresence>
              {code && (
                <motion.button
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.7 }}
                  onClick={() => setCode('')}
                  style={{
                    position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                    background: 'var(--dim)', border: 'none', borderRadius: '50%',
                    width: 26, height: 26, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: 'pointer', color: 'var(--muted)',
                  }}
                >
                  <X size={13} />
                </motion.button>
              )}
            </AnimatePresence>
          </div>
          <p style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 18 }}>
            Find your code in My Bets on the SportyBet app
          </p>
          <motion.button
            className="btn-primary"
            whileTap={{ scale: 0.97 }}
            onClick={handleSubmit}
            disabled={!code}
          >
            Track My Bet
            <ArrowRight size={16} />
          </motion.button>
        </motion.div>

        {/* ── Recent bets ──────────────────────────────────── */}
        {history.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.15 }}
            style={{ marginBottom: 28, flexShrink: 0 }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
              <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
              <span style={{ fontSize: 11, color: 'var(--dim)', letterSpacing: 1 }}>recent bets</span>
              <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {history.slice(0, 4).map((bet, i) => (
                <RecentBetRow key={bet.code} bet={bet} index={i} onSelect={() => setCode(bet.code)} />
              ))}
            </div>
          </motion.div>
        )}

        {/* ── Live matches feed ─────────────────────────────── */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          style={{ paddingBottom: 28 }}
        >
          <LiveMatchesFeed onViewAll={onViewLive} />
        </motion.div>


      </div>
    </div>
  )
}

/* ── Helpers ──────────────────────────────────────────────── */
function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60)    return 'just now'
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function statusStyle(status) {
  const s = (status || '').toLowerCase()
  if (s === 'live') return { bg: 'rgba(255,180,0,0.15)', color: 'var(--amber)' }
  if (s === 'won')  return { bg: 'rgba(0,210,120,0.12)', color: 'var(--green)' }
  if (s === 'lost') return { bg: 'rgba(255,82,82,0.12)',  color: 'var(--red)'  }
  return { bg: 'rgba(74,85,104,0.2)', color: 'var(--muted)' }
}

function RecentBetRow({ bet, index, onSelect }) {
  const { bg, color } = statusStyle(bet.status)
  return (
    <motion.button
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06 }}
      whileTap={{ scale: 0.98 }}
      onClick={onSelect}
      className="glass glass-hover"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '13px 16px', border: 'none', cursor: 'pointer', width: '100%', textAlign: 'left' }}
    >
      <div>
        <p className="font-display" style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', letterSpacing: 1 }}>{bet.code}</p>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>
          {bet.legs} {bet.legs === 1 ? 'leg' : 'legs'} · {timeAgo(bet.date)}
        </p>
      </div>
      <span className="badge" style={{ background: bg, color }}>{(bet.status || 'pending').toUpperCase()}</span>
    </motion.button>
  )
}

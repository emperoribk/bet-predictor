import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, ChevronDown, ChevronUp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { decodeBookingCode, fetchLiveStats } from '../api/betApi'
import useBetStore from '../store/useBetStore'
import { useMatchAlerts } from '../hooks/useMatchAlerts'
import MatchDetailScreen from './MatchDetailScreen'

// ─── Loading steps ─────────────────────────────────────────
const STEPS = [
  'Connecting to SportyBet',
  'Loading bet details',
  'Fetching live match data',
  'Building your tracker',
]

function LoadingPhase({ code }) {
  const [step, setStep] = useState(0)

  useEffect(() => {
    const timings = [600, 1100, 1700, 2400]
    const timers = timings.map((ms, i) =>
      setTimeout(() => setStep(s => Math.max(s, i + 1)), ms)
    )
    return () => timers.forEach(clearTimeout)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '80vh', padding: '0 32px' }}>
      {/* Spinner */}
      <motion.div
        animate={{ rotate: 360 }}
        transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
        style={{ width: 48, height: 48, border: '3px solid rgba(0,200,255,0.15)', borderTopColor: 'var(--cyan)', borderRadius: '50%', marginBottom: 28 }}
      />

      {/* Code */}
      <p className="font-display" style={{ fontSize: 32, fontWeight: 700, letterSpacing: 3, color: 'var(--cyan)', marginBottom: 32 }}>
        {code}
      </p>

      {/* Steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, width: '100%', maxWidth: 280 }}>
        {STEPS.map((label, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: step > i ? 1 : 0.25, x: 0 }}
            transition={{ delay: i * 0.15 }}
            style={{ display: 'flex', alignItems: 'center', gap: 12 }}
          >
            <div style={{
              width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
              background: step > i ? 'rgba(0,210,120,0.15)' : 'rgba(74,85,104,0.2)',
              border: `1.5px solid ${step > i ? 'var(--green)' : 'var(--dim)'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 12, color: step > i ? 'var(--green)' : 'var(--dim)',
            }}>
              {step > i ? '✓' : i + 1}
            </div>
            <span style={{ fontSize: 14, color: step > i ? 'var(--text)' : 'var(--muted)' }}>{label}</span>
          </motion.div>
        ))}
      </div>
    </div>
  )
}

// ─── Status helpers ─────────────────────────────────────────
const STATUS_META = {
  SAFE:      { label: 'Safe',      color: 'var(--green)', bg: 'rgba(0,210,120,0.12)' },
  WON:       { label: 'Won',       color: 'var(--green)', bg: 'rgba(0,210,120,0.12)' },
  DANGER:    { label: 'Danger',    color: 'var(--amber)', bg: 'rgba(255,180,0,0.12)' },
  CRITICAL:  { label: 'Critical',  color: 'var(--red)',   bg: 'rgba(255,82,82,0.15)' },
  LOST:      { label: 'Lost',      color: 'var(--red)',   bg: 'rgba(255,82,82,0.12)' },
  PENDING:   { label: 'Pending',   color: 'var(--muted)', bg: 'rgba(74,85,104,0.2)'  },
  VIRTUAL:   { label: 'Virtual',   color: '#818cf8',      bg: 'rgba(99,102,241,0.15)'},
  UNTRACKED: { label: 'Unknown',   color: 'var(--muted)', bg: 'rgba(74,85,104,0.15)' },
}

function borderFor(s) {
  const map = { SAFE:'var(--green)', WON:'var(--green)', DANGER:'var(--amber)', CRITICAL:'var(--red)', LOST:'var(--red)', PENDING:'var(--dim)', VIRTUAL:'#818cf8', UNTRACKED:'var(--dim)' }
  return map[s] || 'var(--dim)'
}

function isLive(match) {
  return ['IN_PLAY','PAUSED','EXTRA_TIME','PENALTY_SHOOTOUT'].includes(match.match_status)
}

// ─── Match Card ─────────────────────────────────────────────
function MatchCard({ match, index, onSelect }) {
  const [expanded, setExpanded] = useState(false)
  const s = match.analysis_status || 'PENDING'
  const meta = STATUS_META[s] || STATUS_META.PENDING
  const live = isLive(match)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06 }}
      className={`glass ${live ? 'live-glow' : ''}`}
      style={{ borderLeft: `3px solid ${borderFor(s)}`, overflow: 'hidden' }}
    >
      <button
        onClick={() => {
          if (match.fixture_id && onSelect) {
            onSelect({ fixtureId: match.fixture_id, homeTeam: match.home_team, awayTeam: match.away_team, matchStatus: match.match_status })
          } else {
            setExpanded(e => !e)
          }
        }}
        style={{ width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: '14px 16px', textAlign: 'left' }}
      >
        {/* League row */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {live && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'rgba(255,180,0,0.12)', borderRadius: 20, padding: '2px 8px' }}>
                <span className="dot-pulse" style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} />
                <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--amber)' }}>
                  {match.minute ? `${match.minute}'` : 'LIVE'}
                </span>
              </span>
            )}
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>
              {match.match_status === 'SCHEDULED' || match.match_status === 'TIMED' ? 'Upcoming' : match.match_status || 'Upcoming'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="badge" style={{ background: meta.bg, color: meta.color }}>{meta.label}</span>
            {expanded ? <ChevronUp size={14} color="var(--muted)" /> : <ChevronDown size={14} color="var(--muted)" />}
          </div>
        </div>

        {/* Teams + score */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ flex: 1 }}>
            <p className="font-display" style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)', lineHeight: 1.3 }}>
              {match.home_team}
            </p>
            <p style={{ fontSize: 11, color: 'var(--dim)', margin: '2px 0' }}>vs</p>
            <p className="font-display" style={{ fontSize: 17, fontWeight: 700, color: 'var(--text)', lineHeight: 1.3 }}>
              {match.away_team}
            </p>
          </div>
          {live && match.score && match.score !== 'None-None' && (
            <motion.div
              animate={live ? { opacity: [1, 0.6, 1] } : {}}
              transition={{ duration: 2, repeat: Infinity }}
              className="font-display"
              style={{ fontSize: 26, fontWeight: 700, color: '#ffffff', letterSpacing: 2, marginLeft: 12, textShadow: '0 0 20px rgba(92,217,255,0.5)' }}
            >
              {match.score.replace('None', '0').replace('-', ' – ')}
            </motion.div>
          )}
        </div>

        {/* Pick */}
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>Your pick:</span>
          <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--cyan)' }}>{match.user_pick || '—'}</span>
        </div>
      </button>

      {/* Expanded stats */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            style={{ overflow: 'hidden', borderTop: '1px solid var(--border)', padding: '12px 16px' }}
          >
            {/* Analysis reason */}
            {match.analysis_reason && (
              <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12, lineHeight: 1.5 }}>
                {match.analysis_reason}
              </p>
            )}

            {/* Possession bar */}
            {match.possession_home != null && (
              <StatBarRow
                label="Possession"
                homeVal={match.possession_home}
                awayVal={match.possession_away}
                unit="%"
              />
            )}
            {match.shots_on_target_home != null && (
              <StatBarRow
                label="Shots on target"
                homeVal={match.shots_on_target_home}
                awayVal={match.shots_on_target_away}
              />
            )}

            {/* Events */}
            {match.events?.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <p style={{ fontSize: 10, color: 'var(--dim)', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>Events</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {match.events.slice(0, 8).map((ev, i) => (
                    <EventPill key={i} event={ev} />
                  ))}
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

function StatBarRow({ label, homeVal, awayVal, unit = '' }) {
  const total = (homeVal || 0) + (awayVal || 0) || 1
  const homePct = ((homeVal || 0) / total) * 100
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--cyan)' }}>{homeVal}{unit}</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{label}</span>
        <span style={{ fontSize: 12, color: 'var(--red)' }}>{awayVal}{unit}</span>
      </div>
      <div style={{ height: 4, background: 'var(--dim)', borderRadius: 2, overflow: 'hidden', display: 'flex' }}>
        <div style={{ width: `${homePct}%`, background: 'var(--cyan)', transition: 'width 0.6s' }} />
        <div style={{ flex: 1, background: 'var(--red)' }} />
      </div>
    </div>
  )
}

function EventPill({ event }) {
  const typeColors = {
    GOAL: { bg: 'rgba(0,210,120,0.1)', color: 'var(--green)', emoji: '⚽' },
    RED_CARD: { bg: 'rgba(255,82,82,0.1)', color: 'var(--red)', emoji: '🟥' },
    YELLOW_CARD: { bg: 'rgba(255,180,0,0.1)', color: 'var(--amber)', emoji: '🟨' },
    SUBSTITUTION: { bg: 'rgba(74,85,104,0.15)', color: 'var(--muted)', emoji: '🔄' },
  }
  const t = typeColors[event.type] || typeColors.SUBSTITUTION
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: t.bg, borderRadius: 8, padding: '5px 10px' }}>
      <span style={{ fontSize: 13 }}>{t.emoji}</span>
      <span style={{ fontSize: 11, color: 'var(--muted)' }}>{event.minute}'</span>
      <span style={{ fontSize: 12, color: t.color, flex: 1 }}>{event.player || event.type}</span>
      <span style={{ fontSize: 11, color: 'var(--dim)' }}>{event.team}</span>
    </div>
  )
}

// ─── Notification permission banner ─────────────────────────
function NotifBanner() {
  const [status, setStatus] = useState(() =>
    'Notification' in window ? Notification.permission : 'unsupported'
  )

  if (status === 'granted' || status === 'unsupported') return null

  function requestPermission() {
    Notification.requestPermission().then(p => setStatus(p))
  }

  if (status === 'denied') {
    return (
      <div style={{ background: 'rgba(74,85,104,0.15)', border: '1px solid var(--border)', borderRadius: 12, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 16 }}>🔕</span>
        <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5, margin: 0 }}>
          Notifications blocked. Enable them in your browser settings to get goal & danger alerts.
        </p>
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ background: 'rgba(0,200,255,0.07)', border: '1px solid rgba(0,200,255,0.2)', borderRadius: 12, padding: '12px 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 18 }}>🔔</span>
        <p style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.5, margin: 0 }}>
          Enable alerts for goals, red cards & danger warnings
        </p>
      </div>
      <button
        onClick={requestPermission}
        style={{ flexShrink: 0, background: 'var(--cyan)', color: '#030e08', border: 'none', borderRadius: 8, padding: '6px 14px', fontSize: 12, fontWeight: 700, cursor: 'pointer', fontFamily: 'Rajdhani, sans-serif' }}
      >
        Allow
      </button>
    </motion.div>
  )
}

// ─── Slip header ────────────────────────────────────────────
const DOT_COLOR = {
  SAFE:      'var(--green)',
  WON:       'var(--green)',
  DANGER:    'var(--amber)',
  CRITICAL:  'var(--red)',
  LOST:      'var(--red)',
  PENDING:   'var(--dim)',
  VIRTUAL:   '#818cf8',
  UNTRACKED: 'var(--dim)',
}

function SlipHeader({ matches, accumulatorAlive }) {
  const total   = matches.length
  const safe    = matches.filter(m => ['SAFE','WON'].includes(m.analysis_status)).length
  const danger  = matches.filter(m => ['DANGER','CRITICAL'].includes(m.analysis_status)).length
  const lost    = matches.filter(m => m.analysis_status === 'LOST').length
  const live    = matches.filter(m => isLive(m)).length

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{
        background: 'linear-gradient(135deg, #0c1a2e 0%, #0a1520 100%)',
        border: '1px solid rgba(92,217,255,0.14)',
        borderRadius: 20,
        padding: '20px 20px 16px',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Subtle glow orb behind the number */}
      <div style={{
        position: 'absolute', top: -20, right: -20,
        width: 120, height: 120,
        background: 'radial-gradient(circle, rgba(92,217,255,0.08) 0%, transparent 70%)',
        borderRadius: '50%', pointerEvents: 'none',
      }} />

      {/* Top row: match count hero + accumulator pill */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <span className="font-display" style={{ fontSize: 52, fontWeight: 700, color: '#ffffff', lineHeight: 1, display: 'block' }}>
            {total}
          </span>
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', letterSpacing: 1, textTransform: 'uppercase' }}>
            {total === 1 ? 'match' : 'matches'}
          </span>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6, paddingTop: 4 }}>
          {/* Accumulator */}
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            background: accumulatorAlive ? 'rgba(0,226,135,0.1)' : 'rgba(255,82,82,0.1)',
            border: `1px solid ${accumulatorAlive ? 'rgba(0,226,135,0.25)' : 'rgba(255,82,82,0.25)'}`,
            borderRadius: 20, padding: '4px 12px',
            fontSize: 11, fontWeight: 600,
            color: accumulatorAlive ? 'var(--green)' : 'var(--red)',
          }}>
            {accumulatorAlive ? '✓ Accumulator alive' : '✗ Accumulator dead'}
          </span>

          {/* Live badge */}
          {live > 0 && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              background: 'rgba(255,180,0,0.1)',
              border: '1px solid rgba(255,180,0,0.2)',
              borderRadius: 20, padding: '3px 10px',
              fontSize: 11, fontWeight: 600, color: 'var(--amber)',
            }}>
              <span className="dot-pulse" style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} />
              {live} live
            </span>
          )}
        </div>
      </div>

      {/* Match status dots — one per match */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {matches.map((m, i) => (
          <motion.div
            key={m.id}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ delay: i * 0.06, type: 'spring', stiffness: 300 }}
            title={`${m.home_team} vs ${m.away_team}`}
            style={{
              width: 10, height: 10, borderRadius: '50%',
              background: DOT_COLOR[m.analysis_status] || 'var(--dim)',
              boxShadow: isLive(m) ? `0 0 8px ${DOT_COLOR[m.analysis_status]}` : 'none',
            }}
          />
        ))}
      </div>

      {/* Subtle breakdown text */}
      <div style={{ display: 'flex', gap: 14 }}>
        {safe > 0    && <span style={{ fontSize: 11, color: 'var(--green)'  }}>{safe} safe</span>}
        {danger > 0  && <span style={{ fontSize: 11, color: 'var(--amber)'  }}>{danger} at risk</span>}
        {lost > 0    && <span style={{ fontSize: 11, color: 'var(--red)'    }}>{lost} lost</span>}
        {safe === 0 && danger === 0 && lost === 0 && (
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)' }}>Waiting for matches to start</span>
        )}
      </div>
    </motion.div>
  )
}

// ─── Main TrackerScreen ─────────────────────────────────────
export default function TrackerScreen({ code, onBack }) {
  const addBet = useBetStore(s => s.addBet)
  const updateBetStatus = useBetStore(s => s.updateBetStatus)
  const [decodeDone, setDecodeDone] = useState(false)
  const savedRef = useRef(false)

  // Step 1: decode
  const decodeQuery = useQuery({
    queryKey: ['decode', code],
    queryFn: () => decodeBookingCode(code),
    retry: 1,
    staleTime: Infinity,
  })

  // Step 2: live stats (only after decode)
  const liveQuery = useQuery({
    queryKey: ['live', code],
    queryFn: () => fetchLiveStats(code),
    enabled: decodeDone,
    refetchInterval: 10_000,
    retry: 1,
    staleTime: 0,
  })

  useEffect(() => {
    if (decodeQuery.isSuccess) {
      setTimeout(() => setDecodeDone(true), 2800) // let loading animation play
    }
  }, [decodeQuery.isSuccess])

  // Save to history once we have data
  useEffect(() => {
    if (liveQuery.data && !savedRef.current) {
      savedRef.current = true
      const matches = liveQuery.data.matches || []
      const summary = liveQuery.data.summary || {}
      const liveCount = (summary.SAFE || 0) + (summary.DANGER || 0) + (summary.CRITICAL || 0)
      const wonCount = summary.WON || 0
      const lostCount = summary.LOST || 0
      const overallStatus = lostCount > 0 ? 'lost' : liveCount > 0 ? 'live' : wonCount === matches.length ? 'won' : 'pending'

      addBet({ code, legs: matches.length, status: overallStatus })
      updateBetStatus(code, overallStatus)
    }
  }, [liveQuery.data, code, addBet, updateBetStatus])

  const showLoading = !decodeDone || decodeQuery.isLoading
  const data = liveQuery.data
  const error = decodeQuery.error || liveQuery.error

  if (decodeQuery.isError) {
    return (
      <div className="screen-content" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '70vh', padding: '0 24px', textAlign: 'center' }}>
        <p style={{ fontSize: 32, marginBottom: 12 }}>⚠️</p>
        <p className="font-display" style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>Could not load slip</p>
        <p style={{ fontSize: 14, color: 'var(--muted)', marginBottom: 24 }}>{decodeQuery.error?.message || 'Check the booking code and try again.'}</p>
        <button className="btn-ghost" onClick={onBack}>← Back</button>
      </div>
    )
  }

  return (
    <div className="screen-content">
      {/* Top bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 16px 12px' }}>
        <button
          onClick={onBack}
          style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 14, padding: '6px 0' }}
        >
          <ArrowLeft size={16} /> Back
        </button>
        <span className="font-display" style={{ fontSize: 18, fontWeight: 700, letterSpacing: 2, color: 'var(--cyan)' }}>{code}</span>
        <div style={{ width: 40 }} />
      </div>

      <AnimatePresence mode="wait">
        {showLoading ? (
          <motion.div key="loading" initial={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <LoadingPhase code={code} />
          </motion.div>
        ) : (
          <motion.div key="dashboard" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            {data ? <Dashboard data={data} code={code} /> : (
              <div style={{ textAlign: 'center', padding: '48px 24px' }}>
                <p style={{ color: 'var(--muted)', fontSize: 14 }}>Loading live data…</p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Dashboard({ data, code }) {
  const matches = data.matches || []
  const summary = data.summary || {}
  const [selectedFixture, setSelectedFixture] = useState(null)

  // Fire browser notifications when goals / cards / status changes are detected
  useMatchAlerts(matches)

  if (selectedFixture) {
    return (
      <MatchDetailScreen
        fixtureId={selectedFixture.fixtureId}
        homeTeam={selectedFixture.homeTeam}
        awayTeam={selectedFixture.awayTeam}
        matchStatus={selectedFixture.matchStatus}
        onBack={() => setSelectedFixture(null)}
      />
    )
  }

  return (
    <div style={{ padding: '0 16px 32px', display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Notification permission nudge */}
      <NotifBanner />

      {/* Slip header */}
      <SlipHeader matches={matches} accumulatorAlive={data.accumulator_alive} />

      {/* Match cards */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {matches.map((m, i) => (
          <MatchCard key={m.id} match={m} index={i} onSelect={setSelectedFixture} />
        ))}
      </div>

      {/* Virtual/untracked notice */}
      {(summary.VIRTUAL > 0 || summary.UNTRACKED > 0) && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="glass"
          style={{ padding: '12px 14px', borderLeft: '3px solid #818cf8' }}
        >
          <p style={{ fontSize: 12, color: '#818cf8', fontWeight: 600, marginBottom: 4 }}>Some matches can't be tracked</p>
          <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
            SRL (virtual) and unsupported league matches have no live data. Only real top-league matches are tracked.
          </p>
        </motion.div>
      )}
    </div>
  )
}

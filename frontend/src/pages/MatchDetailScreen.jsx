import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowLeft } from 'lucide-react'
import { fetchMatchDetail, fetchMatchPreview } from '../api/betApi'

// ─── helpers ────────────────────────────────────────────────
function formatKickoff(utcDate) {
  if (!utcDate) return ''
  try {
    return new Date(utcDate).toLocaleString([], {
      weekday: 'short', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return '' }
}

function statusColor(s) {
  if (!s) return 'var(--muted)'
  if (['IN_PLAY', 'PAUSED', 'EXTRA_TIME', 'PENALTY_SHOOTOUT'].includes(s)) return 'var(--amber)'
  if (['FINISHED', 'AWARDED'].includes(s)) return 'var(--dim)'
  return 'var(--muted)'
}

function statusLabel(s) {
  const map = {
    IN_PLAY: 'LIVE', PAUSED: 'HT', EXTRA_TIME: 'ET',
    PENALTY_SHOOTOUT: 'PENS', FINISHED: 'FT', AWARDED: 'FT',
    SCHEDULED: 'Upcoming', TIMED: 'Upcoming', POSTPONED: 'PPD',
    CANCELLED: 'Cancelled', SUSPENDED: 'Susp',
  }
  return map[s] || s || '—'
}

const UPCOMING = new Set(['SCHEDULED', 'TIMED'])
const LIVE_SET  = new Set(['IN_PLAY', 'PAUSED', 'EXTRA_TIME', 'PENALTY_SHOOTOUT'])

// ─── Section heading ──────────────────────────────────────────
function SectionTitle({ children }) {
  return (
    <p style={{
      fontSize: 11, fontWeight: 700, letterSpacing: 1.2,
      textTransform: 'uppercase', color: 'var(--dim)', marginBottom: 14,
    }}>
      {children}
    </p>
  )
}

// ─── Stat bar (live/finished) ─────────────────────────────────
function StatBar({ label, homeVal, awayVal, unit = '', decimals = 0, highlight = false }) {
  if (homeVal == null && awayVal == null) return null
  const h = homeVal ?? 0
  const a = awayVal ?? 0
  const total = h + a || 1
  const homePct = (h / total) * 100
  const awayPct = (a / total) * 100
  const fmt = (v) => decimals > 0 ? Number(v).toFixed(decimals) : v
  return (
    <div style={{ marginBottom: highlight ? 18 : 14, padding: highlight ? '10px 12px' : 0, background: highlight ? 'rgba(92,217,255,0.04)' : 'transparent', borderRadius: highlight ? 10 : 0, border: highlight ? '1px solid rgba(92,217,255,0.1)' : 'none' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <span className="font-display" style={{ fontSize: highlight ? 18 : 15, fontWeight: 700, color: 'var(--cyan)', minWidth: 36 }}>
          {fmt(h)}{unit}
        </span>
        <span style={{ fontSize: 11, color: highlight ? 'var(--cyan)' : 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.8, flex: 1, textAlign: 'center', fontWeight: highlight ? 600 : 400 }}>
          {label}
        </span>
        <span className="font-display" style={{ fontSize: highlight ? 18 : 15, fontWeight: 700, color: '#ff7eb3', minWidth: 36, textAlign: 'right' }}>
          {fmt(a)}{unit}
        </span>
      </div>
      <div style={{ display: 'flex', height: highlight ? 6 : 5, borderRadius: 3, overflow: 'hidden', background: 'rgba(255,255,255,0.05)' }}>
        <motion.div initial={{ width: 0 }} animate={{ width: `${homePct}%` }} transition={{ duration: 0.7, ease: 'easeOut' }}
          style={{ background: 'var(--cyan)', borderRadius: '3px 0 0 3px' }} />
        <motion.div initial={{ width: 0 }} animate={{ width: `${awayPct}%` }} transition={{ duration: 0.7, ease: 'easeOut' }}
          style={{ background: '#ff7eb3', borderRadius: '0 3px 3px 0' }} />
      </div>
    </div>
  )
}

// ─── Insight card ─────────────────────────────────────────────
function InsightCard({ insight, index }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.07, duration: 0.35 }}
      style={{ background: 'rgba(14,20,32,0.85)', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 14, padding: '14px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}
    >
      <span style={{ fontSize: 24, lineHeight: 1, flexShrink: 0, marginTop: 1 }}>{insight.icon}</span>
      <div>
        <p className="font-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 4, lineHeight: 1.3 }}>
          {insight.title}
        </p>
        <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6, margin: 0 }}>
          {insight.body}
        </p>
      </div>
    </motion.div>
  )
}

// ─── Goal item ────────────────────────────────────────────────
function GoalItem({ goal, isHome }) {
  const suffix = goal.type === 'OWN_GOAL' ? ' (OG)' : goal.type === 'PENALTY' ? ' (P)' : ''
  return (
    <div style={{ display: 'flex', flexDirection: isHome ? 'row' : 'row-reverse', alignItems: 'center', gap: 10, paddingBottom: 12 }}>
      <span style={{ background: 'rgba(255,180,0,0.12)', color: 'var(--amber)', fontSize: 11, fontWeight: 700, borderRadius: 8, padding: '3px 7px', flexShrink: 0, minWidth: 36, textAlign: 'center' }}>
        {goal.minute ?? '?'}'
      </span>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', flexShrink: 0, boxShadow: '0 0 6px rgba(0,210,120,0.5)' }} />
      <div style={{ textAlign: isHome ? 'left' : 'right', flex: 1 }}>
        <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', margin: 0 }}>⚽ {goal.scorer || 'Unknown'}{suffix}</p>
        {goal.assist && <p style={{ fontSize: 11, color: 'var(--muted)', margin: '1px 0 0' }}>Assist: {goal.assist}</p>}
      </div>
    </div>
  )
}

// ─── Booking item ─────────────────────────────────────────────
function BookingItem({ booking }) {
  const isRed = booking.card === 'RED_CARD' || booking.card === 'YELLOW_RED_CARD'
  const emoji = isRed ? '🟥' : '🟨'
  const color = isRed ? 'var(--red)' : 'var(--amber)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: isRed ? 'rgba(255,82,82,0.07)' : 'rgba(255,180,0,0.07)', border: `1px solid ${isRed ? 'rgba(255,82,82,0.15)' : 'rgba(255,180,0,0.15)'}`, borderRadius: 10, padding: '8px 12px' }}>
      <span style={{ fontSize: 16, flexShrink: 0 }}>{emoji}</span>
      <span style={{ fontSize: 11, color: 'var(--amber)', fontWeight: 700, flexShrink: 0 }}>{booking.minute ?? '?'}'</span>
      <span style={{ fontSize: 13, color, fontWeight: 600, flex: 1 }}>{booking.player || '—'}</span>
      <span style={{ fontSize: 11, color: 'var(--dim)' }}>{booking.team}</span>
    </div>
  )
}

// ─── Lineup column ─────────────────────────────────────────────
function LineupColumn({ team, formation, lineup, align = 'left' }) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <p className="font-display" style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4, textAlign: align, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {team}
      </p>
      {formation && <p style={{ fontSize: 11, color: 'var(--cyan)', marginBottom: 10, textAlign: align }}>{formation}</p>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {lineup.map((p, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 7, flexDirection: align === 'right' ? 'row-reverse' : 'row' }}>
            {p.shirt != null && (
              <span style={{ width: 20, height: 20, borderRadius: '50%', background: 'rgba(0,200,255,0.1)', border: '1px solid rgba(0,200,255,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, fontWeight: 700, color: 'var(--cyan)', flexShrink: 0 }}>
                {p.shirt}
              </span>
            )}
            <span style={{ fontSize: 12, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: align }}>
              {p.name || '—'}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Form pill (W / D / L) ────────────────────────────────────
function FormPill({ result }) {
  const colors = {
    W: { bg: 'rgba(0,226,135,0.15)', border: 'rgba(0,226,135,0.3)', color: 'var(--green)' },
    D: { bg: 'rgba(255,180,0,0.12)', border: 'rgba(255,180,0,0.25)', color: 'var(--amber)' },
    L: { bg: 'rgba(255,82,82,0.12)', border: 'rgba(255,82,82,0.25)', color: 'var(--red)' },
  }
  const c = colors[result] || colors.D
  return (
    <span style={{ width: 26, height: 26, borderRadius: '50%', background: c.bg, border: `1.5px solid ${c.border}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: c.color, flexShrink: 0 }}>
      {result}
    </span>
  )
}

// ─── H2H result row ───────────────────────────────────────────
function H2HRow({ match, index }) {
  const homeScore = match.home_score
  const awayScore = match.away_score
  const hasScore = homeScore != null && awayScore != null
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05 }}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}
    >
      <span style={{ fontSize: 10, color: 'var(--dim)', width: 72, flexShrink: 0 }}>{match.date}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--text-soft)', textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{match.home_team}</span>
      <span className="font-display" style={{ fontSize: 13, fontWeight: 700, color: hasScore ? 'var(--cyan)' : 'var(--dim)', flexShrink: 0, minWidth: 40, textAlign: 'center' }}>
        {hasScore ? `${homeScore}–${awayScore}` : 'vs'}
      </span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--text-soft)', textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{match.away_team}</span>
    </motion.div>
  )
}

// ─── Preview section (upcoming matches) ───────────────────────
function MatchPreview({ data }) {
  const h2h = data.h2h || {}
  const homeForm = data.home_form || []
  const awayForm = data.away_form || []
  const homeLineup = data.home_lineup || []
  const awayLineup = data.away_lineup || []
  const hasLineup = homeLineup.length > 0 || awayLineup.length > 0
  const h2hMatches = h2h.matches || []

  return (
    <>
      {/* H2H summary bar */}
      {h2h.played > 0 && (
        <div className="glass" style={{ padding: '18px 16px' }}>
          <SectionTitle>Head to Head — Last {h2h.played} meetings</SectionTitle>

          {/* Win bar */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span className="font-display" style={{ fontSize: 22, fontWeight: 700, color: 'var(--cyan)' }}>{h2h.home_wins}</span>
              <span style={{ fontSize: 12, color: 'var(--muted)', alignSelf: 'center' }}>{h2h.draws} draws</span>
              <span className="font-display" style={{ fontSize: 22, fontWeight: 700, color: '#ff7eb3' }}>{h2h.away_wins}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--muted)', maxWidth: '40%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {data.home_short}
              </span>
              <span style={{ fontSize: 11, color: 'var(--muted)', maxWidth: '40%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' }}>
                {data.away_short}
              </span>
            </div>
            {/* Bar */}
            {(h2h.home_wins + h2h.away_wins + h2h.draws) > 0 && (() => {
              const total = h2h.played || 1
              const hp = (h2h.home_wins / total) * 100
              const dp = (h2h.draws / total) * 100
              const ap = (h2h.away_wins / total) * 100
              return (
                <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', gap: 2 }}>
                  <div style={{ width: `${hp}%`, background: 'var(--cyan)', transition: 'width 0.7s', borderRadius: 3 }} />
                  <div style={{ width: `${dp}%`, background: 'rgba(255,180,0,0.5)', transition: 'width 0.7s', borderRadius: 3 }} />
                  <div style={{ width: `${ap}%`, background: '#ff7eb3', transition: 'width 0.7s', borderRadius: 3 }} />
                </div>
              )
            })()}
          </div>

          {/* Recent H2H results */}
          {h2hMatches.length > 0 && (
            <div>
              {h2hMatches.slice(0, 6).map((m, i) => <H2HRow key={i} match={m} index={i} />)}
            </div>
          )}
        </div>
      )}

      {/* Recent form */}
      {(homeForm.length > 0 || awayForm.length > 0) && (
        <div className="glass" style={{ padding: '18px 16px' }}>
          <SectionTitle>Recent Form</SectionTitle>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {[
              { label: data.home_short, form: homeForm },
              { label: data.away_short, form: awayForm },
            ].map(({ label, form }) => form.length > 0 && (
              <div key={label}>
                <p className="font-display" style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 10 }}>{label}</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {form.map((f, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <FormPill result={f.result} />
                      <span style={{ fontSize: 11, color: 'var(--cyan)', width: 28, flexShrink: 0, fontWeight: 600 }}>{f.venue}</span>
                      <span style={{ fontSize: 12, color: 'var(--text-soft)', flex: 1 }}>vs {f.opponent}</span>
                      <span className="font-display" style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)' }}>{f.score}</span>
                      <span style={{ fontSize: 10, color: 'var(--dim)', width: 68, textAlign: 'right' }}>{f.date}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Lineups — shown only when released */}
      {hasLineup ? (
        <div className="glass" style={{ padding: '18px 16px' }}>
          <SectionTitle>Starting Lineups</SectionTitle>
          <div style={{ display: 'flex', gap: 16 }}>
            <LineupColumn team={data.home_short} formation={data.home_formation} lineup={homeLineup} align="left" />
            <div style={{ width: 1, background: 'var(--border)', flexShrink: 0 }} />
            <LineupColumn team={data.away_short} formation={data.away_formation} lineup={awayLineup} align="right" />
          </div>
        </div>
      ) : (
        <div className="glass" style={{ padding: '16px', borderLeft: '3px solid rgba(92,217,255,0.2)' }}>
          <p style={{ fontSize: 12, color: 'var(--cyan)', fontWeight: 600, marginBottom: 4 }}>Lineups not yet released</p>
          <p style={{ fontSize: 12, color: 'var(--muted)' }}>Starting XI are usually confirmed 1–2 hours before kickoff.</p>
        </div>
      )}
    </>
  )
}

// ─── Main component ───────────────────────────────────────────
export default function MatchDetailScreen({ fixtureId, homeTeam, awayTeam, matchStatus, onBack }) {
  const isUpcoming = UPCOMING.has(matchStatus)

  const detailQuery = useQuery({
    queryKey: ['match-detail', fixtureId],
    queryFn: () => fetchMatchDetail(fixtureId),
    staleTime: 30_000,
    retry: 1,
    enabled: !isUpcoming,
  })

  const previewQuery = useQuery({
    queryKey: ['match-preview', fixtureId],
    queryFn: () => fetchMatchPreview(fixtureId),
    staleTime: 5 * 60_000,  // 5 min — H2H doesn't change
    retry: 1,
    enabled: isUpcoming,
  })

  const query = isUpcoming ? previewQuery : detailQuery
  const data  = query.data

  // ── Header bar ───────────────────────────────────────────────
  const headerBar = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 16px 12px' }}>
      <button onClick={onBack} style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 14, padding: '6px 0' }}>
        <ArrowLeft size={16} /> Back
      </button>
      <span style={{ fontSize: 12, color: 'var(--dim)', textAlign: 'right', maxWidth: 160, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {data?.competition || ''}
      </span>
    </div>
  )

  if (query.isLoading) {
    return (
      <div className="screen-content">
        {headerBar}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', gap: 20 }}>
          <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
            style={{ width: 40, height: 40, border: '3px solid rgba(0,200,255,0.15)', borderTopColor: 'var(--cyan)', borderRadius: '50%' }} />
          <p style={{ fontSize: 14, color: 'var(--muted)' }}>{isUpcoming ? 'Loading preview…' : 'Loading match…'}</p>
        </div>
      </div>
    )
  }

  if (query.isError || !data) {
    return (
      <div className="screen-content">
        {headerBar}
        <div style={{ textAlign: 'center', padding: '48px 24px' }}>
          <p style={{ fontSize: 32, marginBottom: 12 }}>⚠️</p>
          <p className="font-display" style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>Data unavailable</p>
          <p style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 24 }}>Could not load match information.</p>
          <button className="btn-ghost" onClick={onBack}>← Back</button>
        </div>
      </div>
    )
  }

  const effectiveStatus = data.status || matchStatus
  const isLive = LIVE_SET.has(effectiveStatus)
  const hasScore = data.score_home != null
  const scoreHome = data.score_home ?? 0
  const scoreAway = data.score_away ?? 0

  return (
    <div className="screen-content">
      {headerBar}

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
        style={{ padding: '0 16px 40px', display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* ── Score / kickoff hero ─────────────────────────────── */}
        <div className="glass" style={{ padding: '20px 16px', textAlign: 'center' }}>
          <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'center' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: isLive ? 'rgba(255,180,0,0.12)' : 'rgba(255,255,255,0.05)', borderRadius: 20, padding: '3px 10px' }}>
              {isLive && <span className="dot-pulse" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} />}
              <span style={{ fontSize: 11, fontWeight: 700, color: statusColor(effectiveStatus) }}>
                {isLive && data.minute ? `${data.minute}'` : statusLabel(effectiveStatus)}
              </span>
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
            <p className="font-display" style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', flex: 1, textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {data.home_short || homeTeam}
            </p>
            {hasScore ? (
              <motion.div animate={isLive ? { opacity: [1, 0.55, 1] } : {}} transition={{ duration: 2, repeat: Infinity }}
                className="font-display" style={{ fontSize: 38, fontWeight: 800, color: 'var(--cyan)', letterSpacing: 4, flexShrink: 0 }}>
                {scoreHome} – {scoreAway}
              </motion.div>
            ) : (
              <span className="font-display" style={{ fontSize: 22, color: 'var(--dim)', flexShrink: 0 }}>vs</span>
            )}
            <p className="font-display" style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)', flex: 1, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {data.away_short || awayTeam}
            </p>
          </div>

          {data.ht_home != null && (
            <p style={{ fontSize: 11, color: 'var(--dim)', marginTop: 6 }}>HT {data.ht_home} – {data.ht_away}</p>
          )}

          {/* xG badge — shown when Understat data is available */}
          {data.stats?.xg_home != null && (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginTop: 10 }}>
              <div style={{ textAlign: 'center' }}>
                <p className="font-display" style={{ fontSize: 16, fontWeight: 700, color: 'var(--cyan)' }}>
                  {Number(data.stats.xg_home).toFixed(2)}
                </p>
                <p style={{ fontSize: 9, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>xG</p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <span style={{ fontSize: 10, color: 'rgba(92,217,255,0.3)', fontWeight: 600, letterSpacing: 2 }}>EXPECTED</span>
              </div>
              <div style={{ textAlign: 'center' }}>
                <p className="font-display" style={{ fontSize: 16, fontWeight: 700, color: '#ff7eb3' }}>
                  {Number(data.stats.xg_away).toFixed(2)}
                </p>
                <p style={{ fontSize: 9, color: 'var(--muted)', letterSpacing: 1, textTransform: 'uppercase' }}>xG</p>
              </div>
            </div>
          )}

          {data.kickoff && (
            <p style={{ fontSize: 11, color: 'var(--dim)', marginTop: 4 }}>{formatKickoff(data.kickoff)}</p>
          )}
        </div>

        {/* ── Upcoming: H2H + form + lineup preview ───────────── */}
        {isUpcoming && <MatchPreview data={data} />}

        {/* ── Live / Finished: stats + goals + bookings + lineups ─ */}
        {!isUpcoming && (() => {
          const s = data.stats || {}
          const statRows = [
            // xG rows first (most important)
            s.xg_home != null || s.xg_away != null
              ? { label: 'xG (Expected Goals)', homeVal: s.xg_home, awayVal: s.xg_away, decimals: 2, highlight: true }
              : null,
            s.big_chances_home != null || s.big_chances_away != null
              ? { label: 'Big Chances (xG ≥ 0.25)', homeVal: s.big_chances_home, awayVal: s.big_chances_away }
              : null,
            // Standard stats
            { label: 'Possession', homeVal: s.possession_home, awayVal: s.possession_away, unit: '%' },
            { label: 'Shots', homeVal: s.shots_total_home, awayVal: s.shots_total_away },
            { label: 'Shots on Target', homeVal: s.shots_on_target_home, awayVal: s.shots_on_target_away },
            { label: 'Corners', homeVal: s.corners_home, awayVal: s.corners_away },
            { label: 'Saves', homeVal: s.saves_home, awayVal: s.saves_away },
            { label: 'Fouls', homeVal: s.fouls_home, awayVal: s.fouls_away },
            { label: 'Offsides', homeVal: s.offsides_home, awayVal: s.offsides_away },
            // Attacking patterns (from Understat)
            s.crosses_to_shot_home != null || s.crosses_to_shot_away != null
              ? { label: 'Crosses → Shot', homeVal: s.crosses_to_shot_home, awayVal: s.crosses_to_shot_away }
              : null,
            s.through_balls_home != null || s.through_balls_away != null
              ? { label: 'Through Balls → Shot', homeVal: s.through_balls_home, awayVal: s.through_balls_away }
              : null,
          ].filter(Boolean).filter(r => r.homeVal != null || r.awayVal != null)

          const goals = data.goals || []
          const bookings = data.bookings || []
          const insights = data.insights || []
          const homeLineup = data.home_lineup || []
          const awayLineup = data.away_lineup || []
          const hasLineup = homeLineup.length > 0 || awayLineup.length > 0

          return (
            <>
              {statRows.length > 0 && (
                <div className="glass" style={{ padding: '18px 16px' }}>
                  <SectionTitle>Match Statistics</SectionTitle>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                    <span className="font-display" style={{ fontSize: 12, fontWeight: 700, color: 'var(--cyan)', maxWidth: '42%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {data.home_short || homeTeam}
                    </span>
                    <span className="font-display" style={{ fontSize: 12, fontWeight: 700, color: '#ff7eb3', maxWidth: '42%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' }}>
                      {data.away_short || awayTeam}
                    </span>
                  </div>
                  {statRows.map(r => (
                    <StatBar key={r.label} label={r.label} homeVal={r.homeVal} awayVal={r.awayVal}
                      unit={r.unit || ''} decimals={r.decimals || 0} highlight={r.highlight || false} />
                  ))}
                </div>
              )}

              {insights.length > 0 && (
                <div>
                  <SectionTitle>Match Analysis</SectionTitle>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {insights.map((ins, i) => <InsightCard key={i} insight={ins} index={i} />)}
                  </div>
                </div>
              )}

              {goals.length > 0 && (
                <div className="glass" style={{ padding: '18px 16px' }}>
                  <SectionTitle>Goals</SectionTitle>
                  <div style={{ borderLeft: '2px solid rgba(0,210,120,0.2)', paddingLeft: 12, marginLeft: 4 }}>
                    {goals.map((g, i) => <GoalItem key={i} goal={g} isHome={g.team === data.home_team} />)}
                  </div>
                </div>
              )}

              {bookings.length > 0 && (
                <div className="glass" style={{ padding: '18px 16px' }}>
                  <SectionTitle>Cards</SectionTitle>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {bookings.map((b, i) => <BookingItem key={i} booking={b} />)}
                  </div>
                </div>
              )}

              {hasLineup && (
                <div className="glass" style={{ padding: '18px 16px' }}>
                  <SectionTitle>Starting Lineups</SectionTitle>
                  <div style={{ display: 'flex', gap: 16 }}>
                    <LineupColumn team={data.home_short || homeTeam} formation={data.home_formation} lineup={homeLineup} align="left" />
                    <div style={{ width: 1, background: 'var(--border)', flexShrink: 0 }} />
                    <LineupColumn team={data.away_short || awayTeam} formation={data.away_formation} lineup={awayLineup} align="right" />
                  </div>
                </div>
              )}
            </>
          )
        })()}

      </motion.div>
    </div>
  )
}

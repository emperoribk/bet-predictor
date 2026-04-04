import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react'
import { fetchLiveNow, fetchMatchesToday, fetchFixturesByDate } from '../api/betApi'
import MatchDetailScreen from './MatchDetailScreen'

/* ── Status helpers ─────────────────────────────────────────────────────────── */
const LIVE_STATUSES      = new Set(['IN_PLAY', 'PAUSED', 'EXTRA_TIME', 'PENALTY_SHOOTOUT'])
const FINISHED_STATUSES  = new Set(['FINISHED', 'AWARDED'])
const VOID_STATUSES      = new Set(['CANCELLED', 'SUSPENDED', 'POSTPONED'])

function statusLabel(m) {
  if (LIVE_STATUSES.has(m.status))     return 'live'
  if (FINISHED_STATUSES.has(m.status)) return 'finished'
  if (VOID_STATUSES.has(m.status))     return 'void'
  return 'upcoming'
}

function voidLabel(status) {
  if (status === 'POSTPONED')  return 'PST'
  if (status === 'CANCELLED')  return 'CANC'
  if (status === 'SUSPENDED')  return 'SUSP'
  return 'N/A'
}

function formatKickoff(utcDate) {
  if (!utcDate) return ''
  try { return new Date(utcDate).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  catch { return '' }
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

function mergeAndDedupe(liveMatches, todayMatches) {
  const map = new Map()
  for (const m of todayMatches) map.set(m.id, { ...m })
  for (const m of liveMatches)  map.set(m.id, { ...m })
  return Array.from(map.values())
}

// Priority by competition code (lower = shown first)
const CODE_PRIORITY = {
  CL: 1, EL: 2, UECL: 3, PL: 4, PD: 5, SA: 6, BL1: 7, FL1: 8,
  PPL: 9, DED: 10, ELC: 11, BL2: 12, FAC: 13, EFL: 14,
  CDR: 15, DFB: 16, CIT: 17, CLI: 18, CSA: 19, MLS: 20, BSA: 21, BSB: 22,
}

// Priority by competition name + optional area check
// Used as fallback when the API code doesn't match CODE_PRIORITY
function getGroupPriority(code, name, area) {
  if (CODE_PRIORITY[code] !== undefined) return CODE_PRIORITY[code]
  // European cups
  if (name.includes('Champions League'))          return 1
  if (name.includes('Europa League') && !name.includes('Conference')) return 2
  if (name.includes('Conference League'))          return 3
  // Top 6 leagues — area-disambiguated so e.g. Austrian "Bundesliga" stays lower
  if (name === 'Premier League'   && area === 'England')  return 4
  if (name === 'Primera Division' || name === 'La Liga')  return 5
  if (name === 'Serie A'          && area === 'Italy')    return 6
  if (name === 'Bundesliga'       && area === 'Germany')  return 7
  if (name === 'Ligue 1'          && area === 'France')   return 8
  if (name === 'Primeira Liga'    && area === 'Portugal') return 9
  if (name === 'Eredivisie')                              return 10
  if (name === 'Championship')                            return 11
  if (name === '2. Bundesliga')                           return 12
  // Domestic cups
  if (name === 'FA Cup')                          return 13
  if (name.includes('Football League Cup') || name.includes('Carabao') || name.includes('EFL Cup')) return 14
  if (name.includes('Copa del Rey'))              return 15
  if (name.includes('DFB'))                       return 16
  if (name.includes('Coppa Italia'))              return 17
  // Americas
  if (name.includes('Copa Libertadores'))         return 18
  if (name.includes('Copa Sudamericana'))         return 19
  if (name.includes('Major League') || name === 'MLS') return 20
  if (name.includes('Brasileiro') && name.includes('Série A')) return 21
  if (name.includes('Brasileiro') && name.includes('Série B')) return 22
  return 999
}

function groupByCompetition(matches) {
  const order = new Map()
  for (const m of matches) {
    const code = m.competition_code || ''
    const key  = code || `${m.competition}|${m.area}`
    if (!order.has(key)) {
      order.set(key, { competition: m.competition, area: m.area, code, matches: [] })
    }
    order.get(key).matches.push(m)
  }
  return Array.from(order.values()).sort((a, b) => {
    const aLive = a.matches.some(m => LIVE_STATUSES.has(m.status))
    const bLive = b.matches.some(m => LIVE_STATUSES.has(m.status))
    if (aLive && !bLive) return -1
    if (!aLive && bLive) return 1
    const aPri = getGroupPriority(a.code, a.competition, a.area)
    const bPri = getGroupPriority(b.code, b.competition, b.area)
    if (aPri !== bPri) return aPri - bPri
    return a.competition.localeCompare(b.competition)
  })
}

/* ══════════════════════════════════════════════════════════════════════════════
   Mini Calendar
══════════════════════════════════════════════════════════════════════════════ */
const DAY_LABELS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
const MONTH_NAMES = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
]

function MiniCalendar({ selected, onSelect }) {
  const today = todayStr()
  const [viewYear, setViewYear]   = useState(() => new Date(today).getFullYear())
  const [viewMonth, setViewMonth] = useState(() => new Date(today).getMonth())  // 0-based

  function prevMonth() {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(y => y - 1) }
    else setViewMonth(m => m - 1)
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(y => y + 1) }
    else setViewMonth(m => m + 1)
  }

  // Build the day grid (Mon-start, 6 rows max)
  const days = useMemo(() => {
    const firstDay = new Date(viewYear, viewMonth, 1)
    // Mon=0 offset: getDay() is 0=Sun, so shift
    const startOffset = (firstDay.getDay() + 6) % 7  // Mon-aligned
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
    const cells = []
    for (let i = 0; i < startOffset; i++) cells.push(null)
    for (let d = 1; d <= daysInMonth; d++) {
      const iso = `${viewYear}-${String(viewMonth + 1).padStart(2,'0')}-${String(d).padStart(2,'0')}`
      cells.push(iso)
    }
    // Pad to complete last row
    while (cells.length % 7 !== 0) cells.push(null)
    return cells
  }, [viewYear, viewMonth])

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid var(--border)',
      borderRadius: 16,
      padding: '14px 16px',
      marginBottom: 16,
    }}>
      {/* Month header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <button
          onClick={prevMonth}
          style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', padding: 4, display: 'flex' }}
        >
          <ChevronLeft size={18} />
        </button>
        <span className="font-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
          {MONTH_NAMES[viewMonth]} {viewYear}
        </span>
        <button
          onClick={nextMonth}
          style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', padding: 4, display: 'flex' }}
        >
          <ChevronRight size={18} />
        </button>
      </div>

      {/* Day-of-week headers */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', marginBottom: 4 }}>
        {DAY_LABELS.map(d => (
          <div key={d} style={{ textAlign: 'center', fontSize: 10, fontWeight: 600, color: 'var(--dim)', paddingBottom: 4 }}>
            {d}
          </div>
        ))}
      </div>

      {/* Day cells */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '3px 0' }}>
        {days.map((iso, idx) => {
          if (!iso) return <div key={`empty-${idx}`} />
          const isToday    = iso === today
          const isSelected = iso === selected
          return (
            <button
              key={iso}
              onClick={() => onSelect(iso)}
              style={{
                background: isSelected
                  ? 'var(--cyan)'
                  : isToday
                  ? 'rgba(92,217,255,0.12)'
                  : 'none',
                border: isToday && !isSelected ? '1px solid rgba(92,217,255,0.4)' : '1px solid transparent',
                borderRadius: 8,
                color: isSelected ? '#0a0f1a' : isToday ? 'var(--cyan)' : 'var(--text)',
                fontWeight: isToday || isSelected ? 700 : 400,
                fontSize: 13,
                cursor: 'pointer',
                padding: '6px 0',
                textAlign: 'center',
                transition: 'all 0.15s',
              }}
            >
              {parseInt(iso.slice(-2))}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ══════════════════════════════════════════════════════════════════════════════
   Main Fixtures Screen
══════════════════════════════════════════════════════════════════════════════ */
const FILTERS = ['All', 'Live', 'Upcoming', 'Finished']

export default function FixturesScreen() {
  const today = todayStr()
  const [selectedDate, setSelectedDate] = useState(today)
  const [filter, setFilter]             = useState('All')
  const [calendarOpen, setCalendarOpen] = useState(false)
  const [selectedFixture, setSelectedFixture] = useState(null)

  const isToday = selectedDate === today

  /* Live data — only poll when viewing today */
  const liveQuery = useQuery({
    queryKey: ['live-now'],
    queryFn: fetchLiveNow,
    refetchInterval: isToday ? 10_000 : false,
    staleTime: 8_000,
    enabled: isToday,
  })

  /* Today's full schedule */
  const todayQuery = useQuery({
    queryKey: ['matches-today'],
    queryFn: fetchMatchesToday,
    refetchInterval: isToday ? 30_000 : false,
    staleTime: 20_000,
    enabled: isToday,
  })

  /* Any other date via calendar */
  const dateQuery = useQuery({
    queryKey: ['fixtures-date', selectedDate],
    queryFn: () => fetchFixturesByDate(selectedDate),
    staleTime: 5 * 60_000,
    enabled: !isToday,
  })

  /* Build the match list for the current view */
  const allMatches = useMemo(() => {
    if (isToday) {
      return mergeAndDedupe(
        liveQuery.data?.matches  ?? [],
        todayQuery.data?.matches ?? [],
      )
    }
    return dateQuery.data?.matches ?? []
  }, [isToday, liveQuery.data, todayQuery.data, dateQuery.data])

  const liveCount = allMatches.filter(m => LIVE_STATUSES.has(m.status)).length

  const isLoading = isToday
    ? (liveQuery.isLoading || todayQuery.isLoading)
    : dateQuery.isLoading
  const isError = isToday
    ? (liveQuery.isError && todayQuery.isError)
    : dateQuery.isError

  /* Apply filter */
  const filtered = allMatches.filter(m => {
    if (filter === 'All')      return true
    if (filter === 'Live')     return LIVE_STATUSES.has(m.status)
    if (filter === 'Upcoming') return statusLabel(m) === 'upcoming'
    if (filter === 'Finished') return FINISHED_STATUSES.has(m.status)
    return true
  })

  const groups = groupByCompetition(filtered)

  /* Format the selected-date label shown in the header */
  const dateLabel = useMemo(() => {
    if (isToday) return 'Today'
    const d = new Date(selectedDate + 'T12:00:00')
    return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
  }, [selectedDate, isToday])

  function handleDateSelect(iso) {
    setSelectedDate(iso)
    setFilter('All')
    setCalendarOpen(false)
  }

  if (selectedFixture) {
    return (
      <MatchDetailScreen
        fixtureId={selectedFixture.fixtureId}
        homeTeam={selectedFixture.homeTeam}
        awayTeam={selectedFixture.awayTeam}
        onBack={() => setSelectedFixture(null)}
      />
    )
  }

  return (
    <div className="screen-content">
      <div style={{ padding: '20px 20px 0' }}>

        {/* ── Header ─────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CalendarDays size={20} color="var(--cyan)" />
            <span className="font-display" style={{ fontSize: 22, fontWeight: 700 }}>Fixtures</span>
            {liveCount > 0 && isToday && (
              <motion.span
                initial={{ scale: 0.8 }}
                animate={{ scale: 1 }}
                style={{
                  background: 'rgba(255,82,82,0.12)', color: 'var(--red)',
                  fontSize: 11, fontWeight: 700, borderRadius: 20,
                  padding: '3px 10px', display: 'flex', alignItems: 'center', gap: 5,
                }}
              >
                <span className="dot-pulse" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--red)', display: 'inline-block' }} />
                {liveCount} live
              </motion.span>
            )}
          </div>
          <span style={{ fontSize: 11, color: 'var(--dim)' }}>
            {isToday ? 'Auto ↻ 10s' : dateLabel}
          </span>
        </div>

        {/* ── Date label + calendar toggle ───────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="font-display" style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
              {dateLabel}
            </span>
            {!isToday && (
              <button
                onClick={() => handleDateSelect(today)}
                style={{
                  background: 'rgba(92,217,255,0.1)', border: '1px solid rgba(92,217,255,0.3)',
                  color: 'var(--cyan)', borderRadius: 20, padding: '3px 10px',
                  fontSize: 11, fontWeight: 600, cursor: 'pointer',
                }}
              >
                Today
              </button>
            )}
          </div>
          <button
            onClick={() => setCalendarOpen(o => !o)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              background: calendarOpen ? 'rgba(92,217,255,0.15)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${calendarOpen ? 'rgba(92,217,255,0.4)' : 'var(--border)'}`,
              color: calendarOpen ? 'var(--cyan)' : 'var(--muted)',
              borderRadius: 20, padding: '5px 12px',
              fontSize: 12, fontWeight: 600, cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            <CalendarDays size={13} />
            {calendarOpen ? 'Hide' : 'Pick date'}
            <ChevronRight size={13} style={{ transform: calendarOpen ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }} />
          </button>
        </div>

        {/* ── Collapsible calendar ────────────────────────── */}
        <AnimatePresence initial={false}>
          {calendarOpen && (
            <motion.div
              key="calendar"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: 'easeInOut' }}
              style={{ overflow: 'hidden', marginBottom: 12 }}
            >
              <MiniCalendar selected={selectedDate} onSelect={handleDateSelect} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Filter strip (only meaningful on today) ────── */}
        <div className="filter-strip" style={{ marginBottom: 20 }}>
          {FILTERS.map(f => (
            <button
              key={f}
              className={`filter-chip ${filter === f ? 'active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f}
              {f === 'Live' && liveCount > 0 && isToday && (
                <span style={{
                  marginLeft: 5, background: 'var(--red)', color: '#fff',
                  borderRadius: 10, fontSize: 9, padding: '1px 5px', fontWeight: 700,
                }}>
                  {liveCount}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ── Match list ─────────────────────────────────────── */}
      <div style={{ padding: '0 20px 32px' }}>
        {isLoading ? (
          <LoadingSkeleton />
        ) : isError ? (
          <EmptyState message="Could not load fixtures. Check your connection." />
        ) : groups.length === 0 ? (
          <EmptyState message={
            filter === 'Live'
              ? 'No live matches right now'
              : `No fixtures found for ${dateLabel}`
          } />
        ) : (
          <AnimatePresence mode="popLayout">
            {groups.map((group, gi) => (
              <LeagueGroup
                key={group.competition}
                group={group}
                groupIndex={gi}
                onSelect={setSelectedFixture}
              />
            ))}
          </AnimatePresence>
        )}

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          style={{
            marginTop: 24, padding: '12px 14px',
            background: 'rgba(45,58,74,0.4)', borderRadius: 12,
            border: '1px solid var(--border)',
          }}
        >
          <p style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.6 }}>
            ℹ️ Covered leagues include Premier League, La Liga, Bundesliga, Serie A, Ligue 1,
            Champions League, Europa League, Eredivisie, Primeira Liga, Championship,
            Copa Libertadores and more.
          </p>
        </motion.div>
      </div>
    </div>
  )
}

/* ── League group ──────────────────────────────────────────── */
function LeagueGroup({ group, groupIndex, onSelect }) {
  const hasLive = group.matches.some(m => LIVE_STATUSES.has(m.status))

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ delay: groupIndex * 0.04 }}
      style={{ marginBottom: 24 }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 10, paddingBottom: 8, borderBottom: '1px solid var(--border)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {hasLive && (
            <span className="dot-pulse" style={{
              width: 7, height: 7, borderRadius: '50%',
              background: 'var(--red)', display: 'inline-block', flexShrink: 0,
            }} />
          )}
          <span className="font-display" style={{ fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
            {group.competition}
          </span>
        </div>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{group.area}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {group.matches.map((m, i) => (
          <MatchRow key={m.id} match={m} index={i} onSelect={onSelect} />
        ))}
      </div>
    </motion.div>
  )
}

/* ── Single match row ──────────────────────────────────────── */
function MatchRow({ match, index, onSelect }) {
  const sl        = statusLabel(match)
  const isLiveNow = sl === 'live'
  const isDone    = sl === 'finished'   // actually played
  const isVoid    = sl === 'void'       // postponed / cancelled / suspended

  const leftBorder = isLiveNow
    ? '3px solid var(--cyan)'
    : isDone
    ? '3px solid var(--dim)'
    : isVoid
    ? '3px solid rgba(255,120,0,0.35)'
    : '3px solid rgba(255,255,255,0.06)'

  // Only show scores for matches that were genuinely played
  const scoreHome = isDone ? (match.score_home ?? 0) : null
  const scoreAway = isDone ? (match.score_away ?? 0) : null
  const hasScore  = scoreHome !== null

  function handleClick() {
    if (match.id && onSelect) {
      onSelect({ fixtureId: match.id, homeTeam: match.home_team, awayTeam: match.away_team })
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.03 }}
      className={`glass ${isLiveNow ? 'live-glow' : ''}`}
      style={{ borderLeft: leftBorder, padding: '12px 14px', cursor: match.id ? 'pointer' : 'default' }}
      onClick={handleClick}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>

        {/* Status / time column */}
        <div style={{ width: 44, flexShrink: 0, textAlign: 'center' }}>
          {isLiveNow ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, marginBottom: 2 }}>
                <span className="dot-pulse" style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} />
              </div>
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--amber)' }}>
                {match.minute ? `${match.minute}'` : 'LIVE'}
              </span>
            </div>
          ) : isDone ? (
            <span style={{ fontSize: 11, color: 'var(--dim)', fontWeight: 600 }}>FT</span>
          ) : isVoid ? (
            <span style={{ fontSize: 10, color: 'var(--amber)', fontWeight: 700 }}>{voidLabel(match.status)}</span>
          ) : (
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{formatKickoff(match.kickoff)}</span>
          )}
        </div>

        {/* Teams */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <p className="font-display" style={{
            fontSize: 15, fontWeight: 700, lineHeight: 1.25,
            color: hasScore && scoreHome > scoreAway ? 'var(--text)' : isDone ? 'var(--muted)' : 'var(--text)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {match.home_short || match.home_team}
          </p>
          <p className="font-display" style={{
            fontSize: 15, fontWeight: 700, lineHeight: 1.25, marginTop: 3,
            color: hasScore && scoreAway > scoreHome ? 'var(--text)' : isDone ? 'var(--muted)' : 'var(--text)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {match.away_short || match.away_team}
          </p>
        </div>

        {/* Score or vs */}
        <div style={{ flexShrink: 0, textAlign: 'center', minWidth: 40 }}>
          {hasScore ? (
            <motion.div
              animate={isLiveNow ? { opacity: [1, 0.55, 1] } : {}}
              transition={{ duration: 2.5, repeat: Infinity }}
            >
              <span className="font-display" style={{
                fontSize: 20, fontWeight: 700, display: 'block', lineHeight: 1,
                color: isLiveNow ? 'var(--cyan)' : isDone ? 'var(--muted)' : 'var(--text)',
              }}>{scoreHome}</span>
              <span style={{ fontSize: 10, color: 'var(--dim)', display: 'block', margin: '1px 0' }}>—</span>
              <span className="font-display" style={{
                fontSize: 20, fontWeight: 700, display: 'block', lineHeight: 1,
                color: isLiveNow ? 'var(--cyan)' : isDone ? 'var(--muted)' : 'var(--text)',
              }}>{scoreAway}</span>
            </motion.div>
          ) : (
            <span style={{ fontSize: 13, color: 'var(--dim)' }}>vs</span>
          )}
        </div>
      </div>

      {match.status === 'PAUSED' && (
        <p style={{ fontSize: 10, color: 'var(--amber)', marginTop: 6, textAlign: 'center', fontWeight: 600, letterSpacing: 1 }}>
          HALF TIME
        </p>
      )}
    </motion.div>
  )
}

/* ── Loading skeleton ──────────────────────────────────────── */
function LoadingSkeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {[0, 1, 2].map(g => (
        <div key={g}>
          <div style={{ height: 14, width: 140, background: 'var(--dim)', borderRadius: 4, marginBottom: 12 }} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[0, 1, 2].map(i => (
              <div key={i} className="glass" style={{ padding: '12px 14px', borderLeft: '3px solid var(--dim)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ width: 44, height: 32, background: 'var(--dim)', borderRadius: 6, flexShrink: 0 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ height: 12, background: 'var(--dim)', borderRadius: 4, marginBottom: 6, width: '70%' }} />
                  <div style={{ height: 12, background: 'var(--dim)', borderRadius: 4, width: '55%' }} />
                </div>
                <div style={{ width: 28, height: 40, background: 'var(--dim)', borderRadius: 6 }} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function EmptyState({ message }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 0' }}>
      <p style={{ fontSize: 32, marginBottom: 12 }}>📅</p>
      <p style={{ fontSize: 14, color: 'var(--muted)' }}>{message}</p>
    </div>
  )
}

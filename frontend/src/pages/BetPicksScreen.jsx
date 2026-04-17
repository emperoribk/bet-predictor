import { useQuery } from '@tanstack/react-query'
import { TrendingUp, RefreshCw, AlertCircle } from 'lucide-react'
import { fetchWeekendPicks } from '../api/betApi'

const MARKET_LABEL = {
  DC_1X:      '1X',
  DC_X2:      'X2',
  OVER15:     'O1.5',
  UNDER25:    'U2.5',
  TEAM_SCORE: 'Score',
}

const LEAGUE_NAMES = {
  PL:  'Premier League', PD: 'La Liga', BL1: 'Bundesliga',
  SA:  'Serie A', FL1: 'Ligue 1', DED: 'Eredivisie',
  PPL: 'Primeira Liga', ELC: 'Championship', SPL: 'Scottish Prem',
  BJL: 'Belgian Pro', TSL: 'Süper Lig', BL2: 'Bundesliga 2',
}

function OutcomeBadge({ outcome }) {
  if (outcome === 'WIN')  return <span className="badge badge-won">WIN</span>
  if (outcome === 'LOSS') return <span className="badge badge-lost">LOSS</span>
  return <span className="badge badge-pending">PENDING</span>
}

function ConfidenceBar({ value }) {
  const color = value >= 84 ? '#00e287' : value >= 82 ? '#5cd9ff' : '#ffb400'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        flex: 1, height: 4, background: 'rgba(255,255,255,0.08)',
        borderRadius: 2, overflow: 'hidden',
      }}>
        <div style={{
          width: `${value}%`, height: '100%',
          background: color, borderRadius: 2,
          transition: 'width 0.6s ease',
        }} />
      </div>
      <span style={{ fontSize: 12, fontWeight: 600, color, minWidth: 34 }}>{value}%</span>
    </div>
  )
}

function PickCard({ pick }) {
  const marketTag = MARKET_LABEL[pick.bet_type] || pick.bet_type
  const leagueName = LEAGUE_NAMES[pick.competition_code] || pick.competition_code

  // evidence[1] is "xG: X vs Y", evidence[2] is "Model odds: @X.XX"
  const xgLine   = pick.evidence?.[1] || ''
  const oddsLine = pick.evidence?.[2] || ''

  return (
    <div className="glass glass-hover" style={{
      padding: '14px 16px',
      borderRadius: 14,
      marginBottom: 10,
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#fff', lineHeight: 1.3 }}>
            {pick.home_team}
            <span style={{ color: 'var(--muted)', margin: '0 6px', fontWeight: 400 }}>vs</span>
            {pick.away_team}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-soft)', marginTop: 3 }}>
            {pick.bet_label}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, marginLeft: 10 }}>
          <OutcomeBadge outcome={pick.outcome} />
          <span style={{
            fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
            background: 'rgba(92,217,255,0.12)', color: 'var(--cyan)',
            borderRadius: 5, padding: '2px 6px',
          }}>{marketTag}</span>
        </div>
      </div>

      {/* Confidence bar */}
      <ConfidenceBar value={pick.confidence} />

      {/* Meta row */}
      <div style={{
        display: 'flex', gap: 10, marginTop: 8,
        fontSize: 11, color: 'var(--muted)',
      }}>
        <span style={{
          background: 'rgba(255,255,255,0.06)', borderRadius: 5,
          padding: '2px 7px', color: 'var(--text-soft)',
        }}>{leagueName}</span>
        {xgLine && <span>{xgLine}</span>}
        {oddsLine && <span style={{ marginLeft: 'auto', color: 'var(--cyan)' }}>{oddsLine}</span>}
      </div>
    </div>
  )
}

function DaySection({ day }) {
  const hasPicks = day.picks.length > 0
  const isToday  = day.date === new Date().toISOString().slice(0, 10)

  return (
    <div style={{ marginBottom: 28 }}>
      {/* Day header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 12,
      }}>
        <div>
          <span style={{
            fontFamily: 'Rajdhani, sans-serif', fontWeight: 700,
            fontSize: 18, color: '#fff',
          }}>
            {day.day}
            {isToday && (
              <span style={{
                marginLeft: 8, fontSize: 10, fontWeight: 600,
                background: 'rgba(92,217,255,0.15)', color: 'var(--cyan)',
                borderRadius: 5, padding: '2px 7px', verticalAlign: 'middle',
              }}>TODAY</span>
            )}
          </span>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 1 }}>{day.date_display}</div>
        </div>
        {hasPicks && (
          <div style={{
            textAlign: 'right',
          }}>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>Combined</div>
            <div style={{
              fontFamily: 'Rajdhani, sans-serif', fontWeight: 700,
              fontSize: 20, color: day.combined_odds >= 2.0 ? 'var(--green)' : 'var(--amber)',
            }}>@{day.combined_odds?.toFixed(2)}</div>
          </div>
        )}
      </div>

      {hasPicks ? (
        day.picks.map(pick => <PickCard key={pick.id} pick={pick} />)
      ) : (
        <div className="glass" style={{
          padding: '20px 16px', borderRadius: 14, textAlign: 'center',
          color: 'var(--muted)', fontSize: 13,
        }}>
          <AlertCircle size={20} style={{ marginBottom: 8, opacity: 0.4 }} />
          <div>No picks yet for {day.date}</div>
          <div style={{ fontSize: 11, marginTop: 4, opacity: 0.6 }}>
            Run: <code style={{ color: 'var(--cyan)' }}>run_accumulator --date {day.date}</code>
          </div>
        </div>
      )}
    </div>
  )
}

export default function BetPicksScreen() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['weekend-picks'],
    queryFn: fetchWeekendPicks,
    staleTime: 5 * 60 * 1000,
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '20px 20px 0',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <TrendingUp size={20} color="var(--cyan)" />
            <span style={{
              fontFamily: 'Rajdhani, sans-serif', fontWeight: 700,
              fontSize: 22, color: '#fff',
            }}>Bet Picks</span>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: isFetching ? 'var(--dim)' : 'var(--muted)',
              padding: 6, borderRadius: 8,
            }}
          >
            <RefreshCw size={16} style={{ animation: isFetching ? 'spin 0.7s linear infinite' : 'none' }} />
          </button>
        </div>
        <p style={{ fontSize: 13, color: 'var(--muted)', margin: '0 0 16px' }}>
          Weekend accumulator picks — Fri · Sat · Sun
        </p>
        <div style={{ height: 1, background: 'var(--border)', marginBottom: 20 }} />
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px 20px' }}>
        {isLoading && (
          <div style={{ textAlign: 'center', paddingTop: 60, color: 'var(--muted)' }}>
            <div className="spinner" style={{ margin: '0 auto 12px', borderTopColor: 'var(--cyan)' }} />
            <div style={{ fontSize: 13 }}>Loading picks…</div>
          </div>
        )}

        {isError && (
          <div className="glass" style={{
            padding: '20px 16px', borderRadius: 14, textAlign: 'center',
            color: 'var(--red)', fontSize: 13, marginTop: 20,
          }}>
            <AlertCircle size={20} style={{ marginBottom: 8 }} />
            <div>Could not load picks. Is the backend running?</div>
          </div>
        )}

        {data?.days?.map(day => (
          <DaySection key={day.date} day={day} />
        ))}
      </div>
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { fetchLiveNow } from '../api/betApi'

/** Compact 3-match preview shown on the homepage. */
export default function LiveMatchesFeed({ onViewAll }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['live-now'],
    queryFn: fetchLiveNow,
    refetchInterval: 45_000,
    staleTime: 30_000,
    retry: 1,
  })

  const matches = data?.matches ?? []
  const preview = matches.slice(0, 3)

  return (
    <div>
      {/* Section header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="dot-pulse" style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--red)', display: 'inline-block' }} />
          <span className="font-display" style={{ fontSize: 16, fontWeight: 700 }}>Live Now</span>
          {matches.length > 0 && (
            <span style={{ background: 'rgba(255,82,82,0.12)', color: 'var(--red)', fontSize: 11, fontWeight: 600, borderRadius: 20, padding: '2px 9px' }}>
              {matches.length}
            </span>
          )}
        </div>
        <button
          onClick={onViewAll}
          style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'none', border: 'none', color: 'var(--cyan)', fontSize: 12, fontWeight: 600, cursor: 'pointer', padding: 0 }}
        >
          See all <ArrowRight size={12} />
        </button>
      </div>

      {/* Cards */}
      {isLoading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[1, 2].map(i => <SkeletonCard key={i} />)}
        </div>
      ) : isError || matches.length === 0 ? (
        <div className="glass" style={{ padding: '16px', textAlign: 'center' }}>
          <p style={{ fontSize: 13, color: 'var(--muted)' }}>
            {isError ? 'Unable to load' : 'No matches live right now'}
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {preview.map((m, i) => <PreviewCard key={m.id} match={m} index={i} />)}
          {matches.length > 3 && (
            <button
              onClick={onViewAll}
              className="glass glass-hover"
              style={{ border: 'none', cursor: 'pointer', padding: '11px 16px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, color: 'var(--cyan)', fontSize: 13, fontWeight: 600 }}
            >
              +{matches.length - 3} more matches <ArrowRight size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function PreviewCard({ match, index }) {
  const home = match.home_short || match.home_team
  const away = match.away_short || match.away_team
  const sh = match.score_home ?? '–'
  const sa = match.score_away ?? '–'

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className="glass live-glow"
      style={{ borderLeft: '3px solid var(--cyan)', padding: '11px 14px' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 }}>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{match.competition}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--amber)' }}>
          {match.minute ? `${match.minute}'` : 'LIVE'}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="font-display" style={{ flex: 1, fontSize: 14, fontWeight: 700, textAlign: 'right', color: 'var(--text)' }}>{home}</span>
        <motion.div
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ duration: 2.5, repeat: Infinity }}
          className="font-display"
          style={{ fontSize: 18, fontWeight: 700, color: '#ffffff', minWidth: 52, textAlign: 'center', letterSpacing: 1, textShadow: '0 0 16px rgba(92,217,255,0.55)' }}
        >
          {sh} – {sa}
        </motion.div>
        <span className="font-display" style={{ flex: 1, fontSize: 14, fontWeight: 700, textAlign: 'left', color: 'var(--text)' }}>{away}</span>
      </div>
    </motion.div>
  )
}

function SkeletonCard() {
  return (
    <div className="glass" style={{ padding: '11px 14px', borderLeft: '3px solid var(--dim)' }}>
      <div style={{ height: 10, width: 100, background: 'var(--dim)', borderRadius: 4, marginBottom: 8 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ flex: 1, height: 14, background: 'var(--dim)', borderRadius: 4 }} />
        <div style={{ width: 52, height: 18, background: 'var(--dim)', borderRadius: 4 }} />
        <div style={{ flex: 1, height: 14, background: 'var(--dim)', borderRadius: 4 }} />
      </div>
    </div>
  )
}

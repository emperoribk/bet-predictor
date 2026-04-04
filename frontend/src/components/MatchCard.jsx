import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, ChevronUp, Swords } from 'lucide-react'
import StatusBadge from './StatusBadge'
import StatBar from './StatBar'
import EventTimeline from './EventTimeline'
import KickoffTimer from './KickoffTimer'

const borderColor = {
  PENDING:   'border-slate-700/50',
  SAFE:      'border-emerald-700/50',
  DANGER:    'border-amber-600/60',
  CRITICAL:  'border-rose-600/60',
  WON:       'border-sky-700/50',
  LOST:      'border-red-900/60',
  VIRTUAL:   'border-purple-700/40',
  UNTRACKED: 'border-slate-700/30',
}
const pulseClass = { DANGER: 'pulse-danger', CRITICAL: 'pulse-critical' }

const liveStatuses = ['IN_PLAY', 'PAUSED', 'EXTRA_TIME', 'PENALTY_SHOOTOUT']
const doneStatuses = ['FINISHED', 'AWARDED']

const matchLabel = {
  SCHEDULED:'Not started', TIMED:'Not started', IN_PLAY:'Live',
  PAUSED:'Half Time', FINISHED:'Full Time', EXTRA_TIME:'Extra Time',
  PENALTY_SHOOTOUT:'Penalties', AWARDED:'Awarded',
}

export default function MatchCard({ match, index = 0 }) {
  const [expanded, setExpanded] = useState(false)
  const s = match.analysis_status
  const isLive = liveStatuses.includes(match.match_status)
  const isDone = doneStatuses.includes(match.match_status)
  const hasStats = isLive || isDone
  const [homeScore, awayScore] = (match.score || '0-0').split('-').map(Number)

  return (
    <motion.div layout
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.04 }}
      className={`relative rounded-2xl border overflow-hidden glass ${borderColor[s]} ${pulseClass[s] || ''}`}
    >
      {/* Top accent line */}
      <div className={`h-0.5 w-full ${
        s === 'SAFE' ? 'bg-gradient-to-r from-transparent via-emerald-500 to-transparent' :
        s === 'DANGER' ? 'bg-gradient-to-r from-transparent via-amber-500 to-transparent' :
        s === 'CRITICAL' ? 'bg-gradient-to-r from-transparent via-rose-500 to-transparent' :
        s === 'WON' ? 'bg-gradient-to-r from-transparent via-sky-500 to-transparent' :
        s === 'LOST' ? 'bg-gradient-to-r from-transparent via-red-700 to-transparent' :
        'bg-gradient-to-r from-transparent via-slate-600 to-transparent'
      }`} />

      <div className="px-5 pt-4 pb-4">
        {/* Header row */}
        <div className="flex items-center justify-between mb-4">
          <StatusBadge status={s} />
          <div className="flex items-center gap-2">
            {isLive && (
              <span className="flex items-center gap-1.5 bg-rose-950/60 border border-rose-800/40 rounded-full px-2.5 py-1 text-xs font-bold text-rose-400">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
                {match.minute}&apos;
              </span>
            )}
            <span className="text-xs text-slate-600 font-medium">
              {matchLabel[match.match_status] || match.match_status}
            </span>
          </div>
        </div>

        {/* Score section */}
        <div className="flex items-center gap-3">
          {/* Home */}
          <div className="flex-1 text-right">
            <p className={`font-bold text-base leading-snug ${
              match.user_pick === match.home_team ? 'text-sky-300' : 'text-white'
            }`}>{match.home_team}</p>
            {match.user_pick === match.home_team && (
              <span className="text-[10px] text-sky-500 font-semibold tracking-wide uppercase">Your pick</span>
            )}
          </div>

          {/* Score box */}
          <div className="flex flex-col items-center gap-1 min-w-[72px]">
            {hasStats ? (
              <div className="glass-bright rounded-xl px-4 py-2 text-center border border-sky-800/30">
                <span className="text-xl font-black text-white tracking-widest">
                  {homeScore} <span className="text-slate-600">–</span> {awayScore}
                </span>
              </div>
            ) : (
              <div className="flex items-center gap-1.5 text-slate-600">
                <Swords size={14} />
                <span className="text-xs font-medium">vs</span>
              </div>
            )}
            {!hasStats && match.kickoff_time && (
              <KickoffTimer kickoffTime={match.kickoff_time} />
            )}
          </div>

          {/* Away */}
          <div className="flex-1">
            <p className={`font-bold text-base leading-snug ${
              match.user_pick === match.away_team ? 'text-sky-300' : 'text-white'
            }`}>{match.away_team}</p>
            {match.user_pick === match.away_team && (
              <span className="text-[10px] text-sky-500 font-semibold tracking-wide uppercase">Your pick</span>
            )}
          </div>
        </div>

        {/* Pick badge */}
        <div className="flex items-center justify-center gap-2 mt-3">
          <span className="text-xs text-slate-600">Bet:</span>
          <span className="text-xs font-semibold text-sky-400 bg-sky-950/50 border border-sky-800/40 rounded-full px-3 py-0.5">
            {match.user_pick}
          </span>
        </div>

        {/* Analysis reason */}
        {match.analysis_reason && (s !== 'PENDING') && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            className="mt-3 text-xs text-slate-400 text-center leading-relaxed border-t border-white/5 pt-3"
          >
            {match.analysis_reason}
          </motion.div>
        )}
      </div>

      {/* Expandable stats */}
      {hasStats && (
        <div className="border-t border-white/5">
          <button onClick={() => setExpanded(v => !v)}
            className="w-full flex items-center justify-between px-5 py-2.5 text-xs text-slate-600 hover:text-sky-400 hover:bg-white/3 transition-all"
          >
            <span className="font-medium">Stats & Events</span>
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>

          <AnimatePresence>
            {expanded && (
              <motion.div
                initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.22 }}
                className="overflow-hidden"
              >
                <div className="px-5 pb-5 space-y-4">
                  {/* Team headers */}
                  <div className="flex justify-between text-xs text-slate-500 font-semibold">
                    <span>{match.home_team}</span>
                    <span>{match.away_team}</span>
                  </div>
                  {/* Bars */}
                  <div className="space-y-3">
                    <StatBar label="Possession" homeVal={match.possession_home} awayVal={match.possession_away} unit="%" />
                    <StatBar label="Shots on Target" homeVal={match.shots_on_target_home} awayVal={match.shots_on_target_away} />
                    <StatBar label="Red Cards" homeVal={match.red_cards_home || 0} awayVal={match.red_cards_away || 0} />
                  </div>
                  {/* Events */}
                  <div className="pt-1 border-t border-white/5">
                    <p className="text-[10px] font-bold text-slate-600 uppercase tracking-widest mb-2">Match Events</p>
                    <EventTimeline events={match.events} />
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  )
}

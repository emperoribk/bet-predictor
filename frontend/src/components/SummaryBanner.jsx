import { motion } from 'framer-motion'
import { ShieldCheck, AlertTriangle, Trophy, Clock, XCircle, Flame } from 'lucide-react'

const tiles = [
  { key: 'PENDING',  label: 'Pending',  Icon: Clock,         color: 'text-slate-400',   bg: 'bg-slate-800/60',   border: 'border-slate-700/40' },
  { key: 'SAFE',     label: 'Safe',     Icon: ShieldCheck,   color: 'text-emerald-400', bg: 'bg-emerald-950/50', border: 'border-emerald-800/40' },
  { key: 'DANGER',   label: 'Danger',   Icon: AlertTriangle, color: 'text-amber-400',   bg: 'bg-amber-950/50',   border: 'border-amber-800/40' },
  { key: 'CRITICAL', label: 'Critical', Icon: Flame,         color: 'text-rose-400',    bg: 'bg-rose-950/50',    border: 'border-rose-800/40' },
  { key: 'WON',      label: 'Won',      Icon: Trophy,        color: 'text-sky-300',     bg: 'bg-sky-950/50',     border: 'border-sky-800/40' },
  { key: 'LOST',     label: 'Lost',     Icon: XCircle,       color: 'text-red-400',     bg: 'bg-red-950/50',     border: 'border-red-800/40' },
]

export default function SummaryBanner({ summary, accumulatorAlive }) {
  return (
    <div className="space-y-3">
      {/* Accumulator pill */}
      <motion.div
        initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
        className={`flex items-center justify-center gap-2.5 rounded-2xl px-5 py-3 border font-semibold text-sm
          ${accumulatorAlive
            ? 'bg-sky-950/50 border-sky-700/40 text-sky-300'
            : 'bg-rose-950/50 border-rose-700/40 text-rose-300'}`}
      >
        <span className="text-xl">{accumulatorAlive ? '🎯' : '💀'}</span>
        <span>{accumulatorAlive ? 'Accumulator alive — keep watching!' : 'Accumulator dead — a match already lost'}</span>
      </motion.div>

      {/* Stat tiles */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        {tiles.map(({ key, label, Icon: TileIcon, color, bg, border }, i) => (
          <motion.div key={key}
            initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.05 }}
            className={`glass flex flex-col items-center gap-1 rounded-xl py-3 px-2 border ${bg} ${border}`}
          >
            <TileIcon size={16} className={color} />
            <span className={`text-2xl font-bold ${color}`}>{summary?.[key] ?? 0}</span>
            <span className="text-[11px] text-slate-500 font-medium">{label}</span>
          </motion.div>
        ))}
      </div>
    </div>
  )
}

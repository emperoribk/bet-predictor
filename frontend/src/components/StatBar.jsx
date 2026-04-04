import { motion } from 'framer-motion'

export default function StatBar({ label, homeVal, awayVal, unit = '' }) {
  if (homeVal == null && awayVal == null) return null

  const home = Number(homeVal) || 0
  const away = Number(awayVal) || 0
  const total = home + away
  const homePct = total === 0 ? 50 : Math.round((home / total) * 100)
  const awayPct = 100 - homePct

  const homeColor = homePct >= awayPct ? 'bg-sky-500' : 'bg-slate-600'
  const awayColor = awayPct > homePct ? 'bg-sky-500' : 'bg-slate-600'

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center text-xs">
        <span className="font-semibold text-white w-10">{home}{unit}</span>
        <span className="text-slate-500 text-center flex-1">{label}</span>
        <span className="font-semibold text-white w-10 text-right">{away}{unit}</span>
      </div>
      <div className="flex h-1.5 rounded-full overflow-hidden gap-0.5">
        <motion.div className={`h-full rounded-l-full ${homeColor}`}
          initial={{ width: 0 }} animate={{ width: `${homePct}%` }}
          transition={{ duration: 0.9, ease: 'easeOut' }} />
        <motion.div className={`h-full rounded-r-full ${awayColor}`}
          initial={{ width: 0 }} animate={{ width: `${awayPct}%` }}
          transition={{ duration: 0.9, ease: 'easeOut' }} />
      </div>
    </div>
  )
}

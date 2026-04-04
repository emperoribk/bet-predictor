const config = {
  PENDING:   { label: 'Pending',     bg: 'bg-slate-800/80',       text: 'text-slate-400',   border: 'border-slate-700/50',   icon: '⏳' },
  SAFE:      { label: 'Safe',        bg: 'bg-emerald-950/70',      text: 'text-emerald-400', border: 'border-emerald-700/50', icon: '✅' },
  DANGER:    { label: 'Danger',      bg: 'bg-amber-950/70',        text: 'text-amber-400',   border: 'border-amber-700/50',   icon: '⚠️' },
  CRITICAL:  { label: 'Critical',    bg: 'bg-rose-950/70',         text: 'text-rose-400',    border: 'border-rose-700/50',    icon: '🔴' },
  WON:       { label: 'Won',         bg: 'bg-sky-950/70',          text: 'text-sky-300',     border: 'border-sky-700/50',     icon: '🏆' },
  LOST:      { label: 'Lost',        bg: 'bg-red-950/70',          text: 'text-red-400',     border: 'border-red-900/60',     icon: '❌' },
  VIRTUAL:   { label: 'Virtual',     bg: 'bg-purple-950/70',       text: 'text-purple-400',  border: 'border-purple-700/50',  icon: '🎮' },
  UNTRACKED: { label: 'Not tracked', bg: 'bg-slate-800/60',        text: 'text-slate-500',   border: 'border-slate-700/40',   icon: '🔍' },
}

export default function StatusBadge({ status, size = 'md' }) {
  const c = config[status] || config.PENDING
  const sz = size === 'lg' ? 'px-4 py-1.5 text-sm gap-2' : size === 'sm' ? 'px-2 py-0.5 text-xs gap-1' : 'px-3 py-1 text-xs gap-1.5'
  return (
    <span className={`inline-flex items-center rounded-full font-semibold tracking-wide border ${c.bg} ${c.text} ${c.border} ${sz}`}>
      <span>{c.icon}</span>
      {c.label}
    </span>
  )
}

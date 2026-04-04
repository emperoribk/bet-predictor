const icon = t => ({ GOAL:'⚽', YELLOW:'🟨', RED:'🟥', YELLOW_RED:'🟥', SUBSTITUTION:'🔄' }[t] || '•')
const color = t => ({
  GOAL:'text-emerald-400', YELLOW:'text-yellow-400',
  RED:'text-rose-400', YELLOW_RED:'text-rose-400', SUBSTITUTION:'text-sky-400'
}[t] || 'text-slate-400')

export default function EventTimeline({ events }) {
  if (!events?.length) return (
    <p className="text-xs text-slate-600 italic py-2 text-center">No events yet</p>
  )
  return (
    <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
      {events.map((e, i) => (
        <div key={i} className={`flex items-start gap-2.5 text-xs ${color(e.type)}`}>
          <span className="font-mono text-slate-500 w-7 shrink-0 pt-0.5">{e.minute}&apos;</span>
          <span className="text-base leading-none">{icon(e.type)}</span>
          <span className="text-slate-300 leading-relaxed">
            {e.type === 'GOAL' && <>{e.scorer || 'Unknown'} <span className="text-slate-500 text-xs">({e.team})</span></>}
            {(e.type === 'YELLOW' || e.type === 'RED' || e.type === 'YELLOW_RED') &&
              <>{e.player || 'Unknown'} <span className="text-slate-500 text-xs">({e.team})</span></>}
            {e.type === 'SUBSTITUTION' && (
              <><span className="text-rose-400">↓{e.player_out}</span> → <span className="text-emerald-400">↑{e.player_in}</span> <span className="text-slate-500 text-xs">({e.team})</span></>
            )}
          </span>
        </div>
      ))}
    </div>
  )
}

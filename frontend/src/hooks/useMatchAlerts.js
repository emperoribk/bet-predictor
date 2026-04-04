import { useRef, useEffect } from 'react'
import { useNotifications } from './useNotifications'

/**
 * Watches live match data and fires browser notifications when
 * important events happen: goals, red cards, status changes, danger flags.
 *
 * Call once per tracked slip with the latest `matches` array from the API.
 */
export function useMatchAlerts(matches) {
  const { notify } = useNotifications()
  const prevRef = useRef(null)   // Map<id, matchSnapshot>

  useEffect(() => {
    if (!matches || matches.length === 0) return

    const prev = prevRef.current

    // First load — just store state, don't alert
    if (!prev) {
      prevRef.current = buildSnapshot(matches)
      return
    }

    for (const match of matches) {
      const old = prev.get(match.id)
      if (!old) continue   // new match appeared — skip first time

      const home = match.home_team
      const away = match.away_team
      const label = `${home} vs ${away}`

      // ── Goal scored ──────────────────────────────────────────
      const newHome = match.score?.split('-')[0]
      const newAway = match.score?.split('-')[1]
      const oldHome = old.scoreHome
      const oldAway = old.scoreAway

      if (newHome > oldHome) {
        notify(
          `⚽ GOAL — ${home}!`,
          `${home} ${newHome}–${newAway} ${away}`,
          `goal-${match.id}-${newHome}-${newAway}`
        )
      } else if (newAway > oldAway) {
        notify(
          `⚽ GOAL — ${away}!`,
          `${home} ${newHome}–${newAway} ${away}`,
          `goal-${match.id}-${newHome}-${newAway}`
        )
      }

      // ── Red card ─────────────────────────────────────────────
      const newRedsHome = match.red_cards_home || 0
      const newRedsAway = match.red_cards_away || 0

      if (newRedsHome > (old.redsHome || 0)) {
        notify(
          `🟥 Red Card — ${home}`,
          `${label} — ${home} down to ${11 - newRedsHome} men`,
          `red-${match.id}-home`
        )
      }
      if (newRedsAway > (old.redsAway || 0)) {
        notify(
          `🟥 Red Card — ${away}`,
          `${label} — ${away} down to ${11 - newRedsAway} men`,
          `red-${match.id}-away`
        )
      }

      // ── Analysis status escalation ───────────────────────────
      const newStatus = match.analysis_status
      const oldStatus = old.analysisStatus

      if (newStatus !== oldStatus) {
        if (newStatus === 'DANGER' && !['DANGER','CRITICAL','LOST'].includes(oldStatus)) {
          notify(
            `⚠️ Danger — ${label}`,
            match.analysis_reason || 'Your pick is under pressure.',
            `status-${match.id}`
          )
        }
        if (newStatus === 'CRITICAL') {
          notify(
            `🚨 CRITICAL — ${label}`,
            match.analysis_reason || 'This game might not enter. High risk!',
            `status-${match.id}`
          )
        }
        if (newStatus === 'LOST') {
          notify(
            `❌ Lost — ${label}`,
            `${match.analysis_reason || 'Your pick did not come through.'} Accumulator affected.`,
            `status-${match.id}`
          )
        }
        if (newStatus === 'WON') {
          notify(
            `✅ Won — ${label}`,
            `${match.analysis_reason || 'Your pick came through!'}`,
            `status-${match.id}`
          )
        }
      }

      // ── Opponent heavy pressure (shots on target surge) ──────
      const pick = (match.user_pick || '').toLowerCase()
      const isHomePick = pick && match.home_team?.toLowerCase().includes(pick)
      const oppShots = isHomePick
        ? match.shots_on_target_away
        : match.shots_on_target_home
      const oldOppShots = old.oppShots

      if (
        oppShots != null &&
        oldOppShots != null &&
        oppShots >= 5 &&
        oppShots > oldOppShots &&
        newStatus !== 'WON' &&
        newStatus !== 'LOST'
      ) {
        notify(
          `📊 Heavy pressure — ${label}`,
          `Opponents have ${oppShots} shots on target. Your pick is under siege.`,
          `pressure-${match.id}`
        )
      }

      // ── Half time ────────────────────────────────────────────
      if (match.match_status === 'PAUSED' && old.matchStatus !== 'PAUSED') {
        const scoreText = match.score?.replace('None', '0') || '–'
        notify(
          `⏸ Half Time — ${label}`,
          `Score at the break: ${scoreText}`,
          `ht-${match.id}`
        )
      }

      // ── Full time ────────────────────────────────────────────
      if (match.match_status === 'FINISHED' && old.matchStatus !== 'FINISHED') {
        const scoreText = match.score?.replace('None', '0') || '–'
        notify(
          `🏁 Full Time — ${label}`,
          `Final score: ${scoreText}`,
          `ft-${match.id}`
        )
      }
    }

    // Update snapshot
    prevRef.current = buildSnapshot(matches)
  }, [matches, notify])
}

function buildSnapshot(matches) {
  const map = new Map()
  for (const m of matches) {
    const parts = (m.score || '0-0').split('-')
    map.set(m.id, {
      scoreHome:      parseInt(parts[0]) || 0,
      scoreAway:      parseInt(parts[1]) || 0,
      redsHome:       m.red_cards_home || 0,
      redsAway:       m.red_cards_away || 0,
      analysisStatus: m.analysis_status,
      matchStatus:    m.match_status,
      oppShots:       m.shots_on_target_away,  // rough — recalculated per pick in loop
    })
  }
  return map
}

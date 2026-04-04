import { useEffect, useRef, useCallback } from 'react'

/**
 * Handles browser notification permission + firing alerts.
 * Works while the tab is open or in the background.
 * Does NOT require a service worker.
 */
export function useNotifications() {
  const grantedRef = useRef(false)

  useEffect(() => {
    if (!('Notification' in window)) return
    if (Notification.permission === 'granted') {
      grantedRef.current = true
      return
    }
    if (Notification.permission === 'default') {
      // Ask on first interaction rather than immediately — browsers prefer this
      const onInteraction = () => {
        Notification.requestPermission().then(perm => {
          grantedRef.current = perm === 'granted'
        })
        window.removeEventListener('click', onInteraction, { once: true })
      }
      window.addEventListener('click', onInteraction, { once: true })
    }
  }, [])

  const notify = useCallback((title, body, tag) => {
    if (!('Notification' in window)) return
    if (Notification.permission !== 'granted') {
      // Try requesting if not yet asked
      Notification.requestPermission().then(perm => {
        if (perm === 'granted') {
          grantedRef.current = true
          _fire(title, body, tag)
        }
      })
      return
    }
    _fire(title, body, tag)
  }, [])

  return { notify }
}

function _fire(title, body, tag) {
  try {
    const n = new Notification(title, {
      body,
      tag,           // same tag = replaces previous notification of same kind
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      requireInteraction: false,
    })
    // Auto-close after 6 seconds
    setTimeout(() => n.close(), 6000)
  } catch {
    // Some browsers (e.g. Firefox on HTTPS-only) may throw
  }
}

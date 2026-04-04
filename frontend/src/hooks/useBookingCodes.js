import { useState, useCallback } from 'react'

const STORAGE_KEY = 'betwatch_codes'
const MAX_CODES = 6

function load() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [] }
  catch { return [] }
}

function save(codes) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(codes))
}

export function useBookingCodes() {
  const [codes, setCodes] = useState(() => load())
  const [activeCode, setActiveCode] = useState(() => load()[0] || null)

  const addCode = useCallback((code) => {
    const upper = code.trim().toUpperCase()
    if (!upper) return false
    setCodes(prev => {
      if (prev.includes(upper)) {
        setActiveCode(upper)
        return prev
      }
      const next = [upper, ...prev].slice(0, MAX_CODES)
      save(next)
      setActiveCode(upper)
      return next
    })
    return true
  }, [])

  const removeCode = useCallback((code) => {
    setCodes(prev => {
      const next = prev.filter(c => c !== code)
      save(next)
      if (activeCode === code) setActiveCode(next[0] || null)
      return next
    })
  }, [activeCode])

  return { codes, activeCode, setActiveCode, addCode, removeCode }
}

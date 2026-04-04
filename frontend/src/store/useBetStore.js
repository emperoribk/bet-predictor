import { create } from 'zustand'
import { persist } from 'zustand/middleware'

const useBetStore = create(
  persist(
    (set, get) => ({
      history: [], // [{ code, date, legs, status, stake, payout }]

      addBet: (bet) => set((state) => {
        const existing = state.history.find(h => h.code === bet.code)
        if (existing) {
          // Update existing entry
          return {
            history: state.history.map(h =>
              h.code === bet.code ? { ...h, ...bet, date: new Date().toISOString() } : h
            )
          }
        }
        return {
          history: [
            { ...bet, date: new Date().toISOString() },
            ...state.history,
          ].slice(0, 20) // keep last 20
        }
      }),

      updateBetStatus: (code, status) => set((state) => ({
        history: state.history.map(h =>
          h.code === code ? { ...h, status } : h
        )
      })),

      clearHistory: () => set({ history: [] }),
    }),
    { name: 'betwatch-history' }
  )
)

export default useBetStore

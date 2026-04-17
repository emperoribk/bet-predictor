import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from './components/Toast'
import BottomNav from './components/BottomNav'
import HomeScreen from './pages/HomeScreen'
import MyBetsScreen from './pages/MyBetsScreen'
import TrackerScreen from './pages/TrackerScreen'
import FixturesScreen from './pages/LiveScreen'
import BetPicksScreen from './pages/BetPicksScreen'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <Shell />
      </ToastProvider>
    </QueryClientProvider>
  )
}

function Shell() {
  const [tab, setTab]         = useState('home')     // 'home' | 'mybets'
  const [trackCode, setTrackCode] = useState(null)   // code being tracked

  function handleTrack(code) {
    if (code === null) { setTab('home'); return }
    setTrackCode(code.toUpperCase())
  }

  function handleBack() {
    setTrackCode(null)
  }

  const showTracker = Boolean(trackCode)

  return (
    <div className="app-shell">
      {/* Main content */}
      <AnimatePresence mode="wait">
        {showTracker ? (
          <motion.div
            key="tracker"
            initial={{ x: '100%', opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: '100%', opacity: 0 }}
            transition={{ type: 'tween', duration: 0.28 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          >
            <TrackerScreen code={trackCode} onBack={handleBack} />
          </motion.div>
        ) : tab === 'fixtures' ? (
          <motion.div
            key="fixtures"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          >
            <FixturesScreen />
          </motion.div>
        ) : tab === 'home' ? (
          <motion.div
            key="home"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          >
            <HomeScreen onTrack={handleTrack} onViewLive={() => setTab('fixtures')} />
          </motion.div>
        ) : tab === 'betpicks' ? (
          <motion.div
            key="betpicks"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          >
            <BetPicksScreen />
          </motion.div>
        ) : (
          <motion.div
            key="mybets"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
          >
            <MyBetsScreen onTrack={handleTrack} />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bottom nav — hidden while tracker is open */}
      {!showTracker && (
        <BottomNav active={tab} onChange={setTab} />
      )}
    </div>
  )
}

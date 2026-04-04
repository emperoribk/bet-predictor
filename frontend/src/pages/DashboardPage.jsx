import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, X, WifiOff, Home, Zap } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useLiveStats } from '../hooks/useLiveStats'
import { useBookingCodes } from '../hooks/useBookingCodes'
import { decodeBookingCode } from '../api/betApi'
import SummaryBanner from '../components/SummaryBanner'
import MatchCard from '../components/MatchCard'
import CountdownTimer from '../components/CountdownTimer'

/* ─── Add-code mini-form ───────────────────────────────────── */
function AddCodeForm({ onAdd }) {
  const [val, setVal] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    const code = val.trim().toUpperCase()
    if (!code) return
    setLoading(true); setErr('')
    try {
      await decodeBookingCode(code)
      onAdd(code)
      setVal('')
    } catch (error) {
      setErr(error?.response?.data?.error || 'Code not found')
    } finally { setLoading(false) }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-1">
      <div className="flex gap-2">
        <input value={val} onChange={e => setVal(e.target.value.toUpperCase())}
          placeholder="Enter code…"
          maxLength={20}
          className="w-36 bg-white/5 border border-white/10 focus:border-sky-500 rounded-lg px-3 py-1.5 text-xs font-mono text-white placeholder-slate-600 outline-none transition-all"
        />
        <button type="submit" disabled={loading || !val.trim()}
          className="bg-sky-500 hover:bg-sky-400 disabled:bg-slate-700 disabled:text-slate-500 text-white text-xs font-bold rounded-lg px-3 py-1.5 transition-colors flex items-center gap-1"
        >
          {loading ? <span className="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin" /> : <Zap size={11} />}
          Add
        </button>
      </div>
      {err && <p className="text-xs text-rose-400">{err}</p>}
    </form>
  )
}

/* ─── Single slip view ─────────────────────────────────────── */
function SlipView({ bookingCode }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, isFetching, dataUpdatedAt } = useLiveStats(bookingCode)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['liveStats', bookingCode] })

  if (isLoading) return (
    <div className="flex flex-col items-center justify-center py-24 gap-4">
      <div className="w-10 h-10 border-2 border-sky-800 border-t-sky-500 rounded-full animate-spin" />
      <p className="text-slate-500 text-sm">Loading matches…</p>
    </div>
  )

  if (isError) return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center py-24 gap-3"
    >
      <WifiOff size={36} className="text-slate-600" />
      <p className="text-slate-400 font-semibold">Could not load match data</p>
      <p className="text-slate-600 text-sm">Make sure the Django server is running</p>
      <button onClick={refresh}
        className="mt-2 bg-sky-600 hover:bg-sky-500 text-white text-sm font-bold rounded-xl px-5 py-2.5 transition-colors"
      >Retry</button>
    </motion.div>
  )

  if (!data) return null

  return (
    <div className="space-y-5">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-white">
            {data.matches?.length} match{data.matches?.length !== 1 ? 'es' : ''}
          </h2>
          <p className="text-xs text-slate-600">Last updated {new Date(dataUpdatedAt).toLocaleTimeString()}</p>
        </div>
        <CountdownTimer lastUpdated={dataUpdatedAt} onRefresh={refresh} isLoading={isFetching} />
      </div>

      {/* Summary */}
      <SummaryBanner summary={data.summary} accumulatorAlive={data.accumulator_alive} />

      {/* Match cards */}
      <div className="space-y-3">
        {data.matches?.map((match, i) => (
          <MatchCard key={match.id} match={match} index={i} />
        ))}
      </div>
    </div>
  )
}

/* ─── Main dashboard ───────────────────────────────────────── */
export default function DashboardPage() {
  const navigate = useNavigate()
  const { codes, activeCode, setActiveCode, addCode, removeCode } = useBookingCodes()
  const [showAddForm, setShowAddForm] = useState(false)

  const handleAdd = (code) => {
    addCode(code)
    setShowAddForm(false)
  }

  const handleRemove = (code, e) => {
    e.stopPropagation()
    removeCode(code)
  }

  if (codes.length === 0) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-grid">
        <p className="text-slate-400 font-semibold">No booking codes loaded</p>
        <button onClick={() => navigate('/')}
          className="bg-sky-500 hover:bg-sky-400 text-white font-bold rounded-xl px-6 py-3 transition-colors"
        >Go to home</button>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex flex-col bg-grid relative overflow-hidden">
      {/* Orbs */}
      <div className="orb w-[400px] h-[400px] bg-sky-700/8 top-[-100px] right-[-80px]" />

      {/* Navbar */}
      <nav className="sticky top-0 z-20 flex items-center justify-between px-4 sm:px-6 py-3 border-b border-white/5 bg-[#060d1b]/90 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/')}
            className="w-8 h-8 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 flex items-center justify-center transition-colors"
          >
            <Home size={14} className="text-slate-400" />
          </button>
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-sky-500/20 border border-sky-500/30 flex items-center justify-center">
              <span className="text-sm">⚽</span>
            </div>
            <span className="font-bold text-white text-sm">Bet<span className="text-sky-400">Watch</span></span>
          </div>
        </div>
        <span className="text-[10px] text-slate-600 hidden sm:block">Live Tracker</span>
      </nav>

      {/* Tab bar */}
      <div className="sticky top-[53px] z-10 bg-[#060d1b]/95 backdrop-blur-md border-b border-white/5 px-4 sm:px-6">
        <div className="flex items-center gap-1 overflow-x-auto py-2 scrollbar-none">
          {codes.map(code => (
            <button key={code} onClick={() => setActiveCode(code)}
              className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-mono font-bold whitespace-nowrap transition-all shrink-0 ${
                activeCode === code
                  ? 'bg-sky-500/20 border border-sky-500/40 text-sky-300'
                  : 'bg-white/4 border border-white/8 text-slate-500 hover:text-slate-300 hover:bg-white/8'
              }`}
            >
              {code}
              <span onClick={e => handleRemove(code, e)}
                className="ml-1 opacity-50 hover:opacity-100 hover:text-rose-400 transition-all cursor-pointer"
              >
                <X size={10} />
              </span>
            </button>
          ))}

          {/* Add code button / form */}
          {!showAddForm ? (
            codes.length < 6 && (
              <button onClick={() => setShowAddForm(true)}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-slate-600 hover:text-sky-400 border border-dashed border-white/10 hover:border-sky-700/50 transition-all shrink-0"
              >
                <Plus size={11} /> Add code
              </button>
            )
          ) : (
            <AnimatePresence>
              <motion.div initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
                className="flex items-center gap-2 shrink-0"
              >
                <AddCodeForm onAdd={handleAdd} />
                <button onClick={() => setShowAddForm(false)} className="text-slate-600 hover:text-slate-400">
                  <X size={13} />
                </button>
              </motion.div>
            </AnimatePresence>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="relative z-10 flex-1 max-w-3xl mx-auto w-full px-4 py-6">
        <AnimatePresence mode="wait">
          {activeCode && (
            <motion.div key={activeCode}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.2 }}
            >
              <SlipView bookingCode={activeCode} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

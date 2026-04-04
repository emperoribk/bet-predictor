import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { BarChart2, Bell, Zap, ArrowRight, Shield } from 'lucide-react'
import { decodeBookingCode } from '../api/betApi'
import { useBookingCodes } from '../hooks/useBookingCodes'

const features = [
  { Icon: Zap,       title: 'Instant Decode',     desc: 'Paste your SportyBet code and see all your matches in seconds.' },
  { Icon: BarChart2, title: 'Live Stats',          desc: 'Scores, possession and shots refreshed every 10 seconds.' },
  { Icon: Bell,      title: 'Danger Alerts',       desc: 'CRITICAL cards pulse live so you never miss a threat.' },
  { Icon: Shield,    title: 'Multi-Slip Tracker',  desc: 'Load up to 6 booking codes and track them all at once.' },
]

export default function HomePage() {
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const { codes, addCode } = useBookingCodes()

  const handleSubmit = async (e) => {
    e.preventDefault()
    const trimmed = code.trim().toUpperCase()
    if (!trimmed) return
    setLoading(true)
    setError('')
    try {
      await decodeBookingCode(trimmed)
      addCode(trimmed)
      navigate('/dashboard')
    } catch (err) {
      setError(err?.response?.data?.error || 'Could not decode this code. Please check and try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col relative overflow-hidden bg-grid">
      {/* Background orbs */}
      <div className="orb w-[600px] h-[600px] bg-sky-600/8  top-[-200px] left-[-150px]" />
      <div className="orb w-[500px] h-[500px] bg-indigo-600/8 bottom-[-100px] right-[-120px]" />

      {/* ── Navbar ───────────────────────────────────────── */}
      <nav className="relative z-10 flex items-center justify-between px-6 sm:px-10 py-5 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-sky-500/20 border border-sky-500/30 flex items-center justify-center text-xl">
            ⚽
          </div>
          <span className="text-xl font-black text-white tracking-tight">
            Bet<span className="text-sky-400">Watch</span>
          </span>
        </div>

        {codes.length > 0 && (
          <button
            onClick={() => navigate('/dashboard')}
            className="flex items-center gap-2 text-sm font-semibold text-sky-400 hover:text-sky-300 transition-colors"
          >
            Back to tracker <ArrowRight size={14} />
          </button>
        )}
      </nav>

      {/* ── Hero section ─────────────────────────────── */}
      <div className="relative z-10 flex flex-col items-center justify-center px-5 sm:px-8 pt-20 pb-16 sm:pt-28 sm:pb-20 min-h-[60vh]">

        <motion.div
          initial={{ opacity: 0, y: 32 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="w-full max-w-xl mx-auto text-center"
        >
          {/* Headline */}
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-black text-white leading-[1.12] tracking-tight mb-6">
            Never miss a{' '}
            <span className="gradient-text">danger sign</span>
          </h1>

          {/* Sub-text */}
          <p className="text-slate-400 text-base sm:text-lg leading-relaxed mb-12 max-w-md mx-auto">
            Paste your SportyBet booking code. We track every match live — goals, cards, momentum — and warn you before it&apos;s too late.
          </p>

          {/* ── Input card ─────────────────────────────── */}
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="glass rounded-3xl p-7 sm:p-9 border border-sky-800/30 glow-sky"
          >
            <label className="block text-left text-xs font-bold text-slate-500 uppercase tracking-widest mb-4">
              Booking Code
            </label>

            <form onSubmit={handleSubmit} className="space-y-4">
              {/* Input row */}
              <div className="flex flex-col sm:flex-row gap-3">
                <input
                  type="text"
                  value={code}
                  onChange={e => { setCode(e.target.value.toUpperCase()); setError('') }}
                  placeholder="e.g. XNK2LJ"
                  maxLength={20}
                  disabled={loading}
                  autoComplete="off"
                  className="flex-1 bg-white/5 border border-white/10 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 rounded-2xl px-5 py-4 text-white placeholder-slate-600 outline-none transition-all font-mono text-base tracking-widest"
                />
                <motion.button
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                  type="submit"
                  disabled={loading || !code.trim()}
                  className="bg-sky-500 hover:bg-sky-400 disabled:bg-slate-700 disabled:text-slate-500 text-white font-bold rounded-2xl px-7 py-4 transition-colors flex items-center justify-center gap-2 text-sm glow-sky whitespace-nowrap"
                >
                  {loading
                    ? <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    : <Zap size={15} />}
                  <span>{loading ? 'Analysing…' : 'Analyse Bet'}</span>
                </motion.button>
              </div>

              {/* Error */}
              {error && (
                <motion.p
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="text-xs text-rose-400 bg-rose-950/40 border border-rose-800/40 rounded-xl px-4 py-3 text-left"
                >
                  {error}
                </motion.p>
              )}
            </form>

            {/* Recent codes */}
            {codes.length > 0 && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="mt-6 pt-5 border-t border-white/5 flex flex-wrap items-center gap-2"
              >
                <span className="text-xs text-slate-600 font-medium">Recent:</span>
                {codes.map(c => (
                  <button
                    key={c}
                    onClick={() => { addCode(c); navigate('/dashboard') }}
                    className="flex items-center gap-1.5 glass-bright rounded-full px-3 py-1 text-xs font-mono font-semibold text-sky-400 hover:text-sky-300 border border-sky-800/30 hover:border-sky-600/40 transition-all"
                  >
                    {c} <ArrowRight size={9} />
                  </button>
                ))}
              </motion.div>
            )}
          </motion.div>
        </motion.div>
      </div>

      {/* ── Features section ─────────────────────────────── */}
      <div className="relative z-10 w-full px-5 sm:px-8 pb-20 sm:pb-28">
        {/* Divider */}
        <div className="w-full max-w-4xl mx-auto flex items-center gap-4 mb-10">
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
          <span className="text-xs text-slate-600 font-medium uppercase tracking-widest whitespace-nowrap">How it works</span>
          <div className="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
        </div>

        {/* Feature cards */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.5 }}
          className="w-full max-w-4xl mx-auto grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5"
        >
          {features.map(({ Icon, title, desc }, i) => (
            <motion.div
              key={title}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.45 + i * 0.07 }}
              className="glass rounded-2xl p-6 text-left group hover:border-sky-700/40 hover:bg-white/[0.03] transition-all"
            >
              <div className="w-10 h-10 rounded-xl bg-sky-950/70 border border-sky-800/40 flex items-center justify-center mb-4 group-hover:border-sky-600/50 transition-colors">
                <Icon size={18} className="text-sky-400" />
              </div>
              <h3 className="text-sm font-bold text-white mb-2">{title}</h3>
              <p className="text-xs text-slate-500 leading-relaxed">{desc}</p>
            </motion.div>
          ))}
        </motion.div>
      </div>

      {/* ── Footer ───────────────────────────────────────── */}
      <footer className="relative z-10 text-center py-6 text-xs text-slate-700 border-t border-white/5">
        BetWatch — powered by football-data.org
      </footer>
    </div>
  )
}

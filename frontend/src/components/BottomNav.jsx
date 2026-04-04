import { Home, BookOpen, CalendarDays, User } from 'lucide-react'
import { motion } from 'framer-motion'
import { useToast } from './Toast'

const tabs = [
  { id: 'home',     label: 'Home',     Icon: Home },
  { id: 'mybets',   label: 'My Bets',  Icon: BookOpen },
  { id: 'fixtures', label: 'Fixtures', Icon: CalendarDays },
  { id: 'profile',  label: 'Profile',  Icon: User },
]

export default function BottomNav({ active, onChange }) {
  const toast = useToast()

  function handleTab(id) {
    if (id === 'profile') {
      toast('Coming soon!')
      return
    }
    onChange(id)
  }

  return (
    <nav className="bottom-nav">
      {tabs.map(({ id, label, Icon }) => (
        <button
          key={id}
          className={`nav-tab ${active === id ? 'active' : ''}`}
          onClick={() => handleTab(id)}
        >
          {active === id && (
            <motion.div
              layoutId="nav-indicator"
              style={{
                position: 'absolute',
                top: 0,
                left: '50%',
                transform: 'translateX(-50%)',
                width: 28,
                height: 2.5,
                background: 'linear-gradient(90deg, #5cd9ff, #ffffff)',
                borderRadius: 2,
                boxShadow: '0 0 10px rgba(92,217,255,0.6)',
              }}
              transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            />
          )}
          <Icon size={20} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  )
}

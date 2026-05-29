import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import Feed from './pages/Feed.jsx'
import Members from './pages/Members.jsx'
import MemberDetail from './pages/MemberDetail.jsx'
import Tickers from './pages/Tickers.jsx'
import TickerDetail from './pages/TickerDetail.jsx'
import Signals from './pages/Signals.jsx'
import Sources from './pages/Sources.jsx'

export default function App() {
  return (
    <>
      <header className="topbar">
        <span className="brand">🏛️ Congress Trades</span>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/feed">Feed</NavLink>
          <NavLink to="/signals">Signals</NavLink>
          <NavLink to="/members">Members</NavLink>
          <NavLink to="/tickers">Tickers</NavLink>
          <NavLink to="/sources">Sources</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/feed" element={<Feed />} />
          <Route path="/members" element={<Members />} />
          <Route path="/members/:id" element={<MemberDetail />} />
          <Route path="/tickers" element={<Tickers />} />
          <Route path="/tickers/:symbol" element={<TickerDetail />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/sources" element={<Sources />} />
        </Routes>
      </main>
    </>
  )
}

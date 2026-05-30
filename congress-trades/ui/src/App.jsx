import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import Feed from './pages/Feed.jsx'
import Ideas from './pages/Ideas.jsx'
import Leaderboard from './pages/Leaderboard.jsx'
import Members from './pages/Members.jsx'
import MemberDetail from './pages/MemberDetail.jsx'
import Tickers from './pages/Tickers.jsx'
import TickerDetail from './pages/TickerDetail.jsx'
import Signals from './pages/Signals.jsx'
import Strategies from './pages/Strategies.jsx'
import Portfolio from './pages/Portfolio.jsx'
import Watchlist from './pages/Watchlist.jsx'
import Sources from './pages/Sources.jsx'
import GlobalSearch from './components/GlobalSearch.jsx'
import { WatchlistProvider } from './watchctx.jsx'

export default function App() {
  return (
    <WatchlistProvider>
      <header className="topbar">
        <span className="brand">🏛️ Congress Trades</span>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/ideas">Ideas</NavLink>
          <NavLink to="/strategies">Strategies</NavLink>
          <NavLink to="/feed">Feed</NavLink>
          <NavLink to="/signals">Signals</NavLink>
          <NavLink to="/leaderboard">Leaderboard</NavLink>
          <NavLink to="/members">Members</NavLink>
          <NavLink to="/tickers">Tickers</NavLink>
          <NavLink to="/portfolio">Portfolio</NavLink>
          <NavLink to="/watchlist">Watchlist</NavLink>
          <NavLink to="/sources">Sources</NavLink>
        </nav>
        <GlobalSearch />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/ideas" element={<Ideas />} />
          <Route path="/feed" element={<Feed />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/members" element={<Members />} />
          <Route path="/members/:id" element={<MemberDetail />} />
          <Route path="/tickers" element={<Tickers />} />
          <Route path="/tickers/:symbol" element={<TickerDetail />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/sources" element={<Sources />} />
        </Routes>
      </main>
    </WatchlistProvider>
  )
}

import { lazy, Suspense } from 'react'
import { Navigate, NavLink, Route, Routes } from 'react-router-dom'
import GlobalSearch from './components/GlobalSearch.jsx'
import { WatchlistProvider } from './watchctx.jsx'

const Dashboard = lazy(() => import('./pages/Dashboard.jsx'))
const Feed = lazy(() => import('./pages/Feed.jsx'))
const Ideas = lazy(() => import('./pages/Ideas.jsx'))
const Leaderboard = lazy(() => import('./pages/Leaderboard.jsx'))
const Members = lazy(() => import('./pages/Members.jsx'))
const MemberDetail = lazy(() => import('./pages/MemberDetail.jsx'))
const Tickers = lazy(() => import('./pages/Tickers.jsx'))
const TickerDetail = lazy(() => import('./pages/TickerDetail.jsx'))
const Signals = lazy(() => import('./pages/Signals.jsx'))
const DisclosureLag = lazy(() => import('./pages/DisclosureLag.jsx'))
const Strategies = lazy(() => import('./pages/Strategies.jsx'))
const Portfolio = lazy(() => import('./pages/Portfolio.jsx'))
const Watchlist = lazy(() => import('./pages/Watchlist.jsx'))
const Sources = lazy(() => import('./pages/Sources.jsx'))
const Status = lazy(() => import('./pages/Status.jsx'))
const Committees = lazy(() => import('./pages/Committees.jsx'))
const Reconciliation = lazy(() => import('./pages/Reconciliation.jsx'))
const PolicyContext = lazy(() => import('./pages/PolicyContext.jsx'))

export default function App() {
  const publicSite = window.CONGRESS_TRADES_CONFIG?.publicSite === true

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
          <NavLink to="/lag">Lag</NavLink>
          <NavLink to="/leaderboard">Leaderboard</NavLink>
          <NavLink to="/policy">Policy</NavLink>
          <NavLink to="/committees">Committees</NavLink>
          <NavLink to="/members">Members</NavLink>
          <NavLink to="/tickers">Tickers</NavLink>
          {!publicSite && <NavLink to="/portfolio">Portfolio</NavLink>}
          <NavLink to="/watchlist">Watchlist</NavLink>
          <NavLink to="/sources">Sources</NavLink>
          <NavLink to="/status">Status</NavLink>
        </nav>
        <GlobalSearch />
      </header>
      <main>
        <Suspense fallback={<div className="loading">Loading…</div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/ideas" element={<Ideas />} />
            <Route path="/feed" element={<Feed />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/lag" element={<DisclosureLag />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/portfolio" element={publicSite ? <Navigate to="/" replace /> : <Portfolio />} />
            <Route path="/leaderboard" element={<Leaderboard />} />
            <Route path="/policy" element={<PolicyContext />} />
            <Route path="/committees" element={<Committees />} />
            <Route path="/members" element={<Members />} />
            <Route path="/members/:id" element={<MemberDetail />} />
            <Route path="/tickers" element={<Tickers />} />
            <Route path="/tickers/:symbol" element={<TickerDetail />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/sources" element={<Sources />} />
            <Route path="/status" element={<Status />} />
            <Route path="/reconciliation" element={<Reconciliation />} />
          </Routes>
        </Suspense>
      </main>
    </WatchlistProvider>
  )
}

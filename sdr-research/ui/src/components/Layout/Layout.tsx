import { useState, useRef } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { useGlobalShortcuts } from "../../hooks/useKeyboardShortcuts";
import { useTheme } from "../../hooks/useTheme";
import { useNotifications } from "../../hooks/useNotifications";
import ShortcutsHelp from "../ShortcutsHelp";
import { CommandPalette } from "../CommandPalette";

type NavEntry = {
  label: string;
  path: string;
  children?: { label: string; path: string }[];
};

const navItems: NavEntry[] = [
  { path: "/", label: "Dashboard" },
  { path: "/browse", label: "Browse" },
  { path: "/map", label: "Map" },
  {
    path: "/spectrum",
    label: "Signals",
    children: [
      { path: "/spectrum", label: "Spectrum" },
      { path: "/waterfall", label: "Waterfall" },
      { path: "/spots", label: "HF Spots" },
    ],
  },
  {
    path: "/aprs",
    label: "Decoders",
    children: [
      { path: "/aprs", label: "APRS" },
      { path: "/hf", label: "HFDL" },
      { path: "/sstv", label: "SSTV" },
      { path: "/satellites", label: "Satellites" },
    ],
  },
  {
    path: "/frequencies",
    label: "Frequencies",
    children: [
      { path: "/frequencies", label: "Freq DB" },
      { path: "/repeaters", label: "Repeaters" },
      { path: "/bookmarks", label: "Bookmarks" },
    ],
  },
  {
    path: "/alerts",
    label: "Tools",
    children: [
      { path: "/alerts", label: "Alerts" },
      { path: "/logbook", label: "Logbook" },
      { path: "/tags", label: "Tags" },
      { path: "/lookup", label: "Lookup" },
    ],
  },
];

function NavDropdown({
  item,
  isActive,
}: {
  item: NavEntry;
  isActive: (p: string) => boolean;
}) {
  const [open, setOpen] = useState(false);
  const timeout = useRef<ReturnType<typeof setTimeout>>();

  const anyActive = item.children!.some((c) => isActive(c.path));

  return (
    <div
      className="relative"
      onMouseEnter={() => {
        clearTimeout(timeout.current);
        setOpen(true);
      }}
      onMouseLeave={() => {
        timeout.current = setTimeout(() => setOpen(false), 150);
      }}
    >
      <button
        className={`px-3 py-2 rounded-md text-sm font-medium transition-colors inline-flex items-center gap-1 ${
          anyActive
            ? "bg-gray-700 text-white"
            : "text-gray-300 hover:bg-gray-700 hover:text-white"
        }`}
        onClick={() => setOpen((o) => !o)}
      >
        {item.label}
        <svg
          className={`w-3 h-3 opacity-50 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 bg-gray-800 border border-gray-600 rounded-md shadow-xl py-1 min-w-[150px] z-50">
          {item.children!.map((child) => (
            <Link
              key={child.path}
              to={child.path}
              onClick={() => setOpen(false)}
              className={`block px-4 py-2 text-sm transition-colors whitespace-nowrap ${
                isActive(child.path)
                  ? "bg-gray-700 text-white"
                  : "text-gray-300 hover:bg-gray-700 hover:text-white"
              }`}
            >
              {child.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Layout() {
  useGlobalShortcuts();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobileExpanded, setMobileExpanded] = useState<string | null>(null);
  const { theme, toggle: toggleTheme } = useTheme();
  const {
    enabled: notificationsEnabled,
    permission: notifPermission,
    toggle: toggleNotifications,
  } = useNotifications();

  const isActive = (path: string) =>
    path === "/"
      ? location.pathname === "/"
      : location.pathname.startsWith(path);

  return (
    <div className="min-h-screen flex flex-col min-w-0 overflow-x-hidden">
      <header className="bg-gray-800 border-b border-gray-700">
        <div className="max-w-7xl mx-auto px-3 sm:px-4 py-2 sm:py-3">
          <div className="flex items-center justify-between gap-4">
            <Link
              to="/"
              className="text-xl font-bold text-green-400 shrink-0"
            >
              W3RDW SDR
            </Link>

            {/* Desktop nav */}
            <nav className="hidden md:flex gap-1 items-center flex-1 justify-center">
              {navItems.map((item) =>
                item.children ? (
                  <NavDropdown
                    key={item.label}
                    item={item}
                    isActive={isActive}
                  />
                ) : (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={`px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                      isActive(item.path)
                        ? "bg-gray-700 text-white"
                        : "text-gray-300 hover:bg-gray-700 hover:text-white"
                    }`}
                  >
                    {item.label}
                  </Link>
                ),
              )}
            </nav>

            <div className="hidden md:flex items-center gap-1 shrink-0">
              {/* Search / Command Palette */}
              <button
                className="inline-flex items-center gap-1.5 h-7 px-2 rounded border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors text-xs"
                onClick={() => document.dispatchEvent(new CustomEvent("open-command-palette"))}
                title="Search (⌘K)"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <kbd className="text-[10px] text-gray-500 font-mono">⌘K</kbd>
              </button>

              {/* Admin gear */}
              <Link
                to="/admin"
                className={`inline-flex items-center justify-center w-7 h-7 rounded border transition-colors ${
                  isActive("/admin")
                    ? "border-green-500 text-green-400"
                    : "border-gray-600 text-gray-400 hover:text-white hover:border-gray-400"
                }`}
                title="Admin"
              >
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z"
                  />
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                  />
                </svg>
              </Link>

              {/* Notification toggle */}
              <button
                className={`inline-flex items-center justify-center w-7 h-7 rounded border transition-colors shrink-0 ${
                  notificationsEnabled
                    ? "border-green-500 text-green-400 hover:text-green-300 hover:border-green-400"
                    : "border-gray-600 text-gray-400 hover:text-white hover:border-gray-400"
                }`}
                onClick={toggleNotifications}
                aria-label={
                  notificationsEnabled
                    ? "Disable browser notifications"
                    : "Enable browser notifications"
                }
                title={
                  notifPermission === "denied"
                    ? "Notifications blocked by browser"
                    : notificationsEnabled
                      ? "Notifications ON - click to disable"
                      : "Notifications OFF - click to enable"
                }
              >
                {notificationsEnabled ? (
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
                    />
                  </svg>
                ) : (
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M9.143 17.082a24.248 24.248 0 003.714.318 23.997 23.997 0 002-.143m-5.714-.175a23.848 23.848 0 01-5.454-1.31A8.967 8.967 0 006 9.75V9a6 6 0 0112 0v.75c0 1.768.521 3.445 1.476 4.843M9.143 17.082a3 3 0 105.714 0"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3 3l18 18"
                    />
                  </svg>
                )}
              </button>

              {/* Theme toggle */}
              <button
                className="inline-flex items-center justify-center w-7 h-7 rounded border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors shrink-0"
                onClick={toggleTheme}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
                title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              >
                {theme === "dark" ? (
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
                    />
                  </svg>
                ) : (
                  <svg
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
                    />
                  </svg>
                )}
              </button>

              {/* Keyboard shortcuts help */}
              <button
                className="inline-flex items-center justify-center w-7 h-7 rounded border border-gray-600 text-gray-400 hover:text-white hover:border-gray-400 transition-colors text-sm font-mono shrink-0"
                onClick={() =>
                  document.dispatchEvent(
                    new CustomEvent("toggle-shortcuts-help"),
                  )
                }
                aria-label="Keyboard shortcuts"
                title="Keyboard shortcuts (?)"
              >
                ?
              </button>
            </div>

            {/* Mobile: icons + hamburger */}
            <div className="md:hidden flex items-center gap-1">
              <button
                className={`p-2 rounded ${
                  notificationsEnabled
                    ? "text-green-400 hover:text-green-300"
                    : "text-gray-300 hover:text-white"
                }`}
                onClick={toggleNotifications}
                aria-label={
                  notificationsEnabled
                    ? "Disable notifications"
                    : "Enable notifications"
                }
              >
                {notificationsEnabled ? (
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0"
                    />
                  </svg>
                ) : (
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M9.143 17.082a24.248 24.248 0 003.714.318 23.997 23.997 0 002-.143m-5.714-.175a23.848 23.848 0 01-5.454-1.31A8.967 8.967 0 006 9.75V9a6 6 0 0112 0v.75c0 1.768.521 3.445 1.476 4.843M9.143 17.082a3 3 0 105.714 0"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3 3l18 18"
                    />
                  </svg>
                )}
              </button>
              <button
                className="text-gray-300 hover:text-white p-2 rounded"
                onClick={toggleTheme}
                aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              >
                {theme === "dark" ? (
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
                    />
                  </svg>
                ) : (
                  <svg
                    className="w-5 h-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={2}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
                    />
                  </svg>
                )}
              </button>
              <button
                className="text-gray-300 hover:text-white p-2 rounded"
                onClick={() => setMenuOpen((o) => !o)}
                aria-label="Toggle menu"
              >
                <svg
                  className="w-6 h-6"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  {menuOpen ? (
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  ) : (
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 6h16M4 12h16M4 18h16"
                    />
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* Mobile menu */}
          {menuOpen && (
            <nav className="md:hidden mt-3 pb-2 space-y-1">
              {navItems.map((item) =>
                item.children ? (
                  <div key={item.label}>
                    <button
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                        item.children.some((c) => isActive(c.path))
                          ? "bg-gray-700 text-white"
                          : "text-gray-300 hover:bg-gray-700 hover:text-white"
                      }`}
                      onClick={() =>
                        setMobileExpanded(
                          mobileExpanded === item.label ? null : item.label,
                        )
                      }
                    >
                      {item.label}
                      <svg
                        className={`w-4 h-4 transition-transform ${mobileExpanded === item.label ? "rotate-180" : ""}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M19 9l-7 7-7-7"
                        />
                      </svg>
                    </button>
                    {mobileExpanded === item.label && (
                      <div className="ml-4 mt-1 space-y-1">
                        {item.children.map((child) => (
                          <Link
                            key={child.path}
                            to={child.path}
                            onClick={() => setMenuOpen(false)}
                            className={`block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                              isActive(child.path)
                                ? "bg-gray-700 text-white"
                                : "text-gray-300 hover:bg-gray-700 hover:text-white"
                            }`}
                          >
                            {child.label}
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <Link
                    key={item.path}
                    to={item.path}
                    onClick={() => setMenuOpen(false)}
                    className={`block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                      isActive(item.path)
                        ? "bg-gray-700 text-white"
                        : "text-gray-300 hover:bg-gray-700 hover:text-white"
                    }`}
                  >
                    {item.label}
                  </Link>
                ),
              )}
              <Link
                to="/admin"
                onClick={() => setMenuOpen(false)}
                className={`block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive("/admin")
                    ? "bg-gray-700 text-white"
                    : "text-gray-300 hover:bg-gray-700 hover:text-white"
                }`}
              >
                Admin
              </Link>
            </nav>
          )}
        </div>
      </header>
      <main className="flex-1 max-w-7xl mx-auto w-full min-w-0 px-3 sm:px-4 py-3 sm:py-6">
        <Outlet />
      </main>
      <ShortcutsHelp />
      <CommandPalette />
    </div>
  );
}

export default Layout;

import { LogOut, Menu, Search, UserRound, Wrench, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import { portalPagePath } from './portals.js'

function PageSearch({ portal }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const search = query.trim().toLowerCase()
  const results = search
    ? portal.pages.filter(({ label, title = '' }) => `${label} ${title}`.toLowerCase().includes(search))
    : []

  function handleSubmit(event) {
    event.preventDefault()
    if (results.length) {
      navigate(portalPagePath(portal, results[0]))
      setQuery('')
    }
  }

  return (
    <form
      className="page-search"
      role="search"
      onSubmit={handleSubmit}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setQuery('')
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setQuery('')
      }}
    >
      <label className="search-input">
        <Search size={19} aria-hidden="true" />
        <span className="sr-only">Search portal pages</span>
        <input
          type="search"
          placeholder="Search portal pages…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {search && (
        <div className="header-popover">
          {results.length ? (
            <ul>
              {results.map((page) => (
                <li key={page.path}>
                  <Link to={portalPagePath(portal, page)} onClick={() => setQuery('')}>{page.label}</Link>
                </li>
              ))}
            </ul>
          ) : <p role="status">No matching pages.</p>}
        </div>
      )}
    </form>
  )
}

function RequestSearch() {
  const navigate = useNavigate()

  function handleSubmit(event) {
    event.preventDefault()
    const form = event.currentTarget
    const query = String(new FormData(form).get('q') ?? '').trim()
    const search = query ? `?${new URLSearchParams({ q: query })}` : ''
    navigate({ pathname: '/requester/requests', search })
    form.reset()
  }

  return (
    <form className="page-search" role="search" onSubmit={handleSubmit}>
      <label className="search-input">
        <Search size={19} aria-hidden="true" />
        <span className="sr-only">Search requests</span>
        <input type="search" name="q" maxLength={120} placeholder="Search requests…" />
      </label>
    </form>
  )
}

function PortalHeader({ portal, fullName, menuOpen, onMenuToggle }) {
  const isRequester = portal.id === 'requester'

  return (
    <header className="portal-header glass">
      <div className="portal-heading">
        <div className="portal-title">
          <Link to={`/${portal.id}`}>FieldFix AI 2.0</Link>
          <span>{portal.label} Portal</span>
        </div>
      </div>

      <div className="header-tools">
        {isRequester ? <RequestSearch /> : <PageSearch key={portal.id} portal={portal} />}
        <span className="portal-account" title={fullName}>{fullName}</span>
        {isRequester && (
          <Link className="request-profile-link" to="/requester/profile" aria-label="My Profile">
            <UserRound size={20} aria-hidden="true" />
          </Link>
        )}
        <button
          className="mobile-menu"
          type="button"
          aria-label={menuOpen ? 'Close portal navigation' : 'Open portal navigation'}
          aria-controls="portal-navigation"
          aria-expanded={menuOpen}
          onClick={onMenuToggle}
        >
          {menuOpen ? <X size={22} aria-hidden="true" /> : <Menu size={22} aria-hidden="true" />}
        </button>
      </div>
    </header>
  )
}

function PortalSidebar({ portal, menuOpen, closeNavigation, onSignOut }) {
  const Emblem = portal.emblem ?? Wrench

  return (
    <aside id="portal-navigation" className={`portal-sidebar${menuOpen ? ' is-open' : ''}`}>
      <Link
        className="rail-brand"
        to={`/${portal.id}`}
        aria-label={`${portal.label} dashboard`}
      >
        <Emblem size={24} aria-hidden="true" />
      </Link>
      <nav className="rail-nav" aria-label={`${portal.label} navigation`}>
        <ul>
          {portal.pages.map(({ icon: Icon, ...page }) => (
            <li key={page.path}>
              <NavLink
                className="rail-link"
                to={portalPagePath(portal, page)}
                end={!page.path}
                onClick={closeNavigation}
              >
                <span className="rail-label">{page.label}</span>
                <span className="rail-icon"><Icon size={21} strokeWidth={1.6} aria-hidden="true" /></span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <button className="rail-link rail-exit" type="button" onClick={onSignOut}>
        <span className="rail-label">Log out</span>
        <span className="rail-icon"><LogOut size={21} strokeWidth={1.6} aria-hidden="true" /></span>
      </button>
    </aside>
  )
}

export default function PortalLayout({ portal }) {
  const { user, signOut } = useAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const main = useRef(null)
  const { pathname, state } = useLocation()

  useEffect(() => {
    main.current?.focus({ preventScroll: true })
    window.scrollTo(0, 0)
  }, [pathname])

  function closeNavigation() {
    setMenuOpen(false)
  }

  return (
    <div className="portal-shell" data-portal={portal.id}>
      <a className="skip-link" href="#portal-content">Skip to content</a>
      <PortalHeader portal={portal} fullName={user.full_name} menuOpen={menuOpen} onMenuToggle={() => setMenuOpen((open) => !open)} />
      <PortalSidebar portal={portal} menuOpen={menuOpen} closeNavigation={closeNavigation} onSignOut={signOut} />
      <main id="portal-content" className="portal-content" tabIndex={-1} ref={main}>
        {state?.portalNotice && <p className="glass auth-portal-notice" role="status">{state.portalNotice}</p>}
        <Outlet />
      </main>
    </div>
  )
}

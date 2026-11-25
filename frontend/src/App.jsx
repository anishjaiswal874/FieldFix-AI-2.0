import { lazy, Suspense } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router'
import { AdminCategories, AdminDashboard, AdminSettings, AdminTeams, AdminUsers } from './AdminPages.jsx'
import { useAuth } from './AuthContext.jsx'
import LoginPage, { PasswordResetPage, RegisterPage } from './LoginPage.jsx'
import Panel from './Panel.jsx'
import PortalLayout from './PortalLayout.jsx'
import { PortalPage } from './PortalPages.jsx'
import { RequestReview, WorkOrderDetails } from './ReviewDetails.jsx'
import ResolutionView from './ResolutionView.jsx'
import { ReviewerHistory, ReviewerQueue, ReviewerWorkOrders } from './ReviewerPages.jsx'
import RequesterForm from './RequesterForm.jsx'
import { RequesterDashboard, RequesterRequests, RequestDetails } from './RequesterPages.jsx'
import RequesterProfile from './RequesterProfile.jsx'
import { portals } from './portals.js'

const ReviewerDashboard = lazy(() => import('./ReviewerAnalytics.jsx').then((module) => ({ default: module.ReviewerDashboard })))
const ReviewerImageAnalytics = lazy(() => import('./ReviewerAnalytics.jsx').then((module) => ({ default: module.ReviewerImageAnalytics })))

const requesterPages = {
  '': RequesterDashboard,
  new: RequesterForm,
  requests: RequesterRequests,
  profile: RequesterProfile,
}

const reviewerPages = {
  '': ReviewerDashboard,
  triage: ReviewerQueue,
  'image-analytics': ReviewerImageAnalytics,
  'work-orders': ReviewerWorkOrders,
  history: ReviewerHistory,
}

const adminPages = {
  '': AdminDashboard,
  users: AdminUsers,
  categories: AdminCategories,
  teams: AdminTeams,
  settings: AdminSettings,
}

const portalPages = { requester: requesterPages, reviewer: reviewerPages, admin: adminPages }

function portalPage(portal, page) {
  const Page = portalPages[portal.id][page.path]
  if (Page === ReviewerDashboard || Page === ReviewerImageAnalytics) {
    return <Suspense fallback={<p role="status">Loading reviewer analytics…</p>}><Page /></Suspense>
  }
  return Page ? <Page /> : <PortalPage page={page} />
}

function NotFound({ home = '/login' }) {
  return (
    <Panel>
      <h1>Page not found</h1>
      <Link className="text-link" to={home}>Go back to the portal home</Link>
    </Panel>
  )
}

function ProtectedPortal({ portal }) {
  const { user, accessDenied, signOut } = useAuth()
  if (!user) return <Navigate replace to="/login" state={{ portal: portal.id }} />
  if (accessDenied) {
    return (
      <main className="standalone-page">
        <Panel>
          <h1>Access denied</h1>
          <p>Your account does not have permission to access the portal.</p>
          <button className="text-link" type="button" onClick={signOut}>Log out</button>
        </Panel>
      </main>
    )
  }
  if (user.role !== portal.id) {
    const assigned = portals.find(({ id }) => id === user.role)
    return <main className="standalone-page"><Panel>
      <h1>Access denied</h1>
      <p>You are signed in as {assigned.label}. This page requires the {portal.label} role.</p>
      <Link className="text-link" to={`/${user.role}`}>Continue to my portal</Link>
      <button className="text-link" type="button" onClick={signOut}>Sign out to use another account</button>
    </Panel></main>
  }
  return <PortalLayout portal={portal} />
}

export default function App() {
  const { starting } = useAuth()
  if (starting) return <main className="login-page" role="status">Checking session…</main>
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<PasswordResetPage />} />
      <Route path="/register" element={<RegisterPage />} />

      {portals.map((portal) => (
        <Route key={portal.id} path={portal.id} element={<ProtectedPortal portal={portal} />}>
          {portal.pages.map((page) => (
            <Route
              key={page.path}
              index={!page.path}
              path={page.path || undefined}
              element={portalPage(portal, page)}
            />
          ))}
          {portal.id === 'reviewer' && (
            <>
              <Route path="triage/review" element={<Navigate replace to="/reviewer/triage" />} />
              <Route path="triage/:requestId" element={<RequestReview />} />
              <Route path="work-orders/:orderId" element={<WorkOrderDetails />} />
              <Route path="work-orders/:orderId/resolve" element={<ResolutionView />} />
              <Route path="teams" element={<Navigate replace to="/reviewer/triage#team-workload" />} />
              <Route path="safety-alerts" element={<Navigate replace to="/reviewer/work-orders?status=safety" />} />
              <Route path="performance" element={<Navigate replace to="/reviewer#performance" />} />
              <Route path="settings" element={<Navigate replace to="/reviewer" />} />
            </>
          )}
          {portal.id === 'requester' && <Route path="requests/:requestId" element={<RequestDetails />} />}
          <Route path="*" element={<NotFound home={`/${portal.id}`} />} />
        </Route>
      ))}

      <Route path="*" element={<main className="standalone-page"><NotFound /></main>} />
    </Routes>
  )
}

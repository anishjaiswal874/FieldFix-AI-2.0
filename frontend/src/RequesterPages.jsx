import {
  ArrowLeft, ArrowRight, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert,
  ClipboardList, Clock, FileText, List, Plus, Search, Wrench,
} from 'lucide-react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router'
import AttachmentPhoto from './AttachmentPhoto.jsx'
import { ResolutionEvidence } from './ResolutionView.jsx'
import Panel, { PanelHeading } from './Panel.jsx'
import { REQUESTS_PATH, useRequestData } from './requestApi.js'

const filterLabels = { all: 'All', pending_review: 'Pending Review', needs_info: 'Action Required', completed: 'Completed' }
const statusLabels = {
  submitted: 'Submitted', in_review: 'In Review', needs_info: 'Action Required',
  approved: 'Approved', rejected: 'Rejected', completed: 'Completed',
}
const statusColors = { pending_review: 'in-review', in_review: 'in-review', needs_info: 'action-required' }
const filters = Object.keys(filterLabels)
const dashboardCards = [
  { status: 'all', icon: FileText, title: 'Total Requests' },
  { status: 'pending_review', icon: Clock },
  { status: 'completed', icon: CheckCircle2 },
  { status: 'needs_info', icon: CircleAlert },
]
const pageSize = 5
const dateFormat = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'UTC' })

function StatusBadge({ status }) {
  return <span className="request-status" data-status={statusColors[status] ?? status}>{statusLabels[status] ?? status}</span>
}

function requestNumber(id) { return `FF-${String(id).padStart(3, '0')}` }

function countRequests(data, filter) {
  if (!data) return '—'
  const counts = data.status_counts
  if (filter === 'all') return Object.values(counts).reduce((total, count) => total + count, 0)
  if (filter === 'pending_review') return (counts.submitted ?? 0) + (counts.in_review ?? 0)
  return counts[filter] ?? 0
}

function RequestDate({ date }) {
  return <time dateTime={date}>{dateFormat.format(new Date(date))}</time>
}

function RequestTable({ result, recent = false, search = '' }) {
  const rows = result.data?.items ?? []
  return (
    <div className="preview-table request-table" role="region" aria-label={recent ? 'Recent requests' : 'My requests'} tabIndex={0}>
      <table>
        <thead>
          <tr>
            <th scope="col" className="request-meta-cell">Request ID</th>
            <th scope="col">Title</th>
            {!recent && <th scope="col" className="request-meta-cell">Category</th>}
            <th scope="col" className="request-meta-cell">Date</th>
            <th scope="col">Status</th>
            <th scope="col">Completion</th>
            {!recent && <th scope="col">Action</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((request) => {
            const details = { pathname: `/requester/requests/${request.id}`, search }

            return (
              <tr key={request.id}>
                <td className="request-meta-cell">{requestNumber(request.id)}</td>
                <td>{recent ? <Link className="request-title-link" to={details}>{request.title}</Link> : request.title}</td>
                {!recent && <td className="request-meta-cell">{request.selected_category?.name ?? '—'}</td>}
                <td className="request-meta-cell"><RequestDate date={request.created_at} /></td>
                <td><StatusBadge status={request.status} /></td>
                <td>{request.status === 'completed' ? <StatusBadge status="completed" /> :
                  request.status === 'rejected' ? <span className="request-status">Closed</span> :
                  <span className="request-status" data-status={request.status === 'approved' ? 'in-review' : 'submitted'}>
                    {request.status === 'approved' ? 'In Progress' : 'Pending'}
                  </span>}</td>
                {!recent && (
                  <td><Link className="text-link" to={details} aria-label={`View details for ${request.title}`}>View</Link></td>
                )}
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr><td className="requests-empty" colSpan={recent ? 5 : 7}>
              <RequestState result={result} empty={recent ? 'You have not submitted any requests yet.' : 'No requests match your filters.'} />
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function RequestState({ result, empty }) {
  if (result.loading) return <p role="status">Loading requests…</p>
  if (result.error) return (
    <div><p role="alert">{result.error}</p><button className="text-link" type="button" onClick={result.retry}>Try again</button></div>
  )
  return <p role="status">{empty}</p>
}

export function RequesterDashboard() {
  const result = useRequestData(REQUESTS_PATH)
  return (
    <div className="requester-stack">
      <Panel className="requester-welcome">
        <PanelHeading title="Welcome, Requester" description="Here’s an overview of your maintenance requests." />
        <div className="request-stats">
          {dashboardCards.map(({ status, icon: Icon, title }) => (
            <Link
              className="request-stat"
              data-status={statusColors[status] ?? status}
              to={status === 'all' ? '/requester/requests' : `/requester/requests?status=${status}`}
              key={status}
            >
              <Icon className="request-stat-icon" size={35} strokeWidth={1.75} aria-hidden="true" />
              <div><strong>{countRequests(result.data, status)}</strong><span>{title || filterLabels[status]}</span></div>
            </Link>
          ))}
        </div>
      </Panel>

      <div className="requester-dashboard-grid">
        <Panel>
          <PanelHeading
            as="h2"
            icon={Clock}
            title="Recent Requests"
            actions={<Link className="request-inline-link" to="/requester/requests">View All <ArrowRight size={18} aria-hidden="true" /></Link>}
          />
          <RequestTable result={result} recent />
        </Panel>

        <Panel className="request-callout">
          <div className="request-illustration" aria-hidden="true">
            <ClipboardList className="request-illustration-clipboard" size={114} strokeWidth={1.2} />
            <Wrench className="request-illustration-wrench" size={51} strokeWidth={1.6} />
          </div>
          <div>
            <h2>Report a Maintenance Issue</h2>
            <p>Found a problem in the facility? Submit a request with details and photos.</p>
          </div>
          <Link className="request-primary" to="/requester/new"><Plus size={20} aria-hidden="true" /> New Request</Link>
        </Panel>
      </div>
    </div>
  )
}

export function RequesterRequests() {
  const [params, setParams] = useSearchParams()
  const { search } = useLocation()
  const status = filters.includes(params.get('status')) ? params.get('status') : 'all'
  const query = (params.get('q') ?? '').slice(0, 120)
  const pageParam = Number(params.get('page'))
  const requestedPage = Number.isInteger(pageParam) ? Math.min(1_000_000, Math.max(1, pageParam)) : 1
  const apiParams = new URLSearchParams({ page: requestedPage, per_page: pageSize })
  if (query.trim()) apiParams.set('q', query.trim())
  if (status !== 'all') apiParams.set('status', status)
  const result = useRequestData(`${REQUESTS_PATH}?${apiParams}`)
  const pageCount = Math.max(1, Math.ceil((result.data?.total ?? 0) / pageSize))
  const page = result.data?.page ?? 1
  const firstVisiblePage = Math.max(1, Math.min(page - 2, pageCount - 4))

  function filterRequests(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page')
    setParams(next, { replace: key === 'q' })
  }

  return (
    <Panel className="requester-screen">
      <PanelHeading
        icon={List}
        title="My Requests"
        description="Track the status and details of your maintenance requests."
      />
      <div className="request-filters">
        <div className="request-filter-buttons" role="group" aria-label="Filter requests by status">
          {filters.map((filter) => (
            <button type="button" key={filter} aria-pressed={status === filter} onClick={() => filterRequests('status', filter === 'all' ? '' : filter)}>
              {filterLabels[filter]} ({countRequests(result.data, filter)})
            </button>
          ))}
        </div>
        <label className="search-input request-list-search">
          <Search size={18} aria-hidden="true" />
          <span className="sr-only">Search requests</span>
          <input type="search" value={query} maxLength={120} placeholder="Search requests…" onChange={(event) => filterRequests('q', event.target.value)} />
        </label>
      </div>
      {result.data && <p className="sr-only" role="status">{result.data.total} matching requests. Page {page} of {pageCount}.</p>}
      <RequestTable result={result} search={search} />
      <nav className="request-pagination" aria-label="Request pages">
        <button type="button" disabled={page === 1 || !result.data} onClick={() => filterRequests('page', page - 1)}><ChevronLeft size={15} aria-hidden="true" /> Previous</button>
        {result.data && Array.from({ length: Math.min(pageCount, 5) }, (_, index) => index + firstVisiblePage).map((number) => (
          <button type="button" key={number} aria-label={`Page ${number}`} aria-current={page === number ? 'page' : undefined} onClick={() => filterRequests('page', number)}>{number}</button>
        ))}
        <button type="button" disabled={page === pageCount || !result.data} onClick={() => filterRequests('page', page + 1)}>Next <ChevronRight size={15} aria-hidden="true" /></button>
      </nav>
    </Panel>
  )
}

export function RequestDetails() {
  const { requestId } = useParams()
  const { search } = useLocation()
  const result = useRequestData(`${REQUESTS_PATH}/${encodeURIComponent(requestId)}`)
  const request = result.data

  return (
    <Panel className="requester-screen">
      <Link className="request-inline-link request-back" to={{ pathname: '/requester/requests', search }}>
        <ArrowLeft size={18} aria-hidden="true" /> Back to My Requests
      </Link>
      {request ? (
        <>
          <PanelHeading title={request.title} description={requestNumber(request.id)} icon={FileText} />
          <dl className="request-detail-list">
            <div><dt>Status</dt><dd><StatusBadge status={request.status} /></dd></div>
            <div><dt>Date</dt><dd><RequestDate date={request.created_at} /></dd></div>
            <div><dt>Location</dt><dd>{request.location}</dd></div>
            <div><dt>Category</dt><dd>{request.selected_category?.name ?? 'Not selected'}</dd></div>
          </dl>
          <h2>Request Details</h2>
          <p className="request-detail-description">{request.description}</p>
          {request.resolution && <section className="request-detail-photos"><h2>Resolution</h2>
            <ResolutionEvidence resolution={request.resolution} requestId={request.id} base={REQUESTS_PATH} />
          </section>}
          {request.information_requested && (
            <section className="request-detail-photos">
              <h2>Information requested</h2>
              <p className="request-detail-description">{request.information_requested}</p>
            </section>
          )}
          {request.attachments.length > 0 && (
            <section className="request-detail-photos" aria-labelledby="request-photos-heading">
              <h2 id="request-photos-heading">Photos</h2>
              <ul className="request-photos">
                {request.attachments.map((attachment) => (
                  <AttachmentPhoto key={`${request.id}:${attachment.id}`} path={`${REQUESTS_PATH}/${request.id}/attachments/${attachment.id}`} attachment={attachment} />
                ))}
              </ul>
            </section>
          )}
        </>
      ) : <><h1>Request details</h1><RequestState result={result} empty="Request not found." /></>}
    </Panel>
  )
}

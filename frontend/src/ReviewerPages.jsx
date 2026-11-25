import { Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import Panel from './Panel.jsx'
import { ResolutionEvidence } from './ResolutionView.jsx'
import { useRequestData } from './requestApi.js'
import './reviewer-analytics.css'

export const REVIEWER_API = '/api/reviewer'
export const requestLabel = (id) => 'FF-' + String(id).padStart(3, '0')
export const workOrderLabel = (id) => 'WO-' + String(id).padStart(3, '0')
export const isReviewable = (status) => status === 'submitted' || status === 'in_review'

const labels = {
  submitted: 'Submitted', in_review: 'In Review', needs_info: 'Needs Info',
  approved: 'Approved', rejected: 'Rejected', completed: 'Completed',
  open: 'Open', in_progress: 'In Progress', resolved: 'Resolved',
  low: 'Low', medium: 'Medium', high: 'High', safety: 'Safety Alerts',
}
const analysisLabels = {
  not_analyzed: 'Not analyzed',
  abstained: 'Abstained',
  nlp: 'NLP Analyzed',
  cv: 'CV Screened',
  nlp_cv: 'NLP + CV',
  priority: 'Priority Evaluated',
  analyzed: 'Analyzed',
}
const dateFormatter = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeZone: 'UTC' })
const totalCount = (counts = {}) => Object.values(counts).reduce((sum, count) => sum + count, 0)

export function ReviewBadge({ status }) {
  return <span className="review-badge" data-status={status}>{labels[status] ?? status}</span>
}

export function ReviewDate({ value }) {
  if (!value) return '—'
  const timestamp = /(?:Z|[+-]\d\d:\d\d)$/i.test(value) ? value : value + 'Z'
  return <time dateTime={timestamp}>{dateFormatter.format(new Date(timestamp))}</time>
}

export function ReviewState({ result, empty }) {
  if (result.loading) return <p className="review-message" role="status">Loading…</p>
  if (result.error) return (
    <div className="review-message">
      <p role="alert">{result.error}</p>
      <button className="text-link" type="button" onClick={result.retry}>Try again</button>
    </div>
  )
  return <p className="review-message" role="status">{empty}</p>
}

function ReviewTable({ title, columns, result, row, empty }) {
  const items = result.data?.items ?? []
  return (
    <div className="preview-table review-table" role="region" aria-label={title} tabIndex={0}>
      <table>
        <thead><tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
        <tbody>
          {items.map((item) => <tr key={item.id}>{row(item)}</tr>)}
          {items.length === 0 && <tr><td colSpan={columns.length}><ReviewState result={result} empty={empty} /></td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function RequestRow({ request }) {
  return (
    <>
      <td>{requestLabel(request.id)}</td>
      <td><strong>{request.title}</strong><small>{request.location}</small></td>
      <td><ReviewBadge status={request.status} /></td>
      <td><span className="review-badge">{analysisLabels[request.ai_analysis] ?? 'Status unavailable'}</span></td>
      <td>{request.active_work_order_id ? <Link className="review-button" to={'/reviewer/work-orders/' + request.active_work_order_id + '/resolve'}>Resolve</Link> :
        <Link className="review-button" to={'/reviewer/triage/' + request.id} aria-label={(isReviewable(request.status) ? 'Review ' : 'View ') + request.title}>{isReviewable(request.status) ? 'Review' : 'View'}</Link>}</td>
    </>
  )
}

const queueTable = {
  title: 'Triage & Resolution',
  columns: ['ID', 'Issue & Location', 'Status', 'AI Analysis', 'Action'],
  row: (item) => <RequestRow request={item} />,
  empty: 'No requests match this queue view.',
}

function ReviewListing({ table, endpoint, filter, statuses, searchLabel, children }) {
  const [params, setParams] = useSearchParams()
  const selected = statuses.includes(params.get(filter)) ? params.get(filter) : ''
  const search = (params.get('q') ?? '').slice(0, 120)
  const rawPage = Number(params.get('page'))
  const query = new URLSearchParams({ page: Number.isInteger(rawPage) ? Math.max(1, Math.min(1_000_000, rawPage)) : 1 })
  if (selected === 'safety') query.set('safety', 'true')
  else if (selected) query.set(filter, selected)
  if (search.trim()) query.set('q', search.trim())
  const result = useRequestData(REVIEWER_API + endpoint + '?' + query)
  const data = result.data

  function change(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key !== 'page') next.delete('page')
    setParams(next, { replace: key === 'q' })
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1
  const page = data?.page ?? 1
  return (
    <Panel className="portal-page">
      <div className="page-heading"><h1>{table.title}</h1></div>
      {children}
      {data?.safety_count > 0 && <p className="review-safety-banner" role="status">
        {data.safety_count} high-priority {data.safety_count === 1 ? 'work order' : 'work orders'} flagged for reviewer attention.
      </p>}
      <div className="review-filters">
        <div className="review-actions" role="group" aria-label="Filter results">
          {['', ...statuses].map((status) => {
            const count = status === 'safety' ? (data?.safety_count ?? 0) :
              status ? (data?.status_counts[status] ?? 0) : totalCount(data?.status_counts)
            return (
              <button className="review-button" type="button" key={status} aria-pressed={selected === status} onClick={() => change(filter, status)}>
                {status === 'approved' ? 'Approved / Ready to Resolve' : labels[status] ?? 'All'}{data && ' (' + count + ')'}
              </button>
            )
          })}
        </div>
        <label className="search-input review-search">
          <Search size={18} aria-hidden="true" />
          <span className="sr-only">{searchLabel}</span>
          <input type="search" placeholder={searchLabel + '…'} value={search} maxLength={120} onChange={(event) => change('q', event.target.value)} />
        </label>
      </div>
      <ReviewTable {...table} result={result} />
      {data && (
        <nav className="review-pagination review-actions" aria-label="Result pages">
          <button className="review-button" type="button" disabled={page === 1} onClick={() => change('page', page - 1)}>Previous</button>
          <span role="status">Page {page} of {pages} · {data.total} results</span>
          <button className="review-button" type="button" disabled={page === pages} onClick={() => change('page', page + 1)}>Next</button>
        </nav>
      )}
    </Panel>
  )
}

export function TeamWorkload({ compact = false }) {
  const result = useRequestData(REVIEWER_API + '/team-workload')
  if (!result.data?.length) return <ReviewState result={result} empty="No active maintenance teams." />
  return <ul className="review-team-list">{result.data.map((team) => <li key={team.id}>
    <div><strong>{team.name}</strong>{!compact && <small>{[team.specialty, ...team.categories].join(' · ')}</small>}</div>
    <span>{team.active_work_orders} active</span>
  </li>)}</ul>
}

export function ReviewerQueue() {
  return <ReviewListing table={queueTable} endpoint="/requests" filter="status" statuses={['submitted', 'in_review', 'needs_info', 'approved']} searchLabel="Search requests">
    <details className="review-workload" id="team-workload" open={window.location.hash === '#team-workload' || undefined}>
      <summary>Maintenance Teams &amp; Workload</summary>
      <TeamWorkload />
    </details>
  </ReviewListing>
}

export function ReviewerHistory() {
  const table = {
    title: 'Reviewed Request History',
    columns: ['ID', 'Issue Summary', 'Final Category', 'Reviewer Decision', 'Date', 'Resolution / Completion'],
    empty: 'No review decisions match these filters.',
    row: (entry) => (
      <>
        <td><Link className="text-link" to={'/reviewer/triage/' + entry.request.id}>{requestLabel(entry.request.id)}</Link></td>
        <td><strong>{entry.request.title}</strong><small>{entry.reviewer.full_name}</small></td>
        <td>{entry.final_category?.name ?? '—'}</td>
        <td>
          <ReviewBadge status={entry.decision} />
          {(entry.notes || entry.assigned_team) && (
            <details className="review-history-notes">
              <summary>Decision details</summary>
              {entry.final_priority && <p>Priority · {labels[entry.final_priority]}</p>}
              {entry.assigned_team && <p>Team · {entry.assigned_team.name}</p>}
              {entry.notes && <p className="review-text">{entry.notes}</p>}
            </details>
          )}
        </td>
        <td><ReviewDate value={entry.created_at} /></td>
        <td>{entry.resolution ? <details className="review-history-notes">
          <summary><ReviewBadge status="completed" /> <ReviewDate value={entry.resolution.resolved_at} />{entry.resolution.has_proof && ' · Photo'}</summary>
          <p className="review-text">{entry.resolution.notes.length > 100 ? entry.resolution.notes.slice(0, 100) + '…' : entry.resolution.notes}</p>
          <ResolutionEvidence resolution={entry.resolution} requestId={entry.request.id} />
        </details> : entry.request.status === 'completed' ? <ReviewBadge status="completed" /> : '—'}</td>
      </>
    ),
  }
  return <ReviewListing table={table} endpoint="/history" filter="decision" statuses={['approved', 'rejected', 'needs_info']} searchLabel="Search reviewed requests" />
}

export function ReviewerWorkOrders() {
  const table = {
    title: 'Work Orders',
    columns: ['Work Order', 'Issue & Location', 'Assigned Team', 'Status', 'Action'],
    empty: 'No work orders match these filters.',
    row: (order) => (
      <>
        <td>{workOrderLabel(order.id)}</td>
        <td><strong>{order.request.title}</strong><small>{order.request.location}</small></td>
        <td>{order.assigned_team.name}</td>
        <td><ReviewBadge status={order.status} /> {order.safety_alert && <span className="review-badge" data-status="high">Safety alert</span>}</td>
        <td><div className="review-actions">
          <Link className="review-button" to={'/reviewer/work-orders/' + order.id} aria-label={'View ' + workOrderLabel(order.id)}>View</Link>
          {order.status !== 'resolved' && <Link className="review-button" to={'/reviewer/work-orders/' + order.id + '/resolve'}>Resolve</Link>}
        </div></td>
      </>
    ),
  }
  return <ReviewListing table={table} endpoint="/work-orders" filter="status" statuses={['open', 'in_progress', 'resolved', 'safety']} searchLabel="Search work orders" />
}

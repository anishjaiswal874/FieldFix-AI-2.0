import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import AttachmentPhoto from './AttachmentPhoto.jsx'
import { useAuth } from './AuthContext.jsx'
import Panel from './Panel.jsx'
import ReviewRecommendations, { RecommendationsUnavailable } from './ReviewRecommendations.jsx'
import { FusedTriage } from './WorkflowAnalytics.jsx'
import { ResolutionEvidence } from './ResolutionView.jsx'
import {
  isReviewable, requestLabel, ReviewBadge, ReviewDate, REVIEWER_API, ReviewState, workOrderLabel,
} from './ReviewerPages.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'

const queue = '/reviewer/triage'
const orders = '/reviewer/work-orders'
const actions = {
  approved: 'Approve & Create Work Order',
  needs_info: 'Request More Info',
  rejected: 'Reject',
}
const confirmations = {
  approved: 'Approve this request and create one open work order for the selected team?',
  needs_info: 'Show the requester your note and move this request to Needs Info?',
  rejected: 'Reject this request and close it for review?',
}

function DecisionForm({ request, onSaved, onRefresh }) {
  const { authorizedFetch } = useAuth()
  const options = useRequestData(REVIEWER_API + '/options')
  const [draft, setDraft] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [attempted, setAttempted] = useState(false)
  const confirmation = useRef(null)
  const categories = options.data?.categories ?? []
  const teams = options.data?.teams ?? []
  const initialCategory = categories.find(({ id }) => id === request.selected_category?.id)?.id ?? ''
  const [chosenCategory, setChosenCategory] = useState(null)
  const currentCategory = chosenCategory === null
    ? initialCategory : categories.some(({ id }) => id === chosenCategory) ? chosenCategory : ''

  useEffect(() => {
    if (draft) confirmation.current?.focus()
  }, [draft])

  function confirmDecision(event) {
    event.preventDefault()
    const decision = event.nativeEvent.submitter?.value
    if (!decision) return
    const form = new FormData(event.currentTarget)
    const notes = form.get('notes').trim()
    const body = { decision, notes, override_reason: form.get('override_reason').trim() }
    setError('')
    setAttempted(false)
    if (decision === 'approved') {
      const category = Number(form.get('category'))
      const team = Number(form.get('team'))
      const priority = form.get('priority')
      if (!category || !team || !priority) {
        setError('Select a final category, priority, and receiving team to approve.')
        return
      }
      Object.assign(body, { final_category_id: category, final_priority: priority, assigned_team_id: team })
    }
    if (decision === 'needs_info' && !notes) {
      setError('Enter the information you need from the requester.')
      event.currentTarget.elements.namedItem('notes').focus()
      return
    }
    setDraft(body)
  }

  async function save() {
    if (!draft || saving) return
    setSaving(true)
    setError('')
    setAttempted(true)
    try {
      const response = await requestMaintenance(authorizedFetch, REVIEWER_API + '/requests/' + request.id + '/decisions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
      })
      onSaved(await response.json())
    } catch (error) {
      setError(maintenanceErrorMessage(error))
    } finally {
      setDraft(null)
      setSaving(false)
    }
  }

  return (
    <>
      <ReviewRecommendations request={request} categories={categories} canApply={!draft && !saving}
        onUseCategory={setChosenCategory} />
      <FusedTriage requestId={request.id} />
      <form className="review-decision" onSubmit={confirmDecision}>
        <fieldset disabled={!!draft || saving}>
          <legend className="sr-only">Reviewer decision</legend>
          {options.data ? (
            <div className="review-fields" key={initialCategory}>
              <label>Final Category
                <select name="category" value={currentCategory} onChange={(event) => setChosenCategory(Number(event.target.value))}>
                  <option value="">Select category</option>
                  {categories.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                </select>
              </label>
              <label>Final Priority
                <select name="priority" defaultValue="">
                  <option value="">Select priority</option>
                  {options.data.priorities.map((priority) => (
                    <option key={priority} value={priority}>{priority.charAt(0).toUpperCase() + priority.slice(1)}</option>
                  ))}
                </select>
              </label>
              <label>Assign Team
                <select name="team" defaultValue="">
                  <option value="">Select receiving team</option>
                  {teams.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                </select>
              </label>
              {(!categories.length || !teams.length) && <p className="review-muted">Approval requires an active category and receiving team. Ask an administrator to configure them.</p>}
            </div>
          ) : <ReviewState result={options} />}
          <label>Decision Notes
            <textarea name="notes" maxLength={2000} rows={4} aria-describedby="review-notes-help" />
          </label>
          <p className="review-muted" id="review-notes-help">Notes are required when requesting more information and will be visible to the requester for that decision.</p>
          <label>Reason for overriding an advisory suggestion (optional)<input name="override_reason" maxLength={500} /></label>
          <div className="review-decision-buttons">
            {Object.entries(actions).map(([decision, label]) => (
              <button type="submit" value={decision} className="review-button" data-action={decision} key={decision}
                disabled={decision === 'approved' && (!categories.length || !teams.length)}>
                {label}
              </button>
            ))}
          </div>
        </fieldset>
      </form>
      {draft && (
        <section className="review-confirm" tabIndex={-1} ref={confirmation} aria-labelledby="review-confirm-heading" aria-busy={saving}>
          <h3 id="review-confirm-heading">Confirm decision</h3>
          <p>{confirmations[draft.decision]}</p>
          <div className="review-actions">
            <button type="button" className="review-button" onClick={() => setDraft(null)} disabled={saving}>Cancel</button>
            <button type="button" className="review-button" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Confirm decision'}</button>
          </div>
        </section>
      )}
      {error && <p className="review-error" role="alert">{error}</p>}
      {error && attempted && <button className="text-link" type="button" onClick={onRefresh}>Refresh request</button>}
    </>
  )
}

function ReviewRecord({ request, onRefresh }) {
  const [receipt, setReceipt] = useState(null)
  const status = receipt?.status ?? request.status
  return (
    <>
      <div className="page-heading"><h1>Request {requestLabel(request.id)}</h1><ReviewBadge status={status} /></div>
      <div className="panel-grid review-detail-grid">
        <Panel className="review-evidence">
          <h2>Submitted Evidence</h2>
          <h3>{request.title}</h3>
          <dl className="review-properties">
            <div><dt>Location</dt><dd>{request.location}</dd></div>
            <div><dt>Requester</dt><dd>{request.requester.full_name}</dd></div>
            <div><dt>Selected Category</dt><dd>{request.selected_category?.name ?? 'Unspecified'}</dd></div>
            <div><dt>Submitted</dt><dd><ReviewDate value={request.created_at} /></dd></div>
          </dl>
          <p className="review-text">{request.description}</p>
          {request.attachments.length > 0 ? (
            <ul className="request-photos" aria-label="Submitted photos">
              {request.attachments.map((attachment) => (
                <AttachmentPhoto key={attachment.id} path={REVIEWER_API + '/requests/' + request.id + '/attachments/' + attachment.id} attachment={attachment} />
              ))}
            </ul>
          ) : <p className="review-muted">No photos were submitted.</p>}
        </Panel>
        <Panel>
          <h2>AI Recommendations & Reviewer Decision</h2>
          {receipt ? (
            <>
              <RecommendationsUnavailable />
              <section className="review-confirm">
                <p role="status">Decision saved.</p>
                {receipt.work_order_id && <Link className="text-link" to={orders + '/' + receipt.work_order_id}>Open {workOrderLabel(receipt.work_order_id)}</Link>}
                {receipt.work_order_id && <Link className="review-button" to={orders + '/' + receipt.work_order_id + '/resolve'}>Resolve</Link>}
                <Link className="text-link" to="/reviewer/history">View review history</Link>
              </section>
            </>
          ) : isReviewable(status) ? (
            <DecisionForm request={request} onSaved={setReceipt} onRefresh={onRefresh} />
          ) : <>
            <RecommendationsUnavailable />
            <p className="review-muted">{status === 'needs_info' ? 'This request is awaiting information from the requester.' : 'A reviewer has processed this request.'} <Link className="text-link" to="/reviewer/history">View history</Link></p>
            {request.active_work_order_id && <Link className="review-button" to={orders + '/' + request.active_work_order_id + '/resolve'}>Resolve</Link>}
          </>}
        </Panel>
      </div>
    </>
  )
}

export function RequestReview() {
  const { requestId } = useParams()
  const result = useRequestData(REVIEWER_API + '/requests/' + encodeURIComponent(requestId))
  return (
    <Panel className="portal-page">
      <Link className="text-link back-link" to={queue}><ArrowLeft size={18} aria-hidden="true" /> Back to Queue</Link>
      {result.data ? <ReviewRecord key={requestId} request={result.data} onRefresh={result.retry} /> : (
        <><h1>Request Review</h1><ReviewState result={result} /></>
      )}
    </Panel>
  )
}

export function WorkOrderDetails() {
  const { orderId } = useParams()
  const result = useRequestData(REVIEWER_API + '/work-orders/' + encodeURIComponent(orderId))
  const order = result.data
  return (
    <Panel className="portal-page">
      <Link className="text-link back-link" to={orders}><ArrowLeft size={18} aria-hidden="true" /> Back to Work Orders</Link>
      {order ? (
        <>
          <div className="page-heading"><h1>Work Order {workOrderLabel(order.id)}</h1><ReviewBadge status={order.status} /></div>
          {order.status !== 'resolved' && <Link className="review-button" to={orders + '/' + order.id + '/resolve'}>Resolve</Link>}
          <ResolutionEvidence resolution={order.resolution} requestId={order.request.id} />
          <div className="panel-grid review-detail-grid">
            <Panel>
              <h2>{order.request.title}</h2>
              <dl className="review-properties">
                <div><dt>Request</dt><dd><Link className="text-link" to={queue + '/' + order.request.id}>{requestLabel(order.request.id)}</Link></dd></div>
                <div><dt>Location</dt><dd>{order.request.location}</dd></div>
                <div><dt>Assigned Team</dt><dd>{order.assigned_team.name}</dd></div>
                <div><dt>Opened</dt><dd><ReviewDate value={order.created_at} /></dd></div>
                <div><dt>Last Updated</dt><dd><ReviewDate value={order.updated_at} /></dd></div>
                {order.resolved_at && <div><dt>Resolved</dt><dd><ReviewDate value={order.resolved_at} /></dd></div>}
              </dl>
            </Panel>
            <Panel>
              <h2>Reviewer Approval</h2>
              {order.approval ? (
                <>
                  <dl className="review-properties">
                    <div><dt>Reviewer</dt><dd>{order.approval.reviewer.full_name}</dd></div>
                    <div><dt>Final Category</dt><dd>{order.approval.final_category?.name ?? '—'}</dd></div>
                    <div><dt>Final Priority</dt><dd>{order.approval.final_priority ? <ReviewBadge status={order.approval.final_priority} /> : '—'}</dd></div>
                  </dl>
                  {order.approval.notes && <p className="review-text">{order.approval.notes}</p>}
                </>
              ) : <p className="review-muted">No approval record is linked to this work order.</p>}
            </Panel>
          </div>
        </>
      ) : <><h1>Work Order</h1><ReviewState result={result} /></>}
    </Panel>
  )
}

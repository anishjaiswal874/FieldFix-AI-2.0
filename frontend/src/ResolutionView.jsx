import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import AttachmentPhoto from './AttachmentPhoto.jsx'
import Panel from './Panel.jsx'
import { acceptedPhoto, photoAccept, photoFormatLabel } from './photoUpload.js'
import { REVIEWER_API, ReviewBadge, ReviewDate, ReviewState, requestLabel, workOrderLabel } from './ReviewerPages.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'

export function ResolutionEvidence({ resolution, requestId, base = REVIEWER_API + '/requests' }) {
  if (!resolution) return null
  return <section className="resolution-evidence">
    <ReviewBadge status="completed" />
    <p className="review-text">{resolution.notes}</p>
    <p>Completed by {resolution.resolved_by} · <ReviewDate value={resolution.resolved_at} /></p>
    {resolution.has_proof && <ul className="request-photos" aria-label="Resolution proof photo">
      <AttachmentPhoto path={base + '/' + requestId + '/resolution-proof'}
        attachment={{original_filename: resolution.proof_name || 'Resolution proof'}} />
    </ul>}
  </section>
}

export default function ResolutionView() {
  const { orderId } = useParams()
  const { authorizedFetch } = useAuth()
  const navigate = useNavigate()
  const path = REVIEWER_API + '/work-orders/' + encodeURIComponent(orderId)
  const result = useRequestData(path)
  const order = result.data
  const [saving, setSaving] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')

  async function resolve(event) {
    event.preventDefault()
    if (!confirmed || saving) return
    const body = new FormData(event.currentTarget)
    const photo = body.get('resolution_image')
    if (!photo?.name) body.delete('resolution_image')
    else if (!acceptedPhoto(photo)) {
      setError('Choose one supported image up to 15 MiB.')
      return
    }
    body.set('resolution_confirmed', 'true')
    setError('')
    setSaving(true)
    try {
      await requestMaintenance(authorizedFetch, path + '/resolve', {method:'POST',body})
      navigate('/reviewer/work-orders/' + orderId, {replace:true})
    } catch (cause) {
      setError(maintenanceErrorMessage(cause))
    } finally {
      setSaving(false)
    }
  }

  return <Panel className="portal-page">
    <Link className="text-link" to="/reviewer/triage">Back to Triage &amp; Resolution</Link>
    <h1>{order ? 'Resolve ' + workOrderLabel(order.id) : 'Resolve work order'}</h1>
    {!order ? <ReviewState result={result} /> : <>
      <section className="resolution-evidence">
        <h2>1. Original Issue Summary</h2>
        <dl className="review-properties">
          <div><dt>Complaint</dt><dd>{requestLabel(order.request.id)} · {order.request.title}</dd></div>
          <div><dt>Location</dt><dd>{order.request.location}</dd></div>
          <div><dt>Assigned team</dt><dd>{order.assigned_team.name}</dd></div>
        </dl>
        {order.original_attachments.length > 0 && <ul className="request-photos" aria-label="Original complaint attachments">
          {order.original_attachments.map(photo => <AttachmentPhoto key={photo.id} attachment={photo}
            path={REVIEWER_API + '/requests/' + order.request.id + '/attachments/' + photo.id} />)}
        </ul>}
      </section>
      {order.status === 'resolved' ? <ResolutionEvidence resolution={order.resolution} requestId={order.request.id} /> :
        <form className="analytics-form resolution-form" onSubmit={resolve}>
          <label><strong>2. Resolution Description</strong><textarea name="resolution_notes" rows={4} maxLength={2000} required disabled={saving} /></label>
          <label><strong>3. Resolution Proof Image (optional)</strong>
            <span>{photoFormatLabel} · up to 15 MiB</span>
            <input type="file" name="resolution_image" accept={photoAccept} disabled={saving} />
          </label>
          <label className="review-ai-check"><input type="checkbox" required checked={confirmed} disabled={saving}
            onChange={event => setConfirmed(event.target.checked)} /> 4. I confirm this maintenance task is completed</label>
          <button className="review-button" disabled={!confirmed || saving}>{saving ? 'Saving…' : 'Mark Completed & Resolved'}</button>
          {error && <p className="review-error" role="alert">{error}</p>}
        </form>}
    </>}
  </Panel>
}

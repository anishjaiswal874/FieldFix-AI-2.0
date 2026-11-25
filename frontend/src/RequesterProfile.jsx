import { Pencil, UserRound } from 'lucide-react'
import { useState } from 'react'
import { useAuth } from './AuthContext.jsx'
import Panel, { PanelHeading } from './Panel.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'

const endpoint = '/api/requests/profile'
const fields = [
  ['full_name', 'Full Name', 160, 'text', 'name'],
  ['phone', 'Phone Number', 40, 'tel', 'tel'],
  ['department', 'Department / Block', 120, 'text', 'organization'],
  ['staff_or_student_id', 'Staff / Student ID', 80, 'text', 'off'],
  ['default_location', 'Default Location / Room', 255, 'text', 'off'],
  ['bio', 'Notes / Bio', 1000, 'textarea', 'off'],
]

export default function RequesterProfile() {
  const { authorizedFetch, updateDisplayName } = useAuth()
  const result = useRequestData(endpoint)
  const [saved, setSaved] = useState(null)
  const data = saved ?? result.data
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [error, setError] = useState('')

  async function save(event) {
    event.preventDefault()
    if (busy) return
    const body = Object.fromEntries(new FormData(event.currentTarget))
    setBusy(true)
    setError('')
    setFeedback('')
    try {
      const response = await requestMaintenance(authorizedFetch, endpoint, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      const profile = await response.json()
      setSaved(profile)
      updateDisplayName(profile.full_name)
      setEditing(false)
      setFeedback('Profile saved.')
    } catch (cause) {
      setError(maintenanceErrorMessage(cause))
    } finally {
      setBusy(false)
    }
  }
  return <Panel className="requester-screen">
    <PanelHeading icon={UserRound} title="My Profile" />
    {!data ? <p role={result.error ? 'alert' : 'status'}>{result.error || 'Loading profile…'}</p> :
      <div className="request-profile-grid">
        <div className="request-profile-card profile-identity">
          <span className="profile-avatar"><UserRound size={43} aria-hidden="true" /></span>
          <strong>{data.full_name}</strong><span className="profile-role">Requester</span>
        </div>
        <section className="request-profile-card">
          <PanelHeading as="h2" title="Account Information" actions={<button className="request-secondary" type="button"
            disabled={busy} aria-expanded={editing} aria-controls="profile-fields" onClick={() => {setEditing(!editing);setFeedback('');setError('')}}>
            {editing ? 'Cancel' : <><Pencil size={15} aria-hidden="true" /> Edit Profile</>}
          </button>} />
          <p className="request-detail-description">{data.email}</p>
          <div id="profile-fields">
            {editing ? <form className="profile-edit" onSubmit={save}>
              <fieldset disabled={busy} className="profile-edit">
                {fields.map(([name,label,maxLength,type,autoComplete]) => <label className="request-field" key={name}>{label}
                  {type === 'textarea' ? <textarea name={name} defaultValue={data[name]} maxLength={maxLength} rows={3} /> :
                    <input name={name} type={type} defaultValue={data[name]} autoComplete={autoComplete} maxLength={maxLength} required={name === 'full_name'} />}
                </label>)}
              </fieldset>
              <div className="request-form-footer"><button className="request-primary" disabled={busy}>{busy ? 'Saving…' : 'Save Profile'}</button></div>
            </form> : <dl className="profile-field-list">
              {fields.map(([name,label]) => <div key={name}><dt>{label}</dt><dd>{data[name] || '—'}</dd></div>)}
            </dl>}
          </div>
          {error && <p role="alert">{error}</p>}
          {feedback && <p role="status">{feedback}</p>}
        </section>
      </div>}
  </Panel>
}

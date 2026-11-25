import { ArrowUpRight, ClipboardList, Network, Search, ShieldCheck, SlidersHorizontal, UsersRound } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import Panel from './Panel.jsx'
import { PerformanceAudit } from './WorkflowAnalytics.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'

const api = '/api/admin'
const roles = { requester: 'Requester', reviewer: 'Reviewer', admin: 'Administrator' }

function AdminStatus({ active }) {
  return <span className="admin-status" data-active={active}>{active ? 'Active' : 'Inactive'}</span>
}

function AdminState({ result, empty }) {
  if (result.loading) return <p role="status">Loading…</p>
  if (result.error) return <><p role="alert">{result.error}</p><button className="text-link" type="button" onClick={result.retry}>Try again</button></>
  return <p role="status">{empty}</p>
}

function AdminTable({ title, columns, result, row, empty }) {
  const records = result.data?.items ?? []
  return (
    <div className="preview-table admin-table" role="region" aria-label={title} tabIndex={0}>
      <table>
        <thead><tr>{columns.map((heading) => <th scope="col" key={heading}>{heading}</th>)}</tr></thead>
        <tbody>
          {records.map((record) => <tr key={record.id}>{row(record)}</tr>)}
          {!records.length && <tr><td colSpan={columns.length}><AdminState result={result} empty={empty} /></td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function CategoryCells({ category }) {
  return (
    <>
      <td><strong>{category.name}</strong></td>
      <td>{category.default_team ? <>{category.default_team.name}{!category.default_team.is_active && <small>Inactive team</small>}</> : '—'}</td>
      <td><AdminStatus active={category.is_active} /></td>
    </>
  )
}

export function AdminDashboard() {
  const dashboard = useRequestData(api + '/dashboard')
  const data = dashboard.data
  const cards = [
    [UsersRound, 'Total System Users', data?.total_users, '/admin/users'],
    [SlidersHorizontal, 'Active Categories', data?.active_categories, '/admin/categories'],
    [Network, 'Active Teams', data?.active_teams, '/admin/teams'],
    [ClipboardList, 'Active Work Orders', data?.active_work_orders],
  ]
  return (
    <div className="dashboard admin-dashboard-compact">
      <div className="page-heading"><h1>Administrator Dashboard</h1></div>
      <div className="dashboard-links">
        {cards.map(([Icon, title, value, to]) => {
          const Card = to ? Link : 'div'
          return (
            <Card className="glass overview-link admin-metric" key={title} {...(to ? { to } : {})}>
              <span className="overview-icon"><Icon size={22} aria-hidden="true" /></span>
              <strong>{value ?? '—'}</strong><span>{title}</span>
              {to && <ArrowUpRight className="overview-arrow" size={17} aria-hidden="true" />}
            </Card>
          )
        })}
      </div>
      <div className="panel-grid">
        <PerformanceAudit admin />
        <Panel>
          <h2>Active Role Distribution</h2>
          {data ? (
            <dl className="admin-role-counts">
              {Object.entries(roles).map(([role, label]) => <div key={role}><dt>{label}</dt><dd>{data.active_roles[role] ?? 0}</dd></div>)}
            </dl>
          ) : <AdminState result={dashboard} />}
        </Panel>
      </div>
    </div>
  )
}

function ManagementList({ config }) {
  const [params, setParams] = useSearchParams()
  const [editor, setEditor] = useState(null)
  const [feedback, setFeedback] = useState('')
  const q = (params.get('q') ?? '').slice(0, 120)
  const requestedPage = Number(params.get('page'))
  const query = new URLSearchParams({
    page: Number.isInteger(requestedPage) ? Math.max(1, Math.min(1_000_000, requestedPage)) : 1,
  })
  if (q.trim()) query.set('q', q.trim())
  const result = useRequestData(api + '/' + config.path + '?' + query)
  const data = result.data
  const page = data?.page ?? 1
  const last = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1
  const Editor = config.editor

  function change(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    if (key === 'q') next.delete('page')
    setParams(next, { replace: key === 'q' })
  }

  function openEditor(record) {
    setFeedback('')
    setEditor(record)
  }

  return (
    <Panel className="portal-page">
      <div className="page-heading">
        <h1>{config.title}</h1>
        <div className="page-actions">
          <label className="search-input admin-search">
            <Search size={18} aria-hidden="true" />
            <span className="sr-only">{config.search}</span>
            <input type="search" placeholder={config.search + '…'} value={q} maxLength={120} onChange={(event) => change('q', event.target.value)} />
          </label>
          {config.add && <button type="button" className="admin-button admin-primary" disabled={!!editor} onClick={() => openEditor({})}>+ {config.add}</button>}
        </div>
      </div>
      {feedback && <p className="admin-feedback" role="status">{feedback}</p>}
      {editor && (
        <Editor key={editor.id ?? 'new'} record={editor} path={config.path} onCancel={() => setEditor(null)}
          onSaved={() => { setEditor(null); setFeedback(config.saved); result.retry() }} />
      )}
      <AdminTable title={config.title} columns={[...config.columns, 'Action']} result={result} empty={config.empty}
        row={(record) => (
          <>
            {config.cells(record)}
            <td><button className="text-link" type="button" disabled={!!editor} aria-label={'Edit ' + (record.name ?? record.full_name)} onClick={() => openEditor(record)}>Edit</button></td>
          </>
        )} />
      {data && (
        <nav className="admin-pagination" aria-label="Result pages">
          <button type="button" className="admin-button" disabled={page <= 1} onClick={() => change('page', page - 1)}>Previous</button>
          <span role="status">Page {page} of {last} · {data.total} results</span>
          <button type="button" className="admin-button" disabled={page >= last} onClick={() => change('page', page + 1)}>Next</button>
        </nav>
      )}
    </Panel>
  )
}

function RecordEditor({ record, path, title, children, read, onCancel, onSaved }) {
  const { user, authorizedFetch, signOut } = useAuth()
  const [payload, setPayload] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const heading = useRef(null)
  const confirmation = useRef(null)
  const changesOwnAccess = path === 'users' && record.id === user.id && payload &&
    (payload.role !== user.role || !payload.is_active || payload.email.toLowerCase() !== user.email)

  useEffect(() => { heading.current?.focus() }, [])
  useEffect(() => {
    if (payload) confirmation.current?.focus()
  }, [payload])

  async function save() {
    if (saving || !payload) return
    setSaving(true)
    setError('')
    try {
      await requestMaintenance(authorizedFetch, api + '/' + path + (record.id ? '/' + record.id : ''), {
        method: record.id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (changesOwnAccess) signOut()
      else onSaved()
    } catch (cause) {
      setError(maintenanceErrorMessage(cause))
      setSaving(false)
    }
  }

  return (
    <Panel className="admin-editor">
      <h2 tabIndex={-1} ref={heading}>{title}</h2>
      <form onSubmit={(event) => {
        event.preventDefault()
        setError('')
        setPayload(read(new FormData(event.currentTarget)))
      }}>
        <fieldset disabled={!!payload} className="admin-fields">
          <legend className="sr-only">{title}</legend>
          {children}
          <div className="admin-actions">
            <button type="button" className="admin-button" onClick={onCancel}>Cancel</button>
            <button type="submit" className="admin-button admin-primary">Review changes</button>
          </div>
        </fieldset>
      </form>
      {payload && (
        <section className="admin-confirm" tabIndex={-1} ref={confirmation} aria-label="Confirm changes" aria-busy={saving}>
          <p>{changesOwnAccess ? 'Saving this change will sign you out. Confirm the change to your account?' : 'Apply these changes?'}</p>
          <div className="admin-actions">
            <button type="button" className="admin-button" disabled={saving} onClick={() => { setPayload(null); setError('') }}>Back to editing</button>
            <button type="button" className="admin-button admin-primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Confirm and save'}</button>
          </div>
        </section>
      )}
      {error && <p className="admin-feedback admin-error" role="alert">{error}</p>}
    </Panel>
  )
}

const readActive = (form) => form.has('is_active')
const cleanName = (form, key) => String(form.get(key) ?? '').trim()

function ActiveField({ record, children }) {
  return <label className="admin-toggle"><input type="checkbox" name="is_active" defaultChecked={record.is_active ?? true} /> {children}</label>
}

function UserEditor(props) {
  const { user, authorizedFetch } = useAuth()
  const [action, setAction] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [temporary, setTemporary] = useState('')

  async function confirmAction() {
    setBusy(true)
    setError('')
    try {
      const response = await requestMaintenance(authorizedFetch, api + '/users/' + props.record.id + '/' + action, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: true }),
      })
      if (action === 'archive') props.onSaved()
      else {
        setTemporary((await response.json()).temporary_password)
        setAction('')
      }
    } catch (cause) {
      setError(maintenanceErrorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  if (props.record.archived) return <Panel className="admin-editor">
    <h2>Archived account</h2><p>Its maintenance history is retained.</p>
    <button className="admin-button" onClick={props.onCancel}>Close</button>
  </Panel>

  return (
    <>
    {!action && !temporary && <RecordEditor {...props} title={'Edit ' + props.record.full_name} read={(form) => ({
      full_name: cleanName(form, 'full_name'), email: cleanName(form, 'email'),
      role: form.get('role'), is_active: readActive(form),
    })}>
      <label>Display name<input name="full_name" defaultValue={props.record.full_name} maxLength={160} required /></label>
      <label>Email address<input name="email" type="email" defaultValue={props.record.email} maxLength={320} required /></label>
      <p className="admin-muted">An email change signs the account out and requires verifying the new address for email OTP.</p>
      <label>Assigned Role
        <select name="role" defaultValue={props.record.role}>
          {Object.entries(roles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <ActiveField record={props.record}>Account active</ActiveField>
    </RecordEditor>}
    <Panel className="admin-editor">
      <h2>Account actions</h2>
      {temporary ? <>
        <p role="status">Temporary password created. Share it privately. It expires in 24 hours and must be replaced before sign-in.</p>
        <label>Temporary password<input readOnly value={temporary} autoComplete="off" /></label>
        <button className="admin-button" onClick={props.onSaved}>Hide password and close</button>
      </> : action ? <>
        <p>{action === 'archive'
          ? 'Archive this account and revoke its access? Requests and review history will be preserved.'
          : 'Replace the current password and revoke sessions? The user will need a temporary password to set a new one.'}</p>
        <div className="admin-actions">
          <button className="admin-button" disabled={busy} onClick={() => { setAction(''); setError('') }}>Cancel action</button>
          <button className="admin-button admin-primary" disabled={busy} onClick={confirmAction}>
            {action === 'archive' ? 'Confirm archive' : 'Issue temporary password'}
          </button>
        </div>
      </> : <div className="admin-actions">
        {props.record.is_active && props.record.id !== user.id &&
          <button className="admin-button" onClick={() => setAction('password-reset')}>Reset password</button>}
        {props.record.role !== 'admin' &&
          <button className="admin-button" onClick={() => setAction('archive')}>Archive account</button>}
      </div>}
      {error && <p className="admin-feedback admin-error" role="alert">{error}</p>}
    </Panel>
    </>
  )
}

function CategoryEditor(props) {
  const options = useRequestData(api + '/team-options')
  const current = props.record.default_team
  const currentMissing = current && !options.data?.some((team) => team.id === current.id)
  return (
    <RecordEditor {...props} title={props.record.id ? 'Edit Category' : 'Add Category'} read={(form) => ({
      name: cleanName(form, 'name'), is_active: readActive(form),
      default_team_id: form.has('team') ? (Number(form.get('team')) || null) : (current?.id ?? null),
    })}>
      <label>Category Name<input name="name" defaultValue={props.record.name} maxLength={120} required /></label>
      <label>Default Team
        <select key={options.data ? 'loaded' : 'waiting'} name="team" defaultValue={current?.id ?? ''} disabled={!options.data}>
          <option value="">{options.loading ? 'Loading teams…' : 'No default team'}</option>
          {currentMissing && <option value={current.id}>{current.name} ({current.is_active ? 'current' : 'inactive'})</option>}
          {options.data?.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
        </select>
      </label>
      {options.error && <AdminState result={options} />}
      <ActiveField record={props.record}>Category active</ActiveField>
    </RecordEditor>
  )
}

function TeamEditor(props) {
  return (
    <RecordEditor {...props} title={props.record.id ? 'Edit Team' : 'New Team'} read={(form) => ({
      name: cleanName(form, 'name'), specialty: cleanName(form, 'specialty'), is_active: readActive(form),
    })}>
      <label>Team Name<input name="name" defaultValue={props.record.name} maxLength={120} required /></label>
      <label>Specialty<input name="specialty" defaultValue={props.record.specialty} maxLength={120} required /></label>
      <ActiveField record={props.record}>Team active</ActiveField>
    </RecordEditor>
  )
}

const userConfig = {
  path: 'users', title: 'Users & Role Management', search: 'Search users', columns: ['User', 'Assigned Role', 'Status'],
  empty: 'No users match this search.', saved: 'User access updated.', editor: UserEditor,
  cells: (user) => <><td><strong>{user.full_name}</strong><small>{user.email}</small></td><td>{roles[user.role]}</td>
    <td>{user.archived ? 'Archived' : <AdminStatus active={user.is_active} />}
      {user.password_reset_pending && !user.archived && <small>Password change required</small>}</td></>,
}
const categoryConfig = {
  path: 'categories', title: 'Maintenance Categories', search: 'Search categories', add: 'Add Category', columns: ['Category', 'Default Team', 'Status'],
  empty: 'No categories match this search.', saved: 'Category saved.', editor: CategoryEditor,
  cells: (category) => <CategoryCells category={category} />,
}
const teamConfig = {
  path: 'teams', title: 'Maintenance Teams', search: 'Search teams', add: 'New Team', columns: ['Team Name', 'Specialty', 'Status', 'Active Work Orders'],
  empty: 'No teams match this search.', saved: 'Team saved.', editor: TeamEditor,
  cells: (team) => <><td><strong>{team.name}</strong></td><td>{team.specialty}</td><td><AdminStatus active={team.is_active} /></td><td>{team.active_work_orders}</td></>,
}

export function AdminUsers() { return <ManagementList config={userConfig} /> }
export function AdminCategories() { return <ManagementList config={categoryConfig} /> }
export function AdminTeams() { return <ManagementList config={teamConfig} /> }

export function AdminSettings() {
  const { authorizedFetch } = useAuth()
  const result = useRequestData(api + '/settings')
  const data = result.data
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState('')
  const labels = { nlp: 'Category suggestions', nlp_demo: 'NLP model demonstration', cv: 'Concrete crack screening', priority: 'Priority rules' }
  const status = { enabled: 'Enabled', research_only: 'Enabled for research only', disabled: 'Disabled in deployment', runtime_missing: 'Local model runtime unavailable', validation_required: 'Awaiting scope validation and approved category bindings' }

  async function save(event) {
    event.preventDefault()
    const allowed = new FormData(event.currentTarget).has('registration')
    setSaving(true)
    setFeedback('')
    try {
      await requestMaintenance(authorizedFetch, api + '/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ requester_self_registration: allowed }),
      })
      setFeedback('Registration setting saved.')
      result.retry()
    } catch (cause) {
      setFeedback(maintenanceErrorMessage(cause))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Panel className="portal-page">
      <div className="page-heading"><h1>System Settings</h1></div>
      {data ? (
        <div className="admin-settings">
          <section className="admin-policy">
            <h2><ShieldCheck size={21} aria-hidden="true" /> AI Decision-Support Disclaimer</h2>
            <p>{data.ai_safety_disclaimer}</p>
          </section>
          <section className="admin-policy">
            <h2>Requester Self-Registration</h2>
            <form className="admin-registration-setting" onSubmit={save}>
              <label className="admin-toggle"><input name="registration" type="checkbox" defaultChecked={data.requester_self_registration} disabled={saving} /> Allow new users to create Requester accounts from the public Register page</label>
              <button className="admin-button admin-primary" disabled={saving}>Save</button>
            </form>
          </section>
          <section className="admin-policy">
            <h2>Recommendation availability</h2>
            {Object.entries(data.recommendations).map(([name, value]) => <p key={name}>{labels[name]} · {status[value]}</p>)}
          </section>
        </div>
      ) : <AdminState result={result} />}
      {feedback && <p className="admin-feedback" role="status">{feedback}</p>}
    </Panel>
  )
}

import { useState } from 'react'

import { Link } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import Panel from './Panel.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'



const api = '/api/reviewer/analytics'
const pct = value => value == null ? '—' : value.toFixed(1) + '%'
const title = value => value ? value[0].toUpperCase() + value.slice(1).replaceAll('_', ' ') : 'Abstained'
const ref = id => 'FF-' + String(id).padStart(3, '0')

export function SimilarComplaints({ matches }) {
  return <div className="analytics-similar">
    <h4>Similar Past Complaints</h4>
    {matches?.length ? <ul>{matches.map(match => <li key={match.id}>
      <Link className="text-link" to={'/reviewer/triage/' + match.id}>{ref(match.id)} · {match.title}</Link>
      <small>{match.similarity}% similarity · {title(match.status)}{match.team && ' · ' + match.team}
        {match.likely_duplicate && <span className="review-badge" data-status="high">Likely duplicate</span>}</small>
    </li>)}</ul> : <p className="analytics-muted">{matches == null ? 'Similarity comparison is unavailable.' : 'No matching past complaints in the comparison window.'}</p>}
  </div>
}

export function ImageQuality({ data }) {
  if (!data) return <p className="analytics-muted">Image quality has not been measured for this photo.</p>
  return <div className="analytics-quality">
    <h4>Image Quality Gate</h4>
    {[
      ['Sharpness', data.sharpness + ' Laplacian variance', data.blur],
      ['Brightness', data.brightness + ' / 255', data.exposure],
      ['Resolution', data.width + ' × ' + data.height, data.resolution],
    ].map(([label, value, status]) => <div key={label}><span>{label} · {value}</span>
      <span className="review-badge" data-status={status === 'warning' ? 'high' : 'low'}>{title(status)}</span>
    </div>)}
  </div>
}

export function TextImageConsistency({ data }) {
  if (!data) return <p className="analytics-muted">Screen a confirmed concrete close-up to compare the image and complaint text.</p>
  return <div>
    <h4>Text–Image Consistency {data.score != null && '· ' + data.score + '%'}</h4>
    <p className="analytics-muted">{data.explanation}</p>
    {data.mismatch && <span className="review-badge" data-status="high">Check manually</span>}
  </div>
}

export function FusedTriage({ requestId, recent = [] }) {
  const { authorizedFetch } = useAuth()
  const [selected, setSelected] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const id = requestId || selected

  async function analyze() {
    setBusy(true)
    setResult(null)
    try {
      const response = await requestMaintenance(authorizedFetch, api + '/requests/' + id + '/fused', { method: 'POST' })
      setResult(await response.json())
    } catch (error) {
      setResult({ error: maintenanceErrorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return <Panel className="analytics-card">
    <div className="analytics-heading"><h3>Fused Triage Decision</h3><span className="review-badge">Advisory only</span></div>
    <div className="analytics-form">
      {!requestId && <label>Recent complaint<select value={selected} disabled={busy} onChange={event => {setSelected(event.target.value); setResult(null)}}>
        <option value="">Select a complaint</option>
        {recent.map(row => <option key={row.id} value={row.id}>{ref(row.id)} · {row.title}</option>)}
      </select></label>}
      <button className="review-button" type="button" disabled={!id || busy} onClick={analyze}>{busy ? 'Analyzing…' : 'Review combined evidence'}</button>
    </div>
    {result?.error && <p role="alert">{result.error}</p>}
    {result?.nlp && <div className="analytics-result" role="status">
      <p><strong>NLP research label</strong> · {result.nlp.label || title(result.nlp.status)}</p>
      <p><strong>Priority rule</strong> · {title(result.priority.priority || result.priority.status)}. {result.priority.explanation}</p>
      <p><strong>Concrete screening</strong> · {result.cv ? (result.cv.crack_detected ? 'Possible crack' : 'Below threshold') : 'No saved screening'}</p>
      <ImageQuality data={result.cv?.quality} />
      <TextImageConsistency data={result.cv?.consistency} />
      <SimilarComplaints matches={result.similar} />
    </div>}
  </Panel>
}

export function PerformanceAudit({ admin = false }) {
  const result = useRequestData(api + '/performance')
  const data = result.data
  return <Panel className="analytics-card audit-card">
    <h2>{admin ? 'System Performance & Overrides' : 'Model Comparison & Human Override'}</h2>
    {data ? <>
      <div className="analytics-rates">
        {[
          ['AI agreement', data.agreement_rate], ['Human override', data.override_rate],
          ['NLP abstention', data.abstention_rate], ['Work order resolution', data.resolution_rate],
        ].map(([label, value]) => <div key={label}>
          <span>{label} <strong>{pct(value)}</strong></span>
          {value != null && <progress max={100} value={value} aria-label={label} />}
        </div>)}
      </div>
      <dl className="analytics-audit-counts">
        {[
          ['AI evaluations', data.total_ai_evaluations], ['NLP evaluations', data.nlp_evaluations],
          ['CV screenings', data.cv_screenings], ['Abstained', data.abstained],
          ['Category overrides', data.category_overrides], ['Priority overrides', data.priority_overrides],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <div className="preview-table audit-comparison"><table>
        <thead><tr><th scope="col">Method</th><th scope="col">Recorded evidence</th><th scope="col">Agreement</th></tr></thead>
        <tbody>
          <tr><td>TF-IDF + LinearSVC</td><td>{data.nlp_evaluations} evaluations · {data.abstained} abstained</td><td>{pct(data.category_agreement_rate)}</td></tr>
          <tr><td>Priority rules</td><td>{data.priorities_compared} decisions compared</td><td>{pct(data.priority_agreement_rate)}</td></tr>
          <tr><td>ResNet-18 · 0.86</td><td>{data.cv_screenings} screenings · {data.flagged_decisions} decisions with a possible crack</td><td>—</td></tr>
          <tr><td>Human decisions</td><td>{data.decisions} recorded · {data.overrides} overrides</td><td>{pct(data.agreement_rate)}</td></tr>
        </tbody>
      </table></div>
      <p className="analytics-muted">Last 30 decisions · {data.recent_category_overrides} category overrides · {data.recent_priority_overrides} priority overrides.</p>
      <p className="analytics-muted">Agreement uses {data.compared} comparable decisions. CV flags are screening results. Evaluation and override counts start with the audit rollout.</p>
    </> : <p role={result.error ? 'alert' : 'status'}>{result.error || 'Loading recorded evaluations…'}</p>}
  </Panel>
}


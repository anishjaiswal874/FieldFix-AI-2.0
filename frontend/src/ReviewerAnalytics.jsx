import { useEffect, useRef, useState } from 'react'
import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from 'chart.js'
import { AudioLines, ChevronDown, ScanLine } from 'lucide-react'
import AttachmentPhoto from './AttachmentPhoto.jsx'
import { useAuth } from './AuthContext.jsx'
import Panel from './Panel.jsx'
import { REVIEWER_API, ReviewState, requestLabel } from './ReviewerPages.jsx'
import { ImageQuality, SimilarComplaints, TextImageConsistency } from './WorkflowAnalytics.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip)

const api = REVIEWER_API + '/analytics'
const palette = ['#5287ff', '#63d1e7', '#9b91ff', '#879ab9']
const number = (value) => value.toLocaleString()
const nlpLabels = [['plumbing', 'Plumbing'], ['electrical', 'Electrical'], ['hvac', 'HVAC'], ['abstained', 'Abstained']]
const priorities = [['high', 'High'], ['medium', 'Medium'], ['low', 'Low'], ['abstained', 'Abstained']]
const cvLabels = [['possible_crack', 'Possible crack'], ['below_threshold', 'Below threshold'], ['unscreened', 'Unscreened']]
const scoreLabels = [['at_or_above', 'At or above threshold'], ['below', 'Below threshold'], ['unrecorded', 'Score unavailable']]

function CountChart({ title, labels, counts, margins, empty }) {
  const canvas = useRef(null)
  const [selection, setSelection] = useState('')
  const total = labels.reduce((sum, [key]) => sum + (counts?.[key] ?? 0), 0)
  useEffect(() => {
    if (!canvas.current) return
    const chart = new Chart(canvas.current, {
      type: 'bar',
      data: {
        labels: labels.map(([, label]) => label),
        datasets: [{ data: labels.map(([key]) => counts?.[key] ?? 0), backgroundColor: palette, borderRadius: 7, maxBarThickness: 20 }],
      },
      options: {
        indexAxis: 'y', maintainAspectRatio: false, animation: false,
        onClick: (_event, items) => {
          if (items.length) setSelection(labels[items[0].index][0])
        },
        scales: {
          x: { beginAtZero: true, ticks: { color: '#aebfda', precision: 0 }, grid: { color: '#233a63' } },
          y: { ticks: { color: '#e2ebff' }, grid: { display: false } },
        },
        plugins: { tooltip: { callbacks: { afterLabel: ({ dataIndex }) => {
          const margin = margins?.[labels[dataIndex][0]]
          return margin == null ? '' : 'Mean native margin ' + margin.toFixed(3)
        } } } },
      },
    })
    return () => chart.destroy()
  }, [labels, counts, margins, total])

  const selectedLabel = labels.find(([key]) => key === selection)?.[1]
  const selectedMargin = margins?.[selection]
  return <Panel className="analytics-card">
    <h3>{title}</h3>
    {total ? <>
      <div className="analytics-chart">
        <canvas ref={canvas} role="img" aria-label={title + '. Counts are listed below.'} />
      </div>
      <div className="analytics-legend" aria-label={title + ' counts'}>
        {labels.map(([key, label], index) => <button key={key} type="button" aria-pressed={selection === key}
          onClick={() => setSelection(key)}><i style={{ backgroundColor: palette[index] }} />{label} <strong>{number(counts?.[key] ?? 0)}</strong></button>)}
      </div>
      {selectedLabel && <p className="analytics-muted" role="status">
        {selectedLabel} · {number(counts?.[selection] ?? 0)}{selectedMargin != null && ' · mean native margin ' + selectedMargin.toFixed(3)}
      </p>}
    </> : <p className="analytics-empty">{empty}</p>}
  </Panel>
}

function PerformanceOverview() {
  const result = useRequestData(REVIEWER_API + '/dashboard')
  const performance = useRequestData(api + '/performance')
  const [expanded, setExpanded] = useState(null)
  const request = result.data?.requests
  const order = result.data?.work_orders
  const resolved = order?.resolved ?? 0
  const active = (order?.open ?? 0) + (order?.in_progress ?? 0)
  const metrics = [
    ['Total requests', Object.values(request ?? {}).reduce((sum, count) => sum + count, 0)],
    ['Pending review', (request?.submitted ?? 0) + (request?.in_review ?? 0)],
    ['Active work orders', active],
    ['Resolved work orders', resolved],
    ['Resolution rate', active + resolved ? (100 * resolved / (active + resolved)).toFixed(1) + '%' : '—'],
  ]
  const teams = performance.data?.teams ?? []
  const breakdown = [
    [['By status', Object.entries(request ?? {}).map(([status,count]) => [status.replaceAll('_',' '),count])],
      ['By category', Object.entries(result.data?.categories ?? {})]],
    [['Pending statuses', [['Submitted', request?.submitted ?? 0], ['In review', request?.in_review ?? 0]]]],
    [['Active per team', teams.map(t => [t.name,t.active])]],
    [['Resolved per team', teams.map(t => [t.name,t.resolved])]],
    [['Resolution calculation', [['Resolved', resolved], ['Total work orders', active + resolved]]]],
  ]
  return <Panel className="analytics-card performance-card" id="performance">
    <h1>Performance Overview</h1>
    {result.data ? <>
      <dl className="analytics-metrics">{metrics.map(([label, value], index) =>
        <div key={label}>
          <dt>{label}</dt>
          <dd>{typeof value === 'number' ? number(value) : value}
            <button className="kpi-arrow" type="button" aria-label={label + ' breakdown'} aria-expanded={expanded === index}
              aria-controls="performance-breakdown" onClick={() => setExpanded(expanded === index ? null : index)}>
              <ChevronDown size={18} aria-hidden="true" />
            </button>
          </dd>
        </div>
      )}</dl>
      <div className="kpi-breakdown" id="performance-breakdown" hidden={expanded === null}>
        {expanded !== null && breakdown[expanded].map(([heading, rows]) =>
          <div key={heading}>
            <strong>{heading}</strong>
            {rows.length ? <ul>{rows.map(([name,count]) => <li key={name}><span>{name}</span><b>{count}</b></li>)}</ul> :
              <p className="analytics-muted">{performance.loading && [2,3].includes(expanded) ? 'Loading…' : 'No records'}</p>}
          </div>
        )}
      </div>
    </> : <ReviewState result={result} />}
  </Panel>
}

function TextInspection({ recent }) {
  const { authorizedFetch } = useAuth()
  const [source, setSource] = useState('')
  const [text, setText] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)

  async function inspect(event) {
    event.preventDefault()
    if (!source && !text.trim()) return
    setBusy(true)
    setResult(null)
    try {
      const response = await requestMaintenance(authorizedFetch, api + '/nlp-inspection', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(source ? { request_id: Number(source) } : { text }),
      })
      setResult(await response.json())
    } catch (error) {
      setResult({ explanation: maintenanceErrorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return <Panel className="analytics-card">
    <div className="analytics-heading"><h3>Inspect complaint text</h3><span className="review-badge">Research only</span></div>
    <p className="analytics-muted">TF-IDF + LinearSVC · Plumbing, Electrical, HVAC. This output is not validated for assignment decisions.</p>
    <form className="analytics-form" onSubmit={inspect}>
      <label>Text source<select disabled={busy} value={source} onChange={(event) => { setSource(event.target.value); setResult(null) }}>
        <option value="">Quick inspect · enter text</option>
        {recent.map(({ id, title }) => <option key={id} value={id}>{requestLabel(id)} · {title}</option>)}
      </select></label>
      {!source && <label>Test description<textarea disabled={busy} maxLength={4000} value={text}
        onChange={(event) => { setText(event.target.value); setResult(null) }} placeholder="Describe a maintenance fault…" rows={3} /></label>}
      <button className="review-button" disabled={busy || (!source && !text.trim())}>{busy ? 'Inspecting…' : 'Inspect text'}</button>
    </form>
    {result && <div className="analytics-result" role="status">
      {result.explanation ? <p>{result.explanation}</p> : <>
        <strong>{result.label || 'Abstained · No reliable suggestion'}</strong>
        {result.margin != null && <p className="analytics-muted">Native decision margin {result.margin.toFixed(3)}. This score is uncalibrated.</p>}
        <details open><summary>Preprocessing and top TF-IDF terms</summary>
          <p><b>Normalized text</b> {result.normalized || '—'}</p>
          <p><b>Tokens ({result.token_count})</b> {result.tokens.join(' · ') || '—'}{result.token_count > result.tokens.length && ' …'}</p>
          <p><b>Stop-word filter</b> {result.stopwords_removed ? 'Applied' : 'None in this frozen pipeline'}</p>
          {result.terms.length ? <ul className="analytics-terms">{result.terms.map(({ term, weight }) =>
            <li key={term}>{term} <strong>{weight.toFixed(3)}</strong></li>)}</ul> : <p>No matching TF-IDF vocabulary terms.</p>}
        </details>
        <SimilarComplaints matches={result.similar} />
      </>}
    </div>}
  </Panel>
}

const previewViews = [['resized', 'Resized · 224 × 224'], ['grayscale', 'Grayscale · display only'],
  ['normalized', 'Normalized RGB · display scale'], ['grad_cam', 'Grad-CAM · crack class']]

function ImageScreening() {
  const { authorizedFetch } = useAuth()
  const [page, setPage] = useState(1)
  const attachments = useRequestData(api + '/attachments?page=' + page)
  const [selected, setSelected] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const items = attachments.data?.items ?? []
  const photo = items.find(({ id }) => String(id) === selected) ?? items[0]
  const jpeg = photo?.content_type === 'image/jpeg'
  const photoRoute = photo && '/requests/' + photo.request_id + '/attachments/' + photo.id

  function resetSelection(id = '') {
    setSelected(id)
    setConfirmed(false)
    setResult(null)
  }

  async function screen() {
    if (!jpeg || !confirmed || busy) return
    setBusy(true)
    setResult(null)
    try {
      const response = await requestMaintenance(authorizedFetch, api + photoRoute + '/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ concrete_closeup: true }),
      })
      const outcome = await response.json()
      setResult(outcome)
    } catch (error) {
      setResult({ explanation: maintenanceErrorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  return <Panel className="analytics-card image-workspace">
    <div className="analytics-heading"><h3>Inspect an attachment</h3><span className="review-badge">ResNet-18</span></div>
    <p className="analytics-muted">Provisional screening for concrete close-ups. A qualified person must assess structural safety.</p>
    {photo ? <>
      <div className="analytics-form">
        <label>Complaint attachment<select value={photo.id} disabled={busy} onChange={(event) => resetSelection(event.target.value)}>
          {items.map((item) => <option key={item.id} value={item.id}>{requestLabel(item.request_id)} · {item.title} · {item.original_filename}</option>)}
        </select></label>
        <div className="review-actions" aria-label="Attachment pages">
          <button className="review-button" disabled={busy || attachments.data.page === 1} onClick={() => { resetSelection(); setPage(page - 1) }}>Previous</button>
          <span>Page {attachments.data.page} of {attachments.data.pages}</span>
          <button className="review-button" disabled={busy || attachments.data.page === attachments.data.pages} onClick={() => { resetSelection(); setPage(page + 1) }}>Next</button>
        </div>
        <label className="review-ai-check"><input type="checkbox" disabled={!jpeg || busy} checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)} /> I inspected this still JPEG and confirm it is a concrete close-up.</label>
        {!jpeg && <p className="analytics-muted">This attachment can be viewed. Screening requires a still JPEG.</p>}
        <button className="review-button" disabled={!jpeg || !confirmed || busy} onClick={screen}>{busy ? 'Screening…' : 'Screen and explain'}</button>
      </div>
      <div className="analytics-images">
        <figure><ul><AttachmentPhoto key={photo.id} path={REVIEWER_API + photoRoute} attachment={photo} /></ul><figcaption>Original attachment</figcaption></figure>
        {result?.status === 'screened' && previewViews.map(([key, caption]) => <figure key={key}>
          {result[key] ? <img src={'data:image/' + (key === 'grad_cam' ? 'png' : 'jpeg') + ';base64,' + result[key]} alt={caption} /> :
            <p className="analytics-empty">No positive crack-class activation.</p>}
          <figcaption>{caption}</figcaption>
        </figure>)}
      </div>
    </> : <ReviewState result={attachments} empty="There are no complaint attachments to inspect." />}
    {result && <div role="status" className="analytics-result">
      {result.status === 'screened' ? <>
        <strong>{result.crack_detected ? 'Possible concrete crack' : 'Crack screening threshold not met'}</strong>
        <p>Crack-class softmax score {result.score.toFixed(3)} · threshold {result.threshold.toFixed(2)}. The score is uncalibrated.</p>
        <ImageQuality data={result.quality} />
        <TextImageConsistency data={result.consistency} />
        <p className="analytics-muted">Grad-CAM highlights regions that increased the crack-class score. The grayscale image is a visual aid. The model receives normalized RGB.</p>
      </> : <p>{result.explanation || 'The screening worker is unavailable. You can still inspect the original photo.'}</p>}
    </div>}
  </Panel>
}

function AnalyticsSection({ icon: Icon, title, description, children }) {
  return <section className="analytics-section">
    <div className="analytics-section-title"><span className="overview-icon"><Icon size={22} aria-hidden="true" /></span>
      <div><h2>{title}</h2><p>{description}</p></div>
    </div>
    {children}
  </section>
}

export function ReviewerDashboard() {
  const summary = useRequestData(api + '/summary')
  const data = summary.data
  return <div className="reviewer-studio">
    <PerformanceOverview />
    {!data && <ReviewState result={summary} />}
    <AnalyticsSection icon={AudioLines} title="NLP Analytics" description="Saved complaint sample · Research-only model outputs">
      {data && <div className="analytics-grid">
        <CountChart title="NLP category and abstention" labels={nlpLabels} counts={data.nlp.counts} margins={data.nlp.mean_margin}
          empty={data.nlp.state === 'unavailable' ? 'The NLP worker could not inspect the sample.' :
            data.nlp.state === 'disabled' ? 'NLP research is disabled.' : 'No saved complaints in this sample.'} />
        <CountChart title="Priority rules distribution" labels={priorities} counts={data.priority}
          empty="No priority results to chart." />
      </div>}
      {data?.nlp.state === 'unavailable' && <button className="text-link" type="button" onClick={summary.retry}>Retry NLP chart</button>}
      {data?.recent.length > 0 && <p className="analytics-muted">Showing {data.recent.length} of up to {data.sample_limit} most recent complaints. Hover over an NLP bar for the mean native margin.</p>}
    </AnalyticsSection>
    <AnalyticsSection icon={ScanLine} title="Computer Vision Analytics" description="Saved screenings and crack-class score distribution">
      {data && <div className="analytics-grid">
        <CountChart title="Attachment screening coverage" labels={cvLabels} counts={data.cv.counts} empty="No image attachments have been submitted." />
        <CountChart title={'Crack score vs. ' + data.cv.threshold + ' threshold'} labels={scoreLabels} counts={data.cv.scores}
          empty="No screening scores have been recorded." />
      </div>}
    </AnalyticsSection>
  </div>
}

export function ReviewerImageAnalytics() {
  const complaints = useRequestData(api + '/complaints')
  return <div className="reviewer-studio">
    <div className="page-heading"><h1>AI Analytics (CV &amp; NLP)</h1></div>
    <div className="analytics-grid split-inspection">
      <ImageScreening />
      <TextInspection recent={complaints.data ?? []} />
    </div>
  </div>
}

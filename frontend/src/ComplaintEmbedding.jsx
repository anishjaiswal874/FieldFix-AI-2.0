import { useEffect, useRef, useState } from 'react'
import { Chart, LinearScale, PointElement, ScatterController, Tooltip } from 'chart.js'
import { Link } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import Panel from './Panel.jsx'
import { maintenanceErrorMessage, requestMaintenance } from './requestApi.js'

Chart.register(ScatterController, PointElement, LinearScale, Tooltip)
const api = '/api/reviewer/analytics'
const title = value => value[0].toUpperCase() + value.slice(1).replaceAll('_', ' ')
const ref = id => 'FF-' + String(id).padStart(3, '0')

const colors = ['#6ce7ff','#8fa4ff','#e2adff','#66e1ad','#f9a963','#9ab8d5']

export function EmbeddingMap() {
  const { authorizedFetch } = useAuth()
  const [result, setResult] = useState(null)
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(false)
  const canvas = useRef(null)
  const points = result?.points

  async function load() {
    setSelected(null)
    setLoading(true)
    try {
      const response = await requestMaintenance(authorizedFetch, api + '/embedding')
      setResult(await response.json())
    } catch (error) {
      setResult({ error: maintenanceErrorMessage(error) })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!canvas.current || !points?.length) return
    const groups = [...new Set(points.map(point => point.category ?? point.status))]
    const chart = new Chart(canvas.current, {
      type: 'scatter',
      data: { datasets: [{
        data: points, pointRadius: points.map(point => point.overridden || point.abstained ? 6 : 4),
        pointBackgroundColor: points.map(point => colors[groups.indexOf(point.category ?? point.status) % colors.length]),
        pointBorderColor: points.map(point => point.overridden ? '#ff9b60' : point.abstained ? '#fff' : '#142341'),
        pointBorderWidth: 2,
      }] },
      options: { maintainAspectRatio: false, animation: false,
        scales: {x: {ticks: {color:'#aabddd'}, grid:{color:'#274061'}}, y: {ticks: {color:'#aabddd'}, grid:{color:'#274061'}}},
        plugins: {tooltip:{callbacks:{label: ({raw}) => raw.title + ' · ' + (raw.category || title(raw.status))}}},
        onClick: (_event, hits) => {if(hits.length) setSelected(points[hits[0].index])},
      },
    })
    return () => chart.destroy()
  }, [points])

  return <Panel className="analytics-card">
    <div className="analytics-heading"><h3>2D Complaint Embedding Map (PCA)</h3>
      <button className="review-button" type="button" onClick={load} disabled={loading}>{loading ? 'Projecting…' : 'Load map'}</button>
    </div>
    <p className="analytics-muted">PCA of saved TF-IDF vectors · up to 200 recent complaints. Color shows category or status. Orange outlines mark overrides and white outlines mark abstentions.</p>
    {points?.length ? <>
      <div className="analytics-chart"><canvas ref={canvas} role="img" aria-label="Complaint TF-IDF scatter plot" /></div>
      <div className="analytics-form"><label>Inspect point<select value={selected?.id ?? ''} onChange={e => setSelected(points.find(p => String(p.id) === e.target.value))}>
        <option value="">Select a complaint</option>{points.map(p => <option key={p.id} value={p.id}>{ref(p.id)} · {p.title}</option>)}
      </select></label></div>
      {selected && <p><Link className="text-link" to={'/reviewer/triage/' + selected.id}>{selected.title}</Link>
        {' · '}{selected.category || title(selected.status)}{selected.abstained ? ' · Abstained' : ''}{selected.overridden ? ' · Overridden' : ''}</p>}
    </> : result && <p className="analytics-muted">{result.error || (result.status === 'insufficient_data' ? 'Add at least two saved complaints to generate the map.' : 'No usable text vectors were available.')}</p>}
  </Panel>
}

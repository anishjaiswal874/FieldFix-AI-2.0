import { useEffect, useRef, useState } from 'react'
import { useAuth } from './AuthContext.jsx'
import { maintenanceErrorMessage, requestMaintenance, useRequestData } from './requestApi.js'
import { REVIEWER_API } from './ReviewerPages.jsx'
import { ImageQuality, TextImageConsistency } from './WorkflowAnalytics.jsx'

const stateLabels = {
  disabled: 'Disabled',
  unavailable: 'Unavailable',
  abstained: 'Manual review',
  unsuitable: 'Screening skipped',
  research_only: 'Research output',
  suggested: 'Suggestion',
  screened: 'Screening result',
}

export function RecommendationsUnavailable() {
  return <p className="review-ai"><span className="review-badge">Not analyzed</span> AI suggestions and image screening are unavailable.</p>
}

function RecommendationResult({ result }) {
  if (!result) return null
  if (result.loading) return <p role="status">Checking…</p>
  if (result.error) return <p className="review-error" role="alert">{result.error}</p>
  return (
    <div className="review-ai-result" role="status">
      <span className="review-badge">{stateLabels[result.status]}</span>
      {result.label_name && <strong>{result.label_name}</strong>}
      {result.priority && <strong>{result.priority[0].toUpperCase() + result.priority.slice(1)}</strong>}
      {result.fact && <p>{result.fact}</p>}
      <p>{result.explanation}</p>
      {result.limitation && <p>{result.limitation}</p>}
      {result.quality && <ImageQuality data={result.quality} />}
      {result.consistency && <TextImageConsistency data={result.consistency} />}
      {result.rule_version && <small>Rule set {result.rule_version}</small>}
    </div>
  )
}

export default function ReviewRecommendations({ request, categories, canApply, onUseCategory }) {
  const { authorizedFetch } = useAuth()
  const features = useRequestData(REVIEWER_API + '/recommendations/features')
  const [results, setResults] = useState({})
  const [selected, setSelected] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [copyError, setCopyError] = useState('')
  const active = useRef({})
  const base = REVIEWER_API + '/requests/' + request.id
  const photos = request.attachments.filter((attachment) => attachment.content_type === 'image/jpeg')
  const show = features.data

  useEffect(() => {
    const jobs = active.current
    return () => Object.values(jobs).forEach((controller) => controller.abort())
  }, [])

  function update(feature, result) {
    setResults((current) => ({ ...current, [feature]: result }))
  }

  async function recommend(feature, path, body) {
    if (active.current[feature]) return
    const controller = new AbortController()
    active.current[feature] = controller
    update(feature, { loading: true })
    if (feature === 'nlp') setCopyError('')
    try {
      const options = { method: 'POST', signal: controller.signal }
      if (body) {
        options.headers = { 'Content-Type': 'application/json' }
        options.body = JSON.stringify(body)
      }
      const response = await requestMaintenance(authorizedFetch, path, options)
      const result = await response.json()
      if (feature === 'cv' && result.status === 'screened') {
        result.explanation = result.crack_detected ? 'Possible concrete crack pattern detected.' : 'Crack screening threshold not met.'
      }
      if (!controller.signal.aborted) update(feature, result)
    } catch (error) {
      if (!controller.signal.aborted) update(feature, { error: maintenanceErrorMessage(error) })
    } finally {
      if (active.current[feature] === controller) delete active.current[feature]
    }
  }

  function changePhoto(id) {
    active.current.cv?.abort()
    delete active.current.cv
    setSelected(id)
    setConfirmed(false)
    update('cv', null)
  }

  const candidate = results.nlp?.actionable === true ? results.nlp.category : null
  async function useCategory() {
    if (!candidate || !canApply || active.current.copy) return
    const controller = new AbortController()
    active.current.copy = controller
    setCopyError('')
    try {
      const current = await requestMaintenance(authorizedFetch, REVIEWER_API + '/options', { signal: controller.signal })
      const options = await current.json()
      if (controller.signal.aborted) return
      if (options.categories.some(({ id, name }) => id === candidate.id && name === candidate.name)) {
        onUseCategory(candidate.id)
      } else {
        setCopyError('This category changed. Refresh the request before choosing a category.')
      }
    } catch (error) {
      if (!controller.signal.aborted) setCopyError(maintenanceErrorMessage(error))
    } finally {
      delete active.current.copy
    }
  }

  if (!show || !Object.values(show).some(Boolean)) return <RecommendationsUnavailable />

  return (
    <div className="review-ai review-ai-options">
      <p>Recommendations are optional. Review the evidence and choose the final decision yourself.</p>
      {show.nlp_demo && (
        <section aria-label="NLP research demo">
          <h3>NLP research demo</h3>
          <p><strong>Research only. This output is not validated for assignment decisions.</strong></p>
          <p>TF-IDF + LinearSVC analyzes the saved complaint description for Plumbing, Electrical or HVAC.</p>
          <button className="review-button" type="button" disabled={!!results.nlp_demo?.loading}
            onClick={() => recommend('nlp_demo', base + '/recommendations/nlp-demo')}>Run NLP demo</button>
          <RecommendationResult result={results.nlp_demo} />
        </section>
      )}
      {show.nlp && (
        <section aria-label="Category recommendation">
          <button className="review-button" type="button" disabled={!!results.nlp?.loading}
            onClick={() => recommend('nlp', base + '/recommendations/nlp')}>Suggest category</button>
          <RecommendationResult result={results.nlp} />
          {candidate && categories.some(({ id, name }) => id === candidate.id && name === candidate.name) &&
            <button className="text-link" type="button" onClick={useCategory} disabled={!canApply}>Use suggested category</button>}
          {copyError && <p className="review-error" role="alert">{copyError}</p>}
        </section>
      )}
      {show.priority && (
        <section aria-label="Rule-based priority recommendation">
          <button className="review-button" type="button" disabled={!!results.priority?.loading}
            onClick={() => recommend('priority', base + '/recommendations/priority')}>Suggest priority by rule</button>
          <RecommendationResult result={results.priority} />
        </section>
      )}
      {show.cv && (
        <section aria-label="Concrete crack screening">
          {photos.length ? (
            <>
              <label>Photo for concrete crack screening
                <select value={selected} onChange={(event) => changePhoto(event.target.value)}>
                  <option value="">Choose a JPEG attachment</option>
                  {photos.map((photo) => <option key={photo.id} value={photo.id}>{photo.original_filename}</option>)}
                </select>
              </label>
              <label className="review-ai-check">
                <input type="checkbox" checked={confirmed} disabled={!selected}
                  onChange={(event) => setConfirmed(event.target.checked)} />
                I inspected this photo and confirm it is a close-up of concrete.
              </label>
              <button className="review-button" type="button" disabled={!selected || !confirmed || !!results.cv?.loading}
                onClick={() => recommend('cv', REVIEWER_API + '/analytics/requests/' + request.id +
                  '/attachments/' + encodeURIComponent(selected) + '/preview', { concrete_closeup: true })}>
                Screen selected photo
              </button>
            </>
          ) : <p>No JPEG attachment is available for concrete screening.</p>}
          <RecommendationResult result={results.cv} />
        </section>
      )}
    </div>
  )
}

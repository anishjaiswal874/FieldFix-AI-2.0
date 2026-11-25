import { Send, UploadCloud, Wrench, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useAuth } from './AuthContext.jsx'
import Panel, { PanelHeading } from './Panel.jsx'
import { acceptedPhoto, photoAccept, photoFormatLabel } from './photoUpload.js'
import { maintenanceErrorMessage, REQUESTS_PATH, requestMaintenance, useRequestData } from './requestApi.js'

const titleLimit = 255
const detailsLimit = 1000
const photoLimit = 5

function PhotoPreview({ file, onRemove, disabled }) {
  const [url, setUrl] = useState('')
  const [unrenderable, setUnrenderable] = useState(false)

  useEffect(() => {
    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])

  return (
    <li className="request-photo">
      {url && !unrenderable ? <img src={url} alt={file.name} onError={() => setUnrenderable(true)} /> :
        <small className="upload-count">Preview after upload</small>}
      <button type="button" aria-label={`Remove ${file.name}`} disabled={disabled} onClick={onRemove}>
        <X size={16} aria-hidden="true" />
      </button>
    </li>
  )
}

export default function RequesterForm() {
  const { authorizedFetch } = useAuth()
  const navigate = useNavigate()
  const { data: categories, loading: categoriesLoading, error: categoriesError } = useRequestData(`${REQUESTS_PATH}/categories`)
  const { data: profile } = useRequestData(`${REQUESTS_PATH}/profile`)
  const [photos, setPhotos] = useState([])
  const [feedback, setFeedback] = useState('')
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const fileInput = useRef(null)
  const locationInput = useRef(null)
  const locationEdited = useRef(false)
  const nextPhotoId = useRef(0)
  const pending = useRef(null)
  useEffect(() => () => pending.current?.abort(), [])
  useEffect(() => {
    if (profile?.default_location && !locationEdited.current && locationInput.current) {
      locationInput.current.value = profile.default_location
    }
  }, [profile?.default_location])

  function addPhotos(files) {
    if (submitting) return
    const images = Array.from(files).filter(acceptedPhoto)
    const available = photoLimit - photos.length
    const added = images.slice(0, available).map((file) => ({ id: nextPhotoId.current++, file }))

    setPhotos((current) => [...current, ...added].slice(0, photoLimit))
    if (images.length > available) setFeedback('You can attach up to five photos.')
    else if (images.length < files.length) setFeedback('Choose ' + photoFormatLabel + ' photos up to 15 MiB each.')
    else setFeedback('')
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (pending.current) return
    const form = new FormData(event.currentTarget)
    for (const name of ['title', 'description', 'location']) {
      const value = form.get(name).trim()
      if (!value) {
        setFeedback('Enter a title, description, and location.')
        event.currentTarget.elements.namedItem(name).focus()
        return
      }
      form.set(name, value)
    }
    if (!form.get('category_id')) form.delete('category_id')
    photos.forEach(({ file }) => form.append('photos', file))

    const controller = new AbortController()
    pending.current = controller
    setSubmitting(true)
    setFeedback('')
    try {
      const response = await requestMaintenance(authorizedFetch, REQUESTS_PATH, { method: 'POST', body: form, signal: controller.signal })
      const request = await response.json()
      if (!controller.signal.aborted) navigate(`/requester/requests/${request.id}`, { replace: true })
    } catch (error) {
      if (!controller.signal.aborted) setFeedback(maintenanceErrorMessage(error))
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setSubmitting(false)
    }
  }

  return (
    <Panel className="requester-screen">
      <PanelHeading
        icon={Wrench}
        title="New Maintenance Request"
        description="Describe the issue, tell us where it is, and add photos if you have them."
      />

      <form className="request-form" onSubmit={handleSubmit} aria-busy={submitting}>
        <div className="request-field">
          <label htmlFor="request-title">Request Title <span className="request-required" aria-hidden="true">*</span></label>
          <input
            id="request-title"
            name="title"
            disabled={submitting}
            required
            maxLength={titleLimit}
            placeholder="Enter a short title"
          />
        </div>
        <div className="request-field">
          <label htmlFor="request-details">Request Details <span className="request-required" aria-hidden="true">*</span></label>
          <textarea
            id="request-details"
            name="description"
            disabled={submitting}
            required
            maxLength={detailsLimit}
            placeholder="Describe what needs attention"
          />
        </div>

        <div className="request-field-pair">
          <div className="request-field">
            <label htmlFor="request-location">Location <span className="request-required" aria-hidden="true">*</span></label>
            <input ref={locationInput} onChange={() => { locationEdited.current = true }} id="request-location" name="location" maxLength={255} placeholder="Building, floor or room" disabled={submitting} required />
          </div>
          <div className="request-field">
            <label htmlFor="request-category">Category <span className="request-optional">(optional)</span></label>
            <select id="request-category" name="category_id" defaultValue="" disabled={submitting || categoriesLoading || !categories?.length}>
              <option value="">{categoriesLoading ? 'Loading categories…' : categoriesError ? 'Categories unavailable' : categories?.length ? 'Select category' : 'No categories available'}</option>
              {categories?.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
            </select>
          </div>
        </div>

        <div className="request-field">
          <p className="request-label" id="request-photos-label">Upload Photos <span className="request-optional">(optional)</span></p>
          <div
            className={`upload-box${dragging ? ' is-dragging' : ''}`}
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={(event) => {
              if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
                setDragging(false)
              }
            }}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              addPhotos(event.dataTransfer.files)
            }}
          >
            <input
              ref={fileInput}
              aria-label="Select photos"
              type="file"
              multiple
              accept={photoAccept}
              disabled={submitting}
              hidden
              onChange={(event) => {
                addPhotos(event.currentTarget.files)
                event.currentTarget.value = ''
              }}
            />
            {photos.length > 0 && (
              <ul className="request-photos" aria-label="Selected photos">
                {photos.map(({ id, file }) => (
                  <PhotoPreview
                    key={id}
                    file={file}
                    disabled={submitting}
                    onRemove={() => {
                      setPhotos((current) => current.filter((photo) => photo.id !== id))
                      setFeedback('')
                    }}
                  />
                ))}
              </ul>
            )}
            <button className="drop-zone" type="button" disabled={submitting} aria-describedby="request-photos-label" onClick={() => fileInput.current.click()}>
              <UploadCloud size={26} aria-hidden="true" />
              <span>Drag and drop images, or <span className="browse-label">Browse</span></span>
              <span className="upload-count">{photos.length} of {photoLimit} images selected · {photoFormatLabel} · 15 MiB each</span>
            </button>
          </div>
        </div>

        <div className="request-form-footer">
          <p role="status">{feedback}</p>
          <div className="request-actions">
            {submitting ? <button className="request-secondary" type="button" disabled>Cancel</button> : <Link className="request-secondary" to="/requester">Cancel</Link>}
            <button className="request-primary" type="submit" disabled={submitting}><Send size={17} aria-hidden="true" /> {submitting ? 'Submitting…' : 'Submit Request'}</button>
          </div>
        </div>
      </form>
    </Panel>
  )
}

import { useEffect, useState } from 'react'
import { useAuth } from './AuthContext.jsx'
import { maintenanceErrorMessage, requestMaintenance } from './requestApi.js'

export default function AttachmentPhoto({ path, attachment }) {
  const { authorizedFetch } = useAuth()
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    async function load() {
      try {
        const response = await requestMaintenance(authorizedFetch, path, { signal: controller.signal })
        const image = await response.blob()
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(image)
        setUrl(objectUrl)
      } catch (error) {
        if (!controller.signal.aborted) setError(maintenanceErrorMessage(error))
      }
    }
    load()
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [authorizedFetch, path])

  return url ? (
    <li className="request-photo"><a href={url} target="_blank" rel="noopener noreferrer"><img src={url} alt={attachment.original_filename} /></a></li>
  ) : <li role="status">{error || 'Loading photo…'}</li>
}

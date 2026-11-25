export default function Panel({ className = '', children, ...attributes }) {
  return (
    <section className={`glass page-panel ${className}`} {...attributes}>
      {children}
    </section>
  )
}

export function PanelHeading({ as: Heading = 'h1', icon: Icon, title, description, actions }) {
  return (
    <div className="page-heading">
      <div className="panel-title">
        {Icon && <span className="panel-title-icon"><Icon size={22} aria-hidden="true" /></span>}
        <div>
          <Heading>{title}</Heading>
          {description && <p>{description}</p>}
        </div>
      </div>
      {actions}
    </div>
  )
}

export function PreviewBadge({ sample = false }) {
  return <span className={`preview-badge${sample ? ' sample-badge' : ''}`}>{sample ? 'SAMPLE DATA' : 'Preview'}</span>
}

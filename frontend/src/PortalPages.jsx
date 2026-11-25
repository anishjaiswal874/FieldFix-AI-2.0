import { Layers3 } from 'lucide-react'
import Panel, { PreviewBadge } from './Panel.jsx'

function EmptyState({ icon: Icon = Layers3, children }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={24} strokeWidth={1.5} aria-hidden="true" /></span>
      <p>{children}</p>
    </div>
  )
}

function PreviewTable({ page }) {
  return (
    <div className="preview-table" role="region" aria-label={`${page.label} table`} tabIndex={0}>
      <table>
        <thead>
          <tr>{page.columns.map((label) => <th key={label} scope="col">{label}</th>)}</tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={page.columns.length}>
              <EmptyState icon={page.icon}>{page.message}</EmptyState>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export function PortalPage({ page }) {
  return (
    <Panel className="portal-page">
      <div className="page-heading">
        <h1>{page.title || page.label}</h1>
        <div className="page-actions"><PreviewBadge /></div>
      </div>
      {page.columns
        ? <PreviewTable page={page} />
        : <div className="empty-panel"><EmptyState icon={page.icon}>{page.message}</EmptyState></div>}
    </Panel>
  )
}


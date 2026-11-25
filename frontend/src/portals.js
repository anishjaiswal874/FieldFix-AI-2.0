import {
  Building2, ClipboardList, Files, History, Images, Inbox, LayoutGrid,
  Network, Settings, SlidersHorizontal, SquarePlus, UserRound, UsersRound,
} from 'lucide-react'

export const portals = [
  {
    id: 'requester',
    label: 'Requester',
    emblem: Building2,
    pages: [
      { path: '', label: 'Dashboard', icon: LayoutGrid },
      { path: 'new', label: 'New Request', icon: SquarePlus },
      { path: 'requests', label: 'My Requests', icon: ClipboardList },
      { path: 'profile', label: 'Profile', icon: UserRound },
    ],
  },
  {
    id: 'reviewer',
    label: 'Reviewer',
    pages: [
      { path: '', label: 'Dashboard', title: 'Reviewer Dashboard', icon: LayoutGrid },
      {
        path: 'triage', label: 'Triage & Resolution', title: 'Triage & Resolution', icon: Inbox,
        columns: ['ID', 'Issue & Location', 'AI Category', 'CV Crack Check', 'Action'],
        message: 'Requests awaiting review will appear here.',
      },
      {
        path: 'image-analytics', label: 'AI Analytics (CV & NLP)', icon: Images,
      },
      {
        path: 'work-orders', label: 'Work Orders', title: 'Active Work Orders', icon: Files,
        columns: ['Work Order', 'Issue & Location', 'Assigned Team', 'Status', 'Action'],
        message: 'Work orders and assignments will appear here.',
      },
      {
        path: 'history', label: 'History', title: 'Reviewed Request History', icon: History,
        columns: ['ID', 'Issue Summary', 'Final Category', 'Reviewer Decision', 'Date'],
        message: 'Reviewed requests will appear here.',
      },
    ],
  },
  {
    id: 'admin',
    label: 'Administrator',
    overview: ['users', 'categories', 'teams', 'settings'],
    panels: ['System Performance & Overrides', 'Active Role Distribution'],
    pages: [
      { path: '', label: 'Dashboard', title: 'Administrator Dashboard', icon: LayoutGrid },
      {
        path: 'users', label: 'Users & Roles', title: 'Users & Role Management', icon: UsersRound,
        columns: ['User', 'Assigned Role', 'Status'], message: 'User accounts and assigned roles will appear here.',
      },
      {
        path: 'categories', label: 'Categories', title: 'Maintenance Categories', icon: SlidersHorizontal,
        columns: ['Category', 'Status', 'Action'], message: 'Facility maintenance categories will appear here.',
      },
      {
        path: 'teams', label: 'Teams', title: 'Maintenance Teams', icon: Network,
        columns: ['Team Name', 'Specialty', 'Availability'], message: 'Maintenance teams will appear here.',
      },
      {
        path: 'settings', label: 'Settings', title: 'System Settings', icon: Settings,
        message: 'Facility and platform preferences will appear here.',
      },
    ],
  },
]

export function portalPagePath(portal, page) {
  return page.path ? `/${portal.id}/${page.path}` : `/${portal.id}`
}

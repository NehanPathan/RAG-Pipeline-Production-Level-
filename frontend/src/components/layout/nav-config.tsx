import {
  ActivityIcon,
  BadgeCheckIcon,
  ClipboardListIcon,
  FileStackIcon,
  FolderKanbanIcon,
  GaugeCircleIcon,
  type LucideIcon,
  MessagesSquareIcon,
  ScaleIcon,
  SearchIcon,
  SettingsIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  UploadCloudIcon,
  UsersIcon,
} from "lucide-react"
import type { Role } from "@/api/types"

export interface NavItem {
  label: string
  to: string
  icon: LucideIcon
  /** Platform roles allowed to see the item. Absent = everyone. */
  roles?: Role[]
  description: string
  /** Matched with startsWith so detail routes keep the parent highlighted. */
  match?: string
  end?: boolean
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const NAV: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      {
        label: "Overview",
        to: "/",
        icon: GaugeCircleIcon,
        end: true,
        description: "Corpus health, live ingestion and what changed today",
      },
      {
        label: "Ask",
        to: "/ask",
        icon: MessagesSquareIcon,
        description: "Grounded questions over the corpus, with citations",
      },
      {
        label: "Search",
        to: "/search",
        icon: SearchIcon,
        description: "Faceted keyword and semantic search across every sheet",
      },
    ],
  },
  {
    label: "Register",
    items: [
      {
        label: "Projects",
        to: "/projects",
        icon: FolderKanbanIcon,
        description: "Jobs, their drawing sets and who can reach them",
      },
      {
        label: "Drawings",
        to: "/drawings",
        icon: ClipboardListIcon,
        description: "Drawing identities and their revision history",
      },
      {
        label: "Documents",
        to: "/documents",
        icon: FileStackIcon,
        description: "Every ingested file, with classification and status",
      },
    ],
  },
  {
    label: "Ingestion",
    items: [
      {
        label: "Upload",
        to: "/ingest",
        icon: UploadCloudIcon,
        roles: ["analyst", "steward", "admin"],
        description: "Add drawings, specifications and revisions",
      },
      {
        label: "Queue",
        to: "/ingest/queue",
        icon: ActivityIcon,
        roles: ["analyst", "steward", "admin"],
        description: "Ingestion jobs, stages, retries and failures",
      },
    ],
  },
  {
    label: "Operations",
    items: [
      {
        label: "Monitoring",
        to: "/ops/monitoring",
        icon: ActivityIcon,
        roles: ["steward", "admin"],
        description: "Service health, throughput and cost",
      },
      {
        label: "Quality",
        to: "/ops/quality",
        icon: BadgeCheckIcon,
        roles: ["analyst", "steward", "admin"],
        description: "Evaluation runs, quality gate and user feedback",
      },
      {
        label: "Retrieval inspector",
        to: "/ops/inspector",
        icon: SlidersHorizontalIcon,
        roles: ["analyst", "steward", "admin"],
        description: "Vector, BM25, fusion and rerank, stage by stage",
      },
      {
        label: "Governance",
        to: "/ops/governance",
        icon: ShieldCheckIcon,
        roles: ["steward", "admin"],
        description: "Policy, kill switches, risk register, audit trail",
      },
      {
        label: "Access",
        to: "/ops/access",
        icon: UsersIcon,
        roles: ["steward", "admin"],
        description: "Roles, clearances and project membership",
      },
      {
        label: "Settings",
        to: "/ops/settings",
        icon: SettingsIcon,
        roles: ["admin"],
        description: "Models, retrieval parameters and chunking",
      },
    ],
  },
]

export const LEGEND_ITEM: NavItem = {
  label: "Legend",
  to: "/legend",
  icon: ScaleIcon,
  description: "What every badge, colour and score on these screens means",
}

export function visibleNav(role: Role | undefined): NavGroup[] {
  if (!role) return []
  return NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.roles || item.roles.includes(role)),
  })).filter((group) => group.items.length > 0)
}

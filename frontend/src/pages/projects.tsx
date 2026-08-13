import * as React from "react"
import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  ClipboardListIcon,
  FileStackIcon,
  FolderKanbanIcon,
  MapPinIcon,
  SearchIcon,
  UsersIcon,
  WeightIcon,
} from "lucide-react"
import { api } from "@/api"
import type { ProjectStatus } from "@/api/types"
import { cn, formatDate, formatNumber, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader } from "@/components/domain/layout"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Progress, SegmentedTrack, ToggleGroup, ToggleGroupItem } from "@/components/ui/misc"
import { Hint } from "@/components/ui/tooltip"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ProjectStatusBadge, SensitivityBadge } from "@/components/domain/badges"
import { CardGridSkeleton, EmptyState, ErrorState } from "@/components/domain/states"

export default function ProjectsPage() {
  const [search, setSearch] = React.useState("")
  const [status, setStatus] = React.useState<ProjectStatus | "all">("all")
  const [view, setView] = React.useState<"grid" | "table">("grid")

  const { data: projects, isPending, isError, error, refetch } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
  })

  const filtered = React.useMemo(() => {
    let items = projects ?? []
    if (status !== "all") items = items.filter((p) => p.status === status)
    if (search.trim()) {
      const q = search.toLowerCase()
      items = items.filter(
        (p) =>
          p.project_number.toLowerCase().includes(q) ||
          p.name.toLowerCase().includes(q) ||
          (p.client_name ?? "").toLowerCase().includes(q) ||
          (p.location ?? "").toLowerCase().includes(q),
      )
    }
    // Active work first — a closed job is reference, not today's problem.
    const rank: Record<string, number> = { active: 0, on_hold: 1, closed: 2, archived: 3 }
    return [...items].sort(
      (a, b) => rank[a.status] - rank[b.status] || a.project_number.localeCompare(b.project_number),
    )
  }, [projects, search, status])

  const counts = React.useMemo(() => {
    const all = projects ?? []
    return {
      all: all.length,
      active: all.filter((p) => p.status === "active").length,
      on_hold: all.filter((p) => p.status === "on_hold").length,
      closed: all.filter((p) => p.status === "closed").length,
    }
  }, [projects])

  return (
    <Page>
      <PageHeader
        eyebrow="Register"
        title="Projects"
        description="A project is an access scope, not a label — it is what lets two engineers on the same job see each other's drawings without making every document global."
      />

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Project number, client, location…"
            className="pl-9"
          />
        </div>
        <SegmentedTrack>
          <ToggleGroup
            type="single"
            value={status}
            onValueChange={(v) => v && setStatus(v as ProjectStatus | "all")}
          >
            <ToggleGroupItem value="all">All {counts.all}</ToggleGroupItem>
            <ToggleGroupItem value="active">Active {counts.active}</ToggleGroupItem>
            <ToggleGroupItem value="on_hold">On hold {counts.on_hold}</ToggleGroupItem>
            <ToggleGroupItem value="closed">Closed {counts.closed}</ToggleGroupItem>
          </ToggleGroup>
        </SegmentedTrack>
        <SegmentedTrack className="ml-auto">
          <ToggleGroup
            type="single"
            value={view}
            onValueChange={(v) => v && setView(v as "grid" | "table")}
          >
            <ToggleGroupItem value="grid">Cards</ToggleGroupItem>
            <ToggleGroupItem value="table">Table</ToggleGroupItem>
          </ToggleGroup>
        </SegmentedTrack>
      </div>

      <div className="mt-4">
        {isPending ? (
          <CardGridSkeleton />
        ) : isError ? (
          <ErrorState error={error} onRetry={refetch} resource="projects" />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={FolderKanbanIcon}
            title={search ? `No project matches “${search}”` : "You are not a member of any project"}
            description={
              search
                ? "Try the project number, the client name, or the site."
                : "Project membership is what grants reach beyond your own uploads. A steward can add you to a job."
            }
          />
        ) : view === "grid" ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((project) => {
              const started = project.start_date ? new Date(project.start_date).getTime() : null
              const target = project.target_completion_date
                ? new Date(project.target_completion_date).getTime()
                : null
              const progress =
                started && target
                  ? Math.min(100, Math.max(0, ((Date.now() - started) / (target - started)) * 100))
                  : null
              const overdue = target != null && Date.now() > target

              return (
                <Link
                  key={project.id}
                  to={`/projects/${project.id}`}
                  className={cn(
                    "group flex flex-col gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/45",
                    project.status !== "active" && "opacity-90",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-mono text-[0.75rem] font-medium tracking-tight text-muted-foreground">
                      {project.project_number}
                    </span>
                    <ProjectStatusBadge status={project.status} />
                  </div>

                  <div className="space-y-1">
                    <h3 className="text-pretty text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
                      {project.name}
                    </h3>
                    <p className="truncate text-xs text-muted-foreground">{project.client_name}</p>
                  </div>

                  <dl className="grid grid-cols-3 gap-2 border-y border-border py-2.5">
                    <Metric icon={ClipboardListIcon} label="Drawings" value={project.drawing_count} />
                    <Metric icon={FileStackIcon} label="Documents" value={project.document_count} />
                    <Metric icon={UsersIcon} label="Members" value={project.member_count} />
                  </dl>

                  <div className="space-y-2">
                    {progress != null && (
                      <div className="space-y-1">
                        <div className="flex items-center justify-between text-[0.6875rem]">
                          <span className="text-muted-foreground">
                            {overdue ? "Past target" : "To target"}
                          </span>
                          <span
                            className={cn(
                              "font-mono tabular-nums",
                              overdue ? "text-error" : "text-muted-foreground",
                            )}
                          >
                            {formatDate(project.target_completion_date)}
                          </span>
                        </div>
                        <Progress value={progress} tone={overdue ? "error" : progress > 80 ? "warn" : "primary"} />
                      </div>
                    )}
                    <div className="flex items-center justify-between gap-2 text-[0.6875rem] text-muted-foreground">
                      <span className="flex min-w-0 items-center gap-1">
                        <MapPinIcon className="size-3 shrink-0" />
                        <span className="truncate">{project.location ?? "—"}</span>
                      </span>
                      {project.tonnage_t != null && (
                        <Hint label="Fabricated tonnage on this package">
                          <span className="flex shrink-0 items-center gap-1 font-mono tabular-nums">
                            <WeightIcon className="size-3" />
                            {formatNumber(project.tonnage_t)} t
                          </span>
                        </Hint>
                      )}
                    </div>
                  </div>
                </Link>
              )
            })}
          </div>
        ) : (
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Number</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead>Client</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Default class.</TableHead>
                  <TableHead numeric>Drawings</TableHead>
                  <TableHead numeric>Docs</TableHead>
                  <TableHead numeric>Tonnage</TableHead>
                  <TableHead>Last activity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((project) => (
                  <TableRow key={project.id} className="cursor-pointer">
                    <TableCell className="font-mono text-[0.8125rem]">
                      <Link to={`/projects/${project.id}`} className="hover:text-primary">
                        {project.project_number}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-xs">
                      <Link
                        to={`/projects/${project.id}`}
                        className="block truncate text-[0.8125rem] font-medium hover:text-primary"
                      >
                        {project.name}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-[14rem] truncate text-[0.8125rem] text-muted-foreground">
                      {project.client_name}
                    </TableCell>
                    <TableCell>
                      <ProjectStatusBadge status={project.status} />
                    </TableCell>
                    <TableCell>
                      {project.default_sensitivity && (
                        <SensitivityBadge value={project.default_sensitivity} />
                      )}
                    </TableCell>
                    <TableCell numeric>{project.drawing_count}</TableCell>
                    <TableCell numeric>{project.document_count}</TableCell>
                    <TableCell numeric>
                      {project.tonnage_t != null ? formatNumber(project.tonnage_t) : "—"}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                      {timeAgo(project.last_activity_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </div>
    </Page>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType
  label: string
  value: number | undefined
}) {
  return (
    <div className="space-y-0.5">
      <dt className="flex items-center gap-1 text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground">
        <Icon className="size-3" />
        {label}
      </dt>
      <dd className="font-mono text-sm tabular-nums">{value ?? "—"}</dd>
    </div>
  )
}

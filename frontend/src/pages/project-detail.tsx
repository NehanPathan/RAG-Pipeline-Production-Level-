import { Link, useParams } from "react-router-dom"
import { useQueries } from "@tanstack/react-query"
import {
  ClipboardListIcon,
  FileStackIcon,
  MessagesSquareIcon,
  SearchIcon,
  UploadCloudIcon,
  UsersIcon,
} from "lucide-react"
import { api } from "@/api"
import { formatDate, formatNumber, initials, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { Breadcrumbs, PageHeader, TitleBlock } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Avatar, AvatarFallback } from "@/components/ui/misc"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  ContentKindBadge,
  DocumentStatusBadge,
  DrawingNumber,
  ProjectStatusBadge,
  RevisionChip,
  RoleBadge,
  SensitivityBadge,
} from "@/components/domain/badges"
import { EmptyState, ErrorState, TableSkeleton } from "@/components/domain/states"

export default function ProjectDetailPage() {
  const { projectId = "" } = useParams()

  const [projectQ, membersQ, drawingsQ, docsQ] = useQueries({
    queries: [
      { queryKey: ["project", projectId], queryFn: () => api.getProject(projectId) },
      { queryKey: ["project-members", projectId], queryFn: () => api.listProjectMembers(projectId) },
      { queryKey: ["drawings", projectId], queryFn: () => api.listDrawings({ project_id: projectId }) },
      {
        queryKey: ["documents", "project", projectId],
        queryFn: () => api.listDocuments({ project_id: projectId, size: 200 }),
      },
    ],
  })

  const project = projectQ.data
  const drawings = drawingsQ.data ?? []
  const documents = docsQ.data?.items ?? []
  const members = membersQ.data ?? []

  if (projectQ.isError) {
    return (
      <Page width="reading">
        <ErrorState error={projectQ.error} onRetry={projectQ.refetch} resource="this project" />
      </Page>
    )
  }

  const indexed = documents.filter((d) => d.status === "indexed").length
  const disciplines = [...new Set(drawings.map((d) => d.discipline).filter(Boolean))] as string[]

  return (
    <Page>
      <Breadcrumbs
        items={[
          { label: "Projects", to: "/projects" },
          { label: project?.project_number ?? "…" },
        ]}
      />

      <PageHeader
        className="mt-3"
        eyebrow={
          <span className="font-mono normal-case tracking-normal">{project?.project_number}</span>
        }
        title={project?.name ?? "Loading…"}
        description={project?.client_name ?? undefined}
        meta={
          project && (
            <>
              <ProjectStatusBadge status={project.status} />
              {project.default_sensitivity && (
                <SensitivityBadge value={project.default_sensitivity} />
              )}
              <span className="text-xs text-muted-foreground">{project.location}</span>
            </>
          )
        }
        actions={
          <>
            <Button asChild variant="outline" size="sm">
              <Link to={`/search?project=${project?.project_number ?? ""}`}>
                <SearchIcon />
                Search this job
              </Link>
            </Button>
            <Button asChild size="sm">
              <Link to={`/ingest?project=${projectId}`}>
                <UploadCloudIcon />
                Upload
              </Link>
            </Button>
          </>
        }
      />

      {project && (
        <TitleBlock
          className="mt-4"
          columns={6}
          fields={[
            { label: "Project no.", value: project.project_number, mono: true },
            { label: "Client", value: project.client_name, span: 2 },
            { label: "Site", value: project.location },
            { label: "Started", value: formatDate(project.start_date), mono: true },
            {
              label: "Target",
              value: formatDate(project.target_completion_date),
              mono: true,
            },
            { label: "Drawings", value: formatNumber(drawings.length), mono: true },
            { label: "Documents", value: formatNumber(documents.length), mono: true },
            { label: "Indexed", value: `${indexed} / ${documents.length}`, mono: true },
            { label: "Tonnage", value: project.tonnage_t ? `${formatNumber(project.tonnage_t)} t` : "—", mono: true },
            { label: "Disciplines", value: disciplines.join(", ") || "—" },
            { label: "Members", value: formatNumber(members.length), mono: true },
          ]}
        />
      )}

      <Tabs defaultValue="drawings" className="mt-6">
        <TabsList>
          <TabsTrigger value="drawings">
            <ClipboardListIcon />
            Drawings
            <Badge variant="muted" className="ml-1 font-mono">
              {drawings.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="documents">
            <FileStackIcon />
            Documents
            <Badge variant="muted" className="ml-1 font-mono">
              {documents.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="members">
            <UsersIcon />
            Access
            <Badge variant="muted" className="ml-1 font-mono">
              {members.length}
            </Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="drawings">
          {drawingsQ.isPending ? (
            <TableSkeleton />
          ) : drawings.length === 0 ? (
            <EmptyState
              icon={ClipboardListIcon}
              title="No drawings registered on this job"
              description="Uploading a file with a drawing number and revision label registers the drawing and files the upload as its first revision."
              action={
                <Button asChild size="sm">
                  <Link to={`/ingest?project=${projectId}`}>Upload a drawing</Link>
                </Button>
              }
            />
          ) : (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Drawing</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Discipline</TableHead>
                    <TableHead>Current rev</TableHead>
                    <TableHead>Provenance</TableHead>
                    <TableHead numeric>Revisions</TableHead>
                    <TableHead>Issued</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {drawings.map((drawing) => (
                    <TableRow key={drawing.id}>
                      <TableCell>
                        <Link to={`/drawings/${drawing.id}`} className="hover:text-primary">
                          <DrawingNumber value={drawing.drawing_number} sheet={drawing.sheet_number} />
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-md">
                        <Link
                          to={`/drawings/${drawing.id}`}
                          className="block truncate text-[0.8125rem] hover:text-primary"
                        >
                          {drawing.title}
                        </Link>
                      </TableCell>
                      <TableCell className="text-[0.8125rem] text-muted-foreground">
                        {drawing.discipline}
                      </TableCell>
                      <TableCell>
                        <RevisionChip
                          label={drawing.current_revision_label}
                          date={drawing.current_revision_date}
                        />
                      </TableCell>
                      <TableCell>
                        <ContentKindBadge kind={drawing.content_kind} compact />
                      </TableCell>
                      <TableCell numeric>{drawing.revision_count}</TableCell>
                      <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                        {timeAgo(drawing.current_revision_date)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="documents">
          {docsQ.isPending ? (
            <TableSkeleton />
          ) : documents.length === 0 ? (
            <EmptyState icon={FileStackIcon} title="No documents on this job yet" />
          ) : (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>File</TableHead>
                    <TableHead>Domain</TableHead>
                    <TableHead>Class.</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead numeric>Pages</TableHead>
                    <TableHead numeric>Chunks</TableHead>
                    <TableHead>Added</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documents.map((doc) => (
                    <TableRow key={doc.id}>
                      <TableCell className="max-w-md">
                        <Link
                          to={`/documents/${doc.id}`}
                          className="flex items-center gap-2 truncate text-[0.8125rem] font-medium hover:text-primary"
                        >
                          <span className="truncate">{doc.file_name}</span>
                          {doc.is_latest === false && <RevisionChip label={doc.revision_label} isLatest={false} size="sm" />}
                        </Link>
                      </TableCell>
                      <TableCell className="text-[0.8125rem] text-muted-foreground">
                        {doc.domain}
                      </TableCell>
                      <TableCell>
                        <SensitivityBadge value={doc.sensitivity} withIcon={false} />
                      </TableCell>
                      <TableCell>
                        <DocumentStatusBadge status={doc.status} />
                      </TableCell>
                      <TableCell numeric>{doc.page_count ?? "—"}</TableCell>
                      <TableCell numeric>{doc.chunk_count ?? "—"}</TableCell>
                      <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                        {timeAgo(doc.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="members">
          <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Member</TableHead>
                    <TableHead>Project role</TableHead>
                    <TableHead>Platform role</TableHead>
                    <TableHead>Added</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {members.map((member) => (
                    <TableRow key={member.user_id}>
                      <TableCell>
                        <div className="flex items-center gap-2.5">
                          <Avatar className="size-7">
                            <AvatarFallback>
                              {initials(member.display_name ?? member.email)}
                            </AvatarFallback>
                          </Avatar>
                          <div className="min-w-0 leading-tight">
                            <p className="truncate text-[0.8125rem] font-medium">
                              {member.display_name ?? member.email}
                            </p>
                            <p className="truncate text-[0.6875rem] text-muted-foreground">
                              {member.email}
                            </p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={member.project_role === "owner" ? "default" : "outline"}
                          className="capitalize"
                        >
                          {member.project_role}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <RoleBadge role={member.platform_role} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-[0.8125rem] text-muted-foreground">
                        {formatDate(member.added_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>

            <Card className="h-fit space-y-3 p-4">
              <h3 className="text-sm font-semibold tracking-tight">How access resolves here</h3>
              <ol className="space-y-2.5 text-xs leading-relaxed text-muted-foreground">
                <li className="flex gap-2">
                  <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[0.625rem]">
                    1
                  </span>
                  <span>
                    <b className="text-foreground">Role</b> decides what a person may do at all —
                    upload, reclassify, flip a kill switch.
                  </span>
                </li>
                <li className="flex gap-2">
                  <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[0.625rem]">
                    2
                  </span>
                  <span>
                    <b className="text-foreground">Clearance</b> decides how deep they may read.
                    A contributor here still cannot open a restricted sheet.
                  </span>
                </li>
                <li className="flex gap-2">
                  <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[0.625rem]">
                    3
                  </span>
                  <span>
                    <b className="text-foreground">Membership</b> of this project decides which
                    documents are in reach. Membership grants reach; clearance grants depth.
                  </span>
                </li>
              </ol>
              <p className="border-t border-border pt-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                All three must pass. Documents with no project are personal and visible only to
                their owner.
              </p>
              <Button asChild variant="outline" size="sm" className="w-full">
                <Link to="/ops/access">
                  <UsersIcon />
                  Manage access
                </Link>
              </Button>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <div className="mt-6">
        <Card className="flex flex-wrap items-center justify-between gap-3 bg-muted/30 p-4">
          <div className="space-y-0.5">
            <p className="text-sm font-medium">Ask a question scoped to this job</p>
            <p className="text-xs text-muted-foreground">
              Answers cite the sheet, page and revision they came from.
            </p>
          </div>
          <Button asChild size="sm">
            <Link to={`/ask?q=${encodeURIComponent(`On project ${project?.project_number ?? ""}, `)}`}>
              <MessagesSquareIcon />
              Open Ask
            </Link>
          </Button>
        </Card>
      </div>
    </Page>
  )
}

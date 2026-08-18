import { useQueries } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import {
  ArrowRightIcon,
  CheckIcon,
  EyeOffIcon,
  KeyRoundIcon,
  ShieldCheckIcon,
  UsersIcon,
  XIcon,
} from "lucide-react"
import { api } from "@/api"
import type { Role, Sensitivity } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, initials } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, Section } from "@/components/domain/layout"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Avatar, AvatarFallback } from "@/components/ui/misc"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Hint } from "@/components/ui/tooltip"
import { RoleBadge, SensitivityBadge } from "@/components/domain/badges"
import { TableSkeleton } from "@/components/domain/states"
import { Button } from "@/components/ui/button"

const CLEARANCE_LEVEL: Record<Sensitivity, number> = {
  public: 0,
  internal: 1,
  confidential: 2,
  restricted: 3,
}

/** What each role may do. Mirrors the `require_role` allow-lists in the API. */
const CAPABILITIES: { action: string; roles: Role[]; note: string }[] = [
  { action: "Read documents in reach", roles: ["viewer", "analyst", "steward", "admin"], note: "Bounded by clearance and project membership." },
  { action: "Ask questions", roles: ["viewer", "analyst", "steward", "admin"], note: "Retrieval is pre-filtered by clearance." },
  { action: "Upload documents", roles: ["analyst", "steward", "admin"], note: "Cannot classify above own clearance." },
  { action: "Start evaluation runs", roles: ["analyst", "steward", "admin"], note: "Runs cost money and feed the CI gate." },
  { action: "Reclassify a document", roles: ["steward", "admin"], note: "Re-propagates to every store; always audited." },
  { action: "Delete a document", roles: ["steward", "admin"], note: "Irreversible, sweeps every store." },
  { action: "Reprocess ingestion", roles: ["steward", "admin"], note: "Idempotent by construction." },
  { action: "Promote feedback to a dataset", roles: ["steward", "admin"], note: "Changes what “passing” means." },
  { action: "Read the audit trail", roles: ["steward", "admin"], note: "Privileged — it maps the least-watched paths." },
  { action: "Change model settings", roles: ["admin"], note: "Durable and attributable." },
  { action: "Flip a kill switch", roles: ["admin"], note: "The most consequential control in the system." },
  { action: "Run a retention purge", roles: ["admin"], note: "Destructive form must be asked for explicitly." },
]

const ROLES: Role[] = ["viewer", "analyst", "steward", "admin"]
const SENSITIVITIES: Sensitivity[] = ["public", "internal", "confidential", "restricted"]

/** Used only if the live policy has not loaded; the server is the authority. */
const DEFAULT_ROLE_CLEARANCE: Record<Role, Sensitivity> = {
  viewer: "public",
  analyst: "internal",
  steward: "confidential",
  admin: "restricted",
}

const SENSITIVITY_BG: Record<Sensitivity, string> = {
  public: "bg-sens-public",
  internal: "bg-sens-internal",
  confidential: "bg-sens-confidential",
  restricted: "bg-sens-restricted",
}

export default function AccessPage() {
  const { principal } = useAuth()

  const [usersQ, projectsQ, policyQ] = useQueries({
    queries: [
      { queryKey: ["users"], queryFn: () => api.listUsers() },
      { queryKey: ["projects"], queryFn: () => api.listProjects() },
      { queryKey: ["policy"], queryFn: () => api.whoAmI() },
    ],
  })

  const users = usersQ.data ?? []
  const projects = projectsQ.data ?? []
  const roleClearance =
    (policyQ.data?.policy.role_clearance as Record<string, Sensitivity>) ?? {}

  const membershipQueries = useQueries({
    queries: projects.map((project) => ({
      queryKey: ["project-members", project.id],
      queryFn: () => api.listProjectMembers(project.id),
    })),
  })

  const membershipByUser = new Map<string, string[]>()
  membershipQueries.forEach((query, i) => {
    for (const member of query.data ?? []) {
      const list = membershipByUser.get(member.user_id) ?? []
      list.push(projects[i].project_number)
      membershipByUser.set(member.user_id, list)
    }
  })

  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="Access"
        description="Three independent dimensions decide whether a request succeeds: platform role, data clearance and project membership. All three must pass — none substitutes for another."
      />

      {/* -------------------------------------------------- the three gates */}
      <div className="mt-5 grid gap-3 lg:grid-cols-3">
        {[
          {
            icon: KeyRoundIcon,
            title: "Role",
            detail:
              "What you may do at all. Read from the database, never from anything the browser sends. require_role is an allow-list, not a rank comparison — a steward is not automatically an admin.",
          },
          {
            icon: EyeOffIcon,
            title: "Clearance",
            detail:
              "How deep you may read. Derived from the role via policy, and enforced twice: pushed into the Qdrant and Elasticsearch queries as a pre-filter, then re-checked after retrieval where any drop is treated as a defect.",
          },
          {
            icon: UsersIcon,
            title: "Project membership",
            detail:
              "Which documents are in reach. A document is readable if you own it or belong to its project. Membership grants reach; clearance grants depth. A document with no project is personal.",
          },
        ].map((gate) => {
          const Icon = gate.icon
          return (
            <Card key={gate.title} className="p-4">
              <p className="flex items-center gap-2 text-sm font-semibold tracking-tight">
                <Icon className="size-4 text-primary" />
                {gate.title}
              </p>
              <p className="mt-1.5 text-pretty text-[0.75rem] leading-relaxed text-muted-foreground">
                {gate.detail}
              </p>
            </Card>
          )
        })}
      </div>

      {/* ------------------------------------------------------------ people */}
      <Section
        className="mt-6"
        title="People"
        description="Roles are held in the database and resolved on every request from a verified token."
      >
        {usersQ.isPending ? (
          <TableSkeleton rows={5} cols={5} />
        ) : (
          <Card>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Person</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Reads up to</TableHead>
                  <TableHead>Projects</TableHead>
                  <TableHead>Identity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((user) => (
                  <TableRow
                    key={user.user_id}
                    data-state={user.user_id === principal?.user_id ? "selected" : undefined}
                  >
                    <TableCell>
                      <div className="flex items-center gap-2.5">
                        <Avatar className="size-7">
                          <AvatarFallback>
                            {initials(user.display_name ?? user.email)}
                          </AvatarFallback>
                        </Avatar>
                        <div className="min-w-0 leading-tight">
                          <p className="truncate text-[0.8125rem] font-medium">
                            {user.display_name ?? user.email}
                            {user.user_id === principal?.user_id && (
                              <span className="ml-1.5 text-[0.625rem] text-muted-foreground">
                                (you)
                              </span>
                            )}
                          </p>
                          <p className="truncate text-[0.6875rem] text-muted-foreground">
                            {user.email}
                          </p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <RoleBadge role={user.role} />
                    </TableCell>
                    <TableCell>
                      <SensitivityBadge value={user.clearance} />
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {(membershipByUser.get(user.user_id) ?? []).map((number) => (
                          <Badge key={number} variant="outline" className="font-mono">
                            {number}
                          </Badge>
                        ))}
                        {(membershipByUser.get(user.user_id) ?? []).length === 0 && (
                          <span className="text-[0.75rem] text-muted-foreground">none</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      {user.authenticated ? (
                        <Badge variant="ok">
                          <ShieldCheckIcon className="size-3" />
                          {user.auth_provider}
                        </Badge>
                      ) : (
                        <Hint label="Resolved from the X-User-Id development header, which proves nothing.">
                          <Badge variant="error">unverified</Badge>
                        </Hint>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </Section>

      {/* ----------------------------------------------------- capabilities */}
      <Section
        className="mt-6"
        title="What each role may do"
        description="Derived from the require_role allow-lists on the API routes themselves."
      >
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Action</TableHead>
                {ROLES.map((role) => (
                  <TableHead key={role} className="text-center capitalize">
                    {role}
                  </TableHead>
                ))}
                <TableHead>Note</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {CAPABILITIES.map((capability) => (
                <TableRow key={capability.action}>
                  <TableCell className="text-[0.8125rem] font-medium">
                    {capability.action}
                  </TableCell>
                  {ROLES.map((role) => {
                    const allowed = capability.roles.includes(role)
                    return (
                      <TableCell key={role} className="text-center">
                        {allowed ? (
                          <CheckIcon className="mx-auto size-3.5 text-ok" />
                        ) : (
                          <XIcon className="mx-auto size-3.5 text-muted-foreground/35" />
                        )}
                      </TableCell>
                    )
                  })}
                  <TableCell className="max-w-sm text-[0.75rem] text-muted-foreground">
                    {capability.note}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </Section>

      {/* ------------------------------------------------------- clearance */}
      <Section
        className="mt-6"
        title="Clearance matrix"
        description="Which classifications each role can read. Clearance is derived from the role in policy, so changing this is one config value and one audit entry — not a migration across every user row."
      >
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                {SENSITIVITIES.map((sensitivity) => (
                  <TableHead key={sensitivity} className="text-center">
                    <SensitivityBadge value={sensitivity} withIcon={false} size="sm" />
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {ROLES.map((role) => {
                const clearance = roleClearance[role] ?? DEFAULT_ROLE_CLEARANCE[role]
                const level = CLEARANCE_LEVEL[clearance]
                return (
                  <TableRow key={role}>
                    <TableCell>
                      <RoleBadge role={role} />
                    </TableCell>
                    {SENSITIVITIES.map((sensitivity) => {
                      const readable = CLEARANCE_LEVEL[sensitivity] <= level
                      return (
                        <TableCell key={sensitivity} className="text-center">
                          <Hint
                            label={
                              readable
                                ? `A ${role} can read ${sensitivity} documents.`
                                : `A ${role} cannot read ${sensitivity} documents — they are filtered out before retrieval, and requesting one directly returns 404.`
                            }
                          >
                            <span
                              className={cn(
                                "mx-auto block size-2.5 rounded-full",
                                readable ? SENSITIVITY_BG[sensitivity] : "border border-border",
                              )}
                            />
                          </Hint>
                        </TableCell>
                      )
                    })}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </Card>
        <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
          A document above your clearance returns <b>404, not 403</b>. A 403 would confirm that a
          document with that id exists, which is itself information you are not cleared for. The
          audit entry records the true reason.
        </p>
      </Section>

      {/* -------------------------------------------------------- projects */}
      <Section
        className="mt-6"
        title="Project rosters"
        description="Who can reach which job."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project, i) => (
            <Card key={project.id} className="p-3.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-mono text-[0.75rem] text-muted-foreground">
                    {project.project_number}
                  </p>
                  <p className="truncate text-[0.8125rem] font-medium">{project.name}</p>
                </div>
                {project.default_sensitivity && (
                  <SensitivityBadge value={project.default_sensitivity} withIcon={false} size="sm" />
                )}
              </div>
              <ul className="mt-2.5 space-y-1 border-t border-border pt-2.5">
                {(membershipQueries[i]?.data ?? []).map((member) => (
                  <li key={member.user_id} className="flex items-center gap-2">
                    <Avatar className="size-5">
                      <AvatarFallback className="text-[0.5rem]">
                        {initials(member.display_name ?? member.email)}
                      </AvatarFallback>
                    </Avatar>
                    <span className="min-w-0 flex-1 truncate text-[0.75rem]">
                      {member.display_name ?? member.email}
                    </span>
                    <span
                      className={cn(
                        "font-mono text-[0.625rem] capitalize",
                        member.project_role === "owner" ? "text-primary" : "text-muted-foreground",
                      )}
                    >
                      {member.project_role}
                    </span>
                  </li>
                ))}
              </ul>
              <Button asChild variant="ghost" size="xs" className="mt-2 w-full">
                <Link to={`/projects/${project.id}`}>
                  Open project
                  <ArrowRightIcon />
                </Link>
              </Button>
            </Card>
          ))}
        </div>
      </Section>
    </Page>
  )
}

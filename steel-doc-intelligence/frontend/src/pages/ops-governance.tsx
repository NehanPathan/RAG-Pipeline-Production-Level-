import * as React from "react"
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ClockIcon,
  FileClockIcon,
  GavelIcon,
  PowerIcon,
  ScrollTextIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  TrashIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { api } from "@/api"
import type { AuditEntry, Risk, RuntimeFlag } from "@/api/types"
import { useAuth } from "@/lib/auth"
import { cn, formatDate, formatNumber, timeAgo } from "@/lib/utils"
import { Page } from "@/components/layout/page"
import { PageHeader, StatTile } from "@/components/domain/layout"
import { Button } from "@/components/ui/button"
import { Input, Textarea } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  Label,
  SegmentedTrack,
  Switch,
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/misc"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Hint } from "@/components/ui/tooltip"
import { RoleBadge } from "@/components/domain/badges"
import { EmptyState, TableSkeleton, TextSkeleton } from "@/components/domain/states"

const FUNCTION_LABEL: Record<string, string> = {
  govern: "Govern",
  map: "Map",
  measure: "Measure",
  manage: "Manage",
}

const FUNCTION_NOTE: Record<string, string> = {
  govern: "Who decides, and under which policy.",
  map: "What could go wrong, and where the data is.",
  measure: "How we know whether it is going wrong.",
  manage: "What we do when it does.",
}

export default function GovernancePage() {
  const { hasRole } = useAuth()
  const [auditAction, setAuditAction] = React.useState("all")
  const [auditHours, setAuditHours] = React.useState(168)
  const [auditSearch, setAuditSearch] = React.useState("")

  const [statusQ, flagsQ, risksQ, auditQ, policyQ] = useQueries({
    queries: [
      { queryKey: ["gov-status"], queryFn: () => api.governanceStatus() },
      { queryKey: ["flags"], queryFn: () => api.listFlags() },
      { queryKey: ["risks"], queryFn: () => api.listRisks() },
      {
        queryKey: ["audit", auditAction, auditHours],
        queryFn: () =>
          api.listAudit({
            limit: 250,
            hours: auditHours,
            action: auditAction === "all" ? undefined : auditAction,
          }),
      },
      { queryKey: ["policy"], queryFn: () => api.whoAmI() },
    ],
  })

  const status = statusQ.data
  const flags = flagsQ.data ?? []
  const risks = risksQ.data?.risks ?? []
  const audit = auditQ.data ?? []
  const policy = policyQ.data?.policy

  const filteredAudit = React.useMemo(() => {
    if (!auditSearch.trim()) return audit
    const q = auditSearch.toLowerCase()
    return audit.filter(
      (entry) =>
        entry.action.includes(q) ||
        (entry.actor_email ?? "").toLowerCase().includes(q) ||
        (entry.resource_label ?? "").toLowerCase().includes(q) ||
        (entry.reason ?? "").toLowerCase().includes(q),
    )
  }, [audit, auditSearch])

  const denials = audit.filter((e) => e.outcome === "denied").length
  const killed = flags.filter((f) => !f.enabled).length

  return (
    <Page>
      <PageHeader
        eyebrow="Operations"
        title="Governance"
        description="The policy actually in force in this process, the switches that can stop it, the risks it is watched against, and an append-only record of who did what."
        meta={
          status && (
            <>
              <Badge variant={status.enforcement_mode === "enforce" ? "ok" : "warn"}>
                {status.enforcement_mode}
              </Badge>
              <span className="font-mono text-xs text-muted-foreground">
                policy {status.policy_version} · register {status.risk_register_version}
              </span>
            </>
          )
        }
      />

      {status && (
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Controls implemented"
            value={status.controls}
            icon={ShieldCheckIcon}
            footer={`Across ${Object.values(status.risks_by_function).reduce((a, b) => a + b, 0)} registered risks`}
          />
          <StatTile
            label="Kill switches off"
            value={killed}
            tone={killed ? "warn" : "ok"}
            icon={PowerIcon}
            footer={killed ? "Something is deliberately disabled" : "Everything is running"}
          />
          <StatTile
            label="Access denials, 7 d"
            value={denials}
            tone={denials > 5 ? "warn" : "default"}
            icon={ShieldAlertIcon}
            footer="Requests the clearance filter refused"
          />
          <StatTile
            label="Retention window"
            value={formatNumber(policy?.retention_days)}
            unit="days"
            icon={FileClockIcon}
            footer="Applied to every document at upload"
          />
        </div>
      )}

      <Tabs defaultValue="flags" className="mt-6">
        <TabsList>
          <TabsTrigger value="flags">
            <PowerIcon />
            Kill switches
          </TabsTrigger>
          <TabsTrigger value="policy">
            <GavelIcon />
            Policy
          </TabsTrigger>
          <TabsTrigger value="risks">
            <ShieldAlertIcon />
            Risk register
            <Badge variant="muted" className="ml-1 font-mono">
              {risks.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="audit">
            <ScrollTextIcon />
            Audit trail
            <Badge variant="muted" className="ml-1 font-mono">
              {audit.length}
            </Badge>
          </TabsTrigger>
          <TabsTrigger value="retention">
            <TrashIcon />
            Retention
          </TabsTrigger>
        </TabsList>

        {/* -------------------------------------------------- kill switches */}
        <TabsContent value="flags">
          {flagsQ.isPending ? (
            <TextSkeleton lines={6} />
          ) : (
            <div className="space-y-2">
              {flags.map((flag) => (
                <FlagRow key={flag.name} flag={flag} canToggle={hasRole("admin")} />
              ))}
              {!hasRole("admin") && (
                <p className="pt-1 text-[0.6875rem] text-muted-foreground">
                  Flipping a switch requires the admin role. Every change is audited with the value
                  before and after.
                </p>
              )}
            </div>
          )}
        </TabsContent>

        {/* --------------------------------------------------------- policy */}
        <TabsContent value="policy">
          {policy ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card className="p-4">
                <p className="eyebrow mb-3">In force in this process</p>
                <dl className="divide-y divide-border text-[0.8125rem]">
                  {[
                    ["Policy version", policy.version],
                    ["Enforcement", policy.enforcement_mode],
                    ["Default classification", policy.default_sensitivity],
                    ["Default clearance", policy.default_clearance],
                    ["Retention", `${policy.retention_days} days`],
                  ].map(([label, value]) => (
                    <div key={label as string} className="flex justify-between gap-4 py-2">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="font-mono capitalize">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-3 border-t border-border pt-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  Read from the running service, not from the repository. A policy that only exists
                  in version control can drift from the one a stale container actually loaded —
                  this makes the difference observable.
                </p>
              </Card>

              <Card className="p-4">
                <p className="eyebrow mb-3">Role → clearance</p>
                <p className="mb-3 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  Clearance is derived from the role rather than stored on the user row, so
                  re-classifying what a role may read is one config value and one audit entry
                  instead of a migration across every user.
                </p>
                <div className="space-y-1.5">
                  {Object.entries(
                    (policy.role_clearance as Record<string, string>) ?? {},
                  ).map(([role, clearance]) => (
                    <div
                      key={role}
                      className="flex items-center gap-3 rounded-sm border border-border px-2.5 py-1.5"
                    >
                      <RoleBadge role={role as never} />
                      <span className="text-muted-foreground">reads up to</span>
                      <span className="ml-auto font-mono text-[0.75rem] capitalize">
                        {clearance}
                      </span>
                    </div>
                  ))}
                </div>

                <p className="eyebrow mb-2 mt-4">Quality floors</p>
                <div className="space-y-1.5">
                  {Object.entries(status?.quality_floors ?? {}).map(([metric, floor]) => (
                    <div key={metric} className="flex items-center justify-between text-[0.8125rem]">
                      <span className="capitalize text-muted-foreground">
                        {metric.replace(/_/g, " ")}
                      </span>
                      <span className="font-mono tabular-nums">{floor.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </Card>

              <Card className="p-4 lg:col-span-2">
                <p className="eyebrow mb-2">Approved providers</p>
                <div className="flex flex-wrap gap-1.5">
                  {((policy.approved_providers as string[]) ?? []).map((provider) => (
                    <Badge key={provider} variant="outline" className="font-mono">
                      {provider}
                    </Badge>
                  ))}
                </div>
                <p className="mt-2.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  Enforced in the LLM gateway as an allow-list. A model from an unapproved vendor is
                  refused before a request is made, not logged after one.
                </p>
              </Card>
            </div>
          ) : (
            <TextSkeleton lines={6} />
          )}
        </TabsContent>

        {/* ---------------------------------------------------------- risks */}
        <TabsContent value="risks">
          <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(status?.risks_by_function ?? {}).map(([fn, count]) => (
              <Card key={fn} className="p-3">
                <p className="text-[0.8125rem] font-semibold">{FUNCTION_LABEL[fn] ?? fn}</p>
                <p className="mt-0.5 text-[0.6875rem] leading-relaxed text-muted-foreground">
                  {FUNCTION_NOTE[fn]}
                </p>
                <p className="mt-2 font-mono text-xl tabular-nums">{count}</p>
              </Card>
            ))}
          </div>

          {risksQ.isPending ? (
            <TextSkeleton lines={8} />
          ) : (
            <Accordion type="multiple" className="rounded-md border border-border bg-card px-4">
              {risks.map((risk) => (
                <RiskItem key={risk.id} risk={risk} />
              ))}
            </Accordion>
          )}
        </TabsContent>

        {/* ---------------------------------------------------------- audit */}
        <TabsContent value="audit">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Input
              value={auditSearch}
              onChange={(e) => setAuditSearch(e.target.value)}
              placeholder="Filter by actor, resource or reason…"
              className="max-w-xs"
            />
            <Select value={auditAction} onValueChange={setAuditAction}>
              <SelectTrigger size="sm" className="w-auto min-w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All actions</SelectItem>
                {[
                  "document_uploaded",
                  "document_reclassified",
                  "document_deleted",
                  "access_denied",
                  "setting_changed",
                  "kill_switch_toggled",
                  "answer_refused",
                  "policy_violation",
                  "eval_run_started",
                  "feedback_submitted",
                  "retention_purged",
                ].map((action) => (
                  <SelectItem key={action} value={action}>
                    {action.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <SegmentedTrack>
              <ToggleGroup
                type="single"
                value={String(auditHours)}
                onValueChange={(v) => v && setAuditHours(Number(v))}
              >
                <ToggleGroupItem value="24">24 h</ToggleGroupItem>
                <ToggleGroupItem value="168">7 d</ToggleGroupItem>
                <ToggleGroupItem value="720">30 d</ToggleGroupItem>
              </ToggleGroup>
            </SegmentedTrack>
          </div>

          {auditQ.isPending ? (
            <TableSkeleton rows={10} cols={6} />
          ) : filteredAudit.length === 0 ? (
            <EmptyState
              icon={ScrollTextIcon}
              title="No entries in this window"
              description="The audit log is append-only and reading it is itself privileged — it records who did what to which resource, which is exactly what an attacker would use to find the least-watched path."
            />
          ) : (
            <Card>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Resource</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead>Reason</TableHead>
                    <TableHead>Control</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredAudit.map((entry) => (
                    <AuditRow key={entry.id} entry={entry} />
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </TabsContent>

        {/* ------------------------------------------------------ retention */}
        <TabsContent value="retention">
          <RetentionPanel canRun={hasRole("admin")} retentionDays={policy?.retention_days} />
        </TabsContent>
      </Tabs>
    </Page>
  )
}

/* ----------------------------------------------------------------- parts */

function FlagRow({ flag, canToggle }: { flag: RuntimeFlag; canToggle: boolean }) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = React.useState(false)
  const [reason, setReason] = React.useState("")

  const critical = flag.name === "answering_enabled" || flag.name === "ingestion_enabled"

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.setFlag(flag.name, enabled, reason),
    onSuccess: (_, enabled) => {
      toast.success(`${flag.name} ${enabled ? "enabled" : "disabled"}`, {
        description: "Recorded in the audit log with the value before and after.",
      })
      void queryClient.invalidateQueries({ queryKey: ["flags"] })
      void queryClient.invalidateQueries({ queryKey: ["gov-status"] })
      setConfirming(false)
      setReason("")
    },
    onError: (error) =>
      toast.error("Could not change the flag", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  function request(next: boolean) {
    // Turning off a critical switch stops the product; it deserves a sentence
    // of justification in the audit trail rather than a bare toggle.
    if (critical && !next) setConfirming(true)
    else toggle.mutate(next)
  }

  return (
    <>
      <Card
        className={cn(
          "flex flex-wrap items-start gap-3 p-3.5",
          !flag.enabled && "border-warn/40 bg-warn-soft/20",
        )}
      >
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[0.8125rem] font-medium">{flag.name}</span>
            <Badge variant={flag.source === "override" ? "technical" : "muted"}>
              {flag.source}
            </Badge>
            {critical && (
              <Hint label="Turning this off stops a core function of the product.">
                <Badge variant="warn">
                  <TriangleAlertIcon className="size-3" />
                  critical
                </Badge>
              </Hint>
            )}
          </div>
          <p className="max-w-3xl text-pretty text-[0.75rem] leading-relaxed text-muted-foreground">
            {flag.description}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <span
            className={cn(
              "font-mono text-[0.6875rem] uppercase tracking-wide",
              flag.enabled ? "text-ok" : "text-warn",
            )}
          >
            {flag.enabled ? "on" : "off"}
          </span>
          <Switch
            checked={flag.enabled}
            disabled={!canToggle || toggle.isPending}
            onCheckedChange={request}
            aria-label={flag.name}
          />
        </div>
      </Card>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Turn off {flag.name}?</DialogTitle>
            <DialogDescription>
              {flag.name === "answering_enabled"
                ? "Every question will be refused without reaching a model. This stops the product."
                : "New uploads will be refused with a 503. The queue will continue to drain."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="flag-reason">Reason (recorded in the audit log)</Label>
            <Textarea
              id="flag-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="Provider incident INC-4471; pausing until the fallback chain is verified."
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={!reason.trim() || toggle.isPending}
              onClick={() => toggle.mutate(false)}
            >
              <PowerIcon />
              Turn off
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function RiskItem({ risk }: { risk: Risk }) {
  const severityTone =
    risk.severity === "critical"
      ? "error"
      : risk.severity === "high"
        ? "warn"
        : ("muted" as const)

  return (
    <AccordionItem value={risk.id} className="last:border-0">
      <AccordionTrigger>
        <span className="flex min-w-0 flex-1 items-center gap-2.5 pr-3">
          <span className="shrink-0 font-mono text-[0.75rem] text-muted-foreground">{risk.id}</span>
          <span className="min-w-0 flex-1 truncate">{risk.title}</span>
          <Badge variant={severityTone as never}>{risk.severity}</Badge>
          <Badge variant="outline" className="capitalize">
            {risk.function}
          </Badge>
        </span>
      </AccordionTrigger>
      <AccordionContent>
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Likelihood", risk.likelihood],
            ["Residual risk", risk.residual_risk],
            ["Owner", risk.owner],
            [
              "Threshold",
              `${risk.threshold_direction === "gte" ? "≥" : "≤"} ${risk.threshold}`,
            ],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground">
                {label}
              </dt>
              <dd className="text-[0.8125rem] capitalize">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-3">
          <p className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground">
            Watched by
          </p>
          <code className="text-[0.75rem]">{risk.metric}</code>
        </div>
        <div className="mt-3 space-y-1.5">
          <p className="text-[0.625rem] uppercase tracking-[0.06em] text-muted-foreground">
            Controls
          </p>
          {risk.controls.map((control) => (
            <div
              key={control.id}
              className="rounded-sm border border-border bg-muted/30 px-2.5 py-1.5"
            >
              <p className="flex items-center gap-2 text-[0.75rem]">
                <span className="font-mono text-muted-foreground">{control.id}</span>
                <span>{control.description}</span>
              </p>
              <code className="text-[0.625rem] text-muted-foreground">
                {control.implemented_in}
              </code>
            </div>
          ))}
        </div>
      </AccordionContent>
    </AccordionItem>
  )
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const denied = entry.outcome === "denied" || entry.outcome === "failed"
  return (
    <TableRow className={cn(denied && "bg-error-soft/25")}>
      <TableCell className="whitespace-nowrap">
        <Hint label={formatDate(entry.created_at, true)}>
          <span className="font-mono text-[0.75rem] tabular-nums text-muted-foreground">
            {timeAgo(entry.created_at)}
          </span>
        </Hint>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1.5">
          <span className="truncate text-[0.75rem]">{entry.actor_email ?? "—"}</span>
          {entry.actor_role && <RoleBadge role={entry.actor_role as never} />}
        </div>
      </TableCell>
      <TableCell>
        <span className="font-mono text-[0.75rem]">{entry.action}</span>
      </TableCell>
      <TableCell className="max-w-[16rem]">
        <span className="block truncate text-[0.75rem] text-muted-foreground">
          {entry.resource_label ?? entry.resource_id ?? "—"}
        </span>
      </TableCell>
      <TableCell>
        <Badge variant={denied ? "error" : entry.outcome === "allowed" ? "ok" : "muted"}>
          {entry.outcome}
        </Badge>
      </TableCell>
      <TableCell className="max-w-[18rem]">
        <span className="block truncate text-[0.75rem] text-muted-foreground">
          {entry.reason ?? "—"}
        </span>
      </TableCell>
      <TableCell>
        {entry.control_id ? (
          <Badge variant="technical" className="font-mono">
            {entry.control_id}
          </Badge>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
  )
}

function RetentionPanel({
  canRun,
  retentionDays,
}: {
  canRun: boolean
  retentionDays?: number
}) {
  const [dryRun, setDryRun] = React.useState(true)

  const run = useMutation({
    mutationFn: () => api.runRetention(dryRun),
    onSuccess: (result) => {
      toast[result.dry_run ? "info" : "success"](
        result.dry_run
          ? `${result.candidates} documents would be purged`
          : `${result.purged} documents purged`,
        {
          description: result.dry_run
            ? "Nothing was deleted. Turn off dry run to execute."
            : `${result.chunks_deleted} chunks removed from every store.`,
        },
      )
    },
    onError: (error) =>
      toast.error("Retention run failed", {
        description: error instanceof Error ? error.message : undefined,
      }),
  })

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <Card className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-0.5">
            <h3 className="text-sm font-semibold tracking-tight">Retention purge</h3>
            <p className="text-xs text-muted-foreground">
              Removes documents past their deadline from Postgres, Qdrant, Elasticsearch, blob
              storage and the answer cache.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Label className="cursor-pointer gap-2 text-[0.8125rem]">
              <Switch checked={dryRun} onCheckedChange={setDryRun} />
              Dry run
            </Label>
            <Button
              variant={dryRun ? "outline" : "destructive"}
              size="sm"
              disabled={!canRun || run.isPending}
              onClick={() => run.mutate()}
            >
              <ClockIcon />
              {dryRun ? "Preview" : "Purge now"}
            </Button>
          </div>
        </div>

        {!dryRun && (
          <p className="mt-3 flex items-start gap-2 rounded-md border border-error/35 bg-error-soft/40 p-2.5 text-[0.6875rem] leading-relaxed text-error">
            <TriangleAlertIcon className="mt-px size-3.5 shrink-0" />
            <span>
              This will permanently delete documents. The destructive form has to be asked for
              explicitly — the default is a dry run for exactly this reason. Every purge writes its
              own audit entry.
            </span>
          </p>
        )}

        {run.data && (
          <div className="mt-4 space-y-2">
            <div className="flex flex-wrap gap-2">
              <Badge variant={run.data.dry_run ? "technical" : "error"}>
                {run.data.dry_run ? "dry run" : "executed"}
              </Badge>
              <Badge variant="outline" className="font-mono">
                {run.data.candidates} candidates
              </Badge>
              <Badge variant="outline" className="font-mono">
                {run.data.purged} purged
              </Badge>
              <Badge variant="outline" className="font-mono">
                {run.data.chunks_deleted} chunks
              </Badge>
            </div>
            {run.data.documents.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Document</TableHead>
                    <TableHead>Retention until</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {run.data.documents.map((doc) => (
                    <TableRow key={doc.id}>
                      <TableCell className="max-w-md truncate text-[0.8125rem]">
                        {doc.file_name}
                      </TableCell>
                      <TableCell className="font-mono text-[0.75rem] tabular-nums">
                        {formatDate(doc.retention_until)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}

        {!canRun && (
          <p className="mt-3 text-[0.6875rem] text-muted-foreground">
            Running a purge requires the admin role.
          </p>
        )}
      </Card>

      <Card className="h-fit space-y-2 p-4">
        <p className="text-sm font-semibold tracking-tight">How retention is set</p>
        <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
          Every document is stamped with a deadline at upload, computed from the policy's{" "}
          <span className="font-mono text-foreground">{retentionDays ?? "—"}</span>-day window.
          Client drawing sets frequently carry contractual retention obligations, so the deadline
          travels with the document rather than being applied globally at purge time.
        </p>
        <p className="text-[0.6875rem] leading-relaxed text-muted-foreground">
          A purge sweeps every store. Deleting only the Postgres row would leave the vectors and
          the BM25 index still answering questions from a document that no longer exists.
        </p>
      </Card>
    </div>
  )
}

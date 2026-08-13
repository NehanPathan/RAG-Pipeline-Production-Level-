import { cn } from "@/lib/utils"

/** An I-section seen end-on — the product's mark. */
export function GirderMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-5", className)} aria-hidden>
      <path
        d="M4.5 4h15v3.2h-5.6v9.6h5.6V20h-15v-3.2h5.6V7.2H4.5z"
        fill="currentColor"
      />
    </svg>
  )
}

export function Wordmark({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <span className="flex items-center gap-2 overflow-hidden">
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <GirderMark className="size-4" />
      </span>
      {!collapsed && (
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-[0.9375rem] font-semibold tracking-tight">
            Girder
          </span>
          <span className="block truncate text-[0.625rem] uppercase tracking-[0.11em] text-muted-foreground">
            Document Intelligence
          </span>
        </span>
      )}
    </span>
  )
}

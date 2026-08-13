import * as React from "react"
import { cn } from "@/lib/utils"

/**
 * A register, not a card grid. Rows are 36px, borders are hairlines, headers
 * are small-caps, and every cell that holds a number is right-aligned with
 * tabular figures. Wide tables scroll inside their own container so the page
 * body never scrolls sideways.
 */
function Table({ className, containerClassName, ...props }: React.ComponentProps<"table"> & {
  containerClassName?: string
}) {
  return (
    <div className={cn("relative w-full overflow-x-auto", containerClassName)}>
      <table
        className={cn("w-full caption-bottom border-collapse text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("[&_tr]:border-b [&_tr]:border-border", className)} {...props} />
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      className={cn("border-t border-border bg-muted/40 font-medium", className)}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-border transition-colors last:border-b-0 hover:bg-muted/45 data-[state=selected]:bg-primary-soft",
        className,
      )}
      {...props}
    />
  )
}

function TableHead({ className, numeric, ...props }: React.ComponentProps<"th"> & {
  numeric?: boolean
}) {
  return (
    <th
      className={cn(
        "h-8 whitespace-nowrap px-3 text-left align-middle text-[0.6875rem] font-semibold uppercase tracking-[0.07em] text-muted-foreground",
        numeric && "text-right",
        className,
      )}
      {...props}
    />
  )
}

function TableCell({ className, numeric, ...props }: React.ComponentProps<"td"> & {
  numeric?: boolean
}) {
  return (
    <td
      className={cn(
        "px-3 py-2 align-middle",
        numeric && "text-right font-mono text-[0.8125rem] tabular-nums",
        className,
      )}
      {...props}
    />
  )
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return <caption className={cn("mt-3 text-xs text-muted-foreground", className)} {...props} />
}

export { Table, TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption }

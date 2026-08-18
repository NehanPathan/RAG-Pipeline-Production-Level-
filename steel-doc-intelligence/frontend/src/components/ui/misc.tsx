/**
 * Small primitives that are a handful of lines each. Kept together rather
 * than in eleven files, because a one-export file per checkbox is noise.
 */
import * as React from "react"
import * as CheckboxPrimitive from "@radix-ui/react-checkbox"
import * as SwitchPrimitive from "@radix-ui/react-switch"
import * as LabelPrimitive from "@radix-ui/react-label"
import * as SeparatorPrimitive from "@radix-ui/react-separator"
import * as ProgressPrimitive from "@radix-ui/react-progress"
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area"
import * as AvatarPrimitive from "@radix-ui/react-avatar"
import * as PopoverPrimitive from "@radix-ui/react-popover"
import * as CollapsiblePrimitive from "@radix-ui/react-collapsible"
import * as AccordionPrimitive from "@radix-ui/react-accordion"
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group"
import * as SliderPrimitive from "@radix-ui/react-slider"
import { CheckIcon, ChevronDownIcon } from "lucide-react"
import { cn } from "@/lib/utils"

/* ---------------------------------------------------------------- Checkbox */

function Checkbox({
  className,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn(
        "peer size-4 shrink-0 rounded-[3px] border border-input bg-card outline-none transition-shadow",
        "focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50",
        "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
        "data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary data-[state=indeterminate]:text-primary-foreground",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
        <CheckIcon className="size-3 stroke-[3]" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

/* ------------------------------------------------------------------ Switch */

function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "peer inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full border border-transparent transition-colors outline-none",
        "focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50",
        "data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-3.5 rounded-full bg-card shadow-sm ring-0 transition-transform data-[state=checked]:translate-x-[15px] data-[state=unchecked]:translate-x-[1px]" />
    </SwitchPrimitive.Root>
  )
}

/* ------------------------------------------------------------------- Label */

function Label({ className, ...props }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      className={cn(
        "flex select-none items-center gap-1.5 text-[0.8125rem] font-medium leading-none",
        "peer-disabled:cursor-not-allowed peer-disabled:opacity-60",
        className,
      )}
      {...props}
    />
  )
}

/* --------------------------------------------------------------- Separator */

function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className,
      )}
      {...props}
    />
  )
}

/* ---------------------------------------------------------------- Progress */

function Progress({
  className,
  value,
  tone = "primary",
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root> & {
  tone?: "primary" | "ok" | "warn" | "error"
}) {
  const bar = {
    primary: "bg-primary",
    ok: "bg-ok",
    warn: "bg-warn",
    error: "bg-error",
  }[tone]
  return (
    <ProgressPrimitive.Root
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        className={cn("h-full w-full flex-1 transition-transform duration-500 ease-out", bar)}
        style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}

/* -------------------------------------------------------------- ScrollArea */

function ScrollArea({
  className,
  children,
  viewportRef,
  ...props
}: React.ComponentProps<typeof ScrollAreaPrimitive.Root> & {
  viewportRef?: React.Ref<HTMLDivElement>
}) {
  return (
    <ScrollAreaPrimitive.Root className={cn("relative overflow-hidden", className)} {...props}>
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        className="size-full rounded-[inherit] outline-none"
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar
        orientation="vertical"
        className="flex w-2 touch-none select-none border-l border-l-transparent p-px transition-colors"
      >
        <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-muted-foreground/30 hover:bg-muted-foreground/50" />
      </ScrollAreaPrimitive.Scrollbar>
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
}

/* ------------------------------------------------------------------ Avatar */

function Avatar({ className, ...props }: React.ComponentProps<typeof AvatarPrimitive.Root>) {
  return (
    <AvatarPrimitive.Root
      className={cn("relative flex size-8 shrink-0 overflow-hidden rounded-md", className)}
      {...props}
    />
  )
}

function AvatarImage({ className, ...props }: React.ComponentProps<typeof AvatarPrimitive.Image>) {
  return <AvatarPrimitive.Image className={cn("aspect-square size-full", className)} {...props} />
}

function AvatarFallback({
  className,
  ...props
}: React.ComponentProps<typeof AvatarPrimitive.Fallback>) {
  return (
    <AvatarPrimitive.Fallback
      className={cn(
        "flex size-full items-center justify-center rounded-md bg-muted text-[0.6875rem] font-semibold tracking-wide text-muted-foreground",
        className,
      )}
      {...props}
    />
  )
}

/* ----------------------------------------------------------------- Popover */

const Popover = PopoverPrimitive.Root
const PopoverTrigger = PopoverPrimitive.Trigger
const PopoverAnchor = PopoverPrimitive.Anchor

function PopoverContent({
  className,
  align = "start",
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 w-72 rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-xl shadow-black/20 outline-none data-[state=open]:animate-rise",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  )
}

/* ------------------------------------------------------------- Collapsible */

const Collapsible = CollapsiblePrimitive.Root
const CollapsibleTrigger = CollapsiblePrimitive.Trigger
const CollapsibleContent = CollapsiblePrimitive.Content

/* --------------------------------------------------------------- Accordion */

const Accordion = AccordionPrimitive.Root

function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item className={cn("border-b border-border", className)} {...props} />
  )
}

function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>) {
  return (
    <AccordionPrimitive.Header className="flex">
      <AccordionPrimitive.Trigger
        className={cn(
          "flex flex-1 items-center justify-between gap-3 py-3 text-left text-sm font-medium outline-none transition-colors hover:text-primary [&[data-state=open]>svg]:rotate-180",
          className,
        )}
        {...props}
      >
        {children}
        <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform duration-200" />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  )
}

function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      className="overflow-hidden text-sm data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down"
      {...props}
    >
      <div className={cn("pb-3 pt-0", className)}>{children}</div>
    </AccordionPrimitive.Content>
  )
}

/* ------------------------------------------------------------- ToggleGroup */

const ToggleGroup = ToggleGroupPrimitive.Root

function ToggleGroupItem({
  className,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item>) {
  return (
    <ToggleGroupPrimitive.Item
      className={cn(
        "inline-flex h-7 items-center justify-center gap-1.5 whitespace-nowrap rounded-sm px-2.5 text-xs font-medium text-muted-foreground transition-colors",
        "hover:bg-muted hover:text-foreground",
        "data-[state=on]:bg-card data-[state=on]:text-foreground data-[state=on]:shadow-sm",
        "[&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  )
}

/** Segmented control shell for ToggleGroup — a recessed track. */
function SegmentedTrack({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-0.5 rounded-md border border-border bg-muted/60 p-0.5",
        className,
      )}
      {...props}
    />
  )
}

/* ------------------------------------------------------------------ Slider */

function Slider({ className, ...props }: React.ComponentProps<typeof SliderPrimitive.Root>) {
  return (
    <SliderPrimitive.Root
      className={cn("relative flex w-full touch-none select-none items-center", className)}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-1 w-full grow overflow-hidden rounded-full bg-muted">
        <SliderPrimitive.Range className="absolute h-full bg-primary" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb className="block size-3.5 rounded-full border-2 border-primary bg-card outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-ring/40" />
    </SliderPrimitive.Root>
  )
}

/* ---------------------------------------------------------------- Skeleton */

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("skeleton-sheen rounded-sm bg-muted", className)}
      aria-hidden
      {...props}
    />
  )
}

/* --------------------------------------------------------------------- Kbd */

function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[3px] border border-border bg-muted px-1 font-mono text-[0.625rem] font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  )
}

export {
  Checkbox,
  Switch,
  Label,
  Separator,
  Progress,
  ScrollArea,
  Avatar,
  AvatarImage,
  AvatarFallback,
  Popover,
  PopoverTrigger,
  PopoverAnchor,
  PopoverContent,
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
  ToggleGroup,
  ToggleGroupItem,
  SegmentedTrack,
  Slider,
  Skeleton,
  Kbd,
}

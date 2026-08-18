import { cn } from "@/lib/utils"

/**
 * The page container.
 *
 * `wide` is the default for register and dashboard screens; `reading` narrows
 * to a comfortable measure for pages that are mostly prose. `flush` removes
 * the padding for screens that own their own full-height layout (Ask).
 */
export function Page({
  children,
  className,
  width = "wide",
  flush = false,
}: {
  children: React.ReactNode
  className?: string
  width?: "wide" | "reading" | "full"
  flush?: boolean
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full",
        !flush && "px-4 py-5 md:px-6 md:py-6",
        { wide: "max-w-[1500px]", reading: "max-w-4xl", full: "max-w-none" }[width],
        className,
      )}
    >
      {children}
    </div>
  )
}

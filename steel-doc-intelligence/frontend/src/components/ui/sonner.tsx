import { Toaster as Sonner, type ToasterProps } from "sonner"
import { useTheme } from "@/lib/theme"

/** Toasts inherit the app's tokens so they never look like a third-party
 *  widget dropped on top of the product. */
export function Toaster(props: ToasterProps) {
  const { resolved } = useTheme()
  return (
    <Sonner
      theme={resolved}
      position="bottom-right"
      gap={8}
      toastOptions={{
        classNames: {
          toast:
            "!rounded-md !border !border-border !bg-popover !text-popover-foreground !shadow-xl !shadow-black/25 !font-sans",
          description: "!text-muted-foreground !text-xs",
          actionButton: "!bg-primary !text-primary-foreground !rounded-sm !text-xs",
          cancelButton: "!bg-muted !text-muted-foreground !rounded-sm !text-xs",
          success: "[&_[data-icon]]:!text-ok",
          error: "[&_[data-icon]]:!text-error",
          warning: "[&_[data-icon]]:!text-warn",
        },
      }}
      {...props}
    />
  )
}

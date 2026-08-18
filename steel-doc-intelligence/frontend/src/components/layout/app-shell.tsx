import * as React from "react"
import { NavLink, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom"
import {
  ChevronsLeftIcon,
  ChevronsRightIcon,
  LogOutIcon,
  MenuIcon,
  MonitorIcon,
  MoonIcon,
  PanelLeftIcon,
  SearchIcon,
  SunIcon,
  TriangleAlertIcon,
  UserRoundCogIcon,
} from "lucide-react"
import { cn, initials } from "@/lib/utils"
import { useAuth } from "@/lib/auth"
import { useTheme } from "@/lib/theme"
import { USE_MOCKS } from "@/api"
import { USERS } from "@/api/mock/corpus"
import { Button } from "@/components/ui/button"
import { Kbd, Avatar, AvatarFallback, ScrollArea, Separator, Skeleton } from "@/components/ui/misc"
import { Hint } from "@/components/ui/tooltip"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { RoleBadge, SensitivityBadge } from "@/components/domain/badges"
import { RouteErrorBoundary } from "@/components/domain/error-boundary"
import { Wordmark } from "@/components/layout/brand"
import { LEGEND_ITEM, visibleNav } from "@/components/layout/nav-config"
import { CommandPalette } from "@/components/layout/command-palette"

const SIDEBAR_KEY = "girder.sidebar.collapsed"

export function AppShell() {
  const { principal, signOut, signInAs } = useAuth()
  const { theme, setTheme } = useTheme()
  const navigate = useNavigate()
  const location = useLocation()

  const [collapsed, setCollapsed] = React.useState(
    () => localStorage.getItem(SIDEBAR_KEY) === "true",
  )
  const [mobileOpen, setMobileOpen] = React.useState(false)
  const [paletteOpen, setPaletteOpen] = React.useState(false)

  React.useEffect(() => {
    localStorage.setItem(SIDEBAR_KEY, String(collapsed))
  }, [collapsed])

  // Route change closes the mobile drawer; leaving it open after navigation
  // hides the page the user just asked for.
  React.useEffect(() => setMobileOpen(false), [location.pathname])

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setPaletteOpen((open) => !open)
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "/") {
        event.preventDefault()
        navigate("/ask")
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "b") {
        event.preventDefault()
        setCollapsed((c) => !c)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [navigate])

  const groups = visibleNav(principal?.role)

  return (
    <div className="flex min-h-svh bg-background">
      {/* ---------------------------------------------------- desktop rail */}
      <aside
        className={cn(
          "no-print sticky top-0 hidden h-svh shrink-0 flex-col border-r border-border bg-card/50 transition-[width] duration-200 md:flex",
          collapsed ? "w-[56px]" : "w-[228px]",
        )}
      >
        <div className={cn("flex h-14 shrink-0 items-center px-3", collapsed && "justify-center px-0")}>
          <NavLink to="/" className="min-w-0">
            <Wordmark collapsed={collapsed} />
          </NavLink>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          {/* Collapsed and expanded are laid out separately rather than by
              piling conditional classes onto one tree. They are different
              layouts, not one layout with different padding: expanded is a
              stack of labelled groups, collapsed is a single centred column of
              icons whose only structure is a rule between groups. Trying to
              serve both from one set of classes is what left the icons sitting
              four pixels left of the wordmark above them, with a stray divider
              under the logo and no divider at all before Reference. */}
          {collapsed ? (
            <nav className="flex flex-col items-center gap-1 pb-4">
              {groups.map((group, index) => (
                <React.Fragment key={group.label}>
                  {/* Between groups, never before the first. */}
                  {index > 0 && <Separator className="my-1.5 w-6" />}
                  {group.items.map((item) => (
                    <SidebarLink key={item.to} item={item} collapsed />
                  ))}
                </React.Fragment>
              ))}
              <Separator className="my-1.5 w-6" />
              <SidebarLink item={LEGEND_ITEM} collapsed />
            </nav>
          ) : (
            <nav className="space-y-4 px-2 pb-4">
              {groups.map((group) => (
                <div key={group.label} className="space-y-0.5">
                  <p className="eyebrow px-2 pb-1">{group.label}</p>
                  {group.items.map((item) => (
                    <SidebarLink key={item.to} item={item} collapsed={false} />
                  ))}
                </div>
              ))}
              <div className="space-y-0.5 pt-1">
                <p className="eyebrow px-2 pb-1">Reference</p>
                <SidebarLink item={LEGEND_ITEM} collapsed={false} />
              </div>
            </nav>
          )}
        </ScrollArea>

        <div
          className={cn(
            "shrink-0 border-t border-border p-2",
            collapsed && "flex justify-center px-0",
          )}
        >
          <Button
            variant="ghost"
            size={collapsed ? "icon-sm" : "sm"}
            onClick={() => setCollapsed((c) => !c)}
            className={cn("text-muted-foreground", !collapsed && "w-full justify-start")}
          >
            {collapsed ? <ChevronsRightIcon /> : <ChevronsLeftIcon />}
            {/* Same omission as the nav links: collapsed, this was a button
                with nothing in it but an arrow, so the one control that undoes
                the collapse was the one a screen reader could not name. */}
            {collapsed ? <span className="sr-only">Expand sidebar</span> : <span>Collapse</span>}
          </Button>
        </div>
      </aside>

      {/* ------------------------------------------------------ mobile nav */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-[262px] p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-14 items-center border-b border-border px-4">
            <Wordmark />
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <nav className="space-y-4 px-2 py-3">
              {groups.map((group) => (
                <div key={group.label} className="space-y-0.5">
                  <p className="eyebrow px-2 pb-1">{group.label}</p>
                  {group.items.map((item) => (
                    <SidebarLink key={item.to} item={item} collapsed={false} />
                  ))}
                </div>
              ))}
              <div className="space-y-0.5">
                <p className="eyebrow px-2 pb-1">Reference</p>
                <SidebarLink item={LEGEND_ITEM} collapsed={false} />
              </div>
            </nav>
          </ScrollArea>
        </SheetContent>
      </Sheet>

      {/* ---------------------------------------------------------- content */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="no-print sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background/85 px-3 backdrop-blur-md md:px-5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="md:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <MenuIcon />
          </Button>

          <button
            onClick={() => setPaletteOpen(true)}
            className="group flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-card px-2.5 text-left text-sm text-muted-foreground transition-colors hover:border-primary/35 hover:bg-muted/60 md:max-w-sm"
          >
            <SearchIcon className="size-3.5 shrink-0" />
            <span className="truncate text-[0.8125rem]">Jump to drawing, project or page…</span>
            <span className="ml-auto hidden shrink-0 items-center gap-0.5 sm:flex">
              <Kbd>⌘</Kbd>
              <Kbd>K</Kbd>
            </span>
          </button>

          <div className="ml-auto flex items-center gap-1.5">
            {USE_MOCKS && (
              <Hint label="Running on the bundled sample corpus. Set VITE_USE_MOCKS=false to point at the FastAPI service.">
                <span className="hidden items-center gap-1.5 rounded-sm border border-warn/35 bg-warn-soft px-2 py-1 text-[0.6875rem] font-medium text-warn lg:inline-flex">
                  <TriangleAlertIcon className="size-3" />
                  Sample data
                </span>
              </Hint>
            )}

            {principal && !principal.authenticated && (
              <Hint label="Identity came from the X-User-Id development header, which proves nothing. Any deployment reachable by anyone untrusted must enable Firebase.">
                <span className="hidden items-center gap-1.5 rounded-sm border border-error/35 bg-error-soft px-2 py-1 text-[0.6875rem] font-medium text-error sm:inline-flex">
                  <TriangleAlertIcon className="size-3" />
                  Unverified session
                </span>
              </Hint>
            )}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Theme">
                  {theme === "dark" ? <MoonIcon /> : theme === "light" ? <SunIcon /> : <MonitorIcon />}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Appearance</DropdownMenuLabel>
                <DropdownMenuRadioGroup
                  value={theme}
                  onValueChange={(value) => setTheme(value as "light" | "dark" | "system")}
                >
                  <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            {principal && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="flex items-center gap-2 rounded-md py-1 pl-1 pr-1.5 transition-colors hover:bg-muted">
                    <Avatar className="size-7">
                      <AvatarFallback>{initials(principal.display_name ?? principal.email)}</AvatarFallback>
                    </Avatar>
                    <span className="hidden min-w-0 text-left leading-tight lg:block">
                      <span className="block truncate text-[0.8125rem] font-medium">
                        {principal.display_name ?? principal.email}
                      </span>
                      <span className="block truncate text-[0.625rem] capitalize text-muted-foreground">
                        {principal.role} · {principal.clearance}
                      </span>
                    </span>
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-64">
                  <div className="space-y-2 px-2 py-2">
                    <p className="text-sm font-medium leading-none">
                      {principal.display_name ?? principal.email}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{principal.email}</p>
                    <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                      <RoleBadge role={principal.role} />
                      <SensitivityBadge value={principal.clearance} />
                    </div>
                    <p className="pt-1 text-[0.6875rem] leading-relaxed text-muted-foreground">
                      Your role decides what you may do; your clearance decides how deep you may
                      read. Both must pass.
                    </p>
                  </div>
                  <DropdownMenuSeparator />
                  {USE_MOCKS && (
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>
                        <UserRoundCogIcon className="mr-2 size-4" />
                        Switch identity
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent className="w-60">
                        <DropdownMenuLabel>Try another clearance</DropdownMenuLabel>
                        {USERS.map((user) => (
                          <DropdownMenuItem
                            key={user.user_id}
                            onClick={() => signInAs(user.user_id)}
                            className="flex-col items-start gap-0.5"
                          >
                            <span className="text-[0.8125rem]">{user.display_name}</span>
                            <span className="text-[0.625rem] capitalize text-muted-foreground">
                              {user.role} · reads up to {user.clearance}
                            </span>
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>
                  )}
                  <DropdownMenuItem onClick={() => navigate("/legend")}>
                    <PanelLeftIcon className="mr-2 size-4" />
                    Legend &amp; conventions
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onClick={signOut}>
                    <LogOutIcon className="mr-2 size-4" />
                    Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </header>

        {/* Suspense and the error boundary both sit inside the shell, not
            around it, so neither a lazy chunk arriving nor a page throwing
            blanks the sidebar the user just clicked. Keyed on the path so
            navigating away from a broken page clears the error rather than
            stranding the user on it. */}
        <main className="min-w-0 flex-1">
          <RouteErrorBoundary resetKey={location.pathname}>
            <React.Suspense fallback={<RouteFallback />}>
              <Outlet />
            </React.Suspense>
          </RouteErrorBoundary>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  )
}

/** Page-shaped placeholder while a lazy route's chunk loads. */
function RouteFallback() {
  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-5 px-4 py-5 md:px-6 md:py-6">
      <div className="space-y-2">
        <Skeleton className="h-2.5 w-24" />
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-3 w-full max-w-lg" />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-64 rounded-lg" />
    </div>
  )
}

function SidebarLink({
  item,
  collapsed,
}: {
  item: { label: string; to: string; icon: React.ElementType; description: string; end?: boolean }
  collapsed: boolean
}) {
  const Icon = item.icon

  // Active state is resolved here rather than through NavLink's render props,
  // and that is load-bearing rather than stylistic.
  //
  // Collapsed, the link is wrapped in `Hint`, whose Radix trigger uses
  // `asChild` — and Slot merges `className` by *string concatenation*. Handed
  // NavLink's className **function** it stringified it, so the rail's links
  // carried the literal source text of the callback as their class list. None
  // of `size-9`, the hover state or the active background ever applied: the
  // icons were bare 16px glyphs with no hit target and no selected state,
  // which is most of what "the icons look mixed up" was.
  //
  // Expanded, the same component worked perfectly — no Hint, no Slot, no
  // stringify — which is exactly why this survived so long.
  const match = useMatch({ path: item.to, end: item.end ?? false })
  const isActive = match !== null

  const link = (
    <NavLink
      to={item.to}
      end={item.end}
      className={cn(
        "relative flex items-center gap-2.5 rounded-md text-[0.8125rem] font-medium transition-colors",
        collapsed ? "size-9 justify-center" : "px-2 py-1.5",
        isActive
          ? "bg-primary-soft text-primary"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {isActive && !collapsed && (
        <span className="absolute -left-2 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-r bg-primary" />
      )}
      <Icon className="size-4 shrink-0" />
      {collapsed ? (
        // Collapsed, the icon is the only visible content — and an icon is not
        // an accessible name, so every link in the rail was reaching a screen
        // reader unnamed. The tooltip does not stand in for this: it is
        // hover-and-focus content, not the link's own label.
        <span className="sr-only">{item.label}</span>
      ) : (
        <span className="truncate">{item.label}</span>
      )}
    </NavLink>
  )

  if (!collapsed) return link
  return (
    <Hint side="right" label={<span><b>{item.label}</b> — {item.description}</span>}>
      {link}
    </Hint>
  )
}

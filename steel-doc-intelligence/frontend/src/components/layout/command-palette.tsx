import * as React from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  ClipboardListIcon,
  CornerDownLeftIcon,
  FileTextIcon,
  FolderKanbanIcon,
  MessagesSquareIcon,
  SearchIcon,
} from "lucide-react"
import { api } from "@/api"
import { useAuth } from "@/lib/auth"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command"
import { LEGEND_ITEM, visibleNav } from "@/components/layout/nav-config"
import { DrawingNumber, RevisionChip } from "@/components/domain/badges"

/**
 * ⌘K. Three things engineers actually reach for: a drawing number they half
 * remember, a project, or a page. Typing a question and pressing enter hands
 * it straight to Ask, because that is the other half of "I need to find X".
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const { principal } = useAuth()
  const [query, setQuery] = React.useState("")

  const { data: drawings = [] } = useQuery({
    queryKey: ["drawings", "palette"],
    queryFn: () => api.listDrawings(),
    enabled: open,
    staleTime: 60_000,
  })
  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.listProjects(),
    enabled: open,
    staleTime: 60_000,
  })

  const go = React.useCallback(
    (to: string) => {
      onOpenChange(false)
      setQuery("")
      navigate(to)
    },
    [navigate, onOpenChange],
  )

  const pages = visibleNav(principal?.role).flatMap((group) =>
    group.items.map((item) => ({ ...item, group: group.label })),
  )

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput
        placeholder="Search drawings, projects and pages — or type a question…"
        value={query}
        onValueChange={setQuery}
      />
      <CommandList>
        <CommandEmpty>
          <span className="block">Nothing matched “{query}”.</span>
          <span className="mt-1 block text-xs">
            Press <kbd className="font-mono">Enter</kbd> to ask it as a question instead.
          </span>
        </CommandEmpty>

        {query.trim().length > 2 && (
          <>
            <CommandGroup heading="Ask">
              <CommandItem
                value={`ask ${query}`}
                onSelect={() => go(`/ask?q=${encodeURIComponent(query)}`)}
              >
                <MessagesSquareIcon />
                <span className="truncate">
                  Ask the corpus: <span className="font-medium">{query}</span>
                </span>
                <CommandShortcut>
                  <CornerDownLeftIcon className="size-3" />
                </CommandShortcut>
              </CommandItem>
              <CommandItem
                value={`search ${query}`}
                onSelect={() => go(`/search?q=${encodeURIComponent(query)}`)}
              >
                <SearchIcon />
                <span className="truncate">
                  Search every sheet for <span className="font-medium">{query}</span>
                </span>
              </CommandItem>
            </CommandGroup>
            <CommandSeparator />
          </>
        )}

        {drawings.length > 0 && (
          <CommandGroup heading="Drawings">
            {drawings.slice(0, 40).map((drawing) => (
              <CommandItem
                key={drawing.id}
                value={`${drawing.drawing_number} ${drawing.title} ${drawing.discipline}`}
                onSelect={() => go(`/drawings/${drawing.id}`)}
              >
                <ClipboardListIcon />
                <span className="flex min-w-0 flex-1 items-center gap-2">
                  <DrawingNumber value={drawing.drawing_number} sheet={drawing.sheet_number} />
                  <span className="truncate text-muted-foreground">{drawing.title}</span>
                </span>
                <RevisionChip label={drawing.current_revision_label} size="sm" />
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {projects.length > 0 && (
          <CommandGroup heading="Projects">
            {projects.map((project) => (
              <CommandItem
                key={project.id}
                value={`${project.project_number} ${project.name} ${project.client_name}`}
                onSelect={() => go(`/projects/${project.id}`)}
              >
                <FolderKanbanIcon />
                <span className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="font-mono text-[0.75rem]">{project.project_number}</span>
                  <span className="truncate text-muted-foreground">{project.name}</span>
                </span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        <CommandSeparator />
        <CommandGroup heading="Go to">
          {[...pages, { ...LEGEND_ITEM, group: "Reference" }].map((page) => {
            const Icon = page.icon
            return (
              <CommandItem
                key={page.to}
                value={`${page.label} ${page.group} ${page.description}`}
                onSelect={() => go(page.to)}
              >
                <Icon />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span>{page.label}</span>
                  <span className="truncate text-[0.6875rem] text-muted-foreground">
                    {page.description}
                  </span>
                </span>
              </CommandItem>
            )
          })}
          <CommandItem value="documents library all files" onSelect={() => go("/documents")}>
            <FileTextIcon />
            <span>All documents</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}

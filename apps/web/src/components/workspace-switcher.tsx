import { Link } from "@tanstack/react-router"
import { Check, ChevronsUpDown, Plus, SquareStack } from "lucide-react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import {
  useCurrentWorkspace,
  useSwitchWorkspace,
  useWorkspaces,
} from "@/lib/api_client"

export function WorkspaceSwitcher() {
  const { data: workspaces = [] } = useWorkspaces()
  const current = useCurrentWorkspace()
  const switchTo = useSwitchWorkspace()
  const { isMobile } = useSidebar()

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton
                size="lg"
                className="data-[popup-open]:bg-sidebar-accent data-[popup-open]:text-sidebar-accent-foreground"
              />
            }
          >
            <div className="flex aspect-square size-8 items-center justify-center rounded-lg border border-sidebar-border bg-sidebar-accent">
              <SquareStack className="size-4" />
            </div>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-semibold">zoo</span>
              <span className="truncate text-xs text-muted-foreground">
                {current?.personal
                  ? "Personal workspace"
                  : (current?.name ?? "Workspace")}
              </span>
            </div>
            <ChevronsUpDown className="ml-auto size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--anchor-width) min-w-56 rounded-lg"
            side={isMobile ? "bottom" : "right"}
            align="start"
          >
            <DropdownMenuGroup>
              <DropdownMenuLabel className="text-xs text-muted-foreground">
                Workspaces
              </DropdownMenuLabel>
              {workspaces.map((w) => (
                <DropdownMenuItem key={w.id} onClick={() => switchTo(w)}>
                  <span className="flex-1 truncate">
                    {w.personal ? "Personal" : w.name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {w.role}
                  </span>
                  {w.id === current?.id && <Check className="size-4" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem render={<Link to="/workspace" />}>
              <Plus />
              New or manage workspaces
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

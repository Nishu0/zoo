import { Link, useLocation } from "@tanstack/react-router"
import {
  Activity,
  Box,
  ScrollText,
  Server,
  UserRound,
  Users,
  Vault,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { NavUser } from "@/components/nav-user"
import { WorkspaceSwitcher } from "@/components/workspace-switcher"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import type { User } from "@/lib/api_client"

type NavItem = { title: string; to: string; icon: LucideIcon }

const NAV: Array<{ label: string; items: Array<NavItem> }> = [
  {
    label: "Home",
    items: [
      { title: "Sandboxes", to: "/sandboxes", icon: Box },
      { title: "Monitoring", to: "/monitoring", icon: Activity },
      { title: "Vault", to: "/vault", icon: Vault },
    ],
  },
  {
    label: "Settings",
    items: [
      { title: "Workspace", to: "/workspace", icon: Users },
      { title: "Audit log", to: "/audit", icon: ScrollText },
      { title: "Remote Servers", to: "/servers", icon: Server },
      { title: "Profile", to: "/settings", icon: UserRound },
    ],
  },
]

export function AppSidebar({ user }: { user: User }) {
  const pathname = useLocation({ select: (location) => location.pathname })

  return (
    <Sidebar collapsible="icon" variant="floating">
      <SidebarHeader>
        <WorkspaceSwitcher />
      </SidebarHeader>

      <SidebarContent>
        {NAV.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarMenu>
              {group.items.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton
                    isActive={pathname.startsWith(item.to)}
                    tooltip={item.title}
                    render={<Link to={item.to} />}
                  >
                    <item.icon />
                    <span>{item.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <NavUser user={user} />
        <p className="pb-1 text-center text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">
          Version v0.1.0
        </p>
      </SidebarFooter>
    </Sidebar>
  )
}

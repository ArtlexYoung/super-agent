import { Blocks, MessageSquareText, Settings2, Sparkles } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { AppPage } from "@/lib/types"
import { cn } from "@/lib/utils"

interface AppShellProps {
  page: AppPage
  agentName: string
  children: React.ReactNode
  onPageChange: (page: AppPage) => void
}

const navigation = [
  { page: "conversations" as const, label: "对话", icon: MessageSquareText },
  { page: "copilotkit" as const, label: "CopilotKit", icon: Blocks },
  { page: "configuration" as const, label: "配置", icon: Settings2 },
]

export function AppShell({
  page,
  agentName,
  children,
  onPageChange,
}: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="primary-sidebar">
        <div className="brand-mark" aria-label="Super Agent">
          <Sparkles className="size-4" />
        </div>
        <nav className="primary-navigation" aria-label="主导航">
          {navigation.map((item) => {
            const Icon = item.icon
            return (
              <Tooltip key={item.page}>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    className={cn(
                      "navigation-button",
                      page === item.page && "navigation-button-active"
                    )}
                    aria-current={page === item.page ? "page" : undefined}
                    onClick={() => onPageChange(item.page)}
                  >
                    <Icon />
                    <span>{item.label}</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={8}>
                  {item.label}
                </TooltipContent>
              </Tooltip>
            )
          })}
        </nav>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="agent-presence" tabIndex={0}>
              <span className="agent-presence-dot" />
              <span className="sr-only">{agentName} 已连接</span>
            </div>
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>
            {agentName} 已连接
          </TooltipContent>
        </Tooltip>
      </aside>
      <main className="min-h-0 min-w-0 flex-1">{children}</main>
    </div>
  )
}

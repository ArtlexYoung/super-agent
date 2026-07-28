import { lazy, Suspense, useState } from "react"
import { LoaderCircle, RefreshCw, Sparkles } from "lucide-react"

import { AppShell } from "@/components/app-shell"
import { ConfigurationPage } from "@/components/configuration/configuration-page"
import { ConversationPage } from "@/components/conversation/conversation-page"
import { Button } from "@/components/ui/button"
import type { AppPage } from "@/lib/types"
import { useSuperAgent } from "@/lib/use-super-agent"

const CopilotKitExamplePage = lazy(() =>
  import("@/components/copilotkit/copilotkit-example-page").then((module) => ({
    default: module.CopilotKitExamplePage,
  }))
)

export function App() {
  const [page, setPage] = useState<AppPage>("conversations")
  const controller = useSuperAgent()

  if (controller.loading) {
    return (
      <div className="loading-screen">
        <div className="brand-mark brand-mark-large">
          <Sparkles />
        </div>
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!controller.bootstrap) {
    return (
      <div className="loading-screen gap-4">
        <div className="brand-mark brand-mark-large">
          <Sparkles />
        </div>
        <p className="text-sm text-muted-foreground">
          无法连接 Super Agent Runtime
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={() => location.reload()}
        >
          <RefreshCw />
          重新连接
        </Button>
      </div>
    )
  }

  return (
    <AppShell
      page={page}
      agentName={controller.bootstrap.agent.name}
      onPageChange={setPage}
    >
      {page === "conversations" ? <ConversationPage controller={controller} /> : null}
      {page === "copilotkit" ? (
        <Suspense fallback={<PageLoadingState />}>
          <CopilotKitExamplePage controller={controller} />
        </Suspense>
      ) : null}
      {page === "configuration" ? (
        <ConfigurationPage
          key={JSON.stringify(controller.bootstrap.agent)}
          controller={controller}
        />
      ) : null}
    </AppShell>
  )
}

function PageLoadingState() {
  return (
    <div className="loading-screen">
      <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      <p className="text-sm text-muted-foreground">正在加载示例</p>
    </div>
  )
}

export default App

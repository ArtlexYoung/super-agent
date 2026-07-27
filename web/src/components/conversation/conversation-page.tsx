import { GitBranch, MessagesSquare } from "lucide-react"

import { ChatWorkspace } from "@/components/conversation/chat-workspace"
import { ConversationList } from "@/components/conversation/conversation-list"
import { RunInsightSheet } from "@/components/conversation/run-insight-sheet"
import { RunTree } from "@/components/conversation/run-tree"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import type { SuperAgentController } from "@/lib/use-super-agent"

interface ConversationPageProps {
  controller: SuperAgentController
}

export function ConversationPage({ controller }: ConversationPageProps) {
  const data = controller.bootstrap!
  const list = (
    <ConversationList
      conversations={data.conversations}
      selectedConversationId={controller.selectedConversationId}
      busy={controller.busy}
      onCreate={() => void controller.createNewConversation()}
      onSelect={controller.setSelectedConversationId}
      onRename={(id, title) =>
        void controller.renameExistingConversation(id, title)
      }
      onDelete={(id) => void controller.deleteExistingConversation(id)}
    />
  )
  const tree = (
    <RunTree
      agentName={data.agent.name}
      conversationId={controller.selectedConversationId}
      runs={data.runs}
      subagents={data.subagents}
      onOpenRun={(runId) => void controller.openRunInsight(runId)}
    />
  )
  return (
    <div className="conversation-page">
      <div className="hidden min-h-0 lg:block">{list}</div>
      <div className="mobile-workspace-tools lg:hidden">
        <Sheet>
          <SheetTrigger asChild>
            <Button type="button" variant="outline" size="sm">
              <MessagesSquare />
              会话
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>会话</SheetTitle>
            </SheetHeader>
            {list}
          </SheetContent>
        </Sheet>
        <Sheet>
          <SheetTrigger asChild>
            <Button type="button" variant="outline" size="sm">
              <GitBranch />
              运行树
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="p-0">
            <SheetHeader className="sr-only">
              <SheetTitle>Agent 运行树</SheetTitle>
            </SheetHeader>
            {tree}
          </SheetContent>
        </Sheet>
      </div>
      <ChatWorkspace
        conversation={controller.selectedConversation}
        pendingPrompt={controller.pendingPrompt}
        streamingResponse={controller.streamingResponse}
        runActivity={controller.runActivity}
        running={controller.running}
        busy={controller.busy}
        onSend={(prompt) => void controller.sendMessage(prompt)}
        onStop={controller.stopRun}
        onClear={(id) => void controller.clearExistingConversation(id)}
        onCreate={() => void controller.createNewConversation()}
      />
      <div className="hidden min-h-0 xl:block">{tree}</div>
      <RunInsightSheet
        insight={controller.runInsight}
        loading={controller.runInsightLoading}
        onClose={controller.closeRunInsight}
      />
    </div>
  )
}

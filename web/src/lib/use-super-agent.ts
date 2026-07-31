import { startTransition, useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import {
  clearConversation,
  createConversation,
  deleteConversation,
  deleteModelSkill,
  forgetMemory,
  loadBootstrap,
  loadRunInsight,
  renameConversation,
  saveAgentConfiguration,
  saveModelSkill,
  streamAgentRun,
} from "@/lib/api"
import type {
  AgentConfiguration,
  BootstrapData,
  Conversation,
  ModelSkillInput,
  RunInsight,
} from "@/lib/types"

export interface SuperAgentController {
  bootstrap: BootstrapData | null
  selectedConversation: Conversation | null
  selectedConversationId: string | null
  loading: boolean
  busy: boolean
  running: boolean
  pendingPrompt: string
  streamingResponse: string
  runActivity: string
  runInsight: RunInsight | null
  runInsightLoading: boolean
  setSelectedConversationId: (conversationId: string) => void
  createNewConversation: () => Promise<void>
  renameExistingConversation: (
    conversationId: string,
    title: string
  ) => Promise<void>
  clearExistingConversation: (conversationId: string) => Promise<void>
  deleteExistingConversation: (conversationId: string) => Promise<void>
  saveConfiguration: (configuration: AgentConfiguration) => Promise<void>
  saveModel: (model: ModelSkillInput) => Promise<void>
  deleteModel: (name: string) => Promise<void>
  forgetMemoryItem: (itemId: string) => Promise<void>
  sendMessage: (prompt: string) => Promise<void>
  stopRun: () => void
  openRunInsight: (runId: string) => Promise<void>
  closeRunInsight: () => void
  reloadRuntimeData: (preferredConversationId?: string) => Promise<void>
}

export function useSuperAgent(): SuperAgentController {
  const [bootstrap, setBootstrap] = useState<BootstrapData | null>(null)
  const [selectedConversationId, setSelectedConversationId] = useState<
    string | null
  >(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [pendingPrompt, setPendingPrompt] = useState("")
  const [streamingResponse, setStreamingResponse] = useState("")
  const [runActivity, setRunActivity] = useState("")
  const [runInsight, setRunInsight] = useState<RunInsight | null>(null)
  const [runInsightLoading, setRunInsightLoading] = useState(false)
  const runAbortController = useRef<AbortController | null>(null)

  useEffect(() => {
    let active = true
    loadBootstrap()
      .then((data) => {
        if (active) {
          startTransition(() =>
            applyBootstrap(data, setBootstrap, setSelectedConversationId)
          )
        }
      })
      .catch((error: unknown) => {
        if (active) {
          toast.error(errorMessage(error))
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [])

  const selectedConversation =
    bootstrap?.conversations.find(
      (item) => item.conversation_id === selectedConversationId
    ) || null

  async function refresh(preferredConversationId?: string): Promise<void> {
    const data = await loadBootstrap()
    applyBootstrap(
      data,
      setBootstrap,
      setSelectedConversationId,
      preferredConversationId
    )
  }

  async function createNewConversation(): Promise<void> {
    await performMutation("会话已创建", async () => {
      const created = await createConversation()
      await refresh(created.conversation_id)
    })
  }

  async function renameExistingConversation(
    conversationId: string,
    title: string
  ): Promise<void> {
    await performMutation("会话名称已更新", async () => {
      await renameConversation(conversationId, title)
      await refresh(conversationId)
    })
  }

  async function clearExistingConversation(
    conversationId: string
  ): Promise<void> {
    await performMutation("对话记录已清空", async () => {
      await clearConversation(conversationId)
      await refresh(conversationId)
    })
  }

  async function deleteExistingConversation(
    conversationId: string
  ): Promise<void> {
    await performMutation("会话已删除", async () => {
      await deleteConversation(conversationId)
      await refresh()
    })
  }

  async function saveConfiguration(
    configuration: AgentConfiguration
  ): Promise<void> {
    await performMutation("配置已保存", async () => {
      const data = await saveAgentConfiguration(configuration)
      applyBootstrap(data, setBootstrap, setSelectedConversationId)
    })
  }

  async function saveModel(model: ModelSkillInput): Promise<void> {
    await performMutation("模型配置已保存", async () => {
      const data = await saveModelSkill(model)
      applyBootstrap(data, setBootstrap, setSelectedConversationId)
    })
  }

  async function deleteModel(name: string): Promise<void> {
    await performMutation("模型配置已删除", async () => {
      const data = await deleteModelSkill(name)
      applyBootstrap(data, setBootstrap, setSelectedConversationId)
    })
  }

  async function forgetMemoryItem(itemId: string): Promise<void> {
    await performMutation("记忆已遗忘", async () => {
      const data = await forgetMemory(itemId)
      applyBootstrap(data, setBootstrap, setSelectedConversationId)
    })
  }

  async function sendMessage(prompt: string): Promise<void> {
    const cleanPrompt = prompt.trim()
    if (!cleanPrompt || running) {
      return
    }
    let conversationId = selectedConversationId
    if (!conversationId) {
      try {
        const created = await createConversation()
        conversationId = created.conversation_id
        setSelectedConversationId(conversationId)
      } catch (error) {
        toast.error(errorMessage(error))
        return
      }
    }
    const abortController = new AbortController()
    runAbortController.current = abortController
    setRunning(true)
    setPendingPrompt(cleanPrompt)
    setStreamingResponse("")
    setRunActivity("正在连接 Runtime")
    let streamError = ""
    try {
      await streamAgentRun(
        conversationId,
        cleanPrompt,
        (event) => {
          if (event.type === "TEXT_MESSAGE_CONTENT") {
            setStreamingResponse((current) => current + (event.delta || ""))
          }
          if (event.type === "CUSTOM" && event.name) {
            setRunActivity(runtimeEventLabel(event.name))
          }
          if (event.type === "RUN_ERROR") {
            streamError = event.message || "Agent 运行失败"
          }
        },
        abortController.signal
      )
      if (streamError) {
        throw new Error(streamError)
      }
      await refresh(conversationId)
    } catch (error) {
      if (!abortController.signal.aborted) {
        toast.error(errorMessage(error))
      }
    } finally {
      if (runAbortController.current === abortController) {
        runAbortController.current = null
      }
      setRunning(false)
      setPendingPrompt("")
      setStreamingResponse("")
      setRunActivity("")
    }
  }

  function stopRun(): void {
    runAbortController.current?.abort()
    setRunActivity("已停止接收")
  }

  async function openRunInsight(runId: string): Promise<void> {
    setRunInsightLoading(true)
    try {
      setRunInsight(await loadRunInsight(runId))
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setRunInsightLoading(false)
    }
  }

  function closeRunInsight(): void {
    setRunInsight(null)
  }

  async function performMutation(
    successMessage: string,
    mutation: () => Promise<void>
  ): Promise<void> {
    if (busy) {
      return
    }
    setBusy(true)
    try {
      await mutation()
      toast.success(successMessage)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return {
    bootstrap,
    selectedConversation,
    selectedConversationId,
    loading,
    busy,
    running,
    pendingPrompt,
    streamingResponse,
    runActivity,
    runInsight,
    runInsightLoading,
    setSelectedConversationId,
    createNewConversation,
    renameExistingConversation,
    clearExistingConversation,
    deleteExistingConversation,
    saveConfiguration,
    saveModel,
    deleteModel,
    forgetMemoryItem,
    sendMessage,
    stopRun,
    openRunInsight,
    closeRunInsight,
    reloadRuntimeData: refresh,
  }
}

function applyBootstrap(
  data: BootstrapData,
  setBootstrap: React.Dispatch<React.SetStateAction<BootstrapData | null>>,
  setSelectedConversationId: React.Dispatch<
    React.SetStateAction<string | null>
  >,
  preferredConversationId?: string
): void {
  setBootstrap(data)
  setSelectedConversationId((current) => {
    const requested = preferredConversationId || current
    if (
      requested &&
      data.conversations.some((item) => item.conversation_id === requested)
    ) {
      return requested
    }
    return data.conversations[0]?.conversation_id || null
  })
}

function runtimeEventLabel(eventName: string): string {
  const labels: Record<string, string> = {
    "run.started": "运行已开始",
    "task.started": "正在理解任务",
    "task.selected": "正在选择任务技能",
    "task.scheduled": "正在调度能力",
    "model.call.selected": "正在选择模型",
    "model.call.completed": "模型响应完成",
    "tool.requested": "正在调用工具",
    "tool.completed": "工具调用完成",
    "task.completed": "正在整理回答",
    "run.completed": "运行完成",
  }
  return labels[eventName] || "Agent 正在工作"
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误"
}

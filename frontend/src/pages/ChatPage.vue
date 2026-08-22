<template>
  <SimpleBar ref="simpleBarRef" @scroll="handleScroll">
    <div ref="chatContainerRef" class="relative flex flex-col h-full flex-1 min-w-0 px-5">
      <div ref="observerRef"
        class="sm:min-w-[390px] flex flex-row items-center justify-between pt-3 pb-1 gap-1 sticky top-0 z-10 bg-[var(--background-gray-main)] flex-shrink-0">
        <div class="flex items-center flex-1">
          <div class="relative flex items-center">
            <div @click="toggleLeftPanel" v-if="!isLeftPanelShow"
              class="flex h-7 w-7 items-center justify-center cursor-pointer rounded-md hover:bg-[var(--fill-tsp-gray-main)]">
              <PanelLeft class="size-5 text-[var(--icon-secondary)]" />
            </div>
          </div>
        </div>
        <div class="max-w-full sm:max-w-[768px] sm:min-w-[390px] flex w-full flex-col gap-[4px] overflow-hidden">
          <div
            class="text-[var(--text-primary)] text-lg font-medium w-full flex flex-row items-center justify-between flex-1 min-w-0 gap-2">
            <div class="flex flex-row items-center gap-[6px] flex-1 min-w-0">
              <span class="whitespace-nowrap text-ellipsis overflow-hidden">
                {{ title }}
              </span>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <span class="relative flex-shrink-0" aria-expanded="false" aria-haspopup="dialog">
                <Popover>
                  <PopoverTrigger>
                    <button
                      class="h-8 px-3 rounded-[100px] inline-flex items-center gap-1 clickable outline outline-1 outline-offset-[-1px] outline-[var(--border-btn-main)] hover:bg-[var(--fill-tsp-white-light)] me-1.5">
                      <ShareIcon color="var(--icon-secondary)" />
                      <span class="text-[var(--text-secondary)] text-sm font-medium">{{ t('Share') }}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent>
                    <div
                      class="w-[400px] flex flex-col rounded-2xl bg-[var(--background-menu-white)] shadow-[0px_8px_32px_0px_var(--shadow-S),0px_0px_0px_1px_var(--border-light)]"
                      style="max-width: calc(-16px + 100vw);">
                      <div class="flex flex-col pt-[12px] px-[16px] pb-[16px]">
                        <!-- Private mode option -->
                        <div @click="handleShareModeChange('private')"
                          :class="{'pointer-events-none opacity-50': sharingLoading}"
                          class="flex items-center gap-[10px] px-[8px] -mx-[8px] py-[8px] rounded-[8px] clickable hover:bg-[var(--fill-tsp-white-main)]">
                          <div
                            :class="shareMode === 'private' ? 'bg-[var(--Button-primary-black)]' : 'bg-[var(--fill-tsp-white-dark)]'"
                            class="w-[32px] h-[32px] rounded-[8px] flex items-center justify-center">
                            <Lock :size="16" :stroke="shareMode === 'private' ? 'var(--text-onblack)' : 'var(--icon-primary)'" :stroke-width="2" /></div>
                          <div class="flex flex-col flex-1 min-w-0">
                            <div class="text-sm font-medium text-[var(--text-primary)]">{{ t('Private Only') }}</div>
                            <div class="text-[13px] text-[var(--text-tertiary)]">{{ t('Only visible to you') }}</div>
                          </div><Check :size="20" :class="shareMode === 'private' ? 'ml-auto' : 'ml-auto invisible'" :color="shareMode === 'private' ? 'var(--icon-primary)' : 'var(--icon-tertiary)'" />
                        </div>
                        <!-- Public mode option -->
                        <div @click="handleShareModeChange('public')"
                          :class="{'pointer-events-none opacity-50': sharingLoading}"
                          class="flex items-center gap-[10px] px-[8px] -mx-[8px] py-[8px] rounded-[8px] clickable hover:bg-[var(--fill-tsp-white-main)]">
                          <div
                            :class="shareMode === 'public' ? 'bg-[var(--Button-primary-black)]' : 'bg-[var(--fill-tsp-white-dark)]'"
                            class="w-[32px] h-[32px] rounded-[8px] flex items-center justify-center">
                            <Globe :size="16" :stroke="shareMode === 'public' ? 'var(--text-onblack)' : 'var(--icon-primary)'" :stroke-width="2" /></div>
                          <div class="flex flex-col flex-1 min-w-0">
                            <div class="text-sm font-medium text-[var(--text-primary)]">{{ t('Public Access') }}</div>
                            <div class="text-[13px] text-[var(--text-tertiary)]">{{ t('Anyone with the link can view') }}</div>
                          </div><Check :size="20" :class="shareMode === 'public' ? 'ml-auto' : 'ml-auto invisible'" :color="shareMode === 'public' ? 'var(--icon-primary)' : 'var(--icon-tertiary)'" />
                        </div>
                        <div class="border-t border-[var(--border-main)] mt-[4px]"></div>
                        
                        <!-- Show instant share button when in private mode -->
                        <div v-if="shareMode === 'private'">
                          <button @click.stop="handleInstantShare"
                            :disabled="sharingLoading"
                            class="inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full disabled:opacity-50 disabled:cursor-not-allowed"
                            data-tabindex="" tabindex="-1">
                            <div v-if="sharingLoading" class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                            <Link v-else :size="16" stroke="currentColor" :stroke-width="2" />
                            {{ sharingLoading ? t('Sharing...') : t('Share Instantly') }}
                          </button>
                        </div>
                        
                        <!-- Show copy link button when in public mode -->
                        <div v-else>
                          <button @click.stop="handleCopyLink"
                            :class="linkCopied ? 'inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors active:opacity-80 bg-[var(--Button-primary-white)] text-[var(--text-primary)] hover:opacity-70 active:hover-60 h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full border border-[var(--border-btn-main)] shadow-none' : 'inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors hover:opacity-90 active:opacity-80 bg-[var(--Button-primary-black)] text-[var(--text-onblack)] h-[36px] px-[12px] rounded-[10px] gap-[6px] text-sm min-w-16 mt-[16px] w-full'"
                            data-tabindex="" tabindex="-1">
                            <Link v-if="!linkCopied" :size="16" stroke="currentColor" :stroke-width="2" />
                            <Check v-else :size="16" color="var(--text-primary)" />
                            {{ linkCopied ? t('Link Copied') : t('Copy Link') }}
                          </button>
                        </div>
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>
              </span>
              <button @click="handleFileListShow"
                class="p-[5px] flex items-center justify-center hover:bg-[var(--fill-tsp-white-dark)] rounded-lg cursor-pointer">
                <FileSearch class="text-[var(--icon-secondary)]" :size="18" />
              </button>
            </div>
          </div>
          <div class="w-full flex justify-between items-center">
          </div>
        </div>
        <div class="flex-1"></div>
      </div>
      <div class="mx-auto w-full max-w-full sm:max-w-[768px] sm:min-w-[390px] flex flex-col flex-1">
        <div class="flex flex-col w-full gap-[12px] pb-[80px] pt-[12px] flex-1 overflow-y-auto">
          <ChatMessage v-for="(message, index) in messages"
            :key="message.content.event_id || `${message.type}-${index}`" :message="message"
            :hideHeader="isConsecutiveAssistant(messages, index)"
            @toolClick="handleToolClick" />

          <!-- Loading indicator — hidden while streaming acknowledgment chunks -->
          <LoadingIndicator v-if="isLoading && !streamingMessageContent" :text="currentThinkingText || $t('Thinking')" />
          <!-- Wait indicator — agent is expecting user input -->
          <div v-if="isWaitingForInput && !isLoading"
            class="flex items-center gap-2 px-4 py-2 mx-auto rounded-xl text-sm text-[var(--text-secondary)] bg-[var(--fill-tsp-white-main)] border border-[var(--border-main)] w-fit">
            <span class="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
            {{ $t('Agent is waiting for your response') }}
          </div>
          <div v-if="streamInterrupted && !isLoading"
            class="flex items-center gap-2 px-4 py-2 mx-auto rounded-xl text-sm text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 w-fit">
            {{ $t('Connection ended before the task reached a terminal state. Refresh to resume from the last event.') }}
          </div>
        </div>

        <div class="flex flex-col bg-[var(--background-gray-main)] sticky bottom-0">
          <button @click="handleFollow" v-if="!follow"
            class="flex items-center justify-center w-[36px] h-[36px] rounded-full bg-[var(--background-white-main)] hover:bg-[var(--background-gray-main)] clickable border border-[var(--border-main)] shadow-[0px_5px_16px_0px_var(--shadow-S),0px_0px_1.25px_0px_var(--shadow-S)] absolute -top-20 left-1/2 -translate-x-1/2">
            <ArrowDown class="text-[var(--icon-primary)]" :size="20" />
          </button>
          <PlanPanel v-if="plan && plan.steps.length > 0" :plan="plan" @close="plan = undefined" />
          <ChatBox v-model="inputMessage" :rows="1" @submit="handleSubmit" :isRunning="isLoading" @stop="handleStop"
            :attachments="attachments" />
        </div>
      </div>
    </div>
    <ToolPanel ref="toolPanel" :allTools="allTools" :sessionId="sessionId" :realTime="realTime"
      :isShare="false"
      @jumpToRealTime="jumpToRealTime" />
  </SimpleBar>
</template>

<script setup lang="ts">
import SimpleBar from '../components/SimpleBar.vue';
import { ref, onMounted, watch, nextTick, onUnmounted, reactive, toRefs, computed } from 'vue';
import { useRouter, onBeforeRouteUpdate } from 'vue-router';
import { useI18n } from 'vue-i18n';
import ChatBox from '../components/ChatBox.vue';
import ChatMessage from '../components/ChatMessage.vue';
import * as agentApi from '../api/agent';
import { Message, MessageContent, ToolContent, StepContent, AttachmentsContent, isConsecutiveAssistant } from '../types/message';
import {
  StepEventData,
  ToolEventData,
  MessageEventData,
  MessageChunkEventData,
  ErrorEventData,
  TitleEventData,
  PlanEventData,
  AgentSSEEvent,
} from '../types/event';
import ToolPanel from '../components/ToolPanel.vue'
import { TOOL_FUNCTION_MAP } from '../constants/tool'
import PlanPanel from '../components/PlanPanel.vue';
import { ArrowDown, FileSearch, PanelLeft, Lock, Globe, Link, Check } from 'lucide-vue-next';
import ShareIcon from '@/components/icons/ShareIcon.vue';
import { showErrorToast, showSuccessToast } from '../utils/toast';
import type { FileInfo } from '../api/file';
import { useLeftPanel } from '../composables/useLeftPanel'
import { useSessionFileList } from '../composables/useSessionFileList'
import { useFilePanel } from '../composables/useFilePanel'
import { copyToClipboard } from '../utils/dom'
import { SessionStatus } from '../types/response';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import LoadingIndicator from '@/components/ui/LoadingIndicator.vue';

const router = useRouter()
const { t } = useI18n()
const { toggleLeftPanel, isLeftPanelShow } = useLeftPanel()
const { showSessionFileList } = useSessionFileList()
const { hideFilePanel } = useFilePanel()

// Event IDs are unique in the backend stream and are the frontend's idempotency key.
const seenEventIds = new Set<string>();

// Create initial state factory
const createInitialState = () => ({
  inputMessage: '',
  isLoading: false,
  isWaitingForInput: false,
  sessionId: undefined as string | undefined,
  messages: [] as Message[],
  toolPanelSize: 0,
  realTime: true,
  follow: true,
  title: t('New Chat'),
  plan: undefined as PlanEventData | undefined,
  lastNoMessageTool: undefined as ToolContent | undefined,
  lastMessageTool: undefined as ToolContent | undefined,
  lastTool: undefined as ToolContent | undefined,
  lastEventId: undefined as string | undefined,
  cancelCurrentChat: null as (() => void) | null,
  streamingMessageContent: null as MessageContent | null,
  currentThinkingText: null as string | null,
  streamInterrupted: false,
  streamTerminalEvent: false,
  attachments: [] as FileInfo[],
  shareMode: 'private' as 'private' | 'public', // Default to private mode
  linkCopied: false,
  sharingLoading: false // Loading state for share operations
});

// Create reactive state
const state = reactive(createInitialState());

// Destructure refs from reactive state
const {
  inputMessage,
  isLoading,
  isWaitingForInput,
  sessionId,
  messages,
  realTime,
  follow,
  title,
  plan,
  lastNoMessageTool,
  lastTool,
  lastEventId,
  cancelCurrentChat,
  streamingMessageContent,
  currentThinkingText,
  streamInterrupted,
  streamTerminalEvent,
  attachments,
  shareMode,
  linkCopied,
  sharingLoading
} = toRefs(state);

// Flat ordered list of all non-message tools — used for panel navigation
const allTools = computed<ToolContent[]>(() => {
  const tools: ToolContent[] = []
  for (const msg of messages.value) {
    if (msg.type === 'tool') {
      const tool = msg.content as ToolContent
      if (tool.name !== 'message') tools.push(tool)
    } else if (msg.type === 'step') {
      const step = msg.content as StepContent
      for (const tool of step.tools) {
        if (tool.name !== 'message') tools.push(tool)
      }
    }
  }
  return tools
})

// Non-state refs that don't need reset
const toolPanel = ref<InstanceType<typeof ToolPanel>>()
const simpleBarRef = ref<InstanceType<typeof SimpleBar>>();

// Reset all refs to their initial values
const resetState = () => {
  seenEventIds.clear();
  _chunkBuffer = '';
  if (_chunkRafId !== null) {
    cancelAnimationFrame(_chunkRafId);
    _chunkRafId = null;
  }
  // Cancel any existing chat connection
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
  }

  // Reset reactive state to initial values
  Object.assign(state, createInitialState());
};

// RAF-debounced scroll — called after chunk flushes, not on every token
let scrollRafId: number | null = null;
const scheduleScroll = () => {
  if (scrollRafId !== null) return;
  scrollRafId = requestAnimationFrame(() => {
    scrollRafId = null;
    if (follow.value) simpleBarRef.value?.scrollToBottom();
  });
};

// Watch only for structural message array changes (new messages added),
// not deep mutations. Streaming content changes are handled by scheduleScroll.
watch(() => messages.value.length, async () => {
  await nextTick();
  if (follow.value) simpleBarRef.value?.scrollToBottom();
});



const getLastStep = (): StepContent | undefined => {
  return messages.value.filter(message => message.type === 'step').pop()?.content as StepContent;
}

// Chunk buffer — accumulates tokens between RAF flushes
let _chunkBuffer = '';
let _chunkRafId: number | null = null;

const _flushChunkBuffer = () => {
  _chunkRafId = null;
  if (_chunkBuffer && streamingMessageContent.value) {
    streamingMessageContent.value.content += _chunkBuffer;
    _chunkBuffer = '';
    scheduleScroll();
  }
};

// Handle streaming message chunk event
const handleMessageChunkEvent = (chunkData: MessageChunkEventData) => {
  if (!streamingMessageContent.value) {
    // First chunk — push a new assistant message and hold a reference to its content
    const content: MessageContent = {
      content: chunkData.content,
      event_id: chunkData.event_id,
      timestamp: chunkData.timestamp,
      isStreaming: true,
    };
    messages.value.push({
      type: 'assistant',
      content,
    });
    streamingMessageContent.value = content;
    scheduleScroll();
    return;
  }

  if (chunkData.done) {
    // Flush any remaining buffered text immediately
    if (_chunkRafId !== null) {
      cancelAnimationFrame(_chunkRafId);
      _chunkRafId = null;
    }
    if (_chunkBuffer && streamingMessageContent.value) {
      streamingMessageContent.value.content += _chunkBuffer;
      _chunkBuffer = '';
    }
    // Mark streaming done so ChatMessage switches to full markdown render
    if (streamingMessageContent.value) {
      (streamingMessageContent.value as any).isStreaming = false;
    }
    streamingMessageContent.value = null;
    scheduleScroll();
    return;
  }

  // Buffer the incoming token and flush on next animation frame
  _chunkBuffer += chunkData.content;
  if (_chunkRafId === null) {
    _chunkRafId = requestAnimationFrame(_flushChunkBuffer);
  }
}

// Handle message event
const handleMessageEvent = (messageData: MessageEventData) => {
  // Per-step evidence is already represented by the step/tool panel and is
  // consumed by summarization. Do not add a second assistant bubble for it.
  if (messageData.source === 'step_result') return;

  // Progress notifications belong inside the active step, matching the
  // Manus-style timeline. Keep a bubble only as a safe fallback when the
  // corresponding step event is unavailable during a partial replay.
  if (messageData.source === 'notification') {
    const targetStep = getLastStep();
    if (targetStep) {
      targetStep.notifications ??= [];
      if (!targetStep.notifications.includes(messageData.content)) {
        targetStep.notifications.push(messageData.content);
      }
      return;
    }
  }

  messages.value.push({
    type: messageData.role,
    content: {
      ...messageData,
      event_id: messageData.event_id,
    } as MessageContent,
  });

  if (messageData.attachments?.length > 0) {
    messages.value.push({
      type: 'attachments',
      content: {
        ...messageData
      } as AttachmentsContent,
    });
  }
}

// Handle tool event. A tool call has two lifecycle events (calling/called),
// and calls can be returned in parallel. Reconcile by tool_call_id, not by the
// last tool received, so an out-of-order called event cannot create a duplicate.
const findToolByCallId = (toolCallId: string): ToolContent | undefined => {
  for (const message of messages.value) {
    if (message.type === 'tool') {
      const tool = message.content as ToolContent;
      if (tool.tool_call_id === toolCallId) return tool;
    }
    if (message.type === 'step') {
      const step = message.content as StepContent;
      const tool = step.tools.find(item => item.tool_call_id === toolCallId);
      if (tool) return tool;
    }
  }
  return undefined;
};

const handleToolEvent = (toolData: ToolEventData) => {
  if (toolData.status === 'calling') {
    currentThinkingText.value = t(TOOL_FUNCTION_MAP[toolData.function] || toolData.function);
  } else {
    currentThinkingText.value = null;
  }

  const lastStep = getLastStep();
  const toolContent: ToolContent = {
    ...toolData,
    chart: toolData.content?.chart ?? null,
  };
  const existingTool = findToolByCallId(toolContent.tool_call_id);

  if (existingTool) {
    // Preserve calling args when the called event omits them.
    const savedArgs = existingTool.args;
    Object.assign(existingTool, toolContent);
    if (!toolContent.args || Object.keys(toolContent.args).length === 0) {
      existingTool.args = savedArgs;
    }
    lastTool.value = existingTool;
  } else {
    if (lastStep?.status === 'running') {
      lastStep.tools.push(toolContent);
    } else {
      messages.value.push({
        type: 'tool',
        content: toolContent,
      });
    }
    lastTool.value = toolContent;
  }
  if (toolContent.name !== 'message') {
    lastNoMessageTool.value = existingTool || toolContent;
  }
}

// Sync a step's status into plan.value.steps so PlanPanel stays up-to-date.
// Uses positional matching (first-pending / first-running) instead of ID matching
// because the planner LLM may regenerate step IDs after each plan update.
const syncStepToPlan = (status: string) => {
  if (!plan.value) return;
  if (status === 'running') {
    // Mark the first pending step as running
    const pendingStep = plan.value.steps.find(s => s.status === 'pending');
    if (pendingStep) pendingStep.status = 'running';
  } else if (status === 'completed') {
    // Mark the first running step as completed
    const runningStep = plan.value.steps.find(s => s.status === 'running');
    if (runningStep) runningStep.status = 'completed';
  } else if (status === 'failed') {
    // Mark the first running step as failed
    const runningStep = plan.value.steps.find(s => s.status === 'running');
    if (runningStep) runningStep.status = 'failed';
  }
}

// Handle step event. Step IDs are stable for a step's lifecycle, so do not
// update whichever step happens to be last after a replay or plan update.
const handleStepEvent = (stepData: StepEventData) => {
  const matchingMessage = messages.value.find(message => {
    if (message.type !== 'step') return false;
    return (message.content as StepContent).id === stepData.id;
  });
  const matchingStep = matchingMessage?.content as StepContent | undefined;

  if (stepData.status === 'running') {
    if (!matchingStep) {
      messages.value.push({
        type: 'step',
        content: {
          ...stepData,
          tools: []
        } as StepContent,
      });
    } else {
      matchingStep.status = 'running';
    }
    syncStepToPlan('running');
  } else if (stepData.status === 'completed' || stepData.status === 'failed' || stepData.status === 'skipped') {
    if (matchingStep) {
      matchingStep.status = stepData.status;
    }
    if (stepData.status === 'failed') isLoading.value = false;
    syncStepToPlan(stepData.status === 'skipped' ? 'completed' : stepData.status);
  }
}

// Handle error event
const handleErrorEvent = (errorData: ErrorEventData) => {
  isLoading.value = false;
  messages.value.push({
    type: 'assistant',
    content: {
      content: errorData.error,
      timestamp: errorData.timestamp
    } as MessageContent,
  });
}

// Handle title event
const handleTitleEvent = (titleData: TitleEventData) => {
  title.value = titleData.title;
}

// Handle plan event
const handlePlanEvent = (planData: PlanEventData) => {
  plan.value = planData;
}

const markPlanStoppedLocally = () => {
  if (!plan.value) return;
  plan.value.status = 'completed';
  for (const step of plan.value.steps) {
    if (step.status === 'pending' || step.status === 'running') {
      step.status = 'skipped';
    }
  }
}

// Main event handler function
const handleEvent = (event: AgentSSEEvent) => {
  const eventId = (event.data as { event_id?: string } | undefined)?.event_id;
  if (eventId) {
    if (seenEventIds.has(eventId)) return;
    seenEventIds.add(eventId);
  }

  if (event.event === 'message') {
    // The MessageEvent is the authoritative, persisted form of this response.
    // Remove any streaming bubble that preceded it — whether still in progress
    // or just completed (done=true already received) — to prevent double-bubble.
    if (streamingMessageContent.value) {
      // Stream still active — drop the partial bubble before replacing.
      const lastIdx = messages.value.length - 1;
      if (lastIdx >= 0 && messages.value[lastIdx].type === 'assistant') {
        messages.value.splice(lastIdx, 1);
      }
      streamingMessageContent.value = null;
    } else if (messages.value.length > 0) {
      // Stream already completed (done=true received). The bubble is still in
      // messages with isStreaming=false; remove it so MessageEvent takes its place.
      const last = messages.value[messages.value.length - 1];
      if (last.type === 'assistant' && 'isStreaming' in (last.content as any)) {
        messages.value.splice(messages.value.length - 1, 1);
      }
    }
    handleMessageEvent(event.data as MessageEventData);
  } else if (event.event === 'message_chunk') {
    handleMessageChunkEvent(event.data as MessageChunkEventData);
  } else if (event.event === 'tool') {
    handleToolEvent(event.data as ToolEventData);
  } else if (event.event === 'step') {
    handleStepEvent(event.data as StepEventData);
  } else if (event.event === 'done') {
    streamTerminalEvent.value = true;
    streamInterrupted.value = false;
    isLoading.value = false;
  } else if (event.event === 'wait') {
    streamTerminalEvent.value = true;
    streamInterrupted.value = false;
    isLoading.value = false;
    isWaitingForInput.value = true;
  } else if (event.event === 'error') {
    streamTerminalEvent.value = true;
    streamInterrupted.value = false;
    handleErrorEvent(event.data as ErrorEventData);
  } else if (event.event === 'title') {
    handleTitleEvent(event.data as TitleEventData);
  } else if (event.event === 'plan') {
    handlePlanEvent(event.data as PlanEventData);
  }
  if (eventId) lastEventId.value = eventId;
}

const handleSubmit = () => {
  chat(inputMessage.value, attachments.value);
}

const chat = async (message: string = '', files: FileInfo[] = []) => {
  if (!sessionId.value) return;

  // Cancel any existing chat connection before starting a new one
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
    cancelCurrentChat.value = null;
  }

  if (message.trim()) {
    // Add user message to conversation list
    messages.value.push({
      type: 'user',
      content: {
        content: message,
        timestamp: Math.floor(Date.now() / 1000)
      } as MessageContent,
    });
  }

  if (files.length > 0) {
    messages.value.push({
      type: 'attachments',
      content: {
        role: 'user',
        attachments: files
      } as AttachmentsContent,
    });
  }

  // Automatically enable follow mode when sending message
  follow.value = true;

  // Clear input field and attachments
  inputMessage.value = '';
  attachments.value = [];
  isLoading.value = true;
  isWaitingForInput.value = false;
  streamInterrupted.value = false;
  streamTerminalEvent.value = false;

  try {
    // Use the split event handler function and store the cancel function
    cancelCurrentChat.value = await agentApi.chatWithSession(
      sessionId.value,
      message,
      lastEventId.value,
      files.map((file: FileInfo) => ({
        file_id: file.file_id,
        filename: file.filename,
        content_type: file.content_type,
        size: file.size,
      })),
      {
        onOpen: () => {
          console.log('Chat opened');
          isLoading.value = true;
          streamInterrupted.value = false;
          streamTerminalEvent.value = false;
        },

        onMessage: ({ event, data }) => {
          handleEvent({
            event: event as AgentSSEEvent['event'],
            data: data as AgentSSEEvent['data']
          });
        },
        onClose: () => {
          console.log('Chat closed');
          if (!streamTerminalEvent.value && isLoading.value) {
            streamInterrupted.value = true;
          }
          isLoading.value = false;
          // Clear the cancel function when connection is closed normally
          if (cancelCurrentChat.value) {
            cancelCurrentChat.value = null;
          }
        },
        onError: (error) => {
          console.error('Chat error:', error);
          if (!streamTerminalEvent.value) {
            streamInterrupted.value = true;
          }
          isLoading.value = false;
          // Clear the cancel function when there's an error
          if (cancelCurrentChat.value) {
            cancelCurrentChat.value = null;
          }
        }
      }
    );
  } catch (error) {
    console.error('Chat error:', error);
    isLoading.value = false;
    cancelCurrentChat.value = null;
  }
}

const restoreSession = async () => {
  if (!sessionId.value) {
    showErrorToast(t('Session not found'));
    return;
  }
  const session = await agentApi.getSession(sessionId.value);
  // Initialize share mode based on session state
  shareMode.value = session.is_shared ? 'public' : 'private';
  realTime.value = false;
  for (const event of session.events) {
    handleEvent(event);
  }
  realTime.value = true;
  // Only reconnect to live stream if the session is genuinely still running.
  // If a 'done' event is already in the persisted history, the task has completed
  // even if session.status hasn't been updated yet (update_status runs after DoneEvent
  // is emitted, so there's a brief window where status=RUNNING but done=true).
  // Reconnecting in that window would replay the summary text and show a false
  // "Thinking..." indicator.
  const hasDoneEvent = session.events.some((e: { event: string }) => e.event === 'done');
  if (!hasDoneEvent && (session.status === SessionStatus.RUNNING || session.status === SessionStatus.PENDING)) {
    await chat();
  }
  agentApi.clearUnreadMessageCount(sessionId.value);
}



onBeforeRouteUpdate((to, _, next) => {
  toolPanel.value?.hideToolPanel();
  hideFilePanel();
  resetState();
  if (to.params.sessionId) {
    messages.value = [];
    sessionId.value = String(to.params.sessionId) as string;
    restoreSession();
  }
  next();
})

// Initialize active conversation
onMounted(() => {
  hideFilePanel();
  const routeParams = router.currentRoute.value.params;
  if (routeParams.sessionId) {
    // If sessionId is included in URL, use it directly
    sessionId.value = String(routeParams.sessionId) as string;
    // Get initial message from history.state
    const message = history.state?.message;
    const files: FileInfo[] = history.state?.files;
    history.replaceState({}, document.title);
    if (message) {
      chat(message, files);
    } else {
      restoreSession();
    }
  }


});

onUnmounted(() => {
  if (cancelCurrentChat.value) {
    cancelCurrentChat.value();
    cancelCurrentChat.value = null;
  }
})

const isLastNoMessageTool = (tool: ToolContent) => {
  return tool.tool_call_id === lastNoMessageTool.value?.tool_call_id;
}

const isLiveTool = (tool: ToolContent) => {
  if (tool.status === 'calling') {
    return true;
  }
  if (!isLastNoMessageTool(tool)) {
    return false;
  }
  if (tool.timestamp > Date.now() - 5 * 60 * 1000) {
    return true;
  }
  return false;
}

const handleToolClick = (tool: ToolContent) => {
  realTime.value = false;
  if (sessionId.value) {
    toolPanel.value?.showToolPanel(tool, isLiveTool(tool));
  }
}

const jumpToRealTime = () => {
  realTime.value = true;
  if (lastNoMessageTool.value) {
    toolPanel.value?.showToolPanel(lastNoMessageTool.value, isLiveTool(lastNoMessageTool.value));
  }
}

const handleFollow = () => {
  follow.value = true;
  simpleBarRef.value?.scrollToBottom();
}

const handleScroll = (_: Event) => {
  follow.value = simpleBarRef.value?.isScrolledToBottom() ?? false;
}

const handleStop = async () => {
  if (!sessionId.value || !isLoading.value) return;

  // Give immediate feedback; the backend cancellation below is authoritative
  // and publishes the terminal message/plan/done events for persistence.
  isLoading.value = false;
  isWaitingForInput.value = false;
  streamTerminalEvent.value = true;
  streamInterrupted.value = false;
  currentThinkingText.value = null;
  markPlanStoppedLocally();

  try {
    await agentApi.stopSession(sessionId.value);
  } catch (error) {
    console.error('Failed to stop agent:', error);
  }
}

const handleFileListShow = () => {
  showSessionFileList()
}

// Share functionality handlers
const handleShareModeChange = async (mode: 'private' | 'public') => {
  if (!sessionId.value || sharingLoading.value) return;
  
  // If mode is same as current, no need to call API
  if (shareMode.value === mode) {
    linkCopied.value = false;
    return;
  }
  
  try {
    sharingLoading.value = true;
    
    if (mode === 'public') {
      await agentApi.shareSession(sessionId.value);
    } else {
      await agentApi.unshareSession(sessionId.value);
    }
    
    shareMode.value = mode;
    linkCopied.value = false;
  } catch (error) {
    console.error('Error changing share mode:', error);
    showErrorToast(t('Failed to change sharing settings'));
  } finally {
    sharingLoading.value = false;
  }
}

const handleInstantShare = async () => {
  if (!sessionId.value) return;
  
  try {
    sharingLoading.value = true;
    await agentApi.shareSession(sessionId.value);
    shareMode.value = 'public';
    linkCopied.value = false;
  } catch (error) {
    console.error('Error sharing session:', error);
    showErrorToast(t('Failed to share session'));
  } finally {
    sharingLoading.value = false;
  }
}

const handleCopyLink = async () => {
  if (!sessionId.value) return;
  
  const shareUrl = `${window.location.origin}/share/${sessionId.value}`;
  
  try {
    const success = await copyToClipboard(shareUrl);
    
    if (success) {
      linkCopied.value = true;
      setTimeout(() => {
        linkCopied.value = false;
      }, 3000);
      showSuccessToast(t('Link copied to clipboard'));
    } else {
      showErrorToast(t('Failed to copy link'));
    }
  } catch (error) {
    console.error('Error copying share link:', error);
    showErrorToast(t('Failed to copy link'));
  }
}
</script>

<style scoped>
</style>

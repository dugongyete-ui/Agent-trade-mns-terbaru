<template>
  <Teleport to="body">
    <!-- Backdrop (mobile only) -->
    <Transition name="backdrop-fade">
      <div
        v-if="isShow"
        class="fixed inset-0 bg-black/50 z-[49] sm:hidden"
        @click="hideToolPanel"
      />
    </Transition>

    <!-- Panel -->
    <Transition name="panel-slide">
      <div
        v-if="isShow"
        class="fixed top-0 right-0 h-full w-full sm:w-[520px] lg:w-[600px] z-50 flex flex-col sm:py-3 sm:pr-4 sm:pl-1"
      >
        <ToolPanelContent
          v-if="toolContent"
          :sessionId="sessionId"
          :realTime="realTime"
          :toolContent="toolContent"
          :live="live"
          :isShare="isShare"
          :currentIndex="currentIndex"
          :totalTools="visibleTools.length"
          :hasPrev="currentIndex > 0"
          :hasNext="currentIndex < visibleTools.length - 1"
          @hide="hideToolPanel"
          @jumpToRealTime="onJumpToRealTime"
          @prevTool="navigatePrev"
          @nextTool="navigateNext"
        />
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import type { ToolContent } from '../types/message'
import ToolPanelContent from './ToolPanelContent.vue'
import { eventBus } from '../utils/eventBus'
import { EVENT_SHOW_FILE_PANEL, EVENT_SHOW_TOOL_PANEL } from '../constants/event'

const props = defineProps<{
  sessionId?: string
  realTime: boolean
  isShare: boolean
  allTools?: ToolContent[]
}>()

const emit = defineEmits<{
  (e: 'jumpToRealTime'): void
}>()

const isShow = ref(false)
const live = ref(false)
const toolContent = ref<ToolContent>()
const currentIndex = ref(-1)

const visibleTools = computed(() => props.allTools ?? [])

const showToolPanel = (content: ToolContent, isLive: boolean = false) => {
  eventBus.emit(EVENT_SHOW_TOOL_PANEL)
  toolContent.value = content
  isShow.value = true
  live.value = isLive
  const idx = visibleTools.value.findIndex(t => t.tool_call_id === content.tool_call_id)
  currentIndex.value = idx >= 0 ? idx : visibleTools.value.length - 1
}

const hideToolPanel = () => {
  isShow.value = false
}

const onJumpToRealTime = () => {
  emit('jumpToRealTime')
}

const navigatePrev = () => {
  if (currentIndex.value > 0) {
    currentIndex.value--
    const tool = visibleTools.value[currentIndex.value]
    if (tool) {
      toolContent.value = tool
      live.value = false
    }
  }
}

const navigateNext = () => {
  if (currentIndex.value < visibleTools.value.length - 1) {
    currentIndex.value++
    const tool = visibleTools.value[currentIndex.value]
    if (tool) {
      toolContent.value = tool
      live.value = false
    }
  }
}

onMounted(() => {
  eventBus.on(EVENT_SHOW_FILE_PANEL, hideToolPanel)
})

onUnmounted(() => {
  eventBus.off(EVENT_SHOW_FILE_PANEL, hideToolPanel)
})

defineExpose({
  showToolPanel,
  hideToolPanel,
  isShow
})
</script>

<style scoped>
.backdrop-fade-enter-active,
.backdrop-fade-leave-active {
  transition: opacity 0.2s ease;
}
.backdrop-fade-enter-from,
.backdrop-fade-leave-to {
  opacity: 0;
}

.panel-slide-enter-active,
.panel-slide-leave-active {
  transition: transform 0.28s cubic-bezier(0.4, 0, 0.2, 1);
}
.panel-slide-enter-from,
.panel-slide-leave-to {
  transform: translateX(100%);
}
</style>

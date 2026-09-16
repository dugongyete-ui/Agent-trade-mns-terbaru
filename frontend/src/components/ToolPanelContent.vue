<template>
  <div class="bg-[var(--background-gray-main)] sm:bg-[var(--background-menu-white)] sm:rounded-[22px] shadow-[0px_0px_24px_0px_rgba(0,0,0,0.16)] border border-black/8 dark:border-[var(--border-dark)] flex flex-col h-full w-full overflow-hidden">

    <!-- Header -->
    <div class="flex-shrink-0 flex items-center gap-2 px-4 pt-4 pb-2">
      <div class="text-[var(--text-primary)] text-lg font-semibold flex-1">{{ $t('Dzeck Computer') }}</div>
      <button
        class="w-7 h-7 relative rounded-md inline-flex items-center justify-center gap-2.5 cursor-pointer hover:bg-[var(--fill-tsp-gray-main)]"
        @click="hide">
        <Minimize2 class="w-5 h-5 text-[var(--icon-tertiary)]" />
      </button>
    </div>

    <!-- Tool info -->
    <div v-if="toolInfo" class="flex-shrink-0 flex items-center gap-2 px-4 pb-3">
      <div class="w-[40px] h-[40px] bg-[var(--fill-tsp-gray-main)] rounded-lg flex items-center justify-center flex-shrink-0">
        <component :is="toolInfo.icon" :size="28" />
      </div>
      <div class="flex-1 flex flex-col gap-1 min-w-0">
        <div class="text-[12px] text-[var(--text-tertiary)]">
          {{ $t('Dzeck is using') }}
          <span class="text-[var(--text-secondary)]">{{ toolInfo.name }}</span>
        </div>
        <div
          :title="`${toolInfo.function} ${toolInfo.functionArg}`"
          class="max-w-[100%] w-[max-content] truncate text-[13px] rounded-full inline-flex items-center px-[10px] py-[3px] border border-[var(--border-light)] bg-[var(--fill-tsp-gray-main)] text-[var(--text-secondary)]">
          {{ toolInfo.function }}
          <span class="flex-1 min-w-0 px-1 ml-1 text-[12px] font-mono max-w-full text-ellipsis overflow-hidden whitespace-nowrap text-[var(--text-tertiary)]">
            <code>{{ toolInfo.functionArg }}</code>
          </span>
        </div>
      </div>
    </div>

    <!-- Tool view — fills all remaining vertical space -->
    <div class="flex-1 min-h-0 mx-4 flex flex-col rounded-[12px] overflow-hidden bg-[var(--background-gray-main)] border border-[var(--border-dark)] shadow-[0px_4px_32px_0px_rgba(0,0,0,0.12)]">
      <component
        v-if="toolInfo"
        :is="toolInfo.view"
        :live="live"
        :sessionId="sessionId"
        :toolContent="toolContent"
        :isShare="isShare"
        class="flex-1 min-h-0 flex flex-col"
      />
    </div>

    <!-- Navigation bar -->
    <div class="flex-shrink-0 flex items-center justify-between px-4 py-3 gap-2">

      <!-- Prev button -->
      <button
        @click="emit('prevTool')"
        :disabled="!hasPrev"
        :class="[
          'w-9 h-9 rounded-full inline-flex items-center justify-center border transition-colors',
          hasPrev
            ? 'border-[var(--border-dark)] bg-[var(--background-card)] hover:brightness-110 shadow-sm cursor-pointer'
            : 'border-[var(--border-light)] bg-transparent opacity-30 cursor-not-allowed'
        ]"
      >
        <ChevronLeft :size="18" class="text-[var(--text-primary)]" />
      </button>

      <!-- Center: position + jump to live -->
      <div class="flex items-center gap-2 flex-1 justify-center">
        <span
          v-if="totalTools > 0"
          class="text-xs text-[var(--text-tertiary)] font-medium tabular-nums select-none"
        >
          {{ currentIndex + 1 }}&thinsp;/&thinsp;{{ totalTools }}
        </span>
        <button
          v-if="!realTime"
          @click="jumpToRealTime"
          class="h-8 px-3 border border-[var(--border-dark)] flex items-center gap-1.5 bg-[var(--background-card)] hover:brightness-110 shadow-[0px_5px_16px_0px_var(--shadow-S),0px_0px_1.25px_0px_var(--shadow-S)] rounded-full cursor-pointer transition-colors"
        >
          <PlayIcon :size="14" class="text-[var(--text-primary)]" />
          <span class="text-[var(--text-primary)] text-sm font-medium">{{ $t('Jump to live') }}</span>
        </button>
      </div>

      <!-- Next button -->
      <button
        @click="emit('nextTool')"
        :disabled="!hasNext"
        :class="[
          'w-9 h-9 rounded-full inline-flex items-center justify-center border transition-colors',
          hasNext
            ? 'border-[var(--border-dark)] bg-[var(--background-card)] hover:brightness-110 shadow-sm cursor-pointer'
            : 'border-[var(--border-light)] bg-transparent opacity-30 cursor-not-allowed'
        ]"
      >
        <ChevronRight :size="18" class="text-[var(--text-primary)]" />
      </button>

    </div>
  </div>
</template>

<script setup lang="ts">
import { toRef } from 'vue';
import { Minimize2, PlayIcon, ChevronLeft, ChevronRight } from 'lucide-vue-next';
import type { ToolContent } from '@/types/message';
import { useToolInfo } from '@/composables/useTool';

const props = defineProps<{
  sessionId?: string;
  realTime: boolean;
  toolContent: ToolContent;
  live: boolean;
  isShare: boolean;
  currentIndex: number;
  totalTools: number;
  hasPrev: boolean;
  hasNext: boolean;
}>();

const { toolInfo } = useToolInfo(toRef(props, 'toolContent'));

const emit = defineEmits<{
  (e: 'jumpToRealTime'): void;
  (e: 'hide'): void;
  (e: 'prevTool'): void;
  (e: 'nextTool'): void;
}>();

const hide = () => emit('hide');
const jumpToRealTime = () => emit('jumpToRealTime');
</script>

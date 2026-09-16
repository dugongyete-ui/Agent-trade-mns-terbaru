<template>
  <!-- message_notify_user/message_ask_user are persisted as assistant messages;
       never render their underlying tool lifecycle as a second prose block. -->
  <template v-if="tool.name === 'message'"></template>
  <div v-else-if="toolInfo" class="flex items-center group gap-2">
    <div class="flex-1 min-w-0">
      <div @click="handleClick"
        class="rounded-[10px] items-center gap-2 px-[10px] py-[5px] border border-[var(--border-light)] bg-[var(--fill-tsp-gray-main)] dark:bg-[var(--background-card)] dark:border-[var(--border-main)] inline-flex max-w-full clickable hover:bg-[var(--fill-tsp-gray-dark)] dark:hover:brightness-110">
        <div class="w-[16px] inline-flex items-center text-[var(--icon-tertiary)]">
          <component :is="toolInfo.icon" :size="21" />
        </div>
        <div class="flex-1 h-full min-w-0 flex">
          <div class="inline-flex items-center h-full rounded-full text-[14px] text-[var(--text-secondary)] max-w-[100%]">
            <div class="max-w-[100%] text-ellipsis overflow-hidden whitespace-nowrap text-[13px]"
              :title="`${toolInfo.function}${toolInfo.functionArg}`">
              <div class="flex items-center">
                {{ toolInfo.function
                }}<span
                  class="flex-1 min-w-0 rounded-[6px] px-1 ml-1 relative top-[0px] text-[12px] font-mono max-w-full text-ellipsis overflow-hidden whitespace-nowrap text-[var(--text-tertiary)]"><code>{{ toolInfo.functionArg }}</code></span>
              </div>
            </div>
          </div>
        </div>
        <!-- Status indicator -->
        <div class="flex items-center flex-shrink-0">
          <span v-if="tool.status === 'calling'" class="relative flex h-[6px] w-[6px]">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--icon-tertiary)] opacity-60" />
            <span class="relative inline-flex rounded-full h-[6px] w-[6px] bg-[var(--icon-tertiary)]" />
          </span>
          <svg v-else width="10" height="10" viewBox="0 0 10 10" fill="none">
            <circle cx="5" cy="5" r="4.5" fill="var(--fill-tsp-gray-dark)" />
            <path d="M2.8 5L4.2 6.4L7.2 3.6" stroke="var(--icon-tertiary)" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>
    </div>
    <div class="float-right transition text-[12px] text-[var(--text-tertiary)] invisible group-hover:visible">
      {{ relativeTime(tool.timestamp) }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { ToolContent } from "../types/message";
import { useToolInfo } from "../composables/useTool";
import { useRelativeTime } from "../composables/useTime";
const props = defineProps<{
  tool: ToolContent;
}>();

const emit = defineEmits<{
  (e: "click"): void;
}>();

const { relativeTime } = useRelativeTime();
const { toolInfo } = useToolInfo(ref(props.tool));

const handleClick = () => {
  emit("click");
};
</script>

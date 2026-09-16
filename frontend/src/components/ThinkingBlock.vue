<template>
  <div class="thinking-block w-full my-1" :class="{ 'mt-3': !hideHeader }">
    <!-- Header — always visible, clickable -->
    <div
      class="flex items-center gap-1.5 cursor-pointer select-none w-fit rounded-md px-1.5 py-0.5 -ml-1.5 transition-colors duration-200 hover:bg-[var(--fill-tsp-gray-main)]"
      @click="expanded = !expanded"
      :aria-expanded="expanded"
      role="button"
      tabindex="0"
      @keydown.enter.prevent="expanded = !expanded"
      @keydown.space.prevent="expanded = !expanded"
    >
      <span v-if="isStreaming" class="thinking-orb" aria-hidden="true"></span>
      <Brain v-else :size="14" class="text-[var(--icon-secondary)] flex-shrink-0" />
      <span
        class="text-[13px] leading-none transition-colors duration-200"
        :class="isStreaming
          ? 'thinking-shimmer text-[var(--text-secondary)]'
          : 'text-[var(--text-tertiary)]'"
      >
        {{ isStreaming ? t('Thinking') : t('Thought process') }}
      </span>
      <span
        v-if="!isStreaming && durationLabel"
        class="text-[11px] leading-none text-[var(--text-tertiary)] opacity-70"
      >{{ durationLabel }}</span>
      <svg
        xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        class="lucide w-3.5 h-3.5 text-[var(--icon-tertiary)] transition-transform duration-300 ease-in-out"
        :class="{ 'rotate-180': expanded }"
      >
        <path d="m6 9 6 6 6-6"></path>
      </svg>
    </div>

    <!-- Reasoning whisper: last live line of the reasoning, visible while
         streaming AND collapsed (Vibe-Trading-style). Hidden when expanded
         (full text already visible) or when the run finished. -->
    <div
      v-if="isStreaming && !expanded && whisper"
      class="whisper-line"
      aria-hidden="true"
    >{{ whisper }}</div>

    <!-- Body — smooth grid-rows collapse -->
    <div class="thinking-grid" :class="expanded ? 'open' : 'closed'" aria-live="polite">
      <div class="thinking-grid-inner">
        <div class="thinking-body" ref="bodyRef">
          <div class="thinking-rule" aria-hidden="true"></div>
          <div class="thinking-text whitespace-pre-wrap break-words">{{ content || '…' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed, onMounted, onBeforeUnmount } from 'vue';
import { Brain } from 'lucide-vue-next';
import { useI18n } from 'vue-i18n';

const props = defineProps<{
  content: string;
  isStreaming: boolean;
  startedAt?: number;
  hideHeader?: boolean;
}>();

const { t } = useI18n();

// Start expanded while live-streaming; start COLLAPSED for replayed blocks
// (page reload) so a long merged reasoning text doesn't blow open the page.
const expanded = ref(props.isStreaming);
const bodyRef = ref<HTMLElement | null>(null);
const userTouched = ref(false);
const endedAt = ref<number | null>(null);

// Collapse smoothly the moment thinking finishes (Claude-style),
// unless the user has interacted with the block already.
watch(
  () => props.isStreaming,
  (streaming, prev) => {
    if (prev && !streaming) {
      endedAt.value = Date.now() / 1000;
      if (!userTouched.value) expanded.value = false;
    } else if (streaming) {
      endedAt.value = null;
      userTouched.value = false;
    }
  },
  { immediate: true }
);

// While streaming and expanded, keep the tail of the reasoning in view.
watch(
  () => props.content,
  async () => {
    if (props.isStreaming && expanded.value && bodyRef.value && !userTouched.value) {
      await nextTick();
      bodyRef.value.scrollTop = bodyRef.value.scrollHeight;
    }
  }
);

const markUserTouched = () => { userTouched.value = true; };
onMounted(() => {
  bodyRef.value?.addEventListener('wheel', markUserTouched, { passive: true });
  bodyRef.value?.addEventListener('touchmove', markUserTouched, { passive: true });
});
onBeforeUnmount(() => {
  bodyRef.value?.removeEventListener('wheel', markUserTouched);
  bodyRef.value?.removeEventListener('touchmove', markUserTouched);
});

const durationLabel = computed(() => {
  if (props.isStreaming || !props.startedAt) return '';
  const end = endedAt.value ?? Date.now() / 1000;
  const secs = Math.max(1, Math.round(end - props.startedAt));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
});

// Tail of the live reasoning for the collapsed whisper line
const whisper = computed(() => {
  if (!props.isStreaming || !props.content) return '';
  const lines = props.content.split('\n').map((l) => l.trim()).filter(Boolean);
  const last = lines[lines.length - 1] || '';
  return last.length > 140 ? last.slice(-140) : last;
});
</script>

<style scoped>
/* Smooth height collapse via grid-template-rows trick (no max-height jump) */
.thinking-grid {
  display: grid;
  transition: grid-template-rows 0.35s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease;
}
.thinking-grid.open { grid-template-rows: 1fr; opacity: 1; }
.thinking-grid.closed { grid-template-rows: 0fr; opacity: 0; }
.thinking-grid-inner { overflow: hidden; min-height: 0; }

.thinking-body {
  display: flex;
  gap: 10px;
  max-height: 260px;
  overflow-y: auto;
  scrollbar-width: thin;
  padding: 8px 0 2px;
}

.thinking-rule {
  width: 2px;
  flex-shrink: 0;
  border-radius: 2px;
  background: linear-gradient(
    to bottom,
    var(--border-main),
    var(--border-dark)
  );
}

.thinking-text {
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-tertiary);
  min-width: 0;
}

/* Live one-line whisper of the reasoning while collapsed + streaming */
.whisper-line {
  margin-top: 4px;
  padding-left: 8px;
  font-size: 11.5px;
  line-height: 1.45;
  font-style: italic;
  color: var(--text-tertiary);
  opacity: 0.75;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  direction: rtl;
  text-align: left;
  animation: whisper-fade 0.5s ease;
}
@keyframes whisper-fade {
  from { opacity: 0; }
  to { opacity: 0.75; }
}

/* Pulsing orb shown while the model is thinking */
.thinking-orb {
  width: 12px;
  height: 12px;
  border-radius: 9999px;
  flex-shrink: 0;
  background: radial-gradient(circle at 35% 35%, var(--icon-primary), var(--icon-secondary));
  animation: thinking-pulse 1.4s ease-in-out infinite;
}
@keyframes thinking-pulse {
  0%, 100% { transform: scale(1); opacity: 0.75; }
  50% { transform: scale(1.25); opacity: 1; }
}

/* Shimmering label while streaming */
.thinking-shimmer {
  background: linear-gradient(
    90deg,
    var(--text-tertiary) 0%,
    var(--text-primary) 50%,
    var(--text-tertiary) 100%
  );
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: thinking-shimmer-move 2s linear infinite;
}
@keyframes thinking-shimmer-move {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>

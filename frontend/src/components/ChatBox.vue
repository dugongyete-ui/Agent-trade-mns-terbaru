<template>
    <div class="pb-3 relative bg-[var(--background-gray-main)]">
        <div
            class="flex flex-col gap-3 rounded-[22px] transition-all relative bg-[var(--fill-input-chat)] py-3 max-h-[300px] shadow-[0px_12px_32px_0px_rgba(0,0,0,0.08)] border border-black/8 dark:border-[var(--border-dark)]">
            <ChatBoxFiles ref="chatBoxFileListRef" :attachments="attachments" />
            <div class="overflow-y-auto pl-4 pr-2">
                <textarea
                    ref="textareaRef"
                    class="flex rounded-md border-input focus-visible:outline-none focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 flex-1 bg-transparent p-0 pt-[1px] border-0 focus-visible:ring-0 focus-visible:ring-offset-0 w-full placeholder:text-[var(--text-disable)] text-[15px] shadow-none resize-none"
                    :rows="rows" :value="modelValue"
                    @input="onInput"
                    @compositionstart="isComposing = true" @compositionend="isComposing = false"
                    @keydown="handleKeydown"
                    :placeholder="t('Give Dzeck a task to work on...')"
                    :style="{ minHeight: '46px', height: textareaHeight }"></textarea>
            </div>
            <footer class="flex flex-row justify-between w-full px-3">
                <div class="flex gap-2 pr-2 items-center">
                    <button @click="uploadFile"
                        class="rounded-full border border-[var(--border-main)] inline-flex items-center justify-center gap-1 clickable cursor-pointer text-xs text-[var(--text-secondary)] hover:bg-[var(--fill-tsp-gray-main)] w-8 h-8 p-0 data-[popover-trigger]:bg-[var(--fill-tsp-gray-main)] shrink-0"
                        aria-expanded="false" aria-haspopup="dialog">
                        <Paperclip :size="16" />
                    </button>
                    <!-- Thinking mode ON/OFF — runtime toggle, applies to the next AI call -->
                    <button @click="toggleThinking" :disabled="thinkingLoading"
                        class="rounded-full border inline-flex items-center justify-center gap-1 clickable cursor-pointer text-xs w-8 h-8 p-0 shrink-0 transition-colors disabled:opacity-50"
                        :class="thinkingOn
                            ? 'border-transparent bg-[var(--Button-primary-black)] text-[var(--text-onblack)]'
                            : 'border-[var(--border-main)] text-[var(--text-secondary)] hover:bg-[var(--fill-tsp-gray-main)]'"
                        :title="t('Thinking mode') + ': ' + (thinkingOn ? t('On') : t('Off'))">
                        <Brain :size="16" />
                    </button>
                </div>
                <div class="flex gap-2">
                    <button v-if="!isRunning || sendEnabled || hideStopButton"
                        class="whitespace-nowrap text-sm font-medium focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 text-primary-foreground hover:bg-primary/90 p-0 w-8 h-8 rounded-full flex items-center justify-center transition-colors hover:opacity-90"
                        :class="!sendEnabled ? 'cursor-not-allowed bg-[var(--fill-tsp-white-dark)]' : 'cursor-pointer bg-[var(--Button-primary-black)]'"
                        @click="handleSubmit">
                        <SendIcon :disabled="!sendEnabled" />
                    </button>
                    <button v-else-if="!hideStopButton" @click="handleStop"
                        class="inline-flex items-center justify-center whitespace-nowrap text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring bg-[var(--Button-primary-black)] text-[var(--text-onblack)] gap-[4px] hover:opacity-90 rounded-full p-0 w-8 h-8">
                        <div class="w-[10px] h-[10px] bg-[var(--icon-onblack)] rounded-[2px]">
                        </div>
                    </button>
                </div>
            </footer>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch, computed, onMounted } from 'vue';
import SendIcon from './icons/SendIcon.vue';
import { useI18n } from 'vue-i18n';
import ChatBoxFiles from './ChatBoxFiles.vue';
import { Paperclip, Brain } from 'lucide-vue-next';
import type { FileInfo } from '../api/file';
import { getThinkingMode, setThinkingMode } from '../api/config';
import { showSuccessToast, showErrorToast } from '../utils/toast';

const { t } = useI18n();
const hasTextInput = ref(false);
const isComposing = ref(false);
const chatBoxFileListRef = ref();
const textareaRef = ref<HTMLTextAreaElement>();
const textareaHeight = ref('46px');

const autoResize = () => {
    const el = textareaRef.value;
    if (!el) return;
    el.style.height = '46px';
    textareaHeight.value = Math.min(el.scrollHeight, 240) + 'px';
};

const props = defineProps<{
    modelValue: string;
    rows: number;
    isRunning: boolean;
    attachments: FileInfo[];
    hideStopButton?: boolean;
    allowSendFilesOnly?: boolean;
}>();

const sendEnabled = computed(() => {
    const hasFiles = (props.attachments?.length ?? 0) > 0;
    const allUploaded = chatBoxFileListRef.value?.isAllUploaded ?? true;
    if (props.allowSendFilesOnly) {
        return hasTextInput.value || (hasFiles && allUploaded);
    }
    return hasTextInput.value && (!hasFiles || allUploaded);
});

const emit = defineEmits<{
    (e: 'update:modelValue', value: string): void;
    (e: 'submit'): void;
    (e: 'stop'): void;
}>();

const onInput = (event: Event) => {
    emit('update:modelValue', (event.target as HTMLTextAreaElement).value);
    autoResize();
};

const handleKeydown = (event: KeyboardEvent) => {
    // Enter without any modifier → send
    if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
        if (isComposing.value) return;
        event.preventDefault();
        if (sendEnabled.value) {
            handleSubmit();
        }
        return;
    }
    // Shift+Enter → new line (let browser insert naturally, do nothing here)
};

const handleSubmit = () => {
    if (!sendEnabled.value) return;
    emit('submit');
};

const handleStop = () => {
    emit('stop');
};

const uploadFile = () => {
    chatBoxFileListRef.value?.uploadFile();
};

// ── Thinking mode toggle ────────────────────────────────────────────────
// Source of truth is the backend (GET /config/thinking, default from the
// THINKING_MODE env). The choice is global: it applies to every new AI call
// from any session. Cached in localStorage only so the button can show a
// state instantly before the backend answers.
const THINKING_LS_KEY = 'dzeck.thinkingMode';
const thinkingOn = ref(localStorage.getItem(THINKING_LS_KEY) !== 'off');
const thinkingLoading = ref(false);

onMounted(async () => {
    try {
        const mode = await getThinkingMode();
        thinkingOn.value = mode === 'on';
        localStorage.setItem(THINKING_LS_KEY, mode);
    } catch {
        // Not logged in yet or endpoint unavailable — keep the cached value.
    }
});

const toggleThinking = async () => {
    if (thinkingLoading.value) return;
    const next = !thinkingOn.value;
    thinkingLoading.value = true;
    try {
        const mode = await setThinkingMode(next);
        thinkingOn.value = mode === 'on';
        localStorage.setItem(THINKING_LS_KEY, mode);
        showSuccessToast(t('Thinking mode') + ': ' + (thinkingOn.value ? t('On') : t('Off')));
    } catch (error) {
        console.error('Failed to toggle thinking mode:', error);
        showErrorToast(t('Failed to change thinking mode'));
    } finally {
        thinkingLoading.value = false;
    }
};

watch(() => props.modelValue, (value) => {
    hasTextInput.value = value.trim() !== '';
    if (value === '') {
        textareaHeight.value = '46px';
    }
});
</script>

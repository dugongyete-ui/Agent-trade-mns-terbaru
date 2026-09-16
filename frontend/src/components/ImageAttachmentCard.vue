<template>
    <div class="relative w-full">
        <!-- Thumbnail -->
        <div class="w-full h-40 overflow-hidden rounded-t-[11px] bg-[var(--background-gray-main)] flex items-center justify-center">
            <img
                v-if="imageUrl && !error"
                :src="imageUrl"
                :alt="file.filename ?? ''"
                class="w-full h-full object-cover"
                @error="error = true"
            />
            <div v-else-if="loading" class="w-6 h-6 border-2 border-[var(--border-main)] border-t-transparent rounded-full animate-spin" />
            <span v-else class="text-[var(--text-tertiary)] text-xs uppercase font-mono">
                {{ file.filename?.split('.').pop() ?? 'img' }}
            </span>
        </div>
        <!-- File name bar -->
        <div class="flex items-center gap-1.5 px-2 py-2 border-t border-[var(--border-light)]">
            <div class="text-sm text-[var(--text-primary)] text-ellipsis overflow-hidden whitespace-nowrap flex-1 min-w-0">
                {{ file.filename }}
            </div>
            <div class="shrink-0 text-xs text-[var(--text-tertiary)] uppercase font-mono">
                {{ file.filename?.split('.').pop() }}
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { getFileDownloadUrl } from '../api/file';
import type { FileInfo } from '../api/file';

const props = defineProps<{ file: FileInfo }>();

const imageUrl = ref('');
const loading = ref(true);
const error = ref(false);

watch(
    () => props.file,
    async (file) => {
        if (!file?.file_id) {
            loading.value = false;
            error.value = true;
            return;
        }
        try {
            loading.value = true;
            error.value = false;
            imageUrl.value = await getFileDownloadUrl(file);
        } catch {
            error.value = true;
        } finally {
            loading.value = false;
        }
    },
    { immediate: true },
);
</script>

<template>
    <div class="w-full h-32 bg-[var(--background-gray-main)] overflow-hidden">
        <img
            v-if="imageUrl && !error"
            :src="imageUrl"
            :alt="file.filename ?? ''"
            class="w-full h-full object-cover"
            @error="error = true"
        />
        <div v-else class="w-full h-full flex items-center justify-center">
            <div v-if="loading" class="w-5 h-5 border-2 border-[var(--border-main)] border-t-transparent rounded-full animate-spin" />
            <span v-else class="text-[var(--text-tertiary)] text-xs uppercase">
                {{ file.filename?.split('.').pop() ?? 'img' }}
            </span>
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

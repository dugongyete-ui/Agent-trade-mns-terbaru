<template>
    <div class="relative overflow-auto flex-1 min-h-0 p-5">
        <div class="relative w-full max-w-[768px] mx-auto" style="min-height: calc(-200px + 100vh);">
            <div class="prose prose-gray max-w-none dark:prose-invert
                        [&_a]:text-blue-500 dark:[&_a]:text-blue-400 [&_a]:underline [&_a]:break-all
                        [&_pre]:bg-[var(--background-card)] [&_pre]:text-[var(--text-primary)]
                        [&_code]:bg-[var(--fill-tsp-gray-main)] [&_code]:text-[var(--text-primary)] [&_code]:rounded [&_code]:px-1"
                 v-html="renderedContent">
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import { marked, Renderer } from 'marked';
import DOMPurify from 'dompurify';
import type { FileInfo } from '../../api/file';
import { getFileDownloadUrl } from '../../api/file';

const content = ref('');

const props = defineProps<{
    file: FileInfo;
}>();

const escapeAttribute = (value: string): string => value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/[\r\n]/g, ' ');

const renderer = new Renderer();
renderer.link = ({ href, title, text }: { href: string; title?: string | null; text: string }) => {
    const safeHref = escapeAttribute(href || '#');
    const titleAttr = title ? ` title="${escapeAttribute(title)}"` : '';
    return `<a href="${safeHref}" target="_blank" rel="noopener noreferrer"${titleAttr}>${text}</a>`;
};

const renderedContent = computed(() => {
    if (!content.value) return '';
    try {
        const html = marked(content.value, {
            renderer,
            gfm: true,
            breaks: true,
        }) as string;
        return DOMPurify.sanitize(html, {
            ADD_ATTR: ['target', 'rel'],
        });
    } catch (error) {
        console.error('Failed to render markdown:', error);
        return `<pre class="text-sm text-[var(--text-secondary)] whitespace-pre-wrap">${content.value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre>`;
    }
});

watch(() => props.file, async (file) => {
    if (!file?.file_id) return;
    try {
        const url = await getFileDownloadUrl(file);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        content.value = await response.text();
    } catch (error) {
        console.error('Failed to load file content:', error);
        content.value = '(Failed to load file content)';
    }
}, { immediate: true, deep: false });
</script>

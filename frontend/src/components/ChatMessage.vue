<template>
  <div v-if="message.type === 'user'" class="flex w-full flex-col items-end justify-end gap-1 group mt-3">
    <div class="flex items-end">
      <div class="flex items-center justify-end gap-[2px] invisible group-hover:visible">
        <div class="float-right transition text-[12px] text-[var(--text-tertiary)] invisible group-hover:visible">
          {{ relativeTime(message.content.timestamp) }}
        </div>
      </div>
    </div>
    <div class="flex max-w-[90%] relative flex-col gap-2 items-end">
      <div
        class="relative flex items-center rounded-[12px] overflow-hidden bg-[var(--fill-white)] dark:bg-[var(--fill-tsp-white-main)] p-3 ltr:rounded-br-none rtl:rounded-bl-none border border-[var(--border-main)] dark:border-0"
        v-html="renderMarkdown(messageContent.content)">
      </div>
    </div>
  </div>
  <div v-else-if="message.type === 'assistant'" class="flex flex-col gap-2 w-full group" :class="hideAssistantHeader ? 'mt-0' : 'mt-3'">
    <div v-if="!hideAssistantHeader" class="flex items-center justify-between h-7 group">
      <div class="flex items-center gap-[3px]">
        <component v-if="assistantIcon" :is="assistantIcon" :size="24" class="w-6 h-6" />
        <Bot v-else :size="24" class="w-6 h-6" />
        <span v-if="assistantName" class="text-base text-[var(--text-primary)] tracking-tight leading-none ml-0.5">{{ assistantName }}</span>
        <DzeckTextIcon v-else-if="!assistantIcon" />
      </div>
      <div class="flex items-center gap-[2px] invisible group-hover:visible">
        <div class="float-right transition text-[12px] text-[var(--text-tertiary)] invisible group-hover:visible">
          {{ relativeTime(message.content.timestamp) }}
        </div>
      </div>
    </div>
    <div
      class="md-prose max-w-none p-0 m-0 prose dark:prose-invert text-[var(--text-primary)]"
      v-html="renderMarkdown(messageContent.content)"
      @click="handleMarkdownClick"></div>
  </div>
  <ToolUse v-else-if="message.type === 'tool'" :tool="toolContent" @click="handleToolClick(toolContent)" />
  <div v-else-if="message.type === 'step'" class="flex flex-col">
    <div class="text-sm w-full clickable flex gap-2 justify-between group/header truncate text-[var(--text-primary)]"
      data-event-id="HNtP7XOMUOhPemItd2EkK2">
      <div class="flex flex-row gap-2 justify-center items-center truncate">
        <div v-if="stepContent.status !== 'completed'"
          class="w-4 h-4 flex-shrink-0 flex items-center justify-center border border-[var(--border-dark)] rounded-[15px]">
        </div>
        <div v-else
          class="w-4 h-4 flex-shrink-0 flex items-center justify-center border-[var(--border-dark)] rounded-[15px] bg-[var(--text-disable)] dark:bg-[var(--fill-tsp-white-dark)] border-0">
          <CheckIcon class="text-[var(--icon-white)] dark:text-[var(--icon-white-tsp)]" :size="10" />
        </div>
        <div class="truncate font-medium markdown-content"
          v-html="stepContent.description ? renderMarkdown(stepContent.description) : ''">
        </div>
        <span class="flex-shrink-0 flex" @click="isExpanded = !isExpanded;">
          <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            class="lucide lucide-chevron-down transition-transform duration-300 w-4 h-4"
            :class="{ 'rotate-180': isExpanded }">
            <path d="m6 9 6 6 6-6"></path>
          </svg>
        </span>
      </div>
      <div class="float-right transition text-[12px] text-[var(--text-tertiary)] invisible group-hover/header:visible">
        {{ relativeTime(message.content.timestamp) }}
      </div>
    </div>
    <div class="flex">
      <div class="w-[24px] relative">
        <div class="border-l border-dashed border-[var(--border-dark)] absolute start-[8px] top-0 bottom-0"
          style="height: calc(100% + 14px);"></div>
      </div>
      <div
        class="flex flex-col gap-3 flex-1 min-w-0 overflow-hidden pt-2 transition-[max-height,opacity] duration-150 ease-in-out"
        :class="{ 'max-h-[100000px] opacity-100': isExpanded, 'max-h-0 opacity-0': !isExpanded }">
        <div v-for="(notification, index) in stepContent.notifications || []" :key="`notification-${index}`"
          class="text-sm text-[var(--text-secondary)] leading-relaxed prose dark:prose-invert max-w-none"
          v-html="renderMarkdown(notification)"></div>
        <ToolUse v-for="(tool, index) in stepContent.tools" :key="index" :tool="tool" @click="handleToolClick(tool)" />
      </div>
    </div>
  </div>
  <div v-else-if="message.type === 'attachments' && attachmentsContent.role === 'assistant'" class="flex flex-col gap-2 w-full group" :class="hideAssistantHeader ? 'mt-0' : 'mt-3'">
    <div v-if="!hideAssistantHeader" class="flex items-center justify-between h-7 group">
      <div class="flex items-center gap-[3px]">
        <component v-if="assistantIcon" :is="assistantIcon" :size="24" class="w-6 h-6" />
        <Bot v-else :size="24" class="w-6 h-6" />
        <span v-if="assistantName" class="text-base text-[var(--text-primary)] tracking-tight leading-none ml-0.5">{{ assistantName }}</span>
        <DzeckTextIcon v-else-if="!assistantIcon" />
      </div>
      <div class="flex items-center gap-[2px] invisible group-hover:visible">
        <div class="float-right transition text-[12px] text-[var(--text-tertiary)] invisible group-hover:visible">
          {{ relativeTime(attachmentsContent.timestamp) }}
        </div>
      </div>
    </div>
    <AttachmentsMessage :content="attachmentsContent" :hideAllFilesButton="hideAllFilesButton"/>
  </div>
  <AttachmentsMessage v-else-if="message.type === 'attachments'" :content="attachmentsContent" :hideAllFilesButton="hideAllFilesButton"/>
</template>

<script setup lang="ts">
import DzeckTextIcon from './icons/DzeckTextIcon.vue';
import { Message, MessageContent, AttachmentsContent } from '../types/message';
import ToolUse from './ToolUse.vue';
import { marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import hljs from 'highlight.js';
import DOMPurify from 'dompurify';
import { CheckIcon } from 'lucide-vue-next';
import { computed, ref, type Component } from 'vue';
import { ToolContent, StepContent } from '../types/message';
import { useRelativeTime } from '../composables/useTime';
import { Bot } from 'lucide-vue-next';
import AttachmentsMessage from './AttachmentsMessage.vue';

marked.use(markedHighlight({
  emptyLangClass: 'hljs',
  langPrefix: 'hljs language-',
  highlight(code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
    return hljs.highlight(code, { language }).value;
  }
}));

const props = defineProps<{
  message: Message;
  sessionId?: string;
  assistantIcon?: Component;
  assistantName?: string;
  hideAllFilesButton?: boolean;
  hideHeader?: boolean;
}>();

const hideAssistantHeader = computed(() => props.hideHeader ?? false);

const emit = defineEmits<{
  (e: 'toolClick', tool: ToolContent): void;
}>();

const handleToolClick = (tool: ToolContent) => {
  emit('toolClick', tool);
};

const stepContent = computed(() => props.message.content as StepContent);
const messageContent = computed(() => props.message.content as MessageContent);
const toolContent = computed(() => props.message.content as ToolContent);
const attachmentsContent = computed(() => props.message.content as AttachmentsContent);

const isExpanded = ref(true);

const { relativeTime } = useRelativeTime();

const isSandboxPath = (href: string) =>
  href.startsWith('/home/') || href.startsWith('/root/') || href.startsWith('/tmp/');

const cleanLinkText = (href: string, text: string): string => {
  const isRawUrl = text === href || text.startsWith('http://') || text.startsWith('https://');
  if (isRawUrl) {
    try { return new URL(href).hostname; } catch { /* fall through */ }
  }
  return text;
};

const escapeAttribute = (value: string): string => value
  .replace(/&/g, '&amp;')
  .replace(/"/g, '&quot;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/[\r\n]/g, ' ');

const renderer = new marked.Renderer();

renderer.link = ({ href, text }: { href: string; title?: string | null; text: string }) => {
  const displayText = cleanLinkText(href, text);
  if (!href || isSandboxPath(href)) {
    return `<span class="sandbox-file-reference">${displayText}</span>`;
  }
  return `<a href="${escapeAttribute(href)}" target="_blank" rel="noopener noreferrer" title="${escapeAttribute(href)}">${displayText}</a>`;
};

renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
  const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
  const highlighted = hljs.highlight(text, { language }).value;
  const langLabel = lang ? `<span class="md-code-lang">${escapeAttribute(lang)}</span>` : '';
  const copyBtn = `<button class="md-code-copy" data-copy-code="${escapeAttribute(text)}" title="Copy" type="button">⎘</button>`;
  return `<div class="md-code-block"><div class="md-code-header">${langLabel}${copyBtn}</div><pre><code class="hljs language-${language}">${highlighted}</code></pre></div>`;
};

renderer.blockquote = ({ text }: { text: string }) => {
  const trimmed = text.trim();
  let type = 'default';
  let inner = trimmed;
  const typeMatch = trimmed.match(/^\[!(NOTE|TIP|WARNING|DANGER|INFO)\]\s*/i);
  if (typeMatch) {
    type = typeMatch[1].toLowerCase();
    inner = trimmed.slice(typeMatch[0].length);
  }
  const icons: Record<string, string> = { note: 'ℹ', tip: '✦', warning: '⚠', danger: '✖', info: '◉', default: '❝' };
  return `<div class="md-callout md-callout-${type}"><span class="md-callout-icon">${icons[type] ?? icons.default}</span><div class="md-callout-body">${inner}</div></div>`;
};

renderer.table = (token: any) => {
  const renderCell = (cell: any): string => {
    const raw = typeof cell === 'object' ? (cell.text ?? '') : cell;
    try { return marked.parseInline(raw) as string; } catch { return raw; }
  };
  const header = token.header.map((cell: any) =>
    `<th>${renderCell(cell)}</th>`
  ).join('');
  const rows = token.rows.map((row: any[]) =>
    `<tr>${row.map((cell: any) => `<td>${renderCell(cell)}</td>`).join('')}</tr>`
  ).join('');
  return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
};

const renderMarkdown = (text: string) => {
  if (typeof text !== 'string') return '';
  const html = marked(text, { renderer, gfm: true, breaks: true }) as string;
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ['target', 'data-copy-code', 'title'],
    FORBID_ATTR: ['style', 'onclick', 'onerror', 'onload', 'onmouseover', 'onfocus', 'onblur'],
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'textarea', 'select', 'option', 'svg', 'math'],
    FORCE_BODY: true,
  });
};

const handleMarkdownClick = async (event: MouseEvent) => {
  const target = event.target as HTMLElement;
  const copyButton = target.closest('button[data-copy-code]') as HTMLButtonElement | null;
  if (!copyButton) return;
  try {
    await navigator.clipboard.writeText(copyButton.dataset.copyCode ?? '');
  } catch (error) {
    console.error('Failed to copy code block:', error);
  }
};
</script>

<style>
.duration-300 {
  animation-duration: .3s;
}

.duration-300 {
  transition-duration: .3s;
}

.sandbox-file-reference {
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}
</style>

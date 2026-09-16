<template>
  <!-- User messages (right-aligned) -->
  <div v-if="content.role === 'user'" class="flex flex-col flex-wrap gap-2 items-end justify-end">
    <div class="flex gap-2 flex-wrap max-w-[568px] justify-end">
      <div v-for="attachment in content.attachments" :key="attachment.file_id"
        @click="showFilePanel(attachment)"
        class="group/attach relative overflow-hidden cursor-pointer rounded-[12px] border-[0.5px] border-[var(--border-dark)] bg-[var(--background-menu-white)] hover:bg-[--background-tsp-menu-white]"
        :class="isImageFile(attachment.filename) ? 'w-[280px]' : 'flex items-center gap-1.5 p-2 pr-2.5 w-[280px]'">
        <!-- Image thumbnail for image files -->
        <template v-if="isImageFile(attachment.filename)">
          <ImageAttachmentCard :file="attachment" />
        </template>
        <!-- Generic file card -->
        <template v-else>
          <div class="flex items-center justify-center w-8 h-8 rounded-md">
            <div class="relative flex items-center justify-center">
              <component :is="getFileType(attachment.filename).icon" />
            </div>
          </div>
          <div class="flex flex-col gap-0.5 flex-1 min-w-0">
            <div class="flex-1 min-w-0 flex items-center">
              <div class="text-sm text-[var(--text-primary)] text-ellipsis overflow-hidden whitespace-nowrap flex-1 min-w-0">
                {{ attachment.filename }}
              </div>
            </div>
            <div class="text-xs text-[var(--text-tertiary)]">
              {{ getFileTypeText(attachment.filename) }} · {{ formatFileSize(attachment.size) }}
            </div>
          </div>
          <div class="items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md w-6 h-6 border border-[var(--border-main)] flex opacity-0 group-hover/attach:opacity-100">
            <Eye class="size-5 w-4 h-4 text-[var(--icon-secondary)]" />
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Assistant messages (left-aligned) -->
  <div v-else class="flex flex-col flex-wrap gap-2 justify-start">
    <div class="flex gap-2 flex-wrap max-w-[568px]">
      <div v-for="attachment in content.attachments" :key="attachment.file_id"
        @click="showFilePanel(attachment)"
        class="group/attach relative overflow-hidden cursor-pointer rounded-[12px] border-[0.5px] border-[var(--border-dark)] bg-[var(--background-menu-white)] hover:bg-[--background-tsp-menu-white]"
        :class="isImageFile(attachment.filename) ? 'w-[280px]' : 'flex items-center gap-1.5 p-2 pr-2.5 w-[280px]'">
        <!-- Image thumbnail for image files -->
        <template v-if="isImageFile(attachment.filename)">
          <ImageAttachmentCard :file="attachment" />
        </template>
        <!-- Generic file card -->
        <template v-else>
          <div class="flex items-center justify-center w-8 h-8 rounded-md">
            <div class="relative flex items-center justify-center">
              <component :is="getFileType(attachment.filename).icon" />
            </div>
          </div>
          <div class="flex flex-col gap-0.5 flex-1 min-w-0">
            <div class="flex-1 min-w-0 flex items-center">
              <div class="text-sm text-[var(--text-primary)] text-ellipsis overflow-hidden whitespace-nowrap flex-1 min-w-0">
                {{ attachment.filename }}
              </div>
            </div>
            <div class="text-xs text-[var(--text-tertiary)]">
              {{ getFileTypeText(attachment.filename) }} · {{ formatFileSize(attachment.size) }}
            </div>
          </div>
          <div class="items-center justify-center cursor-pointer hover:bg-[var(--fill-tsp-gray-main)] rounded-md w-6 h-6 border border-[var(--border-main)] flex opacity-0 group-hover/attach:opacity-100">
            <Eye class="size-5 w-4 h-4 text-[var(--icon-secondary)]" />
          </div>
        </template>
      </div>
      <button v-if="!props.hideAllFilesButton" @click="showAllFiles"
        class="h-[54px] pl-4 pr-1.5 flex items-center justify-center gap-1.5 w-[280px] rounded-[12px] border-[0.5px] border-[var(--border-dark)] bg-[var(--background-menu-white)] hover:bg-[var(--background-tsp-menu-white)]">
        <FileSearch :size="16" />
        <span class="text-sm text-[var(--icon-secondary)]">{{ t('View all files in this task') }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { FileSearch, Eye } from 'lucide-vue-next';
import { useI18n } from 'vue-i18n';
import type { AttachmentsContent } from '../types/message';
import { formatFileSize, getFileTypeText } from '../utils/fileType';
import { getFileType } from '../utils/fileType';
import { useSessionFileList } from '../composables/useSessionFileList';
import { useFilePanel } from '../composables/useFilePanel';
import ImageAttachmentCard from './ImageAttachmentCard.vue';

const { t } = useI18n();
const { showFilePanel } = useFilePanel();
const { showSessionFileList } = useSessionFileList();

const props = defineProps<{
  content: AttachmentsContent;
  hideAllFilesButton?: boolean;
}>();

const imageExtensions = new Set([
  'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif', 'heic', 'heif',
]);

function isImageFile(filename: string): boolean {
  const ext = filename.split('.').pop()?.toLowerCase() ?? '';
  return imageExtensions.has(ext);
}

const showAllFiles = () => {
  showSessionFileList();
};
</script>

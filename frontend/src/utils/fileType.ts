import type { Component } from 'vue';
import { useI18n } from 'vue-i18n';
import FileIcon from '../components/icons/FileIcon.vue';
import CodeFileIcon from '../components/icons/CodeFileIcon.vue';
import UnknownFilePreview from '../components/filePreviews/UnknownFilePreview.vue';
import MarkdownFilePreview from '../components/filePreviews/MarkdownFilePreview.vue';
import CodeFilePreview from '../components/filePreviews/CodeFilePreview.vue';
import ImageFilePreview from '../components/filePreviews/ImageFilePreview.vue';

export interface FileType {
  icon: Component;
  preview: Component;
}

const codeFileExtensions = [
  'py', 'js', 'ts', 'jsx', 'tsx', 'vue',
  'java', 'c', 'cpp', 'h', 'hpp',
  'go', 'rust', 'php', 'ruby', 'swift',
  'kotlin', 'scala', 'haskell', 'erlang', 'elixir',
  'ocaml', 'fsharp', 'dart', 'julia',
  'lua', 'perl', 'r', 'sh', 'bash',
  'css', 'scss', 'sass', 'less', 'txt',
  'html', 'xml', 'json', 'yaml', 'yml',
  'sql', 'dockerfile', 'toml', 'ini', 'conf',
];

const imageFileExtensions = [
  'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff', 'tif', 'heic', 'heif',
];


export const getFileType = (filename: string): FileType => {
  const file_extension = filename.split('.').pop()?.toLowerCase();
  
  if (file_extension === 'md') {
    return {
      icon: FileIcon,
      preview: MarkdownFilePreview,
    };
  }
  
  if (file_extension && codeFileExtensions.includes(file_extension)) {
    return {
      icon: CodeFileIcon,
      preview: CodeFilePreview,
    };
  }

  if (file_extension && imageFileExtensions.includes(file_extension)) {
    return {
      icon: FileIcon,
      preview: ImageFilePreview,
    };
  }
  
  return {
    icon: FileIcon,
    preview: UnknownFilePreview,
  };
};

/**
 * Get file type text based on file extension
 * @param filename - The filename to analyze
 * @returns Localized description of file type
 */
export const getFileTypeText = (filename: string): string => {
  const { t } = useI18n();
  const file_extension = filename.split('.').pop()?.toLowerCase();
  
  if (!file_extension) {
    return t('File');
  }

  // Text files
  if (file_extension === 'txt') {
    return t('Text');
  }

  // Markdown files
  if (file_extension === 'md') {
    return t('Markdown');
  }

  // Code files
  if (codeFileExtensions.includes(file_extension)) {
    return t('Code');
  }

  // Image files
  if (imageFileExtensions.includes(file_extension)) {
    return t('Image');
  }

  // Default
  return t('File');
};

/**
 * Format file size from bytes to human readable format
 * @param bytes - File size in bytes (null/undefined treated as 0)
 * @param decimals - Number of decimal places (default: 1)
 * @returns Formatted file size string
 */
export function formatFileSize(bytes: number | null | undefined, decimals: number = 1): string {
  if (!bytes || bytes === 0) return '0 B';

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];

  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
} 
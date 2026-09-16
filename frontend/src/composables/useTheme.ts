import { ref, watch } from 'vue'

type Theme = 'light' | 'dark'

const STORAGE_KEY = 'dzeck-theme'

const theme = ref<Theme>((localStorage.getItem(STORAGE_KEY) as Theme) || 'light')

const applyTheme = (t: Theme) => {
  if (t === 'dark') {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
}

applyTheme(theme.value)

watch(theme, (val) => {
  applyTheme(val)
  localStorage.setItem(STORAGE_KEY, val)
})

export function useTheme() {
  const toggleTheme = () => {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  const isDark = () => theme.value === 'dark'

  return { theme, toggleTheme, isDark }
}

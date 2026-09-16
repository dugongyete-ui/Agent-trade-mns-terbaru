import en from './en'
import id from './id'

export default {
  en,
  id
}

export type Locale = 'en' | 'id'

export const availableLocales: { label: string; value: Locale }[] = [
  { label: 'English', value: 'en' },
  { label: 'Indonesia', value: 'id' }
]

<template>
  <div class="page" :class="{ dark: theme === 'dark', 'is-loaded': isLoaded }">

    <!-- Navbar -->
    <nav class="nav">
      <div class="nav-inner">
        <a href="/" class="nav-logo">
          <Bot :size="22" />
          <span class="nav-logo-text">Dzeck</span>
        </a>
        <div class="nav-right">
          <button @click="toggleTheme" class="icon-btn" :title="theme === 'dark' ? 'Mode Terang' : 'Mode Gelap'">
            <Sun v-if="theme === 'dark'" :size="16" />
            <Moon v-else :size="16" />
          </button>
          <a href="/login" class="btn-secondary">Masuk</a>
          <a href="/login" class="btn-primary">Daftar</a>
        </div>
      </div>
    </nav>

    <!-- Main -->
    <main class="main">
      <div class="hero-section">
        <h1 class="headline fade-up-1">Kuasai analisis pasar Anda.</h1>
        <p class="sub-headline fade-up-2">Kecerdasan pasar yang otonom. Tanpa aturan baku. Dzeck berpikir sendiri — layaknya analis profesional yang sadar — di Forex, Crypto, Gold, dan Saham.</p>
      </div>

      <!-- Input box -->
      <div class="input-wrap fade-up-3">
        <div class="input-box" :class="{ focused: isFocused }">
          <textarea
            ref="textareaRef"
            v-model="message"
            class="input-textarea"
            placeholder="Perintahkan Dzeck untuk menganalisis pasar atau aset..."
            rows="1"
            @focus="isFocused = true"
            @blur="isFocused = false"
            @input="autoResize"
            @keydown.enter.exact.prevent="handleSend"
          />
          <div class="input-actions">
            <button class="attach-btn" title="Lampirkan konteks">
              <Plus :size="18" />
            </button>
            <button
              class="send-btn"
              :class="{ active: message.trim().length > 0 }"
              @click="handleSend"
              title="Kirim"
            >
              <ArrowUp :size="16" />
            </button>
          </div>
        </div>
      </div>

      <!-- Suggestion pills -->
      <div class="suggestions fade-up-4">
        <button
          v-for="sug in suggestions"
          :key="sug.id"
          class="suggestion-pill"
          @click="selectSuggestion(sug)"
        >
          <component :is="sug.icon" :size="14" class="pill-icon" />
          {{ sug.label }}
        </button>
      </div>
    </main>

    <!-- How It Works -->
    <section class="how-section">
      <div class="how-inner">
        <div class="how-header fade-up-5">
          <span class="how-label">Cara Kerja</span>
          <h2 class="how-title">Berpikir seperti trader profesional</h2>
          <p class="how-desc">Dzeck tidak mengikuti skrip. Setiap analisis dimulai dari nol — dari konteks makro, struktur pasar, hingga keputusan eksekusi yang jelas, jujur, dan bisa langsung dijalankan.</p>
        </div>

        <div class="steps-grid">
          <div class="step fade-up-5" style="transition-delay: 0.1s">
            <div class="step-number">01</div>
            <div class="step-icon-wrap">
              <Globe :size="20" />
            </div>
            <h3 class="step-title">Cek konteks makro dulu</h3>
            <p class="step-desc">Sebelum menyentuh chart, Dzeck memeriksa sesi pasar aktif (London / NY / Tokyo / Sydney) dan economic calendar — apakah ada event HIGH IMPACT dalam beberapa jam ke depan. Tanpa ini, sinyal teknikal apapun bisa jadi jebakan.</p>
          </div>

          <div class="step fade-up-5" style="transition-delay: 0.2s">
            <div class="step-number">02</div>
            <div class="step-icon-wrap">
              <Layers :size="20" />
            </div>
            <h3 class="step-title">Membaca struktur pasar dari nol</h3>
            <p class="step-desc">Multi-timeframe dari atas ke bawah (D1 → H4 → H1): bias directional, level kunci, zone supply/demand, Order Block, Fair Value Gap, dan Swing Structure. Tidak ada asumsi sebelumnya — semua dibaca dari data aktual.</p>
          </div>

          <div class="step fade-up-5" style="transition-delay: 0.3s">
            <div class="step-number">03</div>
            <div class="step-icon-wrap">
              <Brain :size="20" />
            </div>
            <h3 class="step-title">Memilih tools & parameter sendiri</h3>
            <p class="step-desc">Berdasarkan karakter pasar yang ditemukan, Dzeck memutuskan sendiri: indikator mana yang relevan hari ini, periode berapa, dan dari timeframe mana. Bukan dari checklist baku — setiap sesi bisa berbeda.</p>
          </div>

          <div class="step fade-up-5" style="transition-delay: 0.4s">
            <div class="step-number">04</div>
            <div class="step-icon-wrap">
              <MessageSquare :size="20" />
            </div>
            <h3 class="step-title">Narasi transparan setiap langkah</h3>
            <p class="step-desc">Sebelum memanggil tool apapun, Dzeck menjelaskan kenapa ia membutuhkannya. Setelah membaca data, Dzeck menjelaskan apa artinya dalam konteks apa yang sudah diketahui. Proses berpikirnya terbuka dan bisa diikuti.</p>
          </div>

          <div class="step fade-up-5" style="transition-delay: 0.5s">
            <div class="step-number">05</div>
            <div class="step-icon-wrap">
              <Scale :size="20" />
            </div>
            <h3 class="step-title">Devil's advocate sebelum keputusan</h3>
            <p class="step-desc">Sebelum menyimpulkan, Dzeck secara eksplisit menyatakan argumen terkuat MELAWAN trade tersebut — dan menjelaskan mengapa ia tetap lanjut atau tidak. Tidak ada keputusan tanpa melewati tahap ini.</p>
          </div>

          <div class="step fade-up-5" style="transition-delay: 0.6s">
            <div class="step-number">06</div>
            <div class="step-icon-wrap">
              <Target :size="20" />
            </div>
            <h3 class="step-title">Keputusan eksekusi yang lengkap</h3>
            <p class="step-desc">BUY, SELL, atau TUNGGU — dengan zona entry spesifik, stop loss berbasis volatilitas aktual, target profit, tingkat keyakinan (HIGH / MEDIUM / LOW), dan kondisi yang akan membatalkan setup. Bukan template. Keputusan sungguhan.</p>
          </div>
        </div>

        <div class="markets-row fade-up-5" style="transition-delay: 0.5s">
          <span class="markets-label">Mendukung</span>
          <div class="markets-chips">
            <span class="market-chip"><TrendingUp :size="13" /> Forex</span>
            <span class="market-chip"><BarChart2 :size="13" /> Gold (XAUUSD)</span>
            <span class="market-chip"><Zap :size="13" /> Crypto</span>
            <span class="market-chip"><Globe :size="13" /> Saham & Indeks</span>
            <span class="market-chip"><Calendar :size="13" /> Kalender Ekonomi</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Footer -->
    <footer class="footer">
      <span class="footer-brand">Dzeck</span>
      <span class="footer-sep">·</span>
      <span class="footer-copy">Analis trading otonom berbasis AI</span>
    </footer>

  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, markRaw, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Bot, Sun, Moon, Plus, ArrowUp,
  TrendingUp, BarChart2, Activity, Globe, Calendar, Zap, Target,
  MessageSquare, Brain, Layers, Scale
} from 'lucide-vue-next'
import { useTheme } from '@/composables/useTheme'

const { theme, toggleTheme } = useTheme()
const router = useRouter()

const message = ref('')
const isFocused = ref(false)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const isLoaded = ref(false)

onMounted(() => {
  setTimeout(() => {
    isLoaded.value = true
  }, 50)
})

interface Suggestion {
  id: string
  label: string
  icon: any
  prompt: string
}

const suggestions: Suggestion[] = [
  { id: 'xauusd', label: 'Cari entry XAUUSD sekarang', icon: markRaw(Target), prompt: 'Cari entry XAUUSD sekarang' },
  { id: 'btc', label: 'Scan momentum BTC', icon: markRaw(Zap), prompt: 'Scan momentum BTC' },
  { id: 'eurusd', label: 'Analisa EURUSD multi-timeframe', icon: markRaw(BarChart2), prompt: 'Analisa EURUSD multi-timeframe' },
  { id: 'calendar', label: 'Cek economic calendar', icon: markRaw(Calendar), prompt: 'Cek economic calendar hari ini dan dampaknya' },
  { id: 'gbpusd', label: 'Posisi terbaik GBPUSD', icon: markRaw(Activity), prompt: 'Posisi terbaik GBPUSD hari ini' },
]

const autoResize = () => {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

const selectSuggestion = async (sug: Suggestion) => {
  message.value = sug.prompt
  await nextTick()
  autoResize()
  textareaRef.value?.focus()
  const end = message.value.length
  textareaRef.value?.setSelectionRange(end, end)
}

const PENDING_KEY = 'dzeck_pending_prompt'

const handleSend = () => {
  if (!message.value.trim()) return
  localStorage.setItem(PENDING_KEY, message.value.trim())
  router.push('/login')
}
</script>

<style scoped>
.page {
  min-height: 100vh;
  background: var(--background-gray-main);
  color: var(--text-primary);
  display: flex;
  flex-direction: column;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  overflow-x: hidden;
}

/* Animations */
.fade-up-1, .fade-up-2, .fade-up-3, .fade-up-4, .fade-up-5 {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.8s cubic-bezier(0.16, 1, 0.3, 1), transform 0.8s cubic-bezier(0.16, 1, 0.3, 1);
}

.is-loaded .fade-up-1 { opacity: 1; transform: translateY(0); transition-delay: 0.1s; }
.is-loaded .fade-up-2 { opacity: 1; transform: translateY(0); transition-delay: 0.2s; }
.is-loaded .fade-up-3 { opacity: 1; transform: translateY(0); transition-delay: 0.3s; }
.is-loaded .fade-up-4 { opacity: 1; transform: translateY(0); transition-delay: 0.4s; }
.is-loaded .fade-up-5 { opacity: 1; transform: translateY(0); }

/* ── Navbar ── */
.nav {
  position: sticky;
  top: 0;
  z-index: 50;
  background: var(--background-gray-main);
  border-bottom: 1px solid var(--border-light);
}
.nav-inner {
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 20px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.nav-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  text-decoration: none;
  color: var(--text-primary);
  transition: opacity 0.2s;
}
.nav-logo:hover { opacity: 0.8; }
.nav-logo-text {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--text-primary);
  text-transform: uppercase;
}
.nav-right { display: flex; align-items: center; gap: 12px; }

.icon-btn {
  display: flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 6px;
  border: 1px solid transparent; background: transparent;
  color: var(--text-secondary); cursor: pointer;
  transition: all 0.2s;
}
.icon-btn:hover { background: var(--fill-tsp-white-main); border-color: var(--border-main); color: var(--text-primary); }

.btn-primary {
  padding: 8px 18px; border-radius: 6px;
  background: var(--text-primary); color: var(--background-gray-main);
  font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  text-decoration: none; border: 1px solid var(--text-primary); cursor: pointer;
  transition: all 0.2s;
}
.btn-primary:hover { background: transparent; color: var(--text-primary); }

.btn-secondary {
  padding: 8px 18px; border-radius: 6px; background: transparent;
  color: var(--text-secondary); font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  text-decoration: none; border: 1px solid var(--border-btn-main); cursor: pointer;
  transition: all 0.2s;
}
.btn-secondary:hover { color: var(--text-primary); border-color: var(--text-primary); background: var(--fill-tsp-white-main); }

/* ── Main ── */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px 80px;
  position: relative;
}

.hero-section {
  text-align: center;
  margin-bottom: 40px;
  max-width: 700px;
}

.headline {
  font-size: clamp(32px, 5vw, 48px);
  font-weight: 500;
  letter-spacing: -0.03em;
  color: var(--text-primary);
  margin: 0 0 16px;
  line-height: 1.1;
}

.sub-headline {
  font-size: clamp(15px, 2vw, 17px);
  font-weight: 400;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 auto;
  max-width: 560px;
}

/* ── Input wrap + box ── */
.input-wrap {
  width: 100%;
  max-width: 720px;
  margin-bottom: 24px;
}

.input-box {
  background: var(--background-card);
  border: 1px solid var(--border-dark);
  border-radius: 12px;
  padding: 16px 16px 12px 20px;
  cursor: text;
  box-shadow: 0 4px 20px var(--shadow-XS);
  transition: box-shadow 0.2s, border-color 0.2s;
}
.input-box:hover { border-color: var(--text-tertiary); }
.input-box.focused { border-color: var(--text-primary); box-shadow: 0 4px 24px var(--shadow-S); }

.input-textarea {
  width: 100%;
  min-height: 24px;
  max-height: 240px;
  background: transparent;
  border: none; outline: none; resize: none;
  font-size: 16px; line-height: 1.5;
  color: var(--text-primary); font-family: inherit;
  margin-bottom: 12px;
  overflow-y: auto;
}
.input-textarea::placeholder { color: var(--text-disable); font-weight: 400; }

.input-actions {
  display: flex; align-items: center; justify-content: space-between;
}
.attach-btn {
  display: flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 6px;
  border: 1px solid var(--border-btn-main);
  background: transparent; color: var(--text-secondary); cursor: pointer;
  transition: all 0.2s;
}
.attach-btn:hover { background: var(--fill-tsp-white-main); color: var(--text-primary); border-color: var(--text-tertiary); }

.send-btn {
  display: flex; align-items: center; justify-content: center;
  width: 32px; height: 32px; border-radius: 6px;
  border: 1px solid transparent; background: var(--background-gray-main);
  color: var(--text-disable); cursor: pointer;
  transition: all 0.2s;
}
.send-btn.active { background: var(--text-primary); color: var(--background-gray-main); border-color: var(--text-primary); }
.send-btn.active:hover { opacity: 0.9; transform: translateY(-1px); }

/* ── Suggestion pills ── */
.suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  max-width: 760px;
}
.suggestion-pill {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 16px; border-radius: 6px;
  border: 1px solid var(--border-btn-main);
  background: var(--background-card);
  color: var(--text-secondary);
  font-size: 13px; font-weight: 500; cursor: pointer;
  transition: all 0.2s;
}
.pill-icon { color: var(--text-tertiary); transition: color 0.2s; }
.suggestion-pill:hover { background: var(--background-gray-main); color: var(--text-primary); border-color: var(--text-tertiary); }
.suggestion-pill:hover .pill-icon { color: var(--text-primary); }

/* ── How It Works ── */
.how-section {
  background: var(--background-card);
  border-top: 1px solid var(--border-main);
  padding: 80px 20px;
}

.how-inner {
  max-width: 1000px;
  margin: 0 auto;
}

.how-header {
  text-align: center;
  margin-bottom: 56px;
}

.how-label {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-bottom: 14px;
  border: 1px solid var(--border-btn-main);
  padding: 4px 12px;
  border-radius: 99px;
}

.how-title {
  font-size: clamp(22px, 3.5vw, 32px);
  font-weight: 500;
  letter-spacing: -0.02em;
  color: var(--text-primary);
  margin: 0 0 14px;
  line-height: 1.2;
}

.how-desc {
  font-size: 15px;
  color: var(--text-secondary);
  line-height: 1.6;
  max-width: 520px;
  margin: 0 auto;
}

/* Steps grid */
.steps-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 2px;
  border: 1px solid var(--border-main);
  border-radius: 12px;
  overflow: hidden;
  margin-bottom: 40px;
}

.step {
  padding: 32px 24px;
  background: var(--background-gray-main);
  position: relative;
  transition: background 0.2s;
}
.step:hover { background: var(--fill-tsp-white-main); }

.step-number {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text-disable);
  margin-bottom: 16px;
  font-variant-numeric: tabular-nums;
}

.step-icon-wrap {
  width: 36px; height: 36px;
  border-radius: 8px;
  border: 1px solid var(--border-btn-main);
  background: var(--background-card);
  display: flex; align-items: center; justify-content: center;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.step-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0 0 10px;
  line-height: 1.3;
  letter-spacing: -0.01em;
}

.step-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0;
}

/* Markets row */
.markets-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.markets-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.markets-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.market-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid var(--border-btn-main);
  background: var(--background-gray-main);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
}

/* ── Footer ── */
.footer {
  border-top: 1px solid var(--border-main);
  padding: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
}

.footer-brand {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-primary);
}

.footer-sep {
  color: var(--text-disable);
  font-size: 13px;
}

.footer-copy {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .steps-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .how-section { padding: 60px 16px; }
}

@media (max-width: 600px) {
  .headline { font-size: 28px; }
  .nav-right .btn-secondary { display: none; }
  .main { padding: 40px 16px 60px; }
  .suggestion-pill { font-size: 12px; padding: 7px 12px; }
  .steps-grid { grid-template-columns: 1fr; }
  .step { padding: 24px 20px; }
  .markets-row { flex-direction: column; align-items: flex-start; gap: 12px; }
  .how-header { margin-bottom: 36px; }
}
</style>

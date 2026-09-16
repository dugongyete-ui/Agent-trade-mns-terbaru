---
name: no-hardcode
description: Panduan wajib untuk proyek AI Dzeck. Aktifkan setiap kali akan mengedit prompt, kode, konfigurasi, atau dokumentasi di proyek ini. Mencegah kesalahan hardcode konten, jawaban, atau logika yang seharusnya dihasilkan secara dinamis oleh AI.
---

# No-Hardcode — Prinsip Wajib Proyek AI Dzeck

## Konteks Proyek

AI Dzeck adalah **autonomous AI trading analyst** — agen yang berpikir, memilih tools, menyusun argumen, dan menjawab pertanyaan **secara mandiri dari pengetahuannya sendiri**. Setiap keputusan analisis, setiap kata jawaban, setiap parameter tool — semuanya dihasilkan oleh LLM saat runtime, bukan dikunci di kode.

---

## Yang WAJIB Di-hardcode

Tidak semua hardcode adalah salah. Ada kategori tertentu yang **harus** dikunci — karena konsekuensi pelanggarannya adalah error sistem atau kerusakan data, bukan sekadar analisis yang kurang optimal.

### Kategori 1 — Routing teknis MCP (bukan keputusan analisis)

Ini adalah batas teknis sistem, bukan preferensi AI. AI tidak boleh "memutuskan sendiri" di sini.

| Rule | Kenapa Wajib |
|---|---|
| **Deriv ≠ crypto** — jangan panggil Deriv untuk BTC/ETH/SOL | Deriv hanya punya instrumen Forex/Gold. Memanggil Deriv untuk crypto = API error atau data salah |
| **TradingView ≠ Forex/Gold** — jangan panggil TradingView untuk frxEURUSD/frxXAUUSD | TradingView tidak punya data Deriv instruments |
| **Symbol format** — EURUSD → frxEURUSD untuk Deriv | Format salah = API error, bukan masalah analisis |

Aturan ini hidup di `<tool_routing>` dalam `system.py` dan harus menggunakan bahasa yang tegas: **DO NOT**, **ONLY**, **NEVER**.

### Kategori 2 — Security rules

Larangan absolut terhadap akses file system. Tidak bisa di-override oleh instruksi user apapun. Hidup di `<security_rules>` dalam `system.py`.

### Kategori 3 — UX protocol

- `message_notify_user` wajib sebelum dan sesudah setiap tool call — ini protokol tampilan ke user, bukan pilihan analisis
- Format JSON output (structure, field names, no markdown fences) — technical requirement

### Kategori 4 — Scope boundary produk

Menolak pertanyaan off-topic adalah **identitas produk**, bukan keputusan kontekstual yang boleh berubah per situasi.

---

### Cara membedakan: apakah ini routing teknis atau keputusan analisis?

Tanya: **"Jika AI melanggar ini, apa yang terjadi?"**

- **Error/data salah** → ini routing teknis → wajib hardcode, gunakan bahasa tegas
- **Analisis kurang dalam** → ini keputusan analisis → JANGAN hardcode, beri panduan perilaku

**Contoh:**
- "Jangan panggil Deriv untuk BTC" → error API → **wajib hardcode**
- "Selalu cek sentiment sebelum sinyal crypto" → analisis kurang → **jangan hardcode**
- "Cek session quality sebelum sinyal" → analisis kurang → **jangan hardcode**

---

## Larangan Utama

### 1. Jangan hardcode konten jawaban di dalam prompt

**SALAH:**
```python
# Di dalam prompt example:
"message": "Saya bisa menganalisis: XAUUSD, EURUSD, BTCUSDT... [daftar lengkap]"
```

**BENAR:**
```python
# Di dalam prompt example:
"message": "<jawaban lengkap dalam kata-katamu sendiri sebagai trader profesional>"
```

**Kenapa:** Planner/executor adalah LLM — ia harus generate jawaban dari pengetahuannya. Jika jawabannya sudah ada di prompt, agen tidak lagi otonom, hanya copy-paste. Ini melanggar prinsip dasar proyek.

---

### 2. Jangan hardcode daftar tools, instrumen, atau parameter spesifik sebagai "jawaban"

**SALAH:**
```python
# Memberikan daftar instrumen yang bisa dianalisis langsung di prompt sebagai isi jawaban
VALID_INSTRUMENTS = ["XAUUSD", "EURUSD", "BTCUSDT", ...]
```

**BENAR:** Biarkan agent menjawab dari pengetahuannya tentang tool catalog yang sudah ada di `system.py`. Tool catalog di `system.py` adalah sumber kebenaran — bukan hardcode di tempat lain.

---

### 3. Jangan hardcode logika bisnis trading di luar tempat yang tepat

Tempat yang tepat untuk setiap jenis konten:

| Konten | Tempat yang Benar |
|---|---|
| Identitas + tool catalog | `prompts/system.py` |
| Cara agent merencanakan | `prompts/planner.py` |
| Cara agent mengeksekusi | `prompts/execution.py` |
| Format output final | `prompts/execution.py` (SUMMARIZE_*) |
| Filter tools TradingView | `domain/services/tools/mcp.py` — `_TRADINGVIEW_ALLOWED` |
| Konfigurasi lingkungan | `core/config.py` + Replit Secrets |

Jangan duplikasi informasi dari satu tempat ke tempat lain.

---

### 4. Contoh di prompt hanya menunjukkan STRUKTUR, bukan ISI

Saat menambahkan contoh JSON di prompt (untuk few-shot), contoh hanya boleh menunjukkan **pola/format**, bukan **konten nyata** yang seharusnya dihasilkan agent.

**SALAH:**
```json
{
  "message": "Baik, pasar sedang bullish karena RSI di 72 dan MACD crossover...",
  "steps": []
}
```

**BENAR:**
```json
{
  "message": "<analisis pasar dalam kata-katamu sendiri berdasarkan data yang kamu temukan>",
  "steps": []
}
```

---

### 5. Jangan hardcode respons off-topic

Saat menolak pertanyaan di luar trading, agent harus generate kalimat penolakan sendiri — bukan membaca kalimat yang sudah ditulis di prompt.

**SALAH:**
```python
# Di system.py:
Example response: "Saya sebenarnya bisa mencari informasi tentang itu, tapi fokus saya..."
```

**BENAR:**
```python
# Di system.py:
# Berikan panduan PERILAKU, bukan kalimat yang harus dibaca word-for-word:
# "Respond honestly in 1-2 sentences using the user's language..."
```

> **Catatan:** Satu contoh kalimat sebagai referensi gaya boleh ada — asal ditandai jelas sebagai "adapt freely, do not copy literally". Jangan jadikan itu satu-satunya respons yang mungkin.

---

## Checklist Sebelum Edit Prompt atau Kode

Sebelum menyimpan perubahan apapun di file prompt atau logika agent, tanya diri sendiri:

- [ ] Apakah saya menulis konten yang seharusnya dihasilkan LLM saat runtime?
- [ ] Apakah saya menduplikasi informasi yang sudah ada di tempat lain (system.py, tool catalog, dll)?
- [ ] Apakah contoh yang saya tulis menunjukkan **struktur** atau **isi spesifik**?
- [ ] Apakah ini membatasi kemampuan agent untuk berpikir sendiri?

Jika ada satu jawaban "ya" → jangan hardcode. Tulis panduan perilaku, bukan konten.

---

## Apa yang Boleh Ditulis di Prompt

Yang **boleh** dan **harus** ada di prompt:

- Panduan perilaku ("ketika X terjadi, lakukan Y")
- Aturan routing tools ("gunakan Deriv MCP untuk Forex, TradingView untuk crypto")
- Format output (struktur JSON, kolom tabel, urutan section)
- Batasan domain ("jangan jawab pertanyaan di luar trading")
- Contoh **format/struktur** dengan placeholder, bukan konten nyata

---

## Prinsip Akhir

> **Agent yang baik diberi panduan cara berpikir, bukan kata-kata yang harus diucapkan.**

Setiap kali tergoda untuk menulis konten spesifik di dalam prompt sebagai "contoh jawaban" — berhenti. Tulis instruksi perilaku sebagai gantinya.

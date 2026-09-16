---
name: xauusd-m5-entry-strategy
description: Strategi entry XAUUSD pada timeframe 5m berdasarkan analisis EMA, ATR, dan struktur pasar
category: strategy
---

# XAUUSD M5 Entry Strategy

## Overview
Strategi ini menggunakan kombinasi EMA multi-timeframe, ATR untuk volatilitas, dan analisis struktur pasar untuk menentukan entry points yang optimal pada timeframe 5m untuk XAUUSD (Gold).

## Core Components

### 1. Trend Filter (EMA Alignment)
- EMA 9 > EMA 21 > EMA 50 = Bullish trend confirmation
- EMA 12 > EMA 26 = MACD bullish momentum
- Entry hanya dijalankan ketika semua EMA terurut dari tinggi ke rendah

### 2. Entry Zones
- **Primary**: Pullback ke EMA 9 (4326) atau EMA 21 (4313) dengan candlestick bullish confirmation
- **Secondary**: Breakout di atas swing high terakhir dengan volume konfirmasi
- **Avoid**: Membeli di atas EMA 9 tanpa pullback (chasing)

### 3. Risk Management
- **Stop Loss**: 1.5 × ATR di bawah entry point
  - ATR 14 period typical: 5.0-6.0 poin
  - SL range: 7.5-9.0 poin dari entry
- **Take Profit**: 
  - TP1: 2 × risiko (RR 1:2)
  - TP2: 3 × risiko (RR 1:3)
  - Alternatif: TP di swing high sebelumnya atau level psikologis

### 4. Validation Criteria
- Harga harus tetap di atas EMA 50 untuk bias bullish
- Volume harus meningkat pada breakout/pullback valid
- Tidak ada bearish divergence pada momentum indicator

### 5. Invalidation Signals
- Close di bawah EMA 50 dengan volume di atas rata-rata
- Breakdown dari swing low terakhir dengan close conviction
- Bearish engulfing pattern di area resistance kuat

## Implementation Rules
1. Tunggu setup yang jelas, jangan paksa entry
2. Konfirmasi dengan price action sebelum entry
3. Sesuaikan SL/TP berdasarkan kondisi volatilitas terkini
4. Maksimal 2-3 trade per sesi untuk menghindari overtrading
5. Selalu perhatikan kondisi pasar fundamental yang mungkin impact volatilitas

## Timeframe Specific Notes (5M)
- Lebih sensitif terhadap noise, perlu filter tambahan
- Cocok untuk scalping dan intra-day trading
- Perhatikan sesi pasar (London/NY overlap untuk volatilitas optimal)
- Hindari trading selama berita berita tinggi impact tanpa analisis tambahan
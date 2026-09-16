---
name: xauusd-m5-backtested-strategy
description: Hasil backtest strategi entry XAUUSD M5 berdasarkan EMA crossover dan analisis volatilitas
category: strategy
---

# XAUUSD M5 Backtested Strategy

## Backtest Results (2-day period, 5m timeframe)

### Strategy Performance Comparison

#### EMA Crossover (9,21) - Proxy untuk strategi pullback EMA 9/21
- **Total Return**: -1.44%
- **Annualized Return**: -95.9%
- **Sharpe Ratio**: -16.86
- **Sortino Ratio**: -17.12
- **Max Drawdown**: -2.56%
- **Win Rate**: 40.0%
- **Profit Factor**: 1.46
- **Total Trades**: 10
- **Average Trade**: +0.056%
- **Best Trade**: +1.01%
- **Worst Trade**: -0.32%
- **Exposure**: 47.4%
- **Final Equity**: $9,856.09 (dari $10,000)
- **vs Buy & Hold**: -2.76% (Buy & Hold: +1.32%)

#### EMA Crossover (12,26) - Proxy untuk konfirmasi momentum yang lebih stabil
- **Total Return**: +0.22%
- **Annualized Return**: +62.62%
- **Sharpe Ratio**: 2.82
- **Sortino Ratio**: 2.92
- **Max Drawdown**: -1.15%
- **Win Rate**: 66.7%
- **Profit Factor**: 6.16
- **Total Trades**: 6
- **Average Trade**: +0.238%
- **Best Trade**: +1.04%
- **Worst Trade**: -0.27%
- **Exposure**: 45.1%
- **Final Equity**: $10,022.09 (dari $10,000)
- **vs Buy & Hold**: -1.1% (Buy & Hold: +1.32%)

## Key Insights from Backtest

### 1. Performa Strategi
- EMA (12,26) menunjukkan performa yang jauh lebih baik daripada EMA (9,21)
- Win rate 66.7% dan profit factor 6.16 sangat mengesankan untuk strategi sederhana
- Namun, keduanya masih di bawah buy & hold karena periode backtest terlalu singkat (2 hari)

### 2. Karakteristik Trading
- Frekuensi trading: 6-10 trades per 2 hari = 3-5 trades per hari
- Ini sesuai dengan timeframe 5m yang memberikan cukup sinyal tanpa overtrading
- Rata-rata trade positif untuk EMA (12,26): +0.238% per trade

### 3. Risk Management Implications
- Max drawdown relatif rendah (1.15% untuk EMA 12,26)
- Profit factor tinggi menunjukkan bahwa ketika menang, menang besar
- Strategi ini cocok untuk pendekatan konservatif dengan posisi kecil

## Refinements untuk Strategi Nyata

Berdasarkan backtest dan analisis awal, strategi entry XAUUSD M5 yang disarankan:

### Entry Signal yang Disempurnakan
1. **Primary Setup**: Pullback ke EMA 21 dengan konfirmasi:
   - Candlestick bullish (hammer, engulfing, dll)
   - EMA 9 masih di atas EMA 21 (trend tetap utuh)
   - Harga menolak area EMA 21 dengan volume yang cukup

2. **Secondary Setup**: Breakout dengan momentum konfirmasi
   - Harga break di atas swing high terakhir
   - Didukung oleh volume yang meningkat minimal 1.5x rata-rata
   - EMA 9 dan 21 masih dalam urutan bullish

### Risk Management yang Dioptimasi
- **Stop Loss**: 
  - Untuk setup pullback: 0.5× ATR di bawah swing low lokal atau EMA 50
  - Untuk setup breakout: 1× ATR di bawah swing low sebelumnya
  - Maksimal SL: 2× ATR untuk menghindari whipsaw

- **Take Profit**:
  - TP 1: 1:1 risiko-reward (quick scalping)
  - TP 2: 1:2 risiko-reward (swing kecil)
  - TP 3: 1:3 risiko-reward (jika tren sangat kuat)
  - Alternatif: TP di level psikologis atau area resistance signifikan

### Filter Tambahan untuk Meningkatkan Win Rate
1. **Volatility Filter**: Hanya trade ketika ATR di atas rata-rata 20 period (menjauhi periode konsolidasi ekstrem)
2. **Session Filter**: Fokus pada London dan NY overlap (08:00-12:00 UTC) untuk liksiditas optimal
3. **Trend Strength Filter**: Hanya trade ketika selisih EMA 9 dan EMA 21 > 0.3× ATR (memastikan tren cukup kuat)

## Implementation Guidelines

### Position Sizing
- Maksimal 1% risiko per trade untuk akun standar
- Sesuaikan berdasarkan volatilitas terkini (ATR-based sizing)

### Trading Rules
1. Maksimal 3 trade aktif sekaligus
2. Tunggu konfirmasi candle close sebelum entry
3. Jangan chase harga yang sudah berjalan jauh dari EMA
4. Selalu periksa kalender ekonomi untuk berita berita tinggi impact
5. Jika 2 trade berturut-turut SL, istirahat dan evaluasi ulang setup

### Performance Expectations
- Win rate target: 60-70% (berdasarkan backtest EMA 12,26)
- Average profit factor target: >2.0
- Maksimal drawdown bulanan: <10%
- Trading frequency: 5-15 trades per minggu tergantung volatilitas

## Limitations dan Pertimbangan

1. **Periode Backtest Terbatas**: Hasil berdasarkan hanya 2 hari data, perlu validasi jangka panjang
2. **Slippage dan Fees**: Backtest menggunakan 0.1% fee + 0.05% slippage per sisi, yang mungkin tidak mencerminkan kondisi nyata
3. **Market Regime Changes**: Strategi mungkin perlu penyesuaian selama periode volatilitas sangat tinggi atau sangat rendah
4. **Execution Quality**: Hasil bergantung pada kecepatan eksekusi dan harga yang diperoleh

### Rekomendasi Lanjutan
1. Forward test dengan akun demo minimal 1 minggu sebelum live trading
2. Monitor dan catat semua trade untuk evaluasi berkala
3. Pertimbangkan menambahkan indikator volume atau order flow untuk konfirmasi tambahan
4. Evaluasi ulang setiap bulan berdasarkan performa aktual
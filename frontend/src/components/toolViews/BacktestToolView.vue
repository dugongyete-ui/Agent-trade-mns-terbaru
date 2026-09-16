<template>
  <div class="flex-1 min-h-0 w-full overflow-y-auto">
    <div class="max-w-[720px] mx-auto px-4 py-3 space-y-3">
      <!-- Loading / waiting state -->
      <div v-if="!payload" class="text-[var(--text-tertiary)] text-sm py-6 text-center">
        {{ toolContent?.status === 'calling' ? t('Tool is executing...') : t('Waiting for result...') }}
      </div>

      <template v-else>
        <!-- Summary header -->
        <div class="flex flex-wrap items-center gap-2 text-[12px] text-[var(--text-secondary)]">
          <span class="px-2 py-0.5 rounded-md bg-[var(--fill-tsp-gray-main)] font-medium">
            {{ payload.symbol }} · {{ payload.timeframe }}
          </span>
          <span class="text-[var(--text-tertiary)]">{{ payload.candles_fetched }} candles</span>
          <span
            v-if="payload.best_by_total_return"
            class="px-2 py-0.5 rounded-full border border-[var(--border-main)] text-[var(--text-secondary)]"
          >★ {{ t('Best by return') }}: {{ labelOf(payload.best_by_total_return) }}</span>
          <span
            v-if="payload.assumptions"
            class="text-[var(--text-tertiary)]"
          >fee {{ payload.assumptions.fee_pct }}% · slip {{ payload.assumptions.slippage_pct }}%</span>
        </div>

        <!-- Per-strategy cards -->
        <div
          v-for="(r, i) in payload.results"
          :key="i"
          class="rounded-xl border border-[var(--border-main)] bg-[var(--background-gray-main)] p-3 space-y-2"
        >
          <div class="flex items-center justify-between gap-2">
            <div class="text-[13px] font-medium text-[var(--text-primary)] truncate">
              {{ labelOf(r.strategy?.type) }}
              <span v-if="paramSummary(r)" class="text-[var(--text-tertiary)] font-normal">· {{ paramSummary(r) }}</span>
            </div>
            <span
              v-if="r.metrics && payload.best_by_total_return === r.strategy?.type"
              class="flex-shrink-0 text-[11px] px-1.5 py-0.5 rounded-md bg-[var(--fill-tsp-gray-main)] text-[var(--text-secondary)]"
            >★</span>
          </div>

          <div v-if="r.error" class="text-[12px] text-red-500">{{ r.error }}</div>

          <template v-else-if="r.metrics">
            <!-- Metric tiles -->
            <div class="grid grid-cols-3 sm:grid-cols-6 gap-2">
              <div
                v-for="tile in metricTiles(r.metrics)"
                :key="tile.label"
                class="rounded-lg bg-[var(--fill-tsp-gray-main)] px-2 py-1.5"
              >
                <div class="text-[10.5px] text-[var(--text-tertiary)] leading-tight">{{ tile.label }}</div>
                <div
                  class="text-[13px] font-semibold leading-tight mt-0.5"
                  :class="tile.tone"
                >
                  <span aria-hidden="true">{{ tile.glyph }}</span> {{ tile.value }}
                </div>
              </div>
            </div>

            <!-- Equity sparkline -->
            <div v-if="r.equity_curve && r.equity_curve.length > 1" class="pt-1">
              <div class="text-[10.5px] text-[var(--text-tertiary)] mb-1">{{ t('Equity curve') }}</div>
              <svg
                :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`"
                class="w-full block"
                style="height:56px"
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <line
                  :x1="0" :x2="SPARK_W"
                  :y1="sparkBaselineY(r)" :y2="sparkBaselineY(r)"
                  stroke="var(--border-main)" stroke-width="1" stroke-dasharray="4 4"
                />
                <polyline
                  :points="sparkPoints(r)"
                  fill="none"
                  :stroke="Number(r.metrics?.total_return_pct) >= 0 ? '#22c55e' : '#ef4444'"
                  stroke-width="2"
                  stroke-linejoin="round"
                  stroke-linecap="round"
                />
              </svg>
            </div>

            <!-- Recent trades -->
            <div v-if="r.trades && r.trades.length" class="pt-1">
              <div class="text-[10.5px] text-[var(--text-tertiary)] mb-1">
                {{ t('Trades') }} · {{ r.metrics.trades }}{{ r.trades.length ? ` (last ${r.trades.length})` : '' }}
              </div>
              <div class="space-y-0.5">
                <div
                  v-for="(tr, j) in r.trades.slice(0, 6)"
                  :key="j"
                  class="flex items-center justify-between gap-2 text-[11.5px] text-[var(--text-secondary)]"
                >
                  <span class="truncate text-[var(--text-tertiary)]">{{ shortDate(tr.entry_utc) }} → {{ shortDate(tr.exit_utc) }}</span>
                  <span class="flex-shrink-0 font-mono" :class="(tr.pnl_pct ?? 0) > 0 ? 'text-emerald-500' : 'text-red-500'">
                    {{ (tr.pnl_pct ?? 0) > 0 ? '▲' : '▼' }} {{ fmtPct(tr.pnl_pct ?? 0) }}
                  </span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { ToolContent } from '@/types/message';

const props = defineProps<{
  sessionId: string;
  toolContent: ToolContent;
  live: boolean;
}>();

const { t } = useI18n();

const SPARK_W = 600;
const SPARK_H = 56;

interface EquityPoint { t: number; e: number }
interface StrategyResult {
  strategy?: { type?: string; [k: string]: unknown };
  metrics?: Record<string, unknown> | null;
  equity_curve?: EquityPoint[];
  trades?: Array<{ entry_utc?: string; exit_utc?: string; pnl_pct?: number }>;
  error?: string;
}
interface Payload {
  symbol?: string;
  timeframe?: string;
  candles_fetched?: number;
  best_by_total_return?: string | null;
  assumptions?: { initial_cash?: number; fee_pct?: number; slippage_pct?: number };
  results?: StrategyResult[];
}

const payload = computed<Payload | null>(() => {
  const c = props.toolContent?.content;
  const res = (c as Record<string, unknown>)?.result ?? c;
  if (res && typeof res === 'object' && Array.isArray((res as Payload).results)) {
    return res as Payload;
  }
  return null;
});

const STRATEGY_LABELS: Record<string, string> = {
  sma_cross: 'SMA Cross',
  ema_cross: 'EMA Cross',
  rsi_reversion: 'RSI Reversion',
  bollinger: 'Bollinger',
  donchian: 'Donchian',
  macd: 'MACD',
  supertrend: 'SuperTrend',
  momentum: 'Momentum',
};

function labelOf(type?: string): string {
  return STRATEGY_LABELS[type || ''] ?? type ?? '—';
}

function paramSummary(r: StrategyResult): string {
  const s = r?.strategy || {};
  const parts: string[] = [];
  const push = (v: unknown, label: string) => {
    if (v !== undefined && v !== null && v !== '') parts.push(`${label} ${v}`);
  };
  if (s.fast !== undefined) push(s.fast, 'fast');
  if (s.slow !== undefined) push(s.slow, 'slow');
  if (s.signal !== undefined) push(s.signal, 'sig');
  if (s.period !== undefined) push(s.period, 'p');
  if (s.oversold !== undefined || s.overbought !== undefined) {
    parts.push(`${s.oversold ?? 30}/${s.overbought ?? 70}`);
  }
  if (s.std !== undefined) push(s.std, 'σ');
  if (s.mode) parts.push(String(s.mode).replace('_', ' '));
  if (s.entry_lookback !== undefined) push(s.entry_lookback, 'in');
  if (s.exit_lookback !== undefined) push(s.exit_lookback, 'out');
  if (s.multiplier !== undefined) push(s.multiplier, '×');
  if (s.threshold !== undefined) push(s.threshold, 'thr');
  return parts.join(' · ');
}

function fmtPct(v: unknown, digits = 1): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function tileTone(n: number, higherBetter: boolean): string {
  if (!Number.isFinite(n) || Math.abs(n) < 1e-9) return 'text-[var(--text-secondary)]';
  const good = higherBetter ? n > 0 : n < 0;
  return good ? 'text-emerald-500' : 'text-red-500';
}

function metricTiles(m: Record<string, unknown>) {
  const ret = Number(m.total_return_pct);
  const dd = Number(m.max_drawdown_pct);
  const wr = Number(m.win_rate_pct);
  const vsBh = Number(m.return_vs_buy_hold_pct);
  return [
    {
      label: 'Return',
      value: fmtPct(ret),
      glyph: ret >= 0 ? '▲' : '▼',
      tone: tileTone(ret, true),
    },
    {
      label: 'Ann. Return',
      value: fmtPct(Number(m.annualized_return_pct)),
      glyph: '',
      tone: tileTone(Number(m.annualized_return_pct), true),
    },
    {
      label: 'Sharpe',
      value: Number.isFinite(Number(m.sharpe)) ? String(m.sharpe) : '—',
      glyph: Number(m.sharpe) >= 0 ? '▲' : '▼',
      tone: tileTone(Number(m.sharpe), true),
    },
    {
      label: 'Max DD',
      value: fmtPct(dd),
      glyph: '',
      tone: tileTone(dd, false),
    },
    {
      label: 'Win rate',
      value: Number.isFinite(wr) ? `${wr.toFixed(0)}%` : '—',
      glyph: '',
      tone: 'text-[var(--text-secondary)]',
    },
    {
      label: 'vs B&H',
      value: fmtPct(vsBh),
      glyph: vsBh >= 0 ? '▲' : '▼',
      tone: tileTone(vsBh, true),
    },
  ];
}

function sparkPoints(r: StrategyResult): string {
  const curve = r.equity_curve || [];
  if (curve.length < 2) return '';
  const values = curve.map((p) => Number(p.e)).filter(Number.isFinite);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return curve
    .map((p, i) => {
      const x = (i / (curve.length - 1)) * SPARK_W;
      const y = 6 + (1 - (Number(p.e) - min) / span) * (SPARK_H - 12);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}

function sparkBaselineY(r: StrategyResult): number {
  // Baseline = initial cash level within the min/max window
  const curve = r.equity_curve || [];
  if (curve.length < 2) return SPARK_H / 2;
  const values = curve.map((p) => Number(p.e)).filter(Number.isFinite);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const initial = Number(payload.value?.assumptions?.initial_cash);
  const clamped = Math.min(max, Math.max(min, Number.isFinite(initial) ? initial : min));
  return 6 + (1 - (clamped - min) / span) * (SPARK_H - 12);
}

function shortDate(iso?: string): string {
  if (!iso) return '?';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '?';
  return `${d.getMonth() + 1}/${d.getDate()}`;
}
</script>

<style scoped>
svg { display: block; }
</style>

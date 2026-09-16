<template>
  <div class="flex flex-col h-full min-h-0">
    <div class="h-[36px] flex items-center justify-between px-3 border-b border-[var(--border-main)] bg-[var(--background-gray-main)]">
      <div class="text-[var(--text-tertiary)] text-sm font-medium">Market chart</div>
      <div class="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
        <span class="font-medium text-[var(--text-secondary)]">{{ chart.symbol }}</span>
        <span class="rounded-full border border-[var(--border-light)] px-2 py-0.5">{{ chart.timeframe }}</span>
      </div>
    </div>

    <div class="flex-1 min-h-0 overflow-y-auto p-3">
      <div class="rounded-xl border border-[var(--border-light)] bg-[var(--background-card)] p-2">
        <svg
          class="w-full h-auto block"
          viewBox="0 0 720 350"
          role="img"
          :aria-label="`${chart.symbol} ${chart.timeframe} candlestick chart`"
          preserveAspectRatio="none"
        >
          <g class="text-[var(--text-tertiary)]">
            <line v-for="line in gridLines" :key="line.y" :x1="chartLeft" :x2="chartRight" :y1="line.y" :y2="line.y" stroke="currentColor" stroke-opacity="0.14" />
            <text v-for="line in gridLines" :key="`label-${line.y}`" :x="chartRight + 8" :y="line.y + 4" font-size="10" fill="currentColor">{{ formatValue(line.value) }}</text>
          </g>

          <g v-for="candle in candleShapes" :key="candle.time">
            <line :x1="candle.x" :x2="candle.x" :y1="candle.highY" :y2="candle.lowY" :stroke="candle.color" stroke-width="1" />
            <rect :x="candle.x - candle.width / 2" :y="candle.bodyY" :width="candle.width" :height="candle.bodyHeight" :fill="candle.color" rx="1" />
          </g>

          <path
            v-for="series in overlayPaths"
            :key="series.name"
            :d="series.path"
            fill="none"
            :stroke="series.color"
            stroke-width="1.5"
            stroke-linejoin="round"
            stroke-linecap="round"
          />

          <line :x1="chartLeft" :x2="chartRight" :y1="chartBottom" :y2="chartBottom" stroke="currentColor" stroke-opacity="0.25" />
          <text :x="chartLeft" :y="chartBottom + 20" font-size="10" fill="currentColor">{{ firstLabel }}</text>
          <text :x="chartRight" :y="chartBottom + 20" text-anchor="end" font-size="10" fill="currentColor">{{ lastLabel }}</text>
        </svg>
      </div>

      <div v-if="legend.length" class="flex flex-wrap gap-x-3 gap-y-1 mt-2 px-1">
        <span v-for="item in legend" :key="item.name" class="inline-flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
          <span class="w-2 h-2 rounded-full" :style="{ backgroundColor: item.color }"></span>
          {{ item.name }}
        </span>
      </div>

      <div v-for="panel in panelCharts" :key="panel.name" class="mt-3 rounded-xl border border-[var(--border-light)] bg-[var(--background-card)] p-2">
        <div class="text-xs text-[var(--text-tertiary)] mb-1">{{ panel.name }}</div>
        <svg class="w-full h-auto block" viewBox="0 0 720 120" role="img" :aria-label="panel.name" preserveAspectRatio="none">
          <line x1="0" x2="720" y1="60" y2="60" stroke="currentColor" stroke-opacity="0.15" />
          <path v-if="panel.type !== 'bar'" :d="panel.path" fill="none" :stroke="panel.color" stroke-width="1.5" />
          <rect
            v-else
            v-for="bar in panel.bars"
            :key="bar.time"
            :x="bar.x"
            :y="bar.y"
            :width="bar.width"
            :height="bar.height"
            :fill="bar.value >= 0 ? panel.color : '#f97316'"
            fill-opacity="0.75"
          />
        </svg>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ChartPayload, ChartPoint, ChartSeries, ToolContent } from '@/types/message';

const props = defineProps<{
  toolContent: ToolContent;
  live: boolean;
  sessionId?: string;
}>();

const chart = computed<ChartPayload>(() => props.toolContent.chart as ChartPayload);
const chartLeft = 8;
const chartRight = 665;
const chartTop = 12;
const chartBottom = 285;

const visibleCandles = computed(() => chart.value.candles.slice(-120));
const minPrice = computed(() => Math.min(...visibleCandles.value.map(c => c.low)));
const maxPrice = computed(() => Math.max(...visibleCandles.value.map(c => c.high)));
const priceRange = computed(() => Math.max(maxPrice.value - minPrice.value, Number.EPSILON));

const xFor = (index: number, total: number, right = chartRight) =>
  chartLeft + (total <= 1 ? 0 : (index / (total - 1)) * (right - chartLeft));
const yForPrice = (value: number) =>
  chartTop + ((maxPrice.value - value) / priceRange.value) * (chartBottom - chartTop);

const gridLines = computed(() => Array.from({ length: 5 }, (_, index) => {
  const value = maxPrice.value - (priceRange.value * index / 4);
  return { value, y: yForPrice(value) };
}));

const candleShapes = computed(() => {
  const candles = visibleCandles.value;
  const width = Math.max(2, Math.min(9, (chartRight - chartLeft) / Math.max(candles.length, 1) * 0.65));
  return candles.map((candle, index) => {
    const openY = yForPrice(candle.open);
    const closeY = yForPrice(candle.close);
    return {
      ...candle,
      x: xFor(index, candles.length),
      highY: yForPrice(candle.high),
      lowY: yForPrice(candle.low),
      bodyY: Math.min(openY, closeY),
      bodyHeight: Math.max(1, Math.abs(closeY - openY)),
      width,
      color: candle.close >= candle.open ? '#22c55e' : '#ef4444',
    };
  });
});

const pointMap = computed(() => new Map(visibleCandles.value.map((candle, index) => [candle.time, index])));

const pathFor = (points: ChartPoint[], min: number, max: number, bottom = chartBottom, top = chartTop) => {
  const range = Math.max(max - min, Number.EPSILON);
  return points
    .map(point => {
      const index = pointMap.value.get(point.time);
      if (index === undefined) return null;
      const x = xFor(index, visibleCandles.value.length);
      const y = top + ((max - point.value) / range) * (bottom - top);
      return `${x},${y}`;
    })
    .filter(Boolean)
    .join(' ');
};

const overlayPaths = computed(() => (chart.value.overlays || []).map((series: ChartSeries) => {
  if (!series.points.length) return { ...series, path: '' };
  return {
    ...series,
    color: series.color || '#38bdf8',
    path: `M ${pathFor(series.points, minPrice.value, maxPrice.value)}`,
  };
}));

const panelCharts = computed(() => (chart.value.panels || []).map((series: ChartSeries) => {
  const values = series.points.map(point => point.value);
  const min = series.min ?? Math.min(...values, 0);
  const max = series.max ?? Math.max(...values, 0);
  const path = pathFor(series.points, min, max, 108, 8);
  const bars = series.points.map(point => {
    const index = pointMap.value.get(point.time);
    if (index === undefined) return null;
    const zeroY = 8 + ((max - 0) / Math.max(max - min, Number.EPSILON)) * 100;
    const valueY = 8 + ((max - point.value) / Math.max(max - min, Number.EPSILON)) * 100;
    return {
      ...point,
      x: xFor(index, visibleCandles.value.length, 720) - 2,
      y: Math.min(zeroY, valueY),
      width: 4,
      height: Math.max(1, Math.abs(valueY - zeroY)),
    };
  }).filter((bar): bar is NonNullable<typeof bar> => bar !== null);
  return {
    ...series,
    color: series.color || '#a855f7',
    path: `M ${path}`,
    bars,
  };
}));

const legend = computed(() => [
  ...(chart.value.overlays || []).map(series => ({ name: series.name, color: series.color || '#38bdf8' })),
  ...(chart.value.panels || []).map(series => ({ name: series.name, color: series.color || '#a855f7' })),
]);

const dateLabel = (time?: number) => time ? new Date(time * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
const firstLabel = computed(() => dateLabel(visibleCandles.value[0]?.time));
const lastLabel = computed(() => dateLabel(visibleCandles.value[visibleCandles.value.length - 1]?.time));
const formatValue = (value: number) => Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 5 }) : '';
</script>
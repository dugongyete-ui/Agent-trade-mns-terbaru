import type { FileInfo } from '../api/file';

export type MessageType = "user" | "assistant" | "tool" | "step" | "attachments";

export interface Message {
  type: MessageType;
  content: BaseContent;
}

export interface BaseContent {
  timestamp: number;
  /** Backend Redis/Mongo event identity; absent only for optimistic local user messages. */
  event_id?: string;
}

export interface MessageContent extends BaseContent {
  content: string;
  isStreaming?: boolean;
}

export interface ToolContent extends BaseContent {
  tool_call_id: string;
  name: string;
  function: string;
  args: any;
  content?: any;
  chart?: ChartPayload | null;
  status: "calling" | "called";
}

export interface ChartPoint {
  time: number;
  value: number;
}

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface ChartSeries {
  name: string;
  color?: string;
  type?: "line" | "bar";
  min?: number;
  max?: number;
  points: ChartPoint[];
}

export interface ChartPayload {
  symbol: string;
  granularity: number;
  timeframe: string;
  candles: ChartCandle[];
  overlays?: ChartSeries[];
  panels?: ChartSeries[];
}

export interface StepContent extends BaseContent {
  id: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  tools: ToolContent[];
  notifications?: string[];
}

export interface AttachmentsContent extends BaseContent {
  role: "user" | "assistant";
  attachments: FileInfo[];
}

export function isConsecutiveAssistant(messages: Message[], index: number): boolean {
  if (index <= 0) return false;
  const isAst = (m: Message) =>
    m.type === 'assistant' ||
    (m.type === 'attachments' && (m.content as AttachmentsContent).role === 'assistant');
  return isAst(messages[index]) && isAst(messages[index - 1]);
}
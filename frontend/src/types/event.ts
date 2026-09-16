import type { FileInfo } from '../api/file';

export type AgentSSEEvent = {
  event: 'tool' | 'step' | 'message' | 'message_chunk' | 'thinking' | 'error' | 'done' | 'title' | 'wait' | 'plan' | 'attachments';
  data: ToolEventData | StepEventData | MessageEventData | MessageChunkEventData | ThinkingEventData | ErrorEventData | DoneEventData | TitleEventData | WaitEventData | PlanEventData;
}

export interface BaseEventData {
  event_id: string;
  timestamp: number;
}

export interface ToolEventData extends BaseEventData {
  tool_call_id: string;
  name: string;
  status: "calling" | "called";
  function: string;
  args: {[key: string]: any};
  content?: any;
}

export interface StepEventData extends BaseEventData {
  status: "pending" | "running" | "completed" | "failed" | "skipped"
  id: string
  description: string
}

export interface MessageEventData extends BaseEventData {
  content: string;
  role: "user" | "assistant";
  attachments: FileInfo[];
  source?: "user" | "ack" | "notification" | "step_result" | "final" | "assistant";
}

export interface MessageChunkEventData extends BaseEventData {
  content: string;
  role: "user" | "assistant";
  done: boolean;
}

export interface ThinkingEventData extends BaseEventData {
  content: string;
  done: boolean;
}

export interface ErrorEventData extends BaseEventData {
  error: string;
}

export interface DoneEventData extends BaseEventData {
}

export interface WaitEventData extends BaseEventData {
}

export interface TitleEventData extends BaseEventData {
  title: string;
}

export interface PlanEventData extends BaseEventData {
  status?: "created" | "updated" | "pending" | "running" | "completed" | "failed" | "skipped";
  steps: StepEventData[];
}
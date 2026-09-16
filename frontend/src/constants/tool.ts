/**
 * Tool function mapping
 */
export const TOOL_FUNCTION_MAP: {[key: string]: string} = {
  // Search tools
  "info_search_web": "Searching web",

  // Message tools
  "message_notify_user": "Sending notification",
  "message_ask_user": "Asking question",

  // Technical indicator tools
  "technical_indicators": "Computing indicators",

  // Backtest / risk / memory toolkits
  "run_backtest": "Running backtest",
  "portfolio_risk": "Analyzing portfolio risk",
  "remember": "Updating memory",

  // Committee / skills toolkits
  "run_committee": "Running committee debate",
  "load_skill": "Loading skill",
  "save_skill": "Saving skill",
  "patch_skill": "Patching skill"
};

/**
 * Display name mapping for tool function parameters
 */
export const TOOL_FUNCTION_ARG_MAP: {[key: string]: string} = {
  "info_search_web": "query",
  "message_notify_user": "message",
  "message_ask_user": "question"
};

/**
 * Tool name mapping
 */
export const TOOL_NAME_MAP: {[key: string]: string} = {
  "search": "Search",
  "info": "Information",
  "message": "Message",
  "mcp": "MCP Tool",
  "technical": "Indicators",
  "backtest": "Backtest",
  "portfolio_risk": "Risk",
  "memory": "Memory",
  "committee": "Committee",
  "skills": "Skills"
};

import SearchIcon from '../components/icons/SearchIcon.vue';
import McpIcon from '../components/icons/McpIcon.vue';

/**
 * Tool icon mapping (per toolkit name)
 */
export const TOOL_ICON_MAP: {[key: string]: any} = {
  "search": SearchIcon,
  "info": SearchIcon,
  "message": "",
  "mcp": McpIcon,
  "technical": McpIcon,
  "backtest": McpIcon,
  "portfolio_risk": McpIcon,
  "memory": McpIcon,
  "committee": McpIcon,
  "skills": McpIcon
};

/**
 * Per-function icon overrides (takes priority over TOOL_ICON_MAP)
 */
export const TOOL_FUNCTION_ICON_MAP: {[key: string]: any} = {};

import SearchToolView from '@/components/toolViews/SearchToolView.vue';
import McpToolView from '@/components/toolViews/McpToolView.vue';
import BacktestToolView from '@/components/toolViews/BacktestToolView.vue';

/**
 * Mapping from tool names to components (fallback)
 */
export const TOOL_COMPONENT_MAP: {[key: string]: any} = {
  "search": SearchToolView,
  "mcp": McpToolView,
  "technical": McpToolView,
  "backtest": BacktestToolView,
  "portfolio_risk": McpToolView,
  "memory": McpToolView,
  "committee": McpToolView,
  "skills": McpToolView
};

/**
 * Mapping from specific function names to components (takes priority over TOOL_COMPONENT_MAP)
 */
export const TOOL_FUNCTION_COMPONENT_MAP: {[key: string]: any} = {
  "run_backtest": BacktestToolView
};

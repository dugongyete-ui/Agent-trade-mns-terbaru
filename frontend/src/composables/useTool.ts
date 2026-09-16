import { computed, Ref } from 'vue';
import type { ToolContent } from '../types/message';
import { useI18n } from 'vue-i18n';
import {
  TOOL_ICON_MAP,
  TOOL_FUNCTION_ICON_MAP,
  TOOL_NAME_MAP,
  TOOL_FUNCTION_MAP,
  TOOL_FUNCTION_ARG_MAP,
  TOOL_COMPONENT_MAP,
  TOOL_FUNCTION_COMPONENT_MAP,
} from '../constants/tool';

export function useToolInfo(tool?: Ref<ToolContent | undefined>) {
  const { t } = useI18n();

  const toolInfo = computed(() => {
    if (!tool || !tool.value) return null;
    
    // MCP tool
    if (tool.value.function?.startsWith('mcp_')) {
      // Format: mcp_{server}_{tool-name}
      // Strip mcp_ prefix, then strip the server name (first segment), then format nicely
      const withoutMcp = tool.value.function.replace(/^mcp_/, '');
      const firstUnderscore = withoutMcp.indexOf('_');
      const serverName = firstUnderscore !== -1 ? withoutMcp.substring(0, firstUnderscore) : '';
      let rawToolName = firstUnderscore !== -1
        ? withoutMcp.substring(firstUnderscore + 1)
        : withoutMcp;

      // Strip redundant server prefix from tool name
      // e.g., server="deriv", tool="deriv-rsi" → "rsi"
      // e.g., server="sentiment", tool="sentiment-fear-greed" → "fear-greed"
      const serverBase = serverName.split('-')[0];
      if (serverName && rawToolName.startsWith(serverName + '-')) {
        rawToolName = rawToolName.substring(serverName.length + 1);
      } else if (serverBase && rawToolName.startsWith(serverBase + '-')) {
        rawToolName = rawToolName.substring(serverBase.length + 1);
      }

      // Known trading acronyms that should stay uppercase
      const ACRONYMS = new Set(['rsi', 'macd', 'ema', 'sma', 'atr', 'bb', 'adx', 'cci', 'sar', 'ls', 'oi', 'tp', 'sl']);
      const displayName = rawToolName
        .replace(/[-_]/g, ' ')
        .replace(/\b\w+/g, (word: string) => {
          const lower = word.toLowerCase();
          return ACRONYMS.has(lower)
            ? lower.toUpperCase()
            : word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
        });

      let functionArg = '';
      const args = tool.value.args;
      if (args && Object.keys(args).length > 0) {
        const firstKey = Object.keys(args)[0];
        const firstValue = args[firstKey];
        if (typeof firstValue === 'string' && firstValue.length < 50) {
          functionArg = firstValue;
        } else if (firstValue !== undefined) {
          functionArg = JSON.stringify(firstValue).substring(0, 30) + '...';
        }
      }

      return {
        icon: TOOL_ICON_MAP['mcp'] || null,
        name: t(TOOL_NAME_MAP['mcp'] || 'MCP Tool'),
        function: displayName,
        functionArg: functionArg,
        view: TOOL_COMPONENT_MAP['mcp'] || null
      };
    }
    
    let functionArg = tool.value.args?.[TOOL_FUNCTION_ARG_MAP[tool.value.function]] ?? '';
    if (typeof functionArg !== 'string') functionArg = JSON.stringify(functionArg) || '';
    if (TOOL_FUNCTION_ARG_MAP[tool.value.function] === 'file') {
      functionArg = (functionArg as string).replace(/^\/home\/runner\//, '');
    }

    // Per-function icon takes priority over per-toolkit icon
    const icon =
      TOOL_FUNCTION_ICON_MAP[tool.value.function] ||
      TOOL_ICON_MAP[tool.value.name] ||
      null;
    
    return {
      icon,
      name: t(TOOL_NAME_MAP[tool.value.name] || ''),
      function: t(TOOL_FUNCTION_MAP[tool.value.function] || tool.value.function),
      functionArg: functionArg,
      view: TOOL_FUNCTION_COMPONENT_MAP[tool.value.function] || TOOL_COMPONENT_MAP[tool.value.name] || null
    };
  });

  return {
    toolInfo
  };
}

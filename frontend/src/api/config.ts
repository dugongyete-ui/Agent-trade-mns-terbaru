import { apiClient, ApiResponse } from './client'

export interface ClientConfigResponse {
  auth_provider: string
  google_analytics_id: string | null
}

let clientConfigCache: ClientConfigResponse | null = null
let isClientConfigLoaded = false

/**
 * Get client runtime configuration.
 */
export async function getClientConfig(): Promise<ClientConfigResponse> {
  const response = await apiClient.get<ApiResponse<ClientConfigResponse>>('/config/frontend')
  return response.data.data
}

/**
 * Get client runtime configuration (cached after first call).
 * Returns null when config has not been fetched yet or fetch failed.
 */
export async function getCachedClientConfig(): Promise<ClientConfigResponse | null> {
  if (isClientConfigLoaded) {
    return clientConfigCache
  }

  try {
    clientConfigCache = await getClientConfig()
    isClientConfigLoaded = true
    return clientConfigCache
  } catch (error) {
    console.warn('Failed to load client runtime configuration:', error)
    isClientConfigLoaded = true
    return null
  }
}

/**
 * Read auth provider from client configuration.
 */
export async function getCachedAuthProvider(): Promise<string | null> {
  const clientConfig = await getCachedClientConfig()
  return clientConfig?.auth_provider || null
}

export interface ThinkingModeResponse {
  thinking_mode: 'on' | 'off'
}

/**
 * Get the current thinking (reasoning) mode from the backend.
 */
export async function getThinkingMode(): Promise<'on' | 'off'> {
  const response = await apiClient.get<ApiResponse<ThinkingModeResponse>>('/config/thinking')
  return response.data.data.thinking_mode
}

/**
 * Toggle the thinking (reasoning) mode at runtime. Applies to all new AI calls.
 */
export async function setThinkingMode(enabled: boolean): Promise<'on' | 'off'> {
  const response = await apiClient.put<ApiResponse<ThinkingModeResponse>>('/config/thinking', {
    enabled,
  })
  return response.data.data.thinking_mode
}

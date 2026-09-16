// Backend API service
import { apiClient, ApiResponse, createSSEConnection, SSECallbacks } from './client';
import { AgentSSEEvent } from '../types/event';
import { CreateSessionResponse, GetSessionResponse, ListSessionResponse, ShareSessionResponse, SharedSessionResponse } from '../types/response';
import type { FileInfo } from './file';



/**
 * Create Session
 * @returns Session
 */
export async function createSession(): Promise<CreateSessionResponse> {
  const response = await apiClient.put<ApiResponse<CreateSessionResponse>>('/sessions');
  return response.data.data;
}

export async function getSession(sessionId: string): Promise<GetSessionResponse> {
  const response = await apiClient.get<ApiResponse<GetSessionResponse>>(`/sessions/${sessionId}`);
  return response.data.data;
}

export async function getSessions(): Promise<ListSessionResponse> {
  const response = await apiClient.get<ApiResponse<ListSessionResponse>>('/sessions');
  return response.data.data;
}

export async function getSessionsSSE(callbacks?: SSECallbacks<ListSessionResponse>): Promise<() => void> {
  return createSSEConnection<ListSessionResponse>(
    '/sessions',
    {
      method: 'POST'
    },
    callbacks
  );
}

export async function deleteSession(sessionId: string): Promise<void> {
  await apiClient.delete<ApiResponse<void>>(`/sessions/${sessionId}`);
}

export async function deleteAllSessions(): Promise<void> {
  await apiClient.delete<ApiResponse<void>>('/sessions');
}

export async function stopSession(sessionId: string): Promise<void> {
  await apiClient.post<ApiResponse<void>>(`/sessions/${sessionId}/stop`);
}

/**
 * Chat with Session (using SSE to receive streaming responses)
 * @returns A function to cancel the SSE connection
 */
export const chatWithSession = async (
  sessionId: string, 
  message: string = '',
  eventId?: string,
  attachments?: Array<{file_id: string; filename: string; content_type?: string; size?: number}>,
  callbacks?: SSECallbacks<AgentSSEEvent['data']>
): Promise<() => void> => {
  return createSSEConnection<AgentSSEEvent['data']>(
    `/sessions/${sessionId}/chat`,
    {
      method: 'POST',
      body: { 
        message, 
        timestamp: Math.floor(Date.now() / 1000), 
        event_id: eventId,
        attachments
      }
    },
    callbacks
  );
};

export async function getSessionFiles(sessionId: string): Promise<FileInfo[]> {
  const response = await apiClient.get<ApiResponse<FileInfo[]>>(`/sessions/${sessionId}/files`);
  return response.data.data;
}

export async function clearUnreadMessageCount(sessionId: string): Promise<void> {
  await apiClient.post<ApiResponse<void>>(`/sessions/${sessionId}/clear_unread_message_count`);
}

/**
 * Share a session to make it publicly accessible
 * @param sessionId Session ID to share
 * @returns Share session response with current sharing status
 * 
 * @example
 * ```typescript
 * // Share a session
 * const result = await shareSession('session123');
 * console.log(result.is_shared); // true
 * ```
 */
export async function shareSession(sessionId: string): Promise<ShareSessionResponse> {
  const response = await apiClient.post<ApiResponse<ShareSessionResponse>>(`/sessions/${sessionId}/share`);
  return response.data.data;
}

/**
 * Unshare a session to make it private again
 * @param sessionId Session ID to unshare
 * @returns Share session response with current sharing status
 * 
 * @example
 * ```typescript
 * // Unshare a session
 * const result = await unshareSession('session123');
 * console.log(result.is_shared); // false
 * ```
 */
export async function unshareSession(sessionId: string): Promise<ShareSessionResponse> {
  const response = await apiClient.delete<ApiResponse<ShareSessionResponse>>(`/sessions/${sessionId}/share`);
  return response.data.data;
}

/**
 * Get a shared session without authentication
 * This endpoint allows public access to sessions that have been marked as shared.
 * No authentication token is required.
 * 
 * @param sessionId Session ID to retrieve
 * @returns Shared session data (accessible publicly)
 * 
 * @example
 * ```typescript
 * // Get a shared session (no auth required)
 * try {
 *   const sharedSession = await getSharedSession('session123');
 *   console.log(sharedSession.title);
 *   console.log(sharedSession.events);
 * } catch (error) {
 *   console.error('Session not found or not shared');
 * }
 * ```
 */
export async function getSharedSession(sessionId: string, shareToken: string): Promise<SharedSessionResponse> {
  const params = new URLSearchParams({ share_token: shareToken });
  const response = await apiClient.get<ApiResponse<SharedSessionResponse>>(
    `/sessions/shared/${encodeURIComponent(sessionId)}?${params.toString()}`
  );
  return response.data.data;
}

export async function getSharedSessionFiles(sessionId: string, shareToken: string): Promise<FileInfo[]> {
  const params = new URLSearchParams({ share_token: shareToken });
  const response = await apiClient.get<ApiResponse<FileInfo[]>>(
    `/sessions/${encodeURIComponent(sessionId)}/share/files?${params.toString()}`
  );
  return response.data.data;
}

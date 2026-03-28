/**
 * server/lib/parse-message.ts
 *
 * Parses inbox messages which often have double-encoded JSON in the text field.
 * The 'text' field may be a JSON string (task assignments, structured messages)
 * or plain text (regular chat messages).
 */

import type { MessageType, DashboardMessage, RawInboxMessage } from './types.js';

interface ParsedMessageContent {
  messageType: MessageType;
  parsedContent: string;
  structuredData: Record<string, unknown> | null;
}

/**
 * Parse a raw inbox message text field into a typed message.
 * Handles double-encoded JSON (common in task_assignment messages).
 */
export function parseMessageText(text: string): ParsedMessageContent {
  // Attempt to parse as JSON (double-encoded pattern)
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;

    if (typeof parsed === 'object' && parsed !== null) {
      const msgType = parsed.type as string | undefined;

      if (msgType === 'task_assignment') {
        const subject = parsed.subject as string || 'Task assigned';
        const description = parsed.description as string || '';
        return {
          messageType: 'task_assignment',
          parsedContent: `Task #${parsed.taskId}: ${subject}${description ? ` -- ${truncate(description, 200)}` : ''}`,
          structuredData: parsed,
        };
      }

      if (msgType === 'shutdown_request') {
        return {
          messageType: 'shutdown_request',
          parsedContent: `Shutdown request${parsed.reason ? `: ${parsed.reason}` : ''}`,
          structuredData: parsed,
        };
      }

      if (msgType === 'shutdown_response') {
        const approved = parsed.approve ? 'approved' : 'rejected';
        return {
          messageType: 'shutdown_response',
          parsedContent: `Shutdown ${approved}${parsed.reason ? `: ${parsed.reason}` : ''}`,
          structuredData: parsed,
        };
      }

      // Generic structured message
      return {
        messageType: 'structured',
        parsedContent: parsed.content as string || parsed.message as string || JSON.stringify(parsed),
        structuredData: parsed,
      };
    }
  } catch {
    // Not JSON -- treat as plain text
  }

  return {
    messageType: 'text',
    parsedContent: text,
    structuredData: null,
  };
}

/**
 * Convert a raw inbox message into a DashboardMessage.
 */
export function toMessage(
  raw: RawInboxMessage,
  teamName: string,
  toAgent: string,
  index: number,
): DashboardMessage {
  const { messageType, parsedContent, structuredData } = parseMessageText(raw.text);

  return {
    id: `${teamName}-${toAgent}-${index}`,
    teamName,
    toAgent,
    from: raw.from,
    timestamp: raw.timestamp,
    read: raw.read,
    color: raw.color ?? null,
    messageType,
    rawText: raw.text,
    parsedContent,
    structuredData,
  };
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen) + '...';
}

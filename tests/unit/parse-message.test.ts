/**
 * Unit tests for server/lib/parse-message.ts
 *
 * Tests message parsing logic:
 * - Plain text messages
 * - JSON-encoded structured messages
 * - Double-encoded task_assignment, shutdown_request, shutdown_response
 * - toMessage() full conversion
 */

import { describe, it, expect } from 'vitest';
import { parseMessageText, toMessage } from '../../server/lib/parse-message.js';

describe('parseMessageText', () => {
  it('should parse plain text as messageType "text"', () => {
    const result = parseMessageText('Hello world');
    expect(result.messageType).toBe('text');
    expect(result.parsedContent).toBe('Hello world');
    expect(result.structuredData).toBeNull();
  });

  it('should parse task_assignment JSON', () => {
    const json = JSON.stringify({
      type: 'task_assignment',
      taskId: '5',
      subject: 'Implement feature X',
      description: 'Build the backend API for feature X',
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('task_assignment');
    expect(result.parsedContent).toContain('Task #5');
    expect(result.parsedContent).toContain('Implement feature X');
    expect(result.structuredData).not.toBeNull();
    expect(result.structuredData?.taskId).toBe('5');
  });

  it('should parse shutdown_request JSON', () => {
    const json = JSON.stringify({
      type: 'shutdown_request',
      reason: 'Session ending',
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('shutdown_request');
    expect(result.parsedContent).toContain('Shutdown request');
    expect(result.parsedContent).toContain('Session ending');
  });

  it('should parse shutdown_response with approve=true', () => {
    const json = JSON.stringify({
      type: 'shutdown_response',
      approve: true,
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('shutdown_response');
    expect(result.parsedContent).toContain('approved');
  });

  it('should parse shutdown_response with approve=false', () => {
    const json = JSON.stringify({
      type: 'shutdown_response',
      approve: false,
      reason: 'Not ready',
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('shutdown_response');
    expect(result.parsedContent).toContain('rejected');
  });

  it('should parse generic structured JSON', () => {
    const json = JSON.stringify({
      content: 'Some structured content',
      extra: 'data',
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('structured');
    expect(result.parsedContent).toBe('Some structured content');
    expect(result.structuredData).not.toBeNull();
  });

  it('should fallback to message field if content is missing', () => {
    const json = JSON.stringify({ message: 'Via message field' });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('structured');
    expect(result.parsedContent).toBe('Via message field');
  });

  it('should handle malformed JSON gracefully as plain text', () => {
    const result = parseMessageText('{not valid json');
    expect(result.messageType).toBe('text');
    expect(result.parsedContent).toBe('{not valid json');
  });

  it('should handle task_assignment without description', () => {
    const json = JSON.stringify({
      type: 'task_assignment',
      taskId: '10',
      subject: 'Quick fix',
    });
    const result = parseMessageText(json);
    expect(result.messageType).toBe('task_assignment');
    expect(result.parsedContent).toContain('Task #10: Quick fix');
    expect(result.parsedContent).not.toContain('--');
  });

  it('should truncate long task descriptions', () => {
    const longDescription = 'A'.repeat(300);
    const json = JSON.stringify({
      type: 'task_assignment',
      taskId: '1',
      subject: 'Test',
      description: longDescription,
    });
    const result = parseMessageText(json);
    // Description truncated to 200 chars + '...'
    expect(result.parsedContent.length).toBeLessThan(300);
    expect(result.parsedContent).toContain('...');
  });
});

describe('toMessage', () => {
  it('should convert a raw inbox message to DashboardMessage', () => {
    const raw = {
      from: 'team-lead',
      text: 'Hello agent',
      timestamp: '2026-03-28T12:00:00Z',
      read: false,
      color: 'blue',
    };

    const msg = toMessage(raw, 'pact-abc12345', 'backend-coder', 0);

    expect(msg.id).toBe('pact-abc12345-backend-coder-0');
    expect(msg.teamName).toBe('pact-abc12345');
    expect(msg.toAgent).toBe('backend-coder');
    expect(msg.from).toBe('team-lead');
    expect(msg.timestamp).toBe('2026-03-28T12:00:00Z');
    expect(msg.read).toBe(false);
    expect(msg.color).toBe('blue');
    expect(msg.messageType).toBe('text');
    expect(msg.rawText).toBe('Hello agent');
    expect(msg.parsedContent).toBe('Hello agent');
    expect(msg.structuredData).toBeNull();
  });

  it('should handle raw message with missing color', () => {
    const raw = {
      from: 'agent-1',
      text: 'test',
      timestamp: '2026-01-01T00:00:00Z',
      read: true,
    };

    const msg = toMessage(raw, 'team-x', 'agent-2', 5);
    expect(msg.color).toBeNull();
  });

  it('should parse JSON text field within raw message', () => {
    const raw = {
      from: 'lead',
      text: JSON.stringify({ type: 'shutdown_request' }),
      timestamp: '2026-01-01T00:00:00Z',
      read: false,
    };

    const msg = toMessage(raw, 'team-y', 'agent-z', 0);
    expect(msg.messageType).toBe('shutdown_request');
    expect(msg.parsedContent).toContain('Shutdown request');
    expect(msg.structuredData).not.toBeNull();
  });
});

import { LOGS_KEY, SETTINGS_KEY } from './config.js';

export const MAX_LOGS = 100;
const ALLOWED_METADATA = new Set(['requestId', 'code', 'durationMs', 'pageChars', 'answerChars', 'frameCount', 'unavailableFrames', 'status', 'model', 'fieldType']);
let queue = Promise.resolve();

export function redactMetadata(metadata = {}) {
  const safe = {};
  for (const [key, value] of Object.entries(metadata)) {
    if (!ALLOWED_METADATA.has(key)) continue;
    if (typeof value === 'number' && Number.isFinite(value)) safe[key] = value;
    else if (typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,120}$/.test(value)) safe[key] = value;
  }
  return safe;
}

/** Never log prompts, page URLs, field labels, answers, profile, resume, or keys. */
export function logEvent(event, metadata = {}, level = 'info', storage = chrome.storage.local) {
  const record = {
    timestamp: new Date().toISOString(),
    level: ['info', 'warn', 'error'].includes(level) ? level : 'info',
    event: /^[a-zA-Z0-9_.:-]{1,80}$/.test(event) ? event : 'unknown',
    ...redactMetadata(metadata),
  };
  queue = queue.catch(() => {}).then(async () => {
    const values = await storage.get([LOGS_KEY, SETTINGS_KEY]);
    if (values[SETTINGS_KEY]?.debug || record.level === 'error') console[record.level === 'error' ? 'error' : record.level === 'warn' ? 'warn' : 'info']('[Wagecuck Input Filler]', record);
    const logs = Array.isArray(values[LOGS_KEY]) ? values[LOGS_KEY] : [];
    await storage.set({ [LOGS_KEY]: [...logs, record].slice(-MAX_LOGS) });
  }).catch(() => {
    console.warn('[Wagecuck Input Filler] Diagnostic storage unavailable.');
  });
  return queue;
}

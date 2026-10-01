export const SETTINGS_KEY = 'wcSettings';
export const LOGS_KEY = 'wcLogs';
export const MAX_RESUME_BYTES = 5 * 1024 * 1024;
const PREVIOUS_CASUAL_INSTRUCTIONS = [
  'Write in a friendly, conversational tone and in the first person. Keep it clear and natural.',
  'Use a casual, straightforward first-person voice. Keep answers short and specific. Contractions are fine. Skip buzzwords, stock enthusiasm, and overly polished phrasing.',
  'Write in a casual, confident first-person voice. Be specific about what I built, led, and changed, and say the results plainly. Use natural contractions. Keep it concise and skip corporate jargon or fake modesty.',
];
export const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  apiKey: '',
  model: 'gpt-4.1-mini',
  profile: '',
  writingInstructions: 'Keep open answers short, casual, and confident, usually 1–3 sentences. Mention one relevant detail from the job posting when it fits. Say what I built and the results plainly, without corporate fluff.',
  resumeText: '',
  resumeFile: null,
  debug: false,
});

/** Storage is deliberately local, never sync. Content scripts cannot access it. */
export function normalizeSettings(value = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const settings = { ...DEFAULT_SETTINGS };
  for (const key of ['apiKey', 'model', 'profile', 'writingInstructions', 'resumeText']) {
    if (typeof source[key] === 'string') settings[key] = source[key];
  }
  for (const previous of PREVIOUS_CASUAL_INSTRUCTIONS) {
    if (settings.writingInstructions.startsWith(previous)) {
      settings.writingInstructions = DEFAULT_SETTINGS.writingInstructions + settings.writingInstructions.slice(previous.length);
      break;
    }
  }
  settings.apiKey = settings.apiKey.trim();
  settings.model = settings.model.trim() || DEFAULT_SETTINGS.model;
  if (!/^[a-zA-Z0-9._:-]{1,120}$/.test(settings.model)) throw new Error('Enter a valid OpenAI model ID.');
  settings.enabled = typeof source.enabled === 'boolean' ? source.enabled : DEFAULT_SETTINGS.enabled;
  settings.debug = typeof source.debug === 'boolean' ? source.debug : DEFAULT_SETTINGS.debug;
  if (source.resumeFile) {
    const file = source.resumeFile;
    if (typeof file.name !== 'string' || !file.name.toLowerCase().endsWith('.pdf') ||
        file.type !== 'application/pdf' || !Number.isInteger(file.size) || file.size < 1 ||
        file.size > MAX_RESUME_BYTES || typeof file.dataUrl !== 'string' ||
        !/^data:application\/pdf;base64,[a-zA-Z0-9+/]+={0,2}$/.test(file.dataUrl)) {
      throw new Error('The resume must be a PDF of 5 MB or less, or pasted as text.');
    }
    const base64 = file.dataUrl.slice(file.dataUrl.indexOf(',') + 1);
    const actualSize = Math.floor(base64.length * 3 / 4) - (base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0);
    if (actualSize !== file.size || actualSize > MAX_RESUME_BYTES) throw new Error('The PDF data does not match its file size. Please upload it again.');
    settings.resumeFile = { name: file.name, type: file.type, dataUrl: file.dataUrl, size: file.size };
  }
  return settings;
}

export async function loadSettings(storage = chrome.storage.local) {
  const values = await storage.get(SETTINGS_KEY);
  return normalizeSettings(values[SETTINGS_KEY]);
}

export async function saveSettings(value, storage = chrome.storage.local) {
  const settings = normalizeSettings(value);
  await storage.set({ [SETTINGS_KEY]: settings });
  return settings;
}

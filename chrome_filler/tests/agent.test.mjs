import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentError, buildBatchRequest, buildRequest, combinePageSnapshots, errorToPublic, generateAnswer, generateBatchAnswers, MAX_PAGE_CHARS, parseBatchResponse, parseResponseAnswer, readSseEvents, validateFieldAndPage } from '../lib/agent.js';
import { DEFAULT_SETTINGS, loadSettings, MAX_RESUME_BYTES, normalizeSettings, saveSettings } from '../lib/config.js';
import { MAX_LOGS, logEvent, redactMetadata } from '../lib/logging.js';

const settings = { ...DEFAULT_SETTINGS, apiKey: 'test-only-key', profile: 'Frontend developer with five years of experience.', profileFacts: { ...DEFAULT_SETTINGS.profileFacts, fullName: 'Jordan Example', veteranStatus: 'No' }, resumeText: 'Worked at Example Studio.', writingInstructions: 'Use a friendly tone. Mention accessibility.' };
const field = { label: 'Why do you want to work here at Acme?', type: 'textarea', placeholder: '', required: true, maxLength: 2000, context: 'Application essay', currentValue: '' };
const page = { title: 'Acme application', url: 'https://example.test/apply', text: 'Acme creates accessible education software.\nHiring a frontend developer.\nPage end marker.', fields: [field] };
const answer = 'I want to help Acme make education more accessible.';
const encodedAnswer = JSON.stringify({ answer, missingInformation: '' });

function streamResponse(events, chunkSize = 17) {
  const bytes = new TextEncoder().encode(events.map(event => `event: ${event.type}\r\ndata: ${JSON.stringify(event)}\r\n\r\n`).join(''));
  return new Response(new ReadableStream({ start(controller) {
    for (let offset = 0; offset < bytes.length; offset += chunkSize) controller.enqueue(bytes.slice(offset, offset + chunkSize));
    controller.close();
  } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function successfulEvents(text = encodedAnswer) {
  return [
    { type: 'response.created', response: { status: 'in_progress' } },
    { type: 'response.output_text.delta', delta: text.slice(0, 11) },
    { type: 'response.output_text.delta', delta: text.slice(11) },
    { type: 'response.completed', response: { status: 'completed', output: [{ type: 'message', content: [{ type: 'output_text', text }] }] } },
  ];
}

test('prompt preserves the entire page, profile, resume, question and writing preferences', () => {
  const request = buildRequest({ settings, field, page });
  const context = JSON.parse(request.input[0].content[0].text);
  assert.equal(context.entirePage.text, page.text);
  assert.equal(context.userProfile, settings.profile);
  assert.deepEqual(context.profileFacts, settings.profileFacts);
  assert.equal(context.resumeText, settings.resumeText);
  assert.deepEqual(context.targetField, field);
  assert.ok(request.instructions.includes(settings.writingInstructions));
  assert.match(request.instructions, /untrusted reference data/);
  assert.match(request.instructions, /make up a short, believable answer based on the user's background/);
  assert.match(request.instructions, /Do not leave open-ended or preference fields empty/);
  assert.match(request.instructions, /Never invent identity, contact information/);
  assert.equal(request.stream, true);
  assert.equal(request.store, false);
  assert.equal(request.model, 'gpt-4.1-mini');
  assert.equal(request.text.format.type, 'json_schema');
  assert.ok(!JSON.stringify(request).includes(settings.apiKey));
});

test('FILL ALL builds required, field-specific schema and uses one API call', async () => {
  const targets = [
    { id: '0:wc-field-1', field: { ...field, label: 'Why this role?', maxLength: 120 } },
    { id: '2:wc-field-7', field: { ...field, label: 'Email address', type: 'email', maxLength: 80 } },
    { id: '2:wc-field-8', field: { ...field, label: 'Years of experience', type: 'number', min: '0', max: '20', step: '1' } },
  ];
  const request = buildBatchRequest({ settings, targets, page });
  assert.deepEqual(JSON.parse(request.input[0].content[0].text).profileFacts, settings.profileFacts);
  const schema = request.text.format.schema;
  assert.deepEqual(schema.required, targets.map(target => target.id));
  assert.deepEqual(Object.keys(schema.properties), schema.required);
  assert.match(schema.properties[targets[0].id].description, /Why this role/);
  assert.match(schema.properties[targets[1].id].description, /Email address/);
  const narrativePattern = schema.properties[targets[0].id].properties.answer.pattern;
  const emailPattern = schema.properties[targets[1].id].properties.answer.anyOf[1].pattern;
  assert.equal(schema.properties[targets[0].id].properties.answer.maxLength, 120);
  assert.deepEqual(schema.properties[targets[0].id].properties.missingInformation.enum, ['']);
  assert.match('A concise answer.', new RegExp(narrativePattern));
  assert.doesNotMatch(narrativePattern, /\(\?=/);
  assert.match('jordan@example.test', new RegExp(emailPattern));
  assert.doesNotMatch('not an email', new RegExp(emailPattern));
  assert.deepEqual(schema.properties[targets[2].id].properties.answer.anyOf[1], {
    type: 'number', description: schema.properties[targets[2].id].description,
    minimum: 0, maximum: 20, multipleOf: 1,
  });
  assert.ok(request.instructions.includes(settings.writingInstructions));
  assert.match(request.instructions, /Give every open-ended or preference field a plausible profile-based answer/);
  assert.equal(request.store, false);
  assert.equal(request.stream, true);
  const batch = JSON.stringify({ [targets[0].id]: { answer: 'I built similar tools.', missingInformation: '' }, [targets[1].id]: { answer: 'jordan@example.test', missingInformation: '' }, [targets[2].id]: { answer: 5, missingInformation: '' } });
  let calls = 0;
  const result = await generateBatchAnswers({ settings, targets, page, fetchImpl: async (_url, init) => {
    calls++;
    assert.equal(JSON.parse(init.body).text.format.name, 'batch_answers');
    return streamResponse(successfulEvents(batch));
  } });
  assert.equal(calls, 1);
  assert.deepEqual(result.map(item => item.answer), ['I built similar tools.', 'jordan@example.test', '5']);
  const invalid = parseBatchResponse(JSON.stringify({ [targets[0].id]: { answer: 'Fine', missingInformation: '' }, [targets[1].id]: { answer: 'not-an-email', missingInformation: '' } }), targets);
  assert.equal(invalid[1].error.code, 'INVALID_ANSWER');
});

test('FILL ALL reserves enough output for a 28-field form and reasoning', () => {
  const targets = Array.from({ length: 28 }, (_, index) => ({ id: `0:wc-field-${index + 1}`, field }));
  const reasoningRequest = buildBatchRequest({ settings: { ...settings, model: 'gpt-6.1-sol' }, targets, page });
  assert.equal(reasoningRequest.max_output_tokens, 25_000 + 28 * 512);
  assert.deepEqual(reasoningRequest.reasoning, { effort: 'low' });
  assert.equal(reasoningRequest.text.format.schema.required.length, 28);
  const classicRequest = buildBatchRequest({ settings: { ...settings, model: 'gpt-4.1-mini' }, targets, page });
  assert.equal(classicRequest.max_output_tokens, 28 * 512);
  assert.equal(classicRequest.reasoning, undefined);
});

test('checkboxes, dropdowns, and radio groups use exact choice schemas', () => {
  const checkbox = { ...field, label: 'Accessible interfaces', type: 'checkbox', currentValue: 'false', maxLength: null };
  const dropdown = { ...field, label: 'Preferred focus', type: 'select', currentValue: '', maxLength: null, options: [
    { value: 'frontend', label: 'Frontend engineering' }, { value: 'design-systems', label: 'Design systems' },
  ] };
  const radio = { ...field, label: 'What is your age range?', type: 'radio', currentValue: '', maxLength: null, options: [
    { value: '21-29', label: '21-29' }, { value: '30-39', label: '30-39' }, { value: 'Prefer not to answer', label: 'Prefer not to answer' },
  ] };
  const request = buildBatchRequest({ settings, targets: [
    { id: '0:wc-field-1', field: checkbox }, { id: '0:wc-field-2', field: dropdown }, { id: '0:wc-field-3', field: radio },
  ], page });
  const fields = request.text.format.schema.properties;
  assert.equal(fields['0:wc-field-1'].properties.answer.anyOf[1].type, 'boolean');
  assert.deepEqual(fields['0:wc-field-2'].properties.answer.enum, ['frontend', 'design-systems']);
  assert.deepEqual(fields['0:wc-field-3'].properties.answer.anyOf[1].enum, ['21-29', '30-39', 'Prefer not to answer']);
  assert.match(fields['0:wc-field-2'].description, /Frontend engineering = frontend/);
  assert.match(fields['0:wc-field-3'].description, /What is your age range/);
  assert.equal(parseResponseAnswer(JSON.stringify({ answer: false, missingInformation: '' }), checkbox), false);
  assert.equal(parseResponseAnswer(JSON.stringify({ answer: true, missingInformation: '' }), checkbox), true);
  assert.equal(parseResponseAnswer(JSON.stringify({ answer: 'design-systems', missingInformation: '' }), dropdown), 'design-systems');
  assert.equal(parseResponseAnswer(JSON.stringify({ answer: '30-39', missingInformation: '' }), radio), '30-39');
  assert.throws(() => parseResponseAnswer(JSON.stringify({ answer: 'backend', missingInformation: '' }), dropdown), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(JSON.stringify({ answer: '40-49', missingInformation: '' }), radio), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => buildRequest({ settings, field: { ...checkbox, label: 'I consent to the terms' }, page }), error => error.code === 'CONSENT_FIELD');
  assert.throws(() => buildRequest({ settings, field: { ...radio, label: 'Do you consent to texts?' }, page }), error => error.code === 'CONSENT_FIELD');
  assert.throws(() => buildRequest({ settings, field: { ...dropdown, label: 'AI Policy for Application' }, page }), error => error.code === 'CONSENT_FIELD');
  assert.throws(() => buildRequest({ settings, field: { ...dropdown, label: 'Agreement to Arbitrate' }, page }), error => error.code === 'CONSENT_FIELD');
});

test('checkbox groups use one array answer with listed choices', () => {
  const group = { ...field, label: 'Which kinds of work have you done?', type: 'checkbox_group', currentValue: '["Design systems"]', options: [
    { value: 'Accessible interfaces', label: 'Accessible interfaces' },
    { value: 'Backend systems', label: 'Backend systems' },
    { value: 'Design systems', label: 'Design systems' },
  ] };
  const request = buildRequest({ settings, field: group, page });
  assert.equal(request.text.format.schema.properties.answer.type, 'array');
  assert.deepEqual(request.text.format.schema.properties.answer.items.enum, group.options.map(option => option.value));
  assert.deepEqual(parseResponseAnswer(JSON.stringify({ answer: ['Accessible interfaces', 'Design systems'], missingInformation: '' }), group), ['Accessible interfaces', 'Design systems']);
  assert.deepEqual(parseResponseAnswer(JSON.stringify({ answer: [], missingInformation: '' }), group), []);
  assert.throws(() => parseResponseAnswer(JSON.stringify({ answer: ['Unknown'], missingInformation: '' }), group), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(JSON.stringify({ answer: ['Design systems', 'Design systems'], missingInformation: '' }), group), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => buildRequest({ settings, field: { ...group, options: [] }, page }), error => error.code === 'INVALID_REQUEST');
});

test('large country dropdowns retain every available choice in the schema', () => {
  const options = Array.from({ length: 244 }, (_, index) => ({ value: `Country ${index + 1}`, label: `Country ${index + 1}` }));
  const country = { ...field, label: 'Country', type: 'select', currentValue: '', maxLength: null, options };
  const request = buildRequest({ settings, field: country, page });
  assert.deepEqual(request.text.format.schema.properties.answer.anyOf[1].enum, options.map(option => option.value));
  assert.throws(() => buildRequest({ settings, field: { ...country, options: [...options, ...options.slice(0, 57)] }, page }), error => error.code === 'INVALID_REQUEST');
});

test('PDF resume is passed as input_file with filename and full base64 data URL', () => {
  const bytes = Buffer.from('%PDF-1.7\nresume fixture');
  const file = { name: 'resume.pdf', type: 'application/pdf', size: bytes.length, dataUrl: `data:application/pdf;base64,${bytes.toString('base64')}` };
  const request = buildRequest({ settings: { ...settings, resumeFile: file }, field, page });
  assert.deepEqual(request.input[0].content[0], { type: 'input_file', filename: file.name, file_data: file.dataUrl });
  assert.equal(JSON.parse(request.input[0].content[1].text).entirePage.text, page.text);
});

test('settings reject oversized or mismatched PDF and preserve safe defaults', () => {
  assert.deepEqual(normalizeSettings(), DEFAULT_SETTINGS);
  assert.throws(() => normalizeSettings({ resumeFile: { name: 'resume.pdf', type: 'application/pdf', size: MAX_RESUME_BYTES + 1, dataUrl: 'data:application/pdf;base64,QQ==' } }), /5 MB/);
  assert.throws(() => normalizeSettings({ resumeFile: { name: 'resume.pdf', type: 'application/pdf', size: 10, dataUrl: 'data:application/pdf;base64,QQ==' } }), /does not match/);
  assert.throws(() => normalizeSettings({ model: 'model\ninjection' }), /valid OpenAI model/);
});

test('previous casual instructions upgrade without losing added preferences', () => {
  const previous = [
    'Write in a friendly, conversational tone and in the first person. Keep it clear and natural.',
    'Use a casual, straightforward first-person voice. Keep answers short and specific. Contractions are fine. Skip buzzwords, stock enthusiasm, and overly polished phrasing.',
    'Write in a casual, confident first-person voice. Be specific about what I built, led, and changed, and say the results plainly. Use natural contractions. Keep it concise and skip corporate jargon or fake modesty.',
  ];
  const extra = '\n\nMention my work on creative tools.';
  for (const preset of previous) {
    assert.equal(normalizeSettings({ writingInstructions: preset + extra }).writingInstructions, DEFAULT_SETTINGS.writingInstructions + extra);
  }
  assert.equal(normalizeSettings({ writingInstructions: 'Keep it formal.' }).writingInstructions, 'Keep it formal.');
});

test('settings storage loads and saves complete objects and reports storage failures', async () => {
  let stored = {};
  const storage = { get: async () => stored, set: async value => { stored = value; } };
  const saved = await saveSettings({ apiKey: '  test-only-key  ' }, storage);
  assert.equal(saved.apiKey, 'test-only-key');
  assert.equal(saved.model, DEFAULT_SETTINGS.model);
  assert.deepEqual(await loadSettings(storage), saved);
  await assert.rejects(saveSettings({}, { set: async () => { throw new Error('storage unavailable'); } }), /storage unavailable/);
  await assert.rejects(loadSettings({ get: async () => { throw new Error('storage unavailable'); } }), /storage unavailable/);
});

test('frame context deduplicates clicked snapshot and reports inaccessible frames', () => {
  const nested = { title: 'Embedded form', url: 'https://forms.test/embed', text: 'Second frame exact marker', fields: [] };
  const combined = combinePageSnapshots(page, [page, nested], 2);
  assert.equal(combined.frameCount, 2);
  assert.equal(combined.text.split('Page end marker.').length, 2);
  assert.ok(combined.text.includes(nested.text));
  assert.match(combined.contextNote, /2 frame\(s\) were inaccessible/);
  assert.equal(combined.unavailableFrames, 2);
});

test('large full-page or combined contexts are rejected without silent truncation', () => {
  assert.throws(() => validateFieldAndPage(field, { ...page, text: 'x'.repeat(MAX_PAGE_CHARS + 1) }), error => error.code === 'PAGE_TOO_LARGE');
  assert.throws(() => combinePageSnapshots(page, [{ ...page, text: 'x'.repeat(MAX_PAGE_CHARS) }]), error => error.code === 'PAGE_TOO_LARGE');
});

test('unsupported, sensitive, and malformed field requests fail before fetch', () => {
  assert.throws(() => buildRequest({ settings, field: { ...field, type: 'password' }, page }), error => error.code === 'UNSUPPORTED_FIELD');
  assert.throws(() => buildRequest({ settings, field: { ...field, label: 'Credit card number', type: 'text' }, page }), error => error.code === 'SENSITIVE_FIELD');
  for (const name of ['creditCardNumber', 'candidate_password', 'oneTimeCode', 'APIKey', 'access_token']) {
    assert.throws(() => buildRequest({ settings, field: { ...field, label: 'Candidate information', type: 'text', name }, page }), error => error.code === 'SENSITIVE_FIELD');
  }
  assert.doesNotThrow(() => buildRequest({ settings, field: { ...field, label: 'Describe your approach to password and API key security', context: 'Never provide a password or API key.' }, page }));
  assert.throws(() => buildRequest({ settings, field: { ...field, maxLength: 0 }, page }), error => error.code === 'UNSUPPORTED_FIELD');
  assert.throws(() => buildRequest({ settings: { ...settings, apiKey: '' }, field, page }), error => error.code === 'NOT_CONFIGURED');
  assert.throws(() => buildRequest({ settings: { ...settings, enabled: false }, field, page }), error => error.code === 'DISABLED');
});

test('SSE parser survives byte-at-a-time chunks, CRLF, and multibyte Unicode', async () => {
  const events = [{ type: 'response.output_text.delta', delta: 'Café 🎉' }, { type: 'response.completed', response: { status: 'completed' } }];
  const received = [];
  for await (const event of readSseEvents(streamResponse(events, 1).body)) received.push(event);
  assert.deepEqual(received, events);
});

test('SSE parser supports multiline data and last event without final separator', async () => {
  const raw = 'data: {"type":\n' + 'data: "response.completed"}\n\n' + 'data: {"type":"response.done"}';
  const received = [];
  for await (const event of readSseEvents(new Response(raw).body)) received.push(event);
  assert.deepEqual(received.map(event => event.type), ['response.completed', 'response.done']);
});

test('generation authenticates only the fetch header and reports waiting then writing', async () => {
  const states = [];
  let calls = 0;
  const result = await generateAnswer({ settings, field, page, onState: state => states.push(state), fetchImpl: async (url, init) => {
    calls++;
    assert.equal(url, 'https://api.openai.com/v1/responses');
    assert.equal(init.headers.Authorization, `Bearer ${settings.apiKey}`);
    assert.ok(!init.body.includes(settings.apiKey));
    return streamResponse(successfulEvents());
  } });
  assert.equal(result, answer);
  assert.deepEqual(states, ['waiting', 'writing']);
  assert.equal(calls, 1);
});

test('completed response can provide answer when there were no deltas', async () => {
  const result = await generateAnswer({ settings, field, page, fetchImpl: async () => streamResponse(successfulEvents().slice(-1)) });
  assert.equal(result, answer);
});

test('authentication, quota and provider errors are actionable and never expose provider text', async () => {
  for (const [status, code, expected] of [[401, 'invalid_api_key', 'AUTHENTICATION'], [429, 'insufficient_quota', 'RATE_LIMIT'], [400, 'invalid_json_schema', 'INVALID_SCHEMA'], [404, 'model_not_found', 'MODEL_UNAVAILABLE'], [400, 'bad_request', 'API_REQUEST'], [503, 'server_error', 'API_ERROR']]) {
    let calls = 0;
    await assert.rejects(generateAnswer({ settings, field, page, fetchImpl: async () => {
      calls++;
      return new Response(JSON.stringify({ error: { code, message: 'secret provider key test-only-key' } }), { status });
    } }), error => error.code === expected && !error.message.includes('test-only-key'));
    assert.equal(calls, 1, 'failed requests must not be retried automatically');
  }
});

test('stream refusals, failures, incomplete answers, and dropped connections never fill', async () => {
  for (const [event, code] of [
    [{ type: 'response.refusal.delta', delta: 'No' }, 'REFUSED'],
    [{ type: 'response.failed', response: { error: { message: 'provider secret' } } }, 'API_ERROR'],
    [{ type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } } }, 'INCOMPLETE_RESPONSE'],
    [{ type: 'response.output_text.delta', delta: encodedAnswer }, 'INCOMPLETE_RESPONSE'],
    [{ type: 'response.completed', response: { status: 'completed', output: [{ content: [{ type: 'refusal', refusal: 'No' }] }] } }, 'REFUSED'],
  ]) {
    await assert.rejects(generateAnswer({ settings, field, page, fetchImpl: async () => streamResponse([event]) }), error => error.code === code);
  }
});

test('output-limit errors identify the cause without inserting partial answers', async () => {
  await assert.rejects(generateAnswer({ settings, field, page, fetchImpl: async () => streamResponse([
    { type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } } },
  ]) }), error => error.code === 'INCOMPLETE_RESPONSE' && /output token budget/.test(error.message));
});

test('missing personal facts produce a helpful error, not invented input', () => {
  assert.throws(() => parseResponseAnswer(JSON.stringify({ answer: '', missingInformation: 'Add your phone number to your saved profile.' }), { ...field, type: 'tel' }), error => error.code === 'MISSING_INFORMATION' && /phone number/.test(error.message));
});

test('answers are validated against field type, length, range, step, and pattern', () => {
  const value = answer => JSON.stringify({ answer, missingInformation: '' });
  assert.throws(() => parseResponseAnswer(value('long answer'), { type: 'text', maxLength: 3 }), error => error.code === 'ANSWER_TOO_LONG');
  assert.throws(() => parseResponseAnswer(value('first\nsecond'), { type: 'text' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('made-up email'), { type: 'email' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('javascript:alert(1)'), { type: 'url' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('3'), { type: 'number', min: '5' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('9'), { type: 'number', max: '8' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('2.3'), { type: 'number', step: '0.5' }), error => error.code === 'INVALID_ANSWER');
  assert.throws(() => parseResponseAnswer(value('wrong'), { type: 'text', pattern: '[A-Z]{2}\\d{3}' }), error => error.code === 'INVALID_ANSWER');
  assert.equal(parseResponseAnswer(value('2.5'), { type: 'number', min: '1', max: '3', step: '0.5' }), '2.5');
  assert.equal(parseResponseAnswer(value('user@example.test'), { type: 'email' }), 'user@example.test');
  assert.equal(parseResponseAnswer(value('https://example.test/me'), { type: 'url' }), 'https://example.test/me');
});

test('timeout and explicit cancellation abort pending fetch', async () => {
  const waitingFetch = (_, { signal }) => new Promise((_, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, { once: true });
  });
  await assert.rejects(generateAnswer({ settings, field, page, fetchImpl: waitingFetch, timeoutMs: 10 }), error => error.code === 'TIMEOUT');
  const controller = new AbortController();
  const pending = generateAnswer({ settings, field, page, signal: controller.signal, fetchImpl: waitingFetch });
  controller.abort();
  await assert.rejects(pending, error => error.code === 'CANCELLED');
});

test('public fallback errors redact unknown exceptions', () => {
  assert.deepEqual(errorToPublic(new Error('secret key/profile')), { code: 'UNEXPECTED_ERROR', message: 'Something went wrong. Please try again or check the extension diagnostics.' });
  assert.deepEqual(errorToPublic(new AgentError('CANCELLED', 'Generation cancelled.')), { code: 'CANCELLED', message: 'Generation cancelled.' });
});

test('diagnostics record only bounded metadata and never store page or user content', async () => {
  assert.deepEqual(redactMetadata({ requestId: 'test-1', durationMs: 20, pageChars: 40, apiKey: 'secret', profile: 'private', label: 'private', url: 'https://private.test', answer: 'private', code: 'bad secret string' }), { requestId: 'test-1', durationMs: 20, pageChars: 40 });
  let stored = { wcLogs: Array.from({ length: MAX_LOGS }, (_, index) => ({ event: `old-${index}` })) };
  const storage = { get: async () => stored, set: async value => { stored = value; } };
  const originalInfo = console.info;
  console.info = () => {};
  try { await logEvent('generation.completed', { requestId: 'test-1', answer: 'private', apiKey: 'secret', answerChars: 7 }, 'info', storage); }
  finally { console.info = originalInfo; }
  assert.equal(stored.wcLogs.length, MAX_LOGS);
  assert.equal(stored.wcLogs.at(-1).requestId, 'test-1');
  assert.equal(stored.wcLogs.at(-1).answerChars, 7);
  assert.ok(!JSON.stringify(stored).includes('private'));
  assert.ok(!JSON.stringify(stored).includes('secret'));
});

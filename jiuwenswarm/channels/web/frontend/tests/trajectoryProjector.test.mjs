import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { projectOtelTrajectory } from '../node_modules/.cache/trajectory-projector/projector.mjs';

function fixtureUrl(name) {
  return new URL(`../src/features/trajectory/fixtures/${name}`, import.meta.url);
}

async function fixtureRecords(name) {
  return JSON.parse(await readFile(fixtureUrl(name), 'utf8'));
}

async function projectFixture(name) {
  return projectOtelTrajectory(await fixtureRecords(name));
}

function cellsOf(snapshot) {
  return snapshot.turns.flatMap(turn => (
    turn.groups.flatMap(group => group.cells)
  ));
}

test('Core forced-close child projects as error with its diagnostic reason', async () => {
  const snapshot = await projectFixture('core-contract-records.json');
  const forcedTool = cellsOf(snapshot).find(cell => (
    cell.kind === 'tool' && cell.callId === 'call-1'
  ));

  assert.ok(forcedTool, 'authoritative tool Span should remain visible');
  assert.equal(forcedTool.status, 'error');
  assert.equal(forcedTool.isError, true);
  assert.equal(forcedTool.result, 'trace_safety_flush');
});

test('standard and OpenJiuwen fields win over conflicting legacy aliases', async () => {
  const snapshot = await projectFixture('core-contract-records.json');
  const request = snapshot.requests?.[0];
  const assistant = cellsOf(snapshot).find(cell => cell.kind === 'message');

  assert.ok(request, 'Core generation should produce a request inspector row');
  assert.equal(request.provider, 'openai');
  assert.equal(request.requestConfig?.temperature, 0);
  assert.equal(request.requestConfig?.maxTokens, 0);
  assert.equal(request.recordedFacts?.correlation?.sessionId, 'core-session');
  assert.deepEqual(request.recordedFacts?.response?.finishReasons, ['tool_calls']);
  assert.equal(request.usage?.input, 0);
  assert.equal(assistant?.text, 'Core answer');
});

test('legacy Langfuse and indexed gen_ai records remain projectable', async () => {
  const snapshot = await projectFixture('legacy-langfuse-records.json');
  const cells = cellsOf(snapshot);

  assert.equal(cells.find(cell => cell.kind === 'system')?.text, 'legacy system');
  assert.ok(cells.some(cell => cell.kind === 'message' && cell.text === 'langfuse answer'));
  assert.ok(cells.some(cell => (
    cell.kind === 'tool'
      && cell.callId === 'call-legacy'
      && cell.result?.includes('legacy-result')
  )));
  assert.ok(snapshot.requests?.some(request => (
    request.recordedFacts?.correlation?.sessionId === 'old-openjiuwen-session'
      && request.usage?.input === 0
      && request.usage?.output === 4
  )));
});

test('behavior projection tracks session prompts, repeated users, attachments, and tools', async () => {
  const snapshot = await projectFixture('agent-loop-records.json');
  const cells = cellsOf(snapshot);
  const inputCells = cells.filter(cell => (
    cell.kind === 'system' || cell.kind === 'user' || cell.kind === 'context'
  ));
  const tool = cells.find(cell => cell.kind === 'tool');
  const assistantCells = cells.filter(cell => cell.kind === 'message');
  const attachmentCells = cells.filter(cell => cell.text.includes('Dynamic context'));
  const contextCells = cells.filter(cell => cell.kind === 'context');
  const repeatedUserCells = cells.filter(cell => cell.text === 'Repeat question');
  const systemCells = cells.filter(cell => cell.kind === 'system');

  assert.deepEqual(systemCells.map(cell => cell.text), [
    'Session system v1',
    'Session system v2',
  ]);
  assert.equal(repeatedUserCells.length, 2);
  assert.equal(cells.filter(cell => cell.text === 'New context fact').length, 1);
  assert.equal(attachmentCells.length, 4);
  assert.ok(attachmentCells.every(cell => cell.kind === 'context'));
  assert.equal(contextCells.length, attachmentCells.length);
  assert.ok(attachmentCells.every(cell => (
    cell.messageSource.role === 'user'
      && cell.messageSource.kind === 'prompt_attachment'
  )));
  assert.deepEqual(
    attachmentCells.map(cell => cell.messageSource.inputIndex),
    [1, 3, 5, 4],
  );
  assert.ok(inputCells.every(cell => cell.timeSeconds === null));

  assert.ok(tool, 'authoritative tool Span should produce one tool item');
  assert.equal(tool.callId, 'call-loop-1');
  assert.match(tool.inputDetail, /"q": "fact"/);
  assert.match(tool.outputDetail, /"answer": 42/);
  assert.equal(tool.timeSeconds, 0.2);

  assert.deepEqual(assistantCells.map(cell => cell.text), [
    'Historical output',
    'I will search',
    'The answer is 42',
    'Done',
  ]);
  assert.deepEqual(assistantCells.map(cell => cell.timeSeconds), [1, 1, 0.6, 0.7]);
  assert.ok(inputCells.every(input => assistantCells.every(assistant => (
    input.timeSeconds === null
      || input.startedAt + input.timeSeconds * 1_000 <= assistant.startedAt
      || assistant.startedAt + assistant.timeSeconds * 1_000 <= input.startedAt
  ))));
});

test('legacy tool records without call ids use trace-local name and order fallback', async () => {
  const records = await fixtureRecords('agent-loop-records.json');
  const spans = records.flatMap(record => (
    record.resourceSpans.flatMap(resource => (
      resource.scopeSpans.flatMap(scope => scope.spans)
    ))
  ));
  for (const span of spans) {
    span.attributes = span.attributes.filter(attribute => (
      attribute.key !== 'gen_ai.tool.call.id'
    ));
    for (const attribute of span.attributes) {
      if (
        attribute.key !== 'gen_ai.input.messages'
        && attribute.key !== 'gen_ai.output.messages'
      ) continue;
      const messages = JSON.parse(attribute.value.stringValue);
      for (const message of messages) {
        for (const part of message.parts) delete part.id;
      }
      attribute.value.stringValue = JSON.stringify(messages);
    }
  }

  const tool = cellsOf(projectOtelTrajectory(records)).find(cell => cell.kind === 'tool');
  assert.ok(tool);
  assert.match(tool.outputDetail, /"answer": 42/);
});

test('additive OpenJiuwen provenance identifies attachments before XML fallback', async () => {
  const records = await fixtureRecords('agent-loop-records.json');
  for (const record of records) {
    for (const resource of record.resourceSpans) {
      for (const scope of resource.scopeSpans) {
        for (const span of scope.spans) {
          const provenance = [];
          for (const attribute of span.attributes) {
            if (attribute.key !== 'gen_ai.input.messages') continue;
            const messages = JSON.parse(attribute.value.stringValue);
            for (const [index, message] of messages.entries()) {
              const attachment = message.parts.some(part => (
                part.type === 'text' && part.content?.includes('Dynamic context')
              ));
              if (!attachment) continue;
              message.parts = [{ type: 'text', content: 'metadata attachment' }];
              provenance.push({
                request_message_index: index + 1,
                input_message_index: index,
                kind: 'prompt_attachment',
                scope: 'request',
                items: [],
              });
            }
            attribute.value.stringValue = JSON.stringify(messages);
          }
          if (provenance.length > 0) {
            span.attributes.push({
              key: 'openjiuwen.gen_ai.input.message_provenance',
              value: { stringValue: JSON.stringify(provenance) },
            });
          }
        }
      }
    }
  }

  const attachments = cellsOf(projectOtelTrajectory(records))
    .filter(cell => cell.text === 'metadata attachment');
  assert.equal(attachments.length, 4);
  assert.ok(attachments.every(cell => (
    cell.kind === 'context'
      && cell.messageSource.role === 'user'
      && cell.messageSource.kind === 'prompt_attachment'
  )));
});

test('ordered snapshots preserve reintroductions while output replay expires after one inference', async () => {
  const snapshot = await projectFixture('behavior-edge-records.json');
  const cells = cellsOf(snapshot);
  const inputCells = cells.filter(cell => (
    cell.kind === 'system' || cell.kind === 'user' || cell.kind === 'context'
  ));

  assert.deepEqual(
    cells.filter(cell => cell.kind === 'system').map(cell => cell.text),
    ['Stable system'],
  );
  assert.deepEqual(
    cells.filter(cell => cell.kind === 'message').map(cell => cell.text),
    ['Same output', 'Same output', 'Same output', 'Final output', 'Unique output', 'End output'],
  );
  assert.equal(cells.filter(cell => cell.kind === 'user' && cell.text === 'A').length, 1);
  assert.equal(cells.filter(cell => cell.kind === 'user' && cell.text === 'B').length, 3);
  assert.equal(cells.filter(cell => cell.kind === 'user' && cell.text === 'C').length, 1);
  assert.deepEqual(
    cells.filter(cell => cell.kind === 'context' && cell.text === 'Same output')
      .map(cell => cell.messageSource.inputIndex),
    [3],
  );
  assert.ok(inputCells.every(cell => cell.timeSeconds === null));
  assert.deepEqual(
    snapshot.requests?.map(request => request.model),
    ['model-a', 'model-b', 'model-c', 'model-d', 'model-e', 'model-f'],
  );
});

test('adjacent assistant replay uses stable text and tool-call projections across shape changes', async () => {
  const snapshot = await projectFixture('output-replay-shape-records.json');
  const contextCells = cellsOf(snapshot).filter(cell => cell.kind === 'context');

  assert.deepEqual(contextCells.map(cell => cell.text), ['Reasoned answer']);
  assert.equal(contextCells[0]?.messageSource.role, 'assistant');
  assert.equal(contextCells[0]?.messageSource.inputIndex, 3);
  assert.ok(contextCells.every(cell => cell.timeSeconds === null));
});

test('provisional inference projects running lifecycle without a fabricated end time', async () => {
  const records = await fixtureRecords('standard-records.json');
  const inferenceRecord = records.find(record => record.resourceSpans.some(resource => (
    resource.scopeSpans.some(scope => scope.spans.some(span => (
      span.attributes.some(attribute => (
        attribute.key === 'openjiuwen.trajectory.record.kind'
          && attribute.value.stringValue === 'inference'
      ))
    )))
  )));
  assert.ok(inferenceRecord);
  const inferenceSpan = inferenceRecord.resourceSpans
    .flatMap(resource => resource.scopeSpans)
    .flatMap(scope => scope.spans)
    .find(span => span.attributes.some(attribute => (
      attribute.key === 'openjiuwen.trajectory.record.kind'
        && attribute.value.stringValue === 'inference'
    )));
  assert.ok(inferenceSpan);
  delete inferenceSpan.endTimeUnixNano;
  const identity = `${inferenceSpan.traceId}:${inferenceSpan.spanId}`;
  const snapshot = projectOtelTrajectory(records, {
    lifecycleByRecordId: new Map([[identity, 'running']]),
  });
  const assistant = cellsOf(snapshot).find(cell => cell.recordId === `${identity}:assistant`);
  const request = snapshot.requests?.find(candidate => candidate.status === 'running');

  assert.ok(assistant);
  assert.equal(assistant.status, 'running');
  assert.equal(assistant.timeSeconds, null);
  assert.equal(assistant.assistantMetrics.completedTime, null);
  assert.ok(request);
  assert.equal(request.completedAt, null);
});

test('every inference keeps an independent request identity inside a shared step', async () => {
  const records = await fixtureRecords('agent-loop-records.json');
  for (const record of records) {
    for (const resource of record.resourceSpans) {
      for (const scope of resource.scopeSpans) {
        for (const span of scope.spans) {
          if (span.name === 'llm.call') {
            span.attributes.push({
              key: 'openjiuwen.inference.id',
              value: { stringValue: span.spanId },
            });
          }
          for (const attribute of span.attributes) {
            if (attribute.key !== 'openjiuwen.step.number') continue;
            attribute.value = { intValue: '1' };
          }
        }
      }
    }
  }
  const snapshot = projectOtelTrajectory(records);
  const assistants = cellsOf(snapshot).filter(cell => cell.kind === 'message');
  const requestIds = snapshot.requests?.map(request => request.recordId) ?? [];

  assert.equal(snapshot.turns[0].groups[0].title, 'Step 1');
  assert.equal(new Set(requestIds).size, requestIds.length);
  assert.ok(requestIds.every(recordId => typeof recordId === 'string'));
  assert.ok(requestIds.every(recordId => recordId.includes(':inference:')));
  assert.deepEqual(
    assistants.map(cell => cell.requestRecordId),
    requestIds,
  );
});

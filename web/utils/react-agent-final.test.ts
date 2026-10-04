import assert from 'node:assert/strict';
import test from 'node:test';

import { decodeAgentFinalAnswer, decodeAgentHistoryAnswer } from './react-agent-final';

test('decodes protocol v2 final answer without mixing citations into content', () => {
  const answer = decodeAgentFinalAnswer({
    type: 'final',
    protocol_version: 2,
    content: 'Conclusion from knowledge base [1].',
    citations: [
      {
        index: 1,
        id: 'chunk-1',
        sourceName: 'sales.md',
        excerpt: 'East China sales grew 12% YoY.',
        score: 0.91,
        path: '/reports/sales.md',
      },
    ],
  });

  assert.deepEqual(answer, {
    content: 'Conclusion from knowledge base [1].',
    citations: [
      {
        index: 1,
        id: 'chunk-1',
        sourceName: 'sales.md',
        excerpt: 'East China sales grew 12% YoY.',
        score: 0.91,
        path: '/reports/sales.md',
      },
    ],
  });
});

test('strips legacy generic references without promoting script output to a citation', () => {
  const legacyPayload = JSON.stringify([
    {
      name: 'Knowledge Base',
      chunks: [
        {
          index: 1,
          id: 7,
          content: "def log():\n    print('do not leak script output')",
          recall_score: 0.82,
        },
      ],
    },
  ]);
  const answer = decodeAgentFinalAnswer(
    `Page content output completed.\n\n<references title="References" references='${legacyPayload}'></references>`,
  );

  assert.equal(answer.content, 'Page content output completed.');
  assert.deepEqual(answer.citations, []);
});

test('keeps an oversized legacy references envelope out of visible summary content', () => {
  const leakedScript = "do not leak script source\nprint('secret')\n".repeat(2500);
  const legacyPayload = JSON.stringify([
    {
      name: 'Knowledge Base',
      chunks: [{ index: 1, id: 7, content: leakedScript }],
    },
  ]);

  const answer = decodeAgentFinalAnswer(
    `Analysis summary completed.\n\n<references title="References" references='${legacyPayload}'></references>`,
  );

  assert.deepEqual(answer, { content: 'Analysis summary completed.', citations: [] });
  assert.equal(answer.content.includes('do not leak script source'), false);
});

test('does not trust a legacy SQL result label as a document identity', () => {
  const legacyPayload = JSON.stringify([
    {
      name: 'SQL Results',
      chunks: [{ index: 1, id: 8, content: 'SELECT secret FROM credentials' }],
    },
  ]);

  const answer = decodeAgentFinalAnswer(
    `Query completed<references title="References" references='${legacyPayload}'></references>`,
  );

  assert.deepEqual(answer, { content: 'Query completed', citations: [] });
});

test('keeps a legacy citation when a generic group carries a concrete document path', () => {
  const legacyPayload = JSON.stringify([
    {
      name: 'Knowledge Base',
      chunks: [
        {
          index: 1,
          id: 9,
          sourceName: 'Knowledge Base',
          file_path: '/handbook/security.md',
          content: 'Never expose tool output as final answer content.',
        },
      ],
    },
  ]);

  const answer = decodeAgentFinalAnswer(
    `Security conclusion<references title='References' references='${legacyPayload}'></references>`,
  );

  assert.deepEqual(answer, {
    content: 'Security conclusion',
    citations: [
      {
        index: 1,
        id: '9',
        sourceName: '/handbook/security.md',
        excerpt: 'Never expose tool output as final answer content.',
        path: '/handbook/security.md',
      },
    ],
  });
});

test('finds the outer legacy envelope when a cited excerpt contains references markup', () => {
  const excerpt = 'Doc example: <references title="References" references=\'[{"fake":true}]\'></references>';
  const legacyPayload = JSON.stringify([
    {
      name: 'guide.md',
      chunks: [{ index: 1, id: 8, content: excerpt }],
    },
  ]);

  const answer = decodeAgentFinalAnswer(
    `Normal answer\n\n<references title="References" references='${legacyPayload}'></references>`,
  );

  assert.deepEqual(answer, {
    content: 'Normal answer',
    citations: [{ index: 1, id: '8', sourceName: 'guide.md', excerpt }],
  });
});

test('fails closed for malformed legacy reference payload while keeping the answer clean', () => {
  const answer = decodeAgentFinalAnswer(
    `Normal answer\n\n<references title="References" references='[{broken json}]'></references>`,
  );

  assert.deepEqual(answer, { content: 'Normal answer', citations: [] });
});

test('decodes the XML-escaped self-closing legacy envelope', () => {
  const answer = decodeAgentFinalAnswer(
    'Legacy knowledge Q&A\n<references title="References" references="[{&quot;name&quot;:&quot;guide.md&quot;,&quot;chunks&quot;:[{&quot;index&quot;:1,&quot;id&quot;:9,&quot;content&quot;:&quot;Quoted content&quot;}]}]" />',
  );

  assert.deepEqual(answer, {
    content: 'Legacy knowledge Q&A',
    citations: [
      {
        index: 1,
        id: '9',
        sourceName: 'guide.md',
        excerpt: 'Quoted content',
      },
    ],
  });
});

test('does not strip references-like text that is not the legacy trailing envelope', () => {
  const content = 'Example code: `<references title="demo">`, followed by body text.';
  assert.deepEqual(decodeAgentFinalAnswer(content), { content, citations: [] });
});

test('normalizes persisted history final_content and structured citations', () => {
  const answer = decodeAgentHistoryAnswer({
    version: 1,
    type: 'react-agent',
    final_content: 'History conclusion [1]',
    citations: [
      {
        index: 1,
        id: 3,
        source_name: 'handbook.md',
        content: 'History knowledge excerpt',
        recall_score: '0.75',
      },
    ],
  });

  assert.deepEqual(answer, {
    content: 'History conclusion [1]',
    citations: [
      {
        index: 1,
        id: '3',
        sourceName: 'handbook.md',
        excerpt: 'History knowledge excerpt',
        score: 0.75,
      },
    ],
  });
});

test('keeps ordinary JSON answers even when they contain content and citations keys', () => {
  const content = JSON.stringify({
    content: 'This is user-visible JSON, not a history envelope.',
    citations: [{ sourceName: 'model-output', excerpt: 'must not be trusted' }],
  });

  assert.deepEqual(decodeAgentHistoryAnswer(content), {
    content,
    citations: [],
  });
});

test('drops malformed citations instead of exposing arbitrary tool output', () => {
  const answer = decodeAgentFinalAnswer({
    content: 'Safe answer',
    citations: [{ index: 1, sourceName: 'missing excerpt' }, "print('tool output')", null],
  });

  assert.deepEqual(answer, { content: 'Safe answer', citations: [] });
});

test('bounds citation count and excerpt size at the compatibility seam', () => {
  const citations = Array.from({ length: 12 }, (_, offset) => ({
    index: offset + 1,
    id: `chunk-${offset + 1}`,
    sourceName: `doc-${offset + 1}.md`,
    excerpt: 'x'.repeat(3_000),
  }));

  const answer = decodeAgentFinalAnswer({ content: 'answer', citations });

  assert.equal(answer.citations.length, 6);
  assert.ok(answer.citations.every(citation => citation.excerpt.length === 2_000));
  assert.equal(
    answer.citations.reduce((total, citation) => total + citation.excerpt.length, 0),
    12_000,
  );
});

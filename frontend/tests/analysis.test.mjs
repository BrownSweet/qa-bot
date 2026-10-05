import test from 'node:test'
import assert from 'node:assert/strict'
import { sameSourceIdentity, templateParameters, templateError, compareTaskRuns, evaluationSummary } from '../src/utils/analysis.mjs'

test('template placeholders match the backend length and syntax contract', () => {
  assert.deepEqual(templateParameters('销售 {{month}} {{region}} {{month}}'), ['month', 'region'])
  assert.equal(templateError('{{' + 'a'.repeat(32) + '}}'), '')
  for (const value of ['{{' + 'a'.repeat(33) + '}}', '{{1month}}', '{{月份}}', '{{month-name}}', '{{month', 'month}}']) {
    assert.ok(templateError(value), value)
  }
  assert.deepEqual(templateParameters('{{' + 'a'.repeat(33) + '}}'), [])
})

test('same-source retries ignore rename but detect a changed database, account or file', () => {
  const source = { type: 'mysql', host: 'localhost', port: 3306, database: 'sales', username: 'report' }
  assert.equal(sameSourceIdentity(source, { ...source, name: '新名称', port: '3306' }), true)
  for (const field of ['type', 'host', 'port', 'database', 'username', 'file_path']) {
    assert.equal(sameSourceIdentity(source, { ...source, [field]: 'changed' }), false, field)
  }
  assert.equal(sameSourceIdentity(null, source), false)
})

test('task history comparison checks the selected runs, SQL and saved scope', () => {
  const source = { type: 'sqlite', file_path: '/tmp/sales.sqlite' }
  const coverage = { row_limit: 200, returned_rows: 1, analyzed_rows: 1, has_more: false }
  const first = { status: 'completed', source, evidence: {
    sql: 'SELECT SUM(amount) AS total FROM sales LIMIT 201', columns: ['total'], rows: [[10]], coverage,
  } }
  const second = { ...first, evidence: { ...first.evidence, rows: [[20]] } }
  assert.equal(compareTaskRuns([first]), null)
  assert.deepEqual(compareTaskRuns([first, second]), { comparable: true, reasons: [] })

  const changed = { ...second, source: { ...source, file_path: '/tmp/other.sqlite' },
    evidence: { ...second.evidence, sql: 'SELECT AVG(amount) AS total FROM sales LIMIT 201',
      coverage: { ...coverage, has_more: true } } }
  const result = compareTaskRuns([first, changed])
  assert.equal(result.comparable, false)
  assert.match(result.reasons.join('；'), /数据源/)
  assert.match(result.reasons.join('；'), /SQL/)
  assert.match(result.reasons.join('；'), /部分结果/)
  assert.match(compareTaskRuns([first, { ...second, status: 'error', evidence: null }]).reasons.join('；'), /没有完整完成/)
})

test('evaluation percentages exclude transport-independent execution errors and changed baselines', () => {
  assert.equal(evaluationSummary({}).percent, null)
  assert.deepEqual(evaluationSummary({ a: { status: 'passed', matched: true }, b: { status: 'mismatch', matched: false }, c: { status: 'error' } }),
    { completed: 3, confirmed: 2, review: 0, errors: 1, passed: 1, failed: 1, percent: 50 })
  assert.equal(evaluationSummary({ a: { status: 'passed', matched: false } }).passed, 0)
  assert.deepEqual(evaluationSummary({ a: { status: 'passed', matched: true, baseline_changed: true } }),
    { completed: 1, confirmed: 0, review: 1, errors: 0, passed: 0, failed: 0, percent: null })
  assert.deepEqual(evaluationSummary({ a: { status: 'error' }, b: { status: 'error' } }),
    { completed: 2, confirmed: 0, review: 0, errors: 2, passed: 0, failed: 0, percent: null })
})

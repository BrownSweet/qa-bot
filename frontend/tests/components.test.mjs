import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { questionForReply, shouldSendOnEnter } from '../src/utils/chat.mjs'
import { sameSourceIdentity, templateParameters, templateError, evaluationSummary } from '../src/utils/analysis.mjs'

const require = createRequire(import.meta.url)
const compiler = require('vue/compiler-sfc')
const sourceRoot = new URL('../src/', import.meta.url)

async function loadComponent(path, bindings = {}) {
  const source = await readFile(new URL(path, sourceRoot), 'utf8')
  const { script } = compiler.parse({ source })
  const code = script.content.replace(/^import .+$/gm, '').replace('export default', 'return')
  return new Function(...Object.keys(bindings), code)(...Object.values(bindings))
}

function instance(options, extras = {}) {
  const defaults = Object.fromEntries(Object.entries(options.props || {}).map(([key, prop]) => [key,
    prop && typeof prop.default === 'function' ? prop.default() : prop && prop.default]))
  const vm = { ...defaults, ...options.data(), $emit() {}, $message: { warning() {}, info() {}, error() {}, success() {} },
    $set(object, key, value) { object[key] = value }, $delete(object, key) { delete object[key] },
    $nextTick(fn) { return Promise.resolve().then(fn) }, ...extras }
  for (const [name, method] of Object.entries(options.methods || {})) vm[name] = method.bind(vm)
  for (const [name, getter] of Object.entries(options.computed || {})) {
    if (!(name in extras)) Object.defineProperty(vm, name, { get: getter.bind(vm) })
  }
  return vm
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

async function chatComponent(api) {
  return loadComponent('components/chat/ChatPanel.vue', { api, questionForReply, sameSourceIdentity, MessageList: {}, ChatInput: {} })
}

test('switching sessions aborts the old stream and ignores its queued callbacks', async () => {
  let callbacks
  let aborted = false
  const options = await chatComponent({
    chatStream(payload, handlers) { callbacks = handlers; return { abort() { aborted = true } } },
    async getMessages() { return { messages: [{ id: 'b-history', role: 'user', content: 'B 问题' }] } },
  })
  const vm = instance(options, { sessionId: 'A', dbConfigId: 'db' })
  vm.send('A 问题')
  callbacks.onMessage({ content: 'A 部分回答' })
  vm.sessionId = 'B'
  options.watch.sessionId.handler.call(vm)
  await Promise.resolve()
  callbacks.onMessage({ content: 'A 旧片段' })
  callbacks.onComplete({ message_id: 'a-answer', status: 'completed' })
  assert.equal(aborted, true)
  assert.deepEqual(vm.messages.map((m) => m.id), ['b-history'])
  assert.equal(vm.typing, false)
  assert.equal(vm.typingContent, '')
})

test('late history from the old session cannot replace the current history', async () => {
  const a = deferred()
  const b = deferred()
  const options = await chatComponent({ getMessages: (id) => id === 'A' ? a.promise : b.promise })
  const vm = instance(options, { sessionId: 'A' })
  const first = vm.loadMessages()
  vm.sessionId = 'B'
  const second = vm.loadMessages()
  b.resolve({ messages: [{ id: 'b' }] })
  await second
  a.resolve({ messages: [{ id: 'a' }] })
  await first
  assert.deepEqual(vm.messages, [{ id: 'b' }])
  assert.equal(vm.loading, false)
})

test('error status plus error message produces one visible error', async () => {
  let callbacks
  const options = await chatComponent({ chatStream(payload, handlers) { callbacks = handlers; return { abort() {} } } })
  const vm = instance(options, { sessionId: 'A', dbConfigId: 'db' })
  vm.send('问题')
  callbacks.onStatus({ status: 'error', message: '连接失败' })
  callbacks.onMessage({ content: '连接失败' })
  callbacks.onComplete({ message_id: 'answer', status: 'error' })
  assert.equal(vm.messages.at(-1).content, '连接失败')
  assert.equal(vm.messages.at(-1).status, 'error')
  assert.equal(vm.typing, false)
})

test('completed chat keeps the saved generation snapshot even without query evidence', async () => {
  let callbacks
  const options = await chatComponent({ chatStream(payload, handlers) { callbacks = handlers; return { abort() {} } } })
  const vm = instance(options, { sessionId: 'A', dbConfigId: 'db' })
  const snapshot = { model: 'model-at-run', schema: 'CREATE TABLE old_data (value INT)', question: '当次问题', history: [] }
  vm.send('当次问题')
  callbacks.onComplete({ message_id: 'a1', status: 'error', generation_snapshot: snapshot })
  assert.deepEqual(vm.messages.at(-1).generation_snapshot, snapshot)
  assert.equal(vm.messages.at(-1).evidence, null)
})

test('stop preserves partial text as cancelled and ignores a late completion', async () => {
  let callbacks
  let aborted = false
  const options = await chatComponent({
    chatStream(payload, handlers) { callbacks = handlers; return { abort() { aborted = true } } },
  })
  const vm = instance(options, { sessionId: 'A', dbConfigId: 'db' })
  vm.send('问题')
  callbacks.onMessage({ content: '部分内容' })
  vm.stop()
  callbacks.onComplete({ message_id: 'late', status: 'completed' })
  assert.equal(aborted, true)
  assert.equal(vm.messages.length, 2)
  assert.equal(vm.messages.at(-1).content, '部分内容')
  assert.equal(vm.messages.at(-1).status, 'cancelled')
  assert.equal(vm.typing, false)
})

test('no data source does not discard the question draft', async () => {
  const options = await loadComponent('components/chat/ChatInput.vue', { shouldSendOnEnter })
  let sent = false
  const vm = instance(options, { text: '保留这段问题', dbSelected: false, disabled: false, $emit() { sent = true } })
  vm.submit()
  assert.equal(vm.text, '保留这段问题')
  assert.equal(sent, false)
})

test('desktop settings do not request the web account profile', async () => {
  let profileRequested = false
  const options = await loadComponent('views/SettingsView.vue', {
    isDesktop: true,
    desktop: {},
    api: {
      async getReadiness() { return { can_manage_ai: true } },
      async getSystemConfig() { return { config: { api_url: 'https://example.invalid', timeout: 30, api_key_set: false } } },
      async getProfile() { profileRequested = true; throw new Error('must not be called') },
    },
  })
  const vm = instance(options)
  await options.mounted.call(vm)
  assert.equal(profileRequested, false)
  assert.equal(vm.sys.api_key_set, false)
  assert.equal(vm.settingsError, '')
})

test('desktop token stays in memory and does not access localStorage', async () => {
  const source = await readFile(new URL('store/index.js', sourceRoot), 'utf8')
  const code = source.replace(/^import .+$/gm, '').replace('export default store', 'return store')
  const storage = new Proxy({}, { get() { throw new Error('desktop must not use localStorage') } })
  const store = new Function('Vue', 'Vuex', 'isDesktop', 'localStorage', code)(
    { use() {} }, { Store: class { constructor(options) { Object.assign(this, options) } } }, true, storage
  )
  store.mutations.setAuth(store.state, { token: 'test-local-token', user: { id: 'local' } })
  assert.equal(store.state.token, 'test-local-token')
  store.mutations.setUser(store.state, { id: 'local', username: '本地用户' })
  store.mutations.clearAuth(store.state)
  assert.equal(store.state.token, '')
})

test('desktop copy uses the write-only bridge and reports failure visibly', async () => {
  let copied = ''
  let notice = ''
  let rejected = false
  const options = await loadComponent('components/chat/MessageList.vue', {
    renderMarkdown: (text) => text,
    QueryEvidence: {},
    GenerationSnapshot: {},
    isDesktop: true,
    desktop: { async copyText(text) { if (rejected) throw new Error('剪贴板写入失败'); copied = text } },
  })
  const vm = { $message: { success(text) { notice = text }, error(text) { notice = text } } }
  await options.methods.copy.call(vm, '需要复制的回答')
  assert.equal(copied, '需要复制的回答')
  assert.equal(notice, '已复制')
  rejected = true
  await options.methods.copy.call(vm, '第二条回答')
  assert.equal(notice, '剪贴板写入失败')
})

test('web copy continues to use the browser clipboard', async () => {
  let copied = ''
  const options = await loadComponent('components/chat/MessageList.vue', {
    renderMarkdown: (text) => text,
    QueryEvidence: {},
    GenerationSnapshot: {},
    isDesktop: false,
    desktop: null,
    navigator: { clipboard: { async writeText(text) { copied = text } } },
  })
  let success = false
  await options.methods.copy.call({ $message: { success() { success = true }, error() {} } }, 'Web 回答')
  assert.equal(copied, 'Web 回答')
  assert.equal(success, true)
})

test('all Vue templates compile', async () => {
  async function inspect(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory)
      if (entry.isDirectory()) await inspect(file)
      else if (entry.name.endsWith('.vue')) {
        const source = await readFile(file, 'utf8')
        const parsed = compiler.parse({ source })
        const result = compiler.compileTemplate({ source: parsed.template.content, filename: fileURLToPath(file) })
        assert.deepEqual(result.errors, [], entry.name)
      }
    }
  }
  await inspect(sourceRoot)
})

test('completed task chat preserves evidence, exact parameters and real question linkage', async () => {
  let callbacks, payload
  const source = { id: 'db', name: '销售', type: 'sqlite', file_path: '/sample.db' }
  const options = await chatComponent({ chatStream(body, handlers) { payload = body; callbacks = handlers; return { abort() {} } } })
  const vm = instance(options, { sessionId: 'session', dbConfigId: 'db', configs: [source] })
  vm.send('2026-09 销售额', { analysis_task_id: 'task', analysis_task_parameters: { month: '2026-09' } })
  assert.deepEqual(payload.analysis_task_parameters, { month: '2026-09' })
  const evidence = { sql: 'SELECT sum(amount) FROM sales LIMIT 1000', columns: ['total'], rows: [[42]], coverage: { returned_rows: 1 } }
  callbacks.onResult({ message_id: 'answer', source, evidence })
  callbacks.onMessage({ content: '销售额为 42。' })
  callbacks.onComplete({ message_id: 'answer', question_message_id: 'question', db_config_id: 'db', status: 'completed', analysis_task_id: 'task' })
  assert.equal(vm.messages[0].id, 'question')
  assert.equal(vm.messages[1].question_message_id, 'question')
  assert.equal(vm.messages[1].analysis_task_id, 'task')
  assert.deepEqual(vm.messages[1].evidence, evidence)
})

test('retry uses the historical source and respects changed-source cancellation', async () => {
  const original = { id: 'db1', type: 'sqlite', file_path: '/old.db', name: '原来源' }
  let sent, confirmed = 0
  const options = await chatComponent({ chatStream(payload) { sent = payload; return { abort() {} } } })
  const vm = instance(options, { sessionId: 'session', dbConfigId: 'db2', configs: [original],
    $confirm: async () => { confirmed++ } })
  const history = () => [{ id: 'q1', role: 'user', content: '第一问' }, { id: 'a1', role: 'assistant', db_config_id: 'db1', source: original, question_message_id: 'q1' }]
  vm.messages = history()
  await vm.retry('a1')
  assert.equal(confirmed, 1)
  assert.equal(sent.db_config_id, 'db1')
  assert.equal(sent.question, '第一问')
  vm.cancelStream(); vm.messages = history(); sent = null
  vm.configs = [{ ...original, file_path: '/changed.db' }]
  vm.$confirm = async () => { throw new Error('cancel') }
  await vm.retry('a1')
  assert.equal(sent, null)
})

test('ordinary web settings load profile without calling the admin AI endpoint', async () => {
  let adminCalls = 0
  const options = await loadComponent('views/SettingsView.vue', { isDesktop: false, desktop: null, api: {
    async getReadiness() { return { can_manage_ai: false, ai_ready: true } },
    async getSystemConfig() { adminCalls++; throw new Error('403') },
    async getProfile() { return { user: { username: '普通用户', phone: 'test' } } },
  } })
  const vm = instance(options)
  await options.mounted.call(vm)
  assert.equal(vm.profile.username, '普通用户')
  assert.equal(vm.tab, 'profile')
  assert.equal(adminCalls, 0)
  assert.equal(vm.settingsError, '')
})

test('local maintenance treats native cancellation as neutral and shows bridge errors', async () => {
  let restarted = false
  const options = await loadComponent('views/SettingsView.vue', { isDesktop: true, api: {}, desktop: {
    async backup() { return null }, async inspectBackup() { throw new Error('备份不完整') }, async restartBackend() { restarted = true },
  } })
  const vm = instance(options, { $confirm: async () => { throw new Error('cancel') } })
  await vm.maintenance('backup')
  assert.equal(vm.maintenanceMessage, '')
  await vm.maintenance('inspectBackup')
  assert.equal(vm.maintenanceError, true)
  assert.equal(vm.maintenanceMessage, '备份不完整')
  assert.equal(vm.maintenanceAction, '')
  await vm.maintenance('restartBackend')
  assert.equal(restarted, false)
})

test('data source editing preserves custom port and never replaces a hidden password', async () => {
  let payload
  const options = await loadComponent('components/db/DbConfigPanel.vue', { isDesktop: false, desktop: null, ResultTable: {}, api: {
    async updateDbConfig(id, body) { payload = { id, ...body }; return { config: { id } } },
    async getDbConfigs() { return { configs: [] } },
  } })
  const vm = instance(options)
  vm.openEdit({ id: 'source', type: 'postgresql', name: '分析库', host: 'localhost', port: 5544, username: 'reader', database: 'sales', password: 'redacted' })
  assert.equal(vm.form.port, 5544)
  assert.equal(vm.form.password, '')
  await vm.save()
  assert.equal(payload.port, 5544)
  assert.equal(payload.password, '')
})

test('save and test keeps the old source on failure and refreshes only after success', async () => {
  let submitted, refreshes = 0, directUpdates = 0, reachable = false
  const options = await loadComponent('components/db/DbConfigPanel.vue', { isDesktop: false, desktop: null, ResultTable: {}, api: {
    async testAndUpdateDbConfig(id, body) {
      submitted = { id, ...body }
      return reachable ? { success: true, message: '连接成功，配置已保存' }
        : { success: false, message: '连接失败；原配置未修改' }
    },
    async updateDbConfig() { directUpdates++ },
    async getDbConfigs() { refreshes++; return { configs: [] } },
  } })
  const vm = instance(options)
  vm.openEdit({ id: 'source', type: 'mysql', name: '原来源', host: 'old.example', port: 3306,
    database: 'sales', username: 'reader' })
  vm.form.host = 'bad.example'
  await vm.doTest()
  assert.deepEqual(submitted, { id: 'source', name: '原来源', type: 'mysql', host: 'bad.example',
    port: 3306, database: 'sales', username: 'reader', password: '' })
  assert.equal(vm.testOk, false)
  assert.equal(vm.testMsg, '连接失败；原配置未修改')
  assert.equal(refreshes, 0)
  assert.equal(directUpdates, 0)
  reachable = true
  await vm.doTest()
  assert.equal(vm.testOk, true)
  assert.equal(refreshes, 1)
  assert.equal(directUpdates, 0)
})

test('failed semantics load cannot overwrite existing unseen business rules', async () => {
  let saved = false
  const options = await loadComponent('components/db/DbConfigPanel.vue', { isDesktop: false, desktop: null, ResultTable: {}, api: {
    async getDbSchema() { return { tables: [] } }, async getDbSemantics() { throw new Error('连接中断') },
    async updateDbSemantics() { saved = true },
  } })
  const vm = instance(options)
  await vm.inspect({ id: 'source', name: '来源' })
  assert.equal(vm.semanticsLoaded, false)
  assert.equal(vm.semanticsError, '连接中断')
  await vm.saveSemantics()
  assert.equal(saved, false)
})

test('template preview rejects stale responses and forwards the approved parameters', async () => {
  const pending = deferred()
  let emitted
  const options = await loadComponent('components/analysis/AnalysisTasks.vue', { templateParameters, templateError, sameSourceIdentity, ResultTable: {}, QueryEvidence: {}, GenerationSnapshot: {}, api: {
    prepareAnalysisTask() { return pending.promise },
  } })
  const vm = instance(options, { $emit(event, body) { emitted = { event, body } } })
  vm.activeId = 'task'; vm.form = { name: '按月销售', question_template: '查询 {{month}}', db_config_id: 'db' }
  vm.savedSignature = JSON.stringify(vm.form); vm.parameters = { month: '九月' }
  const preparing = vm.prepare()
  vm.parameters.month = '十月'
  pending.resolve({ question: '查询 九月', analysis_task_id: 'task', db_config_id: 'db' })
  await preparing
  assert.equal(vm.prepared, null)
  vm.prepared = { question: '查询 十月', analysis_task_id: 'task', db_config_id: 'db' }
  await vm.execute()
  assert.deepEqual(emitted.body.analysis_task_parameters, { month: '十月' })
})

test('evaluation stop completes the current real run and skips the next case', async () => {
  const pending = deferred()
  const executed = []
  const options = await loadComponent('components/analysis/EvaluationPanel.vue', { evaluationSummary, ResultTable: {}, GenerationSnapshot: {}, api: {
    runEvaluationCase(id) { executed.push(id); return pending.promise },
  } })
  const vm = instance(options, { cases: [{ id: 'a' }, { id: 'b' }] })
  const running = vm.runAll()
  assert.equal(vm.currentId, 'a')
  vm.stopRequested = true
  pending.resolve({ run: { id: 'real-run', status: 'mismatch', matched: false } })
  await running
  assert.deepEqual(executed, ['a'])
  assert.equal(vm.summary.completed, 1)
  assert.equal(vm.summary.percent, 0)
  assert.equal(vm.running, false)
})

test('evaluation transport failures are visible and excluded from claimed completed results', async () => {
  const options = await loadComponent('components/analysis/EvaluationPanel.vue', { evaluationSummary, ResultTable: {}, GenerationSnapshot: {}, api: {
    async runEvaluationCase() { throw new Error('请求中断') },
  } })
  const vm = instance(options)
  await vm.executeCase({ id: 'a' })
  assert.equal(vm.requestErrors.a, '请求中断')
  assert.equal(vm.summary.completed, 0)
  assert.equal(vm.summary.percent, null)
})

test('restoring the selected session restores its own data source', async () => {
  const options = await loadComponent('views/DashboardView.vue', { api: {}, isDesktop: true, DbConfigPanel: {}, SessionList: {}, ChatPanel: {}, AnalysisTasks: {}, EvaluationPanel: {} })
  let saved
  const vm = instance(options, { $store: { state: { workspace: { sessionId: 'old' } }, commit(name, value) { saved = value } } })
  vm.sessionsLoaded([{ id: 'new', db_config_id: 'new-db' }, { id: 'old', db_config_id: 'old-db' }])
  assert.equal(vm.activeSessionId, 'old')
  assert.equal(vm.activeDbId, 'old-db')
  assert.deepEqual(saved, { sessionId: 'old', dbConfigId: 'old-db' })
})

test('a retry confirmation cannot dispatch into a different newly selected session', async () => {
  const confirmation = deferred()
  let sent = false
  const options = await chatComponent({ chatStream() { sent = true; return { abort() {} } } })
  const source = { id: 'db', type: 'sqlite', file_path: '/source.db' }
  const vm = instance(options, { sessionId: 'old', dbConfigId: 'other', configs: [source], $confirm: () => confirmation.promise })
  vm.messages = [{ id: 'q', role: 'user', content: '旧问题' }, { id: 'a', role: 'assistant', db_config_id: 'db', source }]
  const retrying = vm.retry('a')
  vm.sessionId = 'new'; confirmation.resolve()
  await retrying
  assert.equal(sent, false)
})

test('stale task source blocks preview until the task is reviewed and saved', async () => {
  let calls = 0
  const options = await loadComponent('components/analysis/AnalysisTasks.vue', { templateParameters, templateError, sameSourceIdentity, ResultTable: {}, QueryEvidence: {}, GenerationSnapshot: {}, api: {
    async prepareAnalysisTask() { calls++; return {} },
  } })
  const vm = instance(options, { activeId: 'task', tasks: [{ id: 'task', source_changed: true }] })
  vm.form = { name: '分析', question_template: '统计订单', db_config_id: 'db' }; vm.savedSignature = JSON.stringify(vm.form)
  assert.equal(vm.needsRebind, true)
  await vm.prepare()
  assert.equal(calls, 0)
})

test('query evidence exposes independent byte and cell truncation without claiming all returned rows were analyzed', async () => {
  const options = await loadComponent('components/chat/QueryEvidence.vue', { api: {}, ResultTable: {}, GenerationSnapshot: {} })
  const vm = instance(options, { evidence: { coverage: {}, rows: [] } })
  assert.equal(vm.truncated, false)
  for (const flag of ['cells_truncated', 'result_truncated', 'analysis_bytes_truncated']) {
    vm.evidence.coverage = { [flag]: true }
    assert.equal(vm.truncated, true, flag)
  }
  vm.evidence.coverage = { cells_truncated: true, truncated_cell_count: 3, result_truncated: true,
    analysis_bytes_truncated: true, analysis_result_byte_limit: 65536, returned_rows: 100, analyzed_rows: 2 }
  assert.match(vm.scopeNotes.join(' '), /3 个单元格/)
  assert.match(vm.scopeNotes.join(' '), /字节上限/)
  assert.match(vm.scopeNotes.join(' '), /64 KiB.*仅送入前 2 行.*其余已返回行未进入/)
})

test('evaluation history uses the saved case snapshot and never falls back to an edited current case', async () => {
  const options = await loadComponent('components/analysis/EvaluationPanel.vue', { evaluationSummary, ResultTable: {}, GenerationSnapshot: {}, api: {} })
  const snapshot = { question: '旧问题', expected_sql: 'SELECT 42 AS old_total', db_config_id: 'source',
    source_identity: { type: 'mysql', host: 'old-host', port: 3306, database: 'old_sales', username: 'old_reader' },
    baseline: { columns: ['old_total'], rows: [[42]] } }
  const vm = instance(options, { form: { question: '新问题', expected_sql: 'SELECT 7', db_config_id: 'source' },
    configs: [{ id: 'source', host: 'new-host', database: 'new_sales' }],
    history: [{ id: 'old', case_snapshot: snapshot }, { id: 'legacy', case_snapshot: null }], selectedRunId: 'old' })
  assert.equal(vm.historicalCase.question, '旧问题')
  assert.equal(vm.historicalCase.expected_sql, 'SELECT 42 AS old_total')
  assert.deepEqual(vm.historicalCase.baseline.rows, [[42]])
  assert.match(vm.historicalSource, /old-host.*3306.*old_sales.*old_reader/)
  assert.doesNotMatch(vm.historicalSource, /new-host|new_sales/)
  vm.selectedRunId = 'legacy'
  assert.equal(vm.historicalCase, null)
  assert.equal(vm.historicalSource, '基准来源未记录')
})

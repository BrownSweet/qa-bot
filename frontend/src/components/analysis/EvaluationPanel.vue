<template>
  <el-dialog title="准确率评测 · 真实标准样例" :visible="visible" width="94%" :close-on-click-modal="!running" @update:visible="$emit('update:visible', $event)" @open="load" @close="stopRequested = true">
    <p class="note">录入真实业务问题与已核对的只读标准 SQL。保存时读取基准；评测时真实调用 AI 并比对查询结果。通过率仅代表这些样例，不代表所有问题的准确率。</p>
    <div class="toolbar">
      <el-button size="small" :disabled="running" @click="newCase">新建样例</el-button>
      <el-button size="small" :disabled="running" @click="showImport = !showImport">批量导入 JSON</el-button>
      <el-button size="small" :disabled="running" :loading="loading" @click="load">刷新列表</el-button>
      <el-button type="primary" size="small" :disabled="!cases.length || running" @click="runAll">顺序运行全部</el-button>
      <el-button v-if="running" type="warning" size="small" :disabled="stopRequested" @click="stopRequested = true">当前项完成后停止</el-button>
    </div>
    <el-alert v-if="running" :title="stopRequested ? '已请求停止；当前查询结束后不再运行下一项。' : '正在执行真实 AI 与数据库查询，请稍候。'" type="info" :closable="false" />
    <p v-if="summary.completed" class="summary">本轮返回 {{ summary.completed }} 项；基线变化需复核 {{ summary.review }} 项，执行错误 {{ summary.errors }} 项。可比较的 {{ summary.confirmed }} 项中通过 {{ summary.passed }} 项，不匹配 {{ summary.failed }} 项<span v-if="summary.percent !== null">，这些样例的通过率 {{ summary.percent }}%</span><span v-else>；本轮没有可确认的通过率</span>。</p>
    <div v-if="showImport" class="import-panel">
      <p class="note">粘贴包含 1–100 个对象的 JSON 数组；每项包含 db_config_id、question、expected_sql。全部通过验证后才会保存。</p>
      <el-input v-model="importText" type="textarea" :rows="5" placeholder="[]" />
      <el-button size="small" :loading="importing" @click="importCases">验证并导入</el-button>
    </div>
    <el-table :data="cases" size="small" max-height="280" @row-click="selectCase">
      <el-table-column prop="question" label="标准问题" min-width="240" show-overflow-tooltip />
      <el-table-column label="数据源" width="150"><template slot-scope="scope">{{ sourceName(scope.row.db_config_id) }}</template></el-table-column>
      <el-table-column label="本轮结果" width="130"><template slot-scope="scope">
        {{ currentId === scope.row.id ? '运行中…' : requestErrors[scope.row.id] ? '请求失败' : results[scope.row.id] && results[scope.row.id].baseline_changed ? '基线变化 · 需复核' : statusLabel(results[scope.row.id] && results[scope.row.id].status) }}
      </template></el-table-column>
      <el-table-column label="操作" width="110"><template slot-scope="scope"><el-button type="text" :disabled="running" @click.stop="runOne(scope.row)">运行此项</el-button></template></el-table-column>
    </el-table>
    <div class="editor">
      <el-form label-width="95px" size="small" :disabled="running || saving">
        <h4>当前样例 · 保存修改后用于后续评测</h4>
        <el-form-item label="数据源"><el-select v-model="form.db_config_id"><el-option v-for="source in configs" :key="source.id" :label="source.name" :value="source.id" /></el-select></el-form-item>
        <el-form-item label="标准问题"><el-input v-model="form.question" maxlength="1000" /></el-form-item>
        <el-form-item label="标准 SQL"><el-input v-model="form.expected_sql" type="textarea" :rows="4" placeholder="已人工核对的只读 SELECT 查询" /></el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存并计算基准</el-button>
        <el-button v-if="activeId" type="danger" plain @click="remove">删除样例</el-button>
      </el-form>
      <div v-if="activeId" class="outcome">
        <h4>评测记录</h4>
        <el-select v-model="selectedRunId" placeholder="选择历史评测" size="small">
          <el-option v-for="run in history" :key="run.id" :value="run.id" :label="`${formatTime(run.created_at)} · ${statusLabel(run.status)}`" />
        </el-select>
        <el-alert v-if="requestErrors[activeId]" :title="requestErrors[activeId]" type="error" :closable="false" />
        <template v-if="selectedRun">
          <p>{{ statusLabel(selectedRun.status) }} · {{ formatTime(selectedRun.created_at) }}</p>
          <div v-if="historicalCase" class="case-snapshot">
            <h4>当次样例快照</h4>
            <p class="note">当次标准问题</p><p class="snapshot-question">{{ historicalCase.question }}</p>
            <p class="note">当次标准 SQL</p><pre>{{ historicalCase.expected_sql }}</pre>
            <p class="note">基准来源（保存时）</p><p class="snapshot-source">{{ historicalSource }}</p>
            <p class="note">来源 ID：{{ historicalCase.db_config_id || '未记录' }} · 基准保存时间：{{ formatTime(historicalCase.updated_at) }}</p>
            <details v-if="historicalCase.baseline">
              <summary>查看当次样例保存的基准结果</summary>
              <result-table :columns="historicalCase.baseline.columns || []" :rows="historicalCase.baseline.rows || []" />
            </details>
          </div>
          <el-alert v-else title="历史基准未记录，不能按当前样例解读" type="warning" :closable="false" />
          <el-alert v-if="selectedRun.baseline_changed" title="源数据相对保存基准已变化，需复核；本次使用重新执行的标准 SQL 结果比较，不计入可确认通过率。" type="warning" :closable="false" />
          <el-alert v-if="selectedRun.error" :title="selectedRun.error" type="error" :closable="false" />
          <p class="note">AI 生成 SQL</p><pre>{{ selectedRun.generated_sql || '未生成' }}</pre>
          <generation-snapshot :snapshot="selectedRun.generation_snapshot" />
          <p v-if="selectedRun.actual" class="note">本次 AI 查询结果</p>
          <result-table v-if="selectedRun.actual" :columns="selectedRun.actual.columns" :rows="selectedRun.actual.rows" />
        </template>
        <p v-else class="note">暂无评测记录。</p>
      </div>
    </div>
  </el-dialog>
</template>

<script>
import * as api from '../../api'
import { evaluationSummary } from '../../utils/analysis.mjs'
import ResultTable from '../chat/ResultTable.vue'
import GenerationSnapshot from './GenerationSnapshot.vue'

const empty = () => ({ db_config_id: '', question: '', expected_sql: '' })
export default {
  name: 'EvaluationPanel', components: { ResultTable, GenerationSnapshot },
  props: { visible: Boolean, configs: { type: Array, default: () => [] } },
  data() { return { cases: [], activeId: '', form: empty(), loading: false, saving: false, running: false, stopRequested: false,
    currentId: '', results: {}, requestErrors: {}, history: [], selectedRunId: '', historySequence: 0,
    showImport: false, importText: '', importing: false } },
  computed: {
    summary() { return evaluationSummary(this.results) },
    selectedRun() { return this.history.find(run => run.id === this.selectedRunId) },
    historicalCase() { return this.selectedRun && this.selectedRun.case_snapshot || null },
    historicalSource() {
      const source = this.historicalCase && this.historicalCase.source_identity
      if (!source) return '基准来源未记录'
      const fields = [['type', '类型'], ['host', '主机'], ['port', '端口'], ['database', '数据库'], ['username', '账号'], ['file_path', '文件']]
      return fields.filter(([key]) => source[key] !== null && source[key] !== undefined && source[key] !== '')
        .map(([key, label]) => `${label}：${source[key]}`).join(' · ') || '基准来源未记录'
    },
  },
  beforeDestroy() { this.stopRequested = true; this.historySequence++ },
  methods: {
    async load() { this.loading = true; try { this.cases = (await api.getEvaluationCases()).cases } catch {} finally { this.loading = false } },
    newCase() { this.activeId = ''; this.form = { ...empty(), db_config_id: this.configs[0]?.id || '' }; this.history = []; this.historySequence++ },
    selectCase(item) { if (this.running) return; this.activeId = item.id; this.form = { db_config_id: item.db_config_id, question: item.question, expected_sql: item.expected_sql }; this.loadHistory() },
    async save() {
      if (!this.form.db_config_id || !this.form.question.trim() || !this.form.expected_sql.trim()) return this.$message.warning('请填写数据源、问题及标准 SQL')
      this.saving = true
      try { const result = this.activeId ? await api.updateEvaluationCase(this.activeId, this.form) : await api.createEvaluationCase(this.form); await this.load(); this.selectCase(result.case); this.$message.success('标准样例和基准已保存') }
      catch {} finally { this.saving = false }
    },
    async remove() { try { await this.$confirm('删除此标准样例？', '删除评测样例'); await api.deleteEvaluationCase(this.activeId); this.newCase(); await this.load() } catch {} },
    async importCases() {
      let cases
      try { cases = JSON.parse(this.importText); if (!Array.isArray(cases) || !cases.length || cases.length > 100) throw new Error('请提供 1–100 项的 JSON 数组') }
      catch (error) { return this.$message.error(error.message || 'JSON 格式不正确') }
      this.importing = true
      try { await api.importEvaluationCases({ cases }); this.importText = ''; this.showImport = false; await this.load(); this.$message.success('标准样例已导入') }
      catch {} finally { this.importing = false }
    },
    async executeCase(item) {
      this.currentId = item.id
      try { const result = await api.runEvaluationCase(item.id); this.$set(this.results, item.id, result.run); this.$delete(this.requestErrors, item.id) }
      catch (error) { this.$set(this.requestErrors, item.id, error.message || '请求失败'); this.$delete(this.results, item.id) }
      finally { this.currentId = ''; if (this.activeId === item.id) await this.loadHistory() }
    },
    async runOne(item) {
      if (this.running) return
      this.selectCase(item); this.running = true; this.stopRequested = false
      try { await this.executeCase(item) } finally { this.running = false }
    },
    async runAll() {
      if (this.running) return
      this.running = true; this.stopRequested = false; this.results = {}; this.requestErrors = {}
      try { for (const item of [...this.cases]) { if (this.stopRequested) break; await this.executeCase(item) } }
      finally { this.running = false; this.currentId = '' }
    },
    async loadHistory() {
      if (!this.activeId) return
      const sequence = ++this.historySequence
      try { const result = await api.getEvaluationRuns(this.activeId); if (sequence === this.historySequence) { this.history = result.runs; this.selectedRunId = this.history[0]?.id || '' } }
      catch {}
    },
    sourceName(id) { return this.configs.find(source => source.id === id)?.name || '来源已删除' },
    statusLabel(status) { return ({ pending: '运行中', passed: '通过', mismatch: '结果不匹配', error: '执行错误' })[status] || '未运行' },
    formatTime(value) { return value ? new Date(value).toLocaleString() : '—' },
  },
}
</script>

<style scoped>
.note { color: #909399; font-size: 12px; line-height: 1.7; }.toolbar { display: flex; gap: 8px; margin: 12px 0; }.summary { color: #409eff; }
.editor { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 22px; }.outcome { min-width: 0; }.outcome pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f7fa; padding: 10px; font-size: 12px; }
.import-panel { padding: 12px; background: #f5f7fa; }.import-panel .el-button { margin-top: 8px; }
.case-snapshot { margin: 12px 0; padding: 12px; border: 1px solid #dcdfe6; border-radius: 4px; }
.case-snapshot h4 { margin-top: 0; }.snapshot-question, .snapshot-source { white-space: pre-wrap; overflow-wrap: anywhere; }
.case-snapshot summary { cursor: pointer; color: #409eff; margin-bottom: 8px; }
</style>

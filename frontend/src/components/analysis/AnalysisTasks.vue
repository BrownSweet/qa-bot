<template>
  <el-dialog title="可重复分析任务" :visible="visible" width="94%" @update:visible="$emit('update:visible', $event)" @open="load">
    <div class="task-toolbar">
      <el-select v-model="activeId" placeholder="选择已保存任务" @change="selectTask"><el-option v-for="task in tasks" :key="task.id" :label="task.name" :value="task.id" /></el-select>
      <el-button size="small" @click="openCreate()">新建任务</el-button>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>
    <div class="task-editor">
      <el-form label-width="85px" size="small">
        <el-form-item label="任务名称"><el-input v-model="form.name" maxlength="100" /></el-form-item>
        <el-form-item label="数据源"><el-select v-model="form.db_config_id" placeholder="选择绑定来源"><el-option v-for="source in configs" :key="source.id" :label="source.name" :value="source.id" /></el-select></el-form-item>
        <el-form-item label="问题模板"><el-input v-model="form.question_template" type="textarea" :rows="3" maxlength="1000" show-word-limit /></el-form-item>
        <p class="note">模板可使用参数，例如「查询 <code v-pre>{{month}}</code> 的销售额」。运行前会显示实际提问，仍通过只读 SQL 查询数据。</p>
        <el-alert v-if="templateValidation" :title="templateValidation" type="error" :closable="false" />
        <el-button type="primary" :loading="saving" @click="save">保存任务</el-button>
        <el-button v-if="activeId" type="danger" plain @click="remove">删除任务</el-button>
      </el-form>
      <div class="task-run">
        <h4>运行参数</h4>
        <el-form label-width="100px" size="small">
          <el-form-item v-for="parameter in parameterNames" :key="parameter" :label="parameter"><el-input v-model="parameters[parameter]" maxlength="200" /></el-form-item>
        </el-form>
        <p v-if="!parameterNames.length" class="note">此模板没有参数。</p>
        <p v-if="dirty" class="note">模板有未保存的修改，请先保存。</p>
        <el-alert v-if="needsRebind" title="来源已变化，需复核并重新保存任务，确认后才可运行。" type="warning" :closable="false" />
        <el-alert v-if="sourceChanged" title="当前绑定数据源与上次运行的连接位置或账号不同；重跑前需要确认来源，不能直接比较为同口径变化。" type="warning" :closable="false" />
        <el-button size="small" :disabled="!activeId || dirty || busy || needsRebind || loadingRuns" :loading="preparing" @click="prepare">预览本次问题</el-button>
        <div v-if="prepared" class="prepared">
          <p>{{ prepared.question }}</p>
          <el-button type="primary" size="small" :disabled="busy" @click="execute">新建会话并运行</el-button>
        </div>
      </div>
    </div>
    <div v-if="activeId" class="history">
      <h4>运行记录与结果对比 <el-button type="text" :loading="loadingRuns" @click="loadRuns">刷新记录</el-button></h4>
      <p class="note">显示最近 30 次运行；选择两次并列核对。来源、SQL 或数据范围不同的结果不可直接当成同口径变化。</p>
      <el-checkbox-group v-model="comparedIds" :max="2" class="run-list">
        <el-checkbox v-for="run in runs" :key="run.message_id" :label="run.message_id">{{ formatTime(run.created_at) }} · {{ statusLabel(run.status) }} · {{ run.question }}</el-checkbox>
      </el-checkbox-group>
      <p v-if="!runs.length" class="note">暂无运行记录，运行本任务后可在这里比较结果。</p>
      <el-alert v-if="comparison && !comparison.comparable" class="comparison-alert" type="warning" :closable="false"
                title="这两次运行不能直接作为同口径变化结论">
        <p v-for="reason in comparison.reasons" :key="reason">{{ reason }}</p>
      </el-alert>
      <el-alert v-else-if="comparison" class="comparison-alert" type="info" :closable="false"
                title="来源、执行 SQL 与已保存结果范围一致；仍请根据实际问题核对业务含义。" />
      <div class="comparisons">
        <div v-for="run in comparedRuns" :key="run.message_id" class="comparison">
          <strong>{{ formatTime(run.created_at) }}</strong><p>{{ run.question }}</p>
          <p class="note">来源：{{ run.source ? run.source.name : '未记录' }} · {{ statusLabel(run.status) }}</p>
          <result-table v-if="run.evidence" :columns="run.evidence.columns || []" :rows="run.evidence.rows || []" />
          <query-evidence :evidence="run.evidence" :source="run.source" :message-id="run.message_id" :pending="run.status === 'pending'" :generation-snapshot="run.generation_snapshot" />
          <generation-snapshot v-if="!run.evidence && run.status !== 'pending'" :snapshot="run.generation_snapshot" />
          <p v-if="!run.evidence" class="note">此次运行没有保存查询结果。</p>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<script>
import * as api from '../../api'
import { templateParameters, templateError, sameSourceIdentity, compareTaskRuns } from '../../utils/analysis.mjs'
import ResultTable from '../chat/ResultTable.vue'
import QueryEvidence from '../chat/QueryEvidence.vue'
import GenerationSnapshot from './GenerationSnapshot.vue'

const blank = () => ({ name: '', question_template: '', db_config_id: '' })
export default {
  name: 'AnalysisTasks', components: { ResultTable, QueryEvidence, GenerationSnapshot },
  props: { visible: Boolean, configs: { type: Array, default: () => [] }, busy: Boolean },
  data() { return { tasks: [], activeId: '', form: blank(), savedSignature: '', parameters: {}, prepared: null,
    runs: [], comparedIds: [], loading: false, saving: false, preparing: false, loadingRuns: false, runsSequence: 0 } },
  computed: {
    parameterNames() { return templateParameters(this.form.question_template) },
    templateValidation() { return templateError(this.form.question_template) },
    needsRebind() { return !!this.tasks.find(task => task.id === this.activeId)?.source_changed },
    dirty() { return this.savedSignature !== JSON.stringify(this.form) },
    comparedRuns() { return this.runs.filter(run => this.comparedIds.includes(run.message_id)) },
    comparison() { return compareTaskRuns(this.comparedRuns) },
    sourceChanged() {
      const previous = this.runs[0] && this.runs[0].source
      const current = this.configs.find(source => source.id === this.form.db_config_id)
      return !!previous && !sameSourceIdentity(previous, current)
    },
  },
  watch: {
    form: { deep: true, handler() { this.prepared = null } },
    parameters: { deep: true, handler() { this.prepared = null } },
    parameterNames(names) { const values = {}; names.forEach(name => { values[name] = this.parameters[name] || '' }); this.parameters = values },
  },
  methods: {
    async load() { this.loading = true; try { this.tasks = (await api.getAnalysisTasks()).tasks; if (this.activeId) this.loadRuns() } catch {} finally { this.loading = false } },
    openCreate(draft = {}) {
      this.activeId = ''; this.form = { name: '', question_template: draft.question || '', db_config_id: draft.db_config_id || this.configs[0]?.id || '' }
      this.savedSignature = ''; this.parameters = {}; this.prepared = null; this.runs = []; this.comparedIds = []; this.runsSequence++; this.loadingRuns = false
    },
    selectTask(id) {
      const task = this.tasks.find(item => item.id === id)
      if (!task) return
      this.activeId = id; this.form = { name: task.name, question_template: task.question_template, db_config_id: task.db_config_id }
      this.savedSignature = JSON.stringify(this.form); this.parameters = {}; this.prepared = null; this.comparedIds = []; this.runs = []; this.loadRuns()
    },
    async save() {
      if (!this.form.name.trim() || !this.form.question_template.trim() || !this.form.db_config_id) return this.$message.warning('请填写名称、问题模板并选择数据源')
      if (this.templateValidation) return this.$message.warning(this.templateValidation)
      this.saving = true
      try {
        const body = { ...this.form, name: this.form.name.trim(), question_template: this.form.question_template.trim() }
        const response = this.activeId ? await api.updateAnalysisTask(this.activeId, body) : await api.createAnalysisTask(body)
        const id = this.activeId || response.task.id
        await this.load(); this.selectTask(id); this.$message.success('分析任务已保存')
      } catch {} finally { this.saving = false }
    },
    async remove() {
      try { await this.$confirm('删除任务模板？已有会话和回答会保留。', '删除分析任务'); await api.deleteAnalysisTask(this.activeId); this.openCreate(); await this.load() } catch {}
    },
    async prepare() {
      if (!this.activeId || this.dirty || this.preparing || this.needsRebind) return
      const id = this.activeId
      const signature = JSON.stringify({ form: this.form, parameters: this.parameters })
      const parameters = Object.fromEntries(this.parameterNames.map(name => [name, this.parameters[name] || '']))
      if (Object.values(parameters).some(value => !value.trim())) return this.$message.warning('请填写全部运行参数')
      this.preparing = true; this.prepared = null
      try {
        const result = await api.prepareAnalysisTask(id, { parameters })
        if (id === this.activeId && signature === JSON.stringify({ form: this.form, parameters: this.parameters })) this.prepared = result
      }
      catch {} finally { this.preparing = false }
    },
    async execute() {
      if (!this.prepared || this.dirty || this.busy) return
      if (this.sourceChanged) {
        try { await this.$confirm('绑定数据源与上次运行已不同。将按当前配置重新查询，请核对来源后比较结果。', '确认任务来源', { type: 'warning' }) }
        catch { return }
      }
      if (!this.prepared || this.dirty || this.busy) return
      this.$emit('run', { ...this.prepared, name: this.form.name, analysis_task_parameters: { ...this.parameters } })
    },
    async loadRuns() {
      const id = this.activeId; if (!id) return
      const sequence = ++this.runsSequence; this.loadingRuns = true
      try { const result = await api.getAnalysisTaskRuns(id); if (sequence === this.runsSequence) this.runs = result.runs }
      catch {} finally { if (sequence === this.runsSequence) this.loadingRuns = false }
    },
    formatTime(value) { return value ? new Date(value).toLocaleString() : '—' },
    statusLabel(status) { return ({ completed: '完成', error: '失败', cancelled: '已停止', pending: '进行中' })[status] || status },
  },
}
</script>

<style scoped>
.task-toolbar { display: flex; gap: 8px; margin-bottom: 16px; }.task-editor { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
.note { color: #909399; font-size: 12px; line-height: 1.7; }.task-run { border-left: 1px solid #ebeef5; padding-left: 22px; }
.prepared { background: #f5f7fa; padding: 12px; margin-top: 12px; }.run-list { display: flex; flex-direction: column; gap: 8px; max-height: 170px; overflow: auto; }
.comparison-alert { margin: 12px 0; }.comparison-alert p { margin: 4px 0; }
.run-list .el-checkbox { margin-right: 0; }.comparisons { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }.comparison { min-width: 0; border: 1px solid #ebeef5; padding: 12px; border-radius: 4px; }
</style>

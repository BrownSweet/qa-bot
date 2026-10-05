<template>
  <div class="db-panel">
    <div class="section-head">
      <span>数据源</span>
      <el-button type="text" icon="el-icon-plus" @click="openAdd">添加</el-button>
    </div>

    <el-select :value="value" :disabled="disabled" placeholder="选择数据源" size="small" class="db-select"
               @input="$emit('input', $event)">
      <el-option v-for="c in configs" :key="c.id" :label="`${c.name} (${c.type})`" :value="c.id" />
    </el-select>

    <div v-if="loading" class="empty-tip">正在加载数据源…</div>
    <div v-else-if="loadError" class="empty-tip">{{ loadError }} <el-button type="text" @click="load">重试</el-button></div>
    <div v-else-if="!configs.length" class="empty-tip">添加文件或数据库，先查看样本再开始分析。</div>
    <ul class="db-list">
      <li v-for="c in configs" :key="c.id" :class="{ active: c.id === value }">
        <button class="source-name" :disabled="disabled" @click="$emit('input', c.id)">
          <i class="el-icon-coin"></i> {{ c.name }}
          <em>{{ c.type }}</em>
        </button>
        <el-dropdown trigger="click" @command="(command) => onCommand(command, c)">
          <el-button type="text" size="mini" icon="el-icon-more" :aria-label="`管理${c.name}`" />
          <el-dropdown-menu slot="dropdown">
            <el-dropdown-item command="inspect">结构、样本与业务口径</el-dropdown-item>
            <el-dropdown-item command="test">重新测试连接</el-dropdown-item>
            <el-dropdown-item command="edit" :disabled="disabled">编辑配置</el-dropdown-item>
            <el-dropdown-item command="delete" :disabled="disabled" divided>删除</el-dropdown-item>
          </el-dropdown-menu>
        </el-dropdown>
      </li>
    </ul>
    <p v-if="connectionMsg" class="empty-tip" role="status">{{ connectionMsg }}</p>

    <!-- 添加弹窗 -->
    <el-dialog :title="editingId ? '编辑数据源' : '添加数据源'" :visible.sync="dialog" width="500px" append-to-body>
      <el-form :model="form" label-width="90px" size="small" :disabled="saving || testing">
        <el-form-item label="类型">
          <el-select v-model="form.type" class="full" @change="changeType">
            <el-option label="MySQL" value="mysql" />
            <el-option label="PostgreSQL" value="postgresql" />
            <el-option label="SQLite" value="sqlite" />
            <el-option label="Excel" value="excel" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="配置名称" /></el-form-item>
        <template v-if="form.type === 'mysql' || form.type === 'postgresql'">
          <el-form-item label="主机"><el-input v-model="form.host" placeholder="localhost" /></el-form-item>
          <el-form-item label="端口"><el-input-number v-model="form.port" :min="1" :max="65535" /></el-form-item>
          <el-form-item label="数据库"><el-input v-model="form.database" /></el-form-item>
          <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
          <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password :placeholder="editingId ? '留空保留已保存密码' : ''" /></el-form-item>
        </template>
        <el-form-item v-if="form.type === 'sqlite'" label="文件路径">
          <el-input v-model="form.file_path" :placeholder="isDesktop ? '选择本机 SQLite 文件' : '后端服务器上的 SQLite 文件路径'">
            <el-button v-if="isDesktop" slot="append" :loading="selecting" @click="chooseFile">选择文件</el-button>
          </el-input>
        </el-form-item>
        <el-form-item v-if="form.type === 'excel'" label="文件路径">
          <el-input v-model="form.file_path" :placeholder="isDesktop ? '选择本机 Excel 文件（.xlsx）' : '后端服务器上的 Excel 文件路径'">
            <el-button v-if="isDesktop" slot="append" :loading="selecting" @click="chooseFile">选择文件</el-button>
          </el-input>
        </el-form-item>
      </el-form>
      <div v-if="testMsg" :class="['test-msg', testOk ? 'ok' : 'fail']">{{ testMsg }}</div>
      <span slot="footer">
        <el-button :loading="testing" :disabled="saving" @click="doTest">{{ editingId ? '保存并测试' : '测试连接' }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="testing" @click="save">保存</el-button>
      </span>
    </el-dialog>

    <el-dialog :title="`${inspected ? inspected.name : ''} · 数据源检查`" :visible.sync="inspectDialog" width="90%" append-to-body>
      <el-tabs v-model="inspectTab">
        <el-tab-pane label="结构与样本" name="data">
          <div v-if="inspecting" class="empty-tip">正在读取表结构…</div>
          <el-alert v-else-if="inspectError" :title="inspectError" type="error" :closable="false" />
          <template v-else>
            <el-select v-model="tableName" placeholder="选择数据表 / 工作表" @change="loadPreview">
              <el-option v-for="table in tables" :key="table.name" :label="table.name" :value="table.name" />
            </el-select>
            <p v-if="!tables.length" class="empty-tip">此数据源没有可读取的数据表。</p>
            <p v-if="activeTable" class="columns">{{ activeTable.columns.map(c => `${c.name} (${c.type})`).join(' · ') }}</p>
            <div v-loading="previewLoading"><result-table v-if="preview" :columns="preview.columns" :rows="preview.rows" /></div>
            <p class="empty-tip">样本最多 10 行，用于检查字段和内容，不代表完整数据。</p>
          </template>
        </el-tab-pane>
        <el-tab-pane label="业务口径" name="semantics">
          <el-alert v-if="semanticsError" :title="semanticsError" type="error" :closable="false" />
          <el-alert v-if="semanticsStale" title="来源已变化，此业务口径暂不用于 AI。请核对表字段与规则后重新保存。" type="warning" :closable="false" />
          <el-button v-if="semanticsError" type="text" @click="inspect(inspected)">重新加载</el-button>
          <p class="empty-tip">填写表与字段含义、关联关系、统计周期、退款或金额计算规则。AI 会参考这些说明，结果仍需核对查询依据。</p>
          <el-input v-model="semantics" :disabled="!semanticsLoaded || savingSemantics" type="textarea" :rows="10" maxlength="12000" show-word-limit placeholder="例如：实收金额=订单金额-退款金额；日期使用付款时间；只统计已完成订单。" />
          <el-button type="primary" size="small" style="margin-top:12px" :disabled="!semanticsLoaded" :loading="savingSemantics" @click="saveSemantics">保存业务口径</el-button>
        </el-tab-pane>
      </el-tabs>
    </el-dialog>
  </div>
</template>

<script>
import * as api from '../../api'
import { desktop, isDesktop } from '../../desktop'
import ResultTable from '../chat/ResultTable.vue'

const emptyForm = () => ({
  name: '', type: 'mysql', host: 'localhost', port: 3306,
  database: '', username: '', password: '', file_path: '',
})

export default {
  name: 'DbConfigPanel',
  components: { ResultTable },
  props: { value: { type: String, default: '' }, disabled: Boolean },
  data() {
    return { isDesktop, selecting: false, configs: [], dialog: false, form: emptyForm(), testing: false, saving: false, testMsg: '', testOk: false,
      loading: false, loadError: '', editingId: '', connectionMsg: '', inspectDialog: false, inspectTab: 'data', inspected: null,
      inspecting: false, inspectError: '', tables: [], tableName: '', preview: null, previewLoading: false, previewSequence: 0,
      semantics: '', savingSemantics: false, semanticsLoaded: false, semanticsError: '', semanticsStale: false }
  },
  computed: { activeTable() { return this.tables.find(table => table.name === this.tableName) } },
  watch: {
    form: { deep: true, handler() { this.testMsg = '' } },
  },
  mounted() { this.load() },
  methods: {
    async load() {
      this.loading = true; this.loadError = ''
      try { this.configs = (await api.getDbConfigs()).configs; this.$emit('loaded', this.configs) }
      catch (error) { this.loadError = error.message || '数据源加载失败' }
      finally { this.loading = false }
    },
    openAdd() { this.editingId = ''; this.form = emptyForm(); this.testMsg = ''; this.dialog = true },
    openEdit(config) { this.editingId = config.id; this.form = { ...emptyForm(), ...config, password: '' }; this.dialog = true },
    changeType(type) { this.form.port = type === 'postgresql' ? 5432 : 3306 },
    async chooseFile() {
      const kind = this.form.type
      this.selecting = true
      try {
        const path = await desktop.selectFile(kind)
        if (path && this.form.type === kind && this.dialog) this.form.file_path = path
      } catch (e) {
        this.$message.error(e.message || '无法打开文件选择器')
      } finally { this.selecting = false }
    },
    payload() {
      const f = this.form
      const p = { name: f.name, type: f.type }
      if (f.type === 'excel' || f.type === 'sqlite') p.file_path = f.file_path
      else { p.host = f.host; p.port = f.port; p.database = f.database; p.username = f.username; p.password = f.password }
      return p
    },
    async doTest() {
      if (this.editingId && !this.form.name.trim()) return this.$message.warning('配置名称不能为空')
      this.testing = true; this.testMsg = ''
      try {
        const res = this.editingId
          ? await api.testAndUpdateDbConfig(this.editingId, this.payload())
          : await api.testConnection(this.payload())
        this.testOk = res.success; this.testMsg = res.message
        if (this.editingId && res.success) await this.load()
      } catch (e) { this.testOk = false; this.testMsg = e.message } finally { this.testing = false }
    },
    async save() {
      if (!this.form.name.trim()) return this.$message.warning('配置名称不能为空')
      this.saving = true
      try {
        const res = this.editingId ? await api.updateDbConfig(this.editingId, this.payload()) : await api.addDbConfig(this.payload())
        this.$message.success('数据源已保存')
        this.dialog = false
        await this.load()
        if (!this.editingId) this.$emit('input', res.config.id)
      } catch (e) { /* handled */ } finally { this.saving = false }
    },
    removeConfig(c) {
      this.$confirm(`删除数据源「${c.name}」？历史回答会保留，关联会话需重新选择来源。`, '删除数据源', { type: 'warning' })
        .then(async () => {
          await api.deleteDbConfig(c.id)
          this.$message.success('配置删除成功')
          this.$emit('deleted', c.id)
          this.load()
        }).catch(() => {})
    },
    onCommand(command, config) {
      if (command === 'edit') this.openEdit(config)
      else if (command === 'inspect') this.inspect(config)
      else if (command === 'test') this.reconnect(config)
      else if (command === 'delete') this.removeConfig(config)
    },
    async reconnect(config) {
      this.connectionMsg = `正在测试 ${config.name}…`
      try { const result = await api.testSavedConnection(config.id); this.connectionMsg = `${config.name}：${result.message}` }
      catch (error) { this.connectionMsg = error.message }
    },
    async inspect(config) {
      this.inspected = config; this.inspectDialog = true; this.inspectTab = 'data'; this.inspecting = true; this.inspectError = ''
      this.tables = []; this.tableName = ''; this.preview = null; this.semantics = ''; this.previewSequence++
      this.semanticsLoaded = false; this.semanticsError = ''; this.semanticsStale = false
      const results = await Promise.allSettled([api.getDbSchema(config.id), api.getDbSemantics(config.id)])
      if (this.inspected.id !== config.id) return
      if (results[0].status === 'fulfilled') this.tables = results[0].value.tables
      else this.inspectError = results[0].reason.message
      if (results[1].status === 'fulfilled') { this.semantics = results[1].value.context || ''; this.semanticsLoaded = true; this.semanticsStale = !!results[1].value.stale }
      else this.semanticsError = results[1].reason.message || '业务口径加载失败，请重新加载后编辑'
      this.inspecting = false
      if (this.tables.length) { this.tableName = this.tables[0].name; this.loadPreview() }
    },
    async loadPreview() {
      const sequence = ++this.previewSequence
      this.previewLoading = true; this.preview = null
      try { const result = await api.getDbPreview(this.inspected.id, this.tableName); if (sequence === this.previewSequence) this.preview = result }
      catch { /* API 已显示错误 */ }
      finally { if (sequence === this.previewSequence) this.previewLoading = false }
    },
    async saveSemantics() {
      if (!this.semanticsLoaded || this.savingSemantics) return
      this.savingSemantics = true
      try { await api.updateDbSemantics(this.inspected.id, { context: this.semantics }); this.semanticsStale = false; this.$message.success('业务口径已保存') }
      catch { /* API 已显示错误 */ } finally { this.savingSemantics = false }
    },
  },
}
</script>

<style scoped>
.db-panel { border-bottom: 1px solid #ebeef5; padding-bottom: 12px; margin-bottom: 12px; }
.section-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #303133; }
.db-select { width: 100%; margin: 8px 0; }
.empty-tip { font-size: 12px; color: #c0c4cc; padding: 4px 0; }
.db-list { list-style: none; padding: 0; margin: 0; max-height: 140px; overflow-y: auto; }
.db-list li { display: flex; justify-content: space-between; align-items: center; padding: 6px 8px;
  border-radius: 4px; cursor: pointer; font-size: 13px; }
.db-list li:hover { background: #f5f7fa; }
.db-list li.active { background: #ecf5ff; color: #409eff; }
.db-list li em { font-style: normal; color: #c0c4cc; font-size: 11px; margin-left: 4px; }
.db-list li .el-icon-delete { color: #f56c6c; }
.source-name { flex: 1; text-align: left; background: transparent; border: 0; padding: 0; cursor: pointer; color: #606266; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.columns { font-size: 12px; color: #606266; line-height: 1.8; }
.full { width: 100%; }
.test-msg { padding: 6px 10px; border-radius: 4px; font-size: 13px; }
.test-msg.ok { background: #f0f9eb; color: #67c23a; }
.test-msg.fail { background: #fef0f0; color: #f56c6c; }
</style>

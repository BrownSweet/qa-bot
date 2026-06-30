<template>
  <div class="db-panel">
    <div class="section-head">
      <span>数据源</span>
      <el-button type="text" icon="el-icon-plus" @click="openAdd">添加</el-button>
    </div>

    <el-select :value="value" placeholder="选择数据源" size="small" class="db-select"
               @input="$emit('input', $event)">
      <el-option v-for="c in configs" :key="c.id" :label="`${c.name} (${c.type})`" :value="c.id" />
    </el-select>

    <div v-if="!configs.length" class="empty-tip">暂无数据源，请先添加</div>
    <ul class="db-list">
      <li v-for="c in configs" :key="c.id" :class="{ active: c.id === value }">
        <span @click="$emit('input', c.id)">
          <i class="el-icon-coin"></i> {{ c.name }}
          <em>{{ c.type }}</em>
        </span>
        <i class="el-icon-delete" @click="removeConfig(c)"></i>
      </li>
    </ul>

    <!-- 添加弹窗 -->
    <el-dialog title="添加数据源" :visible.sync="dialog" width="460px" append-to-body>
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item label="类型">
          <el-select v-model="form.type" class="full">
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
          <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        </template>
        <el-form-item v-if="form.type === 'sqlite'" label="文件路径">
          <el-input v-model="form.file_path" placeholder="/path/to/db.sqlite" />
        </el-form-item>
        <el-form-item v-if="form.type === 'excel'" label="文件路径">
          <el-input v-model="form.file_path" placeholder="/path/to/data.xlsx（支持中文路径）" />
        </el-form-item>
      </el-form>
      <div v-if="testMsg" :class="['test-msg', testOk ? 'ok' : 'fail']">{{ testMsg }}</div>
      <span slot="footer">
        <el-button :loading="testing" @click="doTest">测试连接</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import * as api from '../../api'

const emptyForm = () => ({
  name: '', type: 'mysql', host: 'localhost', port: 3306,
  database: '', username: '', password: '', file_path: '',
})

export default {
  name: 'DbConfigPanel',
  props: { value: { type: String, default: '' } },
  data() {
    return { configs: [], dialog: false, form: emptyForm(), testing: false, saving: false, testMsg: '', testOk: false }
  },
  watch: {
    'form.type'(t) { this.form.port = t === 'postgresql' ? 5432 : 3306 },
  },
  mounted() { this.load() },
  methods: {
    async load() {
      const res = await api.getDbConfigs()
      this.configs = res.configs
      if (!this.value && this.configs.length) this.$emit('input', this.configs[0].id)
    },
    openAdd() { this.form = emptyForm(); this.testMsg = ''; this.dialog = true },
    payload() {
      const f = this.form
      const p = { name: f.name, type: f.type }
      if (f.type === 'excel' || f.type === 'sqlite') p.file_path = f.file_path
      else { p.host = f.host; p.port = f.port; p.database = f.database; p.username = f.username; p.password = f.password }
      return p
    },
    async doTest() {
      this.testing = true; this.testMsg = ''
      try {
        const res = await api.testConnection(this.payload())
        this.testOk = res.success; this.testMsg = res.message
      } catch (e) { this.testOk = false; this.testMsg = e.message } finally { this.testing = false }
    },
    async save() {
      if (!this.form.name.trim()) return this.$message.warning('配置名称不能为空')
      this.saving = true
      try {
        const res = await api.addDbConfig(this.payload())
        this.$message.success('配置添加成功')
        this.dialog = false
        await this.load()
        this.$emit('input', res.config.id)
      } catch (e) { /* handled */ } finally { this.saving = false }
    },
    removeConfig(c) {
      this.$confirm(`确定删除数据源「${c.name}」？`, '二次确认', { type: 'warning' })
        .then(async () => {
          await api.deleteDbConfig(c.id)
          this.$message.success('配置删除成功')
          if (this.value === c.id) this.$emit('input', '')
          this.load()
        }).catch(() => {})
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
.full { width: 100%; }
.test-msg { padding: 6px 10px; border-radius: 4px; font-size: 13px; }
.test-msg.ok { background: #f0f9eb; color: #67c23a; }
.test-msg.fail { background: #fef0f0; color: #f56c6c; }
</style>

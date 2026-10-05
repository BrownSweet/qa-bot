<template>
  <div class="session-panel">
    <div class="section-head">
      <span>会话</span>
      <el-button type="text" icon="el-icon-plus" :loading="creating" @click="create">新建</el-button>
    </div>

    <el-input v-model="keyword" placeholder="搜索会话" size="small" prefix-icon="el-icon-search"
              clearable @input="onSearch" class="search" />

    <div class="list">
      <div v-if="loading" class="empty">正在加载会话…</div>
      <div v-else-if="loadError" class="empty">{{ loadError }} <el-button type="text" @click="load">重试</el-button></div>
      <div v-else-if="!sessions.length" class="empty">暂无会话</div>
      <div v-for="s in sessions" :key="s.id" class="item" :class="{ active: s.id === activeId }"
           role="button" tabindex="0" @keydown.enter="$emit('select', s)" @click="$emit('select', s)">
        <i v-if="s.is_pinned" class="el-icon-top pin"></i>
        <span class="name">{{ s.name }}</span>
        <el-dropdown trigger="click" @command="(cmd) => onCommand(cmd, s)" @click.native.stop>
          <i class="el-icon-more more" @click.stop></i>
          <el-dropdown-menu slot="dropdown">
            <el-dropdown-item command="rename">改名</el-dropdown-item>
            <el-dropdown-item command="pin">{{ s.is_pinned ? '取消置顶' : '置顶' }}</el-dropdown-item>
            <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
          </el-dropdown-menu>
        </el-dropdown>
      </div>
    </div>
  </div>
</template>

<script>
import * as api from '../../api'

export default {
  name: 'SessionList',
  props: { activeId: { type: String, default: '' }, dbConfigId: String },
  data() { return { sessions: [], keyword: '', timer: null, loading: false, loadError: '', creating: false, sequence: 0 } },
  mounted() { this.load() },
  beforeDestroy() { clearTimeout(this.timer); this.sequence++ },
  methods: {
    async load() {
      const sequence = ++this.sequence
      this.loading = true; this.loadError = ''
      try {
        const res = await api.getSessions(this.keyword || undefined)
        if (sequence === this.sequence) { this.sessions = res.sessions; this.$emit('loaded', this.sessions) }
      } catch (error) { if (sequence === this.sequence) this.loadError = error.message }
      finally { if (sequence === this.sequence) this.loading = false }
    },
    onSearch() {
      clearTimeout(this.timer)
      this.timer = setTimeout(this.load, 250)
    },
    async create() {
      if (this.creating) return
      this.creating = true
      try {
        const res = await api.createSession({ db_config_id: this.dbConfigId || null })
        this.keyword = ''; await this.load(); this.$emit('select', res.session)
        this.$message.success('会话创建成功')
      } catch { /* API 已显示错误 */ } finally { this.creating = false }
    },
    onCommand(cmd, s) {
      if (cmd === 'rename') this.rename(s)
      else if (cmd === 'pin') this.togglePin(s)
      else if (cmd === 'delete') this.remove(s)
    },
    rename(s) {
      this.$prompt('请输入新的会话名称', '改名', { inputValue: s.name, inputPattern: /\S/, inputErrorMessage: '名称不能为空' })
        .then(async ({ value }) => { await api.updateSession(s.id, { name: value }); this.load() })
        .catch(() => {})
    },
    async togglePin(s) { await api.updateSession(s.id, { is_pinned: !s.is_pinned }); this.load() },
    remove(s) {
      this.$confirm(`确定删除会话「${s.name}」及其所有消息？`, '二次确认', { type: 'warning' })
        .then(async () => {
          await api.deleteSession(s.id)
          this.$message.success('会话删除成功')
          if (this.activeId === s.id) this.$emit('select', null)
          this.load()
        }).catch(() => {})
    },
  },
}
</script>

<style scoped>
.session-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.section-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; }
.search { margin: 8px 0; }
.list { flex: 1; overflow-y: auto; }
.empty { text-align: center; color: #c0c4cc; padding: 20px; font-size: 13px; }
.item { display: flex; align-items: center; padding: 8px; border-radius: 4px; cursor: pointer; font-size: 13px; }
.item:hover { background: #f5f7fa; }
.item.active { background: #ecf5ff; color: #409eff; }
.item .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item .pin { color: #e6a23c; margin-right: 4px; }
.item .more { color: #909399; padding: 4px; }
</style>

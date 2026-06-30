<template>
  <div class="session-panel">
    <div class="section-head">
      <span>会话</span>
      <el-button type="text" icon="el-icon-plus" @click="create">新建</el-button>
    </div>

    <el-input v-model="keyword" placeholder="搜索会话" size="small" prefix-icon="el-icon-search"
              clearable @input="onSearch" class="search" />

    <div class="list">
      <div v-if="!sessions.length" class="empty">暂无会话</div>
      <div v-for="s in sessions" :key="s.id" class="item" :class="{ active: s.id === activeId }"
           @click="$emit('select', s.id)">
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
  props: { activeId: { type: String, default: '' } },
  data() { return { sessions: [], keyword: '', timer: null } },
  mounted() { this.load() },
  methods: {
    async load() {
      const res = await api.getSessions(this.keyword || undefined)
      this.sessions = res.sessions
    },
    onSearch() {
      clearTimeout(this.timer)
      this.timer = setTimeout(this.load, 250)
    },
    async create() {
      const res = await api.createSession({})
      this.$message.success('会话创建成功')
      await this.load()
      this.$emit('select', res.session.id)
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
          if (this.activeId === s.id) this.$emit('select', '')
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

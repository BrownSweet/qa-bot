<template>
  <el-container class="dash">
    <el-header class="topbar">
      <div class="brand"><i class="el-icon-chat-dot-round"></i> 问答机器人 <el-tag v-if="isDesktop" size="mini">本地版</el-tag></div>
      <div class="actions">
        <el-button size="small" icon="el-icon-star-off" @click="openTasks">分析任务</el-button>
        <el-button size="small" icon="el-icon-data-analysis" @click="evaluationVisible = true">准确率评测</el-button>
        <!-- 通知 -->
        <el-popover placement="bottom" width="320" trigger="click" @show="loadNotifications">
          <div class="notif-head">
            <span>通知</span>
            <el-link type="primary" :underline="false" @click="markAllRead">全部已读</el-link>
          </div>
          <div class="notif-list">
            <div v-if="!notifications.length" class="empty">暂无通知</div>
            <div v-for="n in notifications" :key="n.id" class="notif-item" :class="{ unread: !n.is_read }">
              <div class="notif-title">
                {{ n.title }}
                <i class="el-icon-close" @click.stop="removeNotification(n)"></i>
              </div>
              <div class="notif-content">{{ n.content }}</div>
              <el-link v-if="!n.is_read" type="primary" :underline="false" @click="markRead(n)">标为已读</el-link>
            </div>
          </div>
          <el-badge slot="reference" :value="unread" :hidden="!unread" class="bell">
            <el-button icon="el-icon-bell" circle size="small" aria-label="通知" />
          </el-badge>
        </el-popover>

        <el-button icon="el-icon-setting" circle size="small" aria-label="设置" @click="$router.push('/settings')" />
        <el-dropdown v-if="!isDesktop" @command="onUserCommand">
          <span class="user"><i class="el-icon-user-solid"></i> {{ username }}<i class="el-icon-arrow-down"></i></span>
          <el-dropdown-menu slot="dropdown">
            <el-dropdown-item command="settings">设置</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </el-dropdown>
      </div>
    </el-header>

    <el-container class="body">
      <el-aside width="300px" class="sidebar">
        <db-config-panel ref="dbPanel" :value="activeDbId" :disabled="busy || taskStarting || sourceChanging" @input="changeSource" @loaded="sourcesLoaded" @deleted="sourceDeleted" />
        <session-list ref="sessionList" :active-id="activeSessionId" :db-config-id="activeDbId" @select="selectSession" @loaded="sessionsLoaded" />
      </el-aside>

      <el-main class="main">
        <div v-if="!readiness || !readiness.ai_ready || !configs.length || !activeDbId" class="setup-bar">
          <span v-if="readinessError">{{ readinessError }}</span>
          <span v-else>开始分析前：{{ readiness && readiness.ai_ready ? 'AI Key 已配置（连接状态以设置页测试为准）' : '配置 AI 服务' }} · {{ configs.length ? '选择会话数据源' : '添加数据源' }}</span>
          <el-button v-if="readiness && readiness.can_manage_ai" size="mini" @click="$router.push('/settings')">配置 AI</el-button>
          <span v-else-if="readiness && !readiness.ai_ready" class="setup-note">请联系管理员配置 AI</span>
          <el-button size="mini" @click="$refs.dbPanel.openAdd()">添加数据源</el-button>
          <el-button size="mini" type="text" @click="loadReadiness">刷新状态</el-button>
        </div>
        <chat-panel
          v-if="activeSessionId"
          ref="chatPanel"
          :session-id="activeSessionId"
          :db-config-id="activeDbId"
          :configs="configs"
          :ai-ready="!!(readiness && readiness.ai_ready)"
          @updated="refreshSessions"
          @busy="busy = $event"
          @source-restored="restoreSource"
          @save-task="saveTask"
        />
        <div v-else class="placeholder">
          <i class="el-icon-chat-line-round"></i>
          <p>请选择或新建一个会话开始提问</p>
        </div>
      </el-main>
    </el-container>
    <analysis-tasks ref="analysisTasks" :visible.sync="tasksVisible" :configs="configs" :busy="busy || taskStarting" @run="runTask" />
    <evaluation-panel :visible.sync="evaluationVisible" :configs="configs" />
  </el-container>
</template>

<script>
import * as api from '../api'
import DbConfigPanel from '../components/db/DbConfigPanel.vue'
import SessionList from '../components/session/SessionList.vue'
import ChatPanel from '../components/chat/ChatPanel.vue'
import { isDesktop } from '../desktop'
import AnalysisTasks from '../components/analysis/AnalysisTasks.vue'
import EvaluationPanel from '../components/analysis/EvaluationPanel.vue'

export default {
  name: 'DashboardView',
  components: { DbConfigPanel, SessionList, ChatPanel, AnalysisTasks, EvaluationPanel },
  data() {
    return { isDesktop, activeDbId: '', activeSessionId: '', notifications: [], unread: 0, configs: [],
      readiness: null, readinessError: '', selectionRestored: false, busy: false, sourceChanging: false,
      tasksVisible: false, evaluationVisible: false, taskStarting: false }
  },
  computed: {
    username() { return (this.$store.state.user && this.$store.state.user.username) || '用户' },
  },
  mounted() { this.loadNotifications(); this.loadReadiness() },
  methods: {
    rememberSelection() { this.$store.commit('setWorkspace', { sessionId: this.activeSessionId, dbConfigId: this.activeDbId }) },
    selectSession(session) {
      if (this.activeSessionId !== (session && session.id)) this.busy = false
      this.activeSessionId = session ? session.id : ''
      this.activeDbId = session ? session.db_config_id || '' : ''
      this.rememberSelection()
    },
    sessionsLoaded(sessions) {
      if (this.selectionRestored) return
      this.selectionRestored = true
      const remembered = this.$store.state.workspace && this.$store.state.workspace.sessionId
      const session = sessions.find(item => item.id === remembered) || sessions[0]
      if (session) this.selectSession(session)
    },
    sourcesLoaded(configs) {
      this.configs = configs
      if (this.activeDbId && !configs.some(item => item.id === this.activeDbId)) this.activeDbId = ''
      if (!this.activeSessionId && !this.activeDbId && configs.length) this.activeDbId = configs[0].id
    },
    async changeSource(id) {
      if (this.busy || this.taskStarting || this.sourceChanging || id === this.activeDbId) return
      const sessionId = this.activeSessionId
      this.sourceChanging = true
      try {
        if (sessionId && this.activeDbId) {
          const name = this.configs.find(item => item.id === id)?.name || '新数据源'
          await this.$confirm(`此会话后续问题将使用「${name}」。历史回答保留原来源，重试仍使用原来源。`, '切换数据源', { type: 'warning' })
        }
        if (sessionId) await api.updateSession(sessionId, { db_config_id: id || null })
        if (sessionId === this.activeSessionId) { this.activeDbId = id; this.rememberSelection(); this.refreshSessions() }
      } catch { /* 用户取消或 API 已显示错误 */ }
      finally { this.sourceChanging = false }
    },
    restoreSource(id) { this.activeDbId = id; this.rememberSelection() },
    sourceDeleted(id) { if (this.activeDbId === id) { this.activeDbId = ''; this.rememberSelection() }; this.refreshSessions() },
    async loadReadiness() {
      this.readinessError = ''
      try { this.readiness = await api.getReadiness() }
      catch (error) { this.readinessError = error.message || '无法检查准备状态' }
    },
    openTasks() { this.tasksVisible = true },
    saveTask(draft) { this.tasksVisible = true; this.$nextTick(() => this.$refs.analysisTasks.openCreate(draft)) },
    async runTask(prepared) {
      if (this.busy || this.taskStarting) return this.$message.warning('请等待当前回答结束')
      if (!this.readiness || !this.readiness.ai_ready) return this.$message.warning('请先配置 AI 服务')
      this.taskStarting = true
      try {
        const res = await api.createSession({ name: prepared.name || '分析任务', db_config_id: prepared.db_config_id })
        this.selectSession(res.session); this.tasksVisible = false; await this.refreshSessions()
        await this.$nextTick()
        const panel = this.$refs.chatPanel
        if (panel && this.activeSessionId === res.session.id) {
          await panel.loadMessages()
          if (this.activeSessionId === res.session.id && panel.sessionId === res.session.id) panel.send(prepared.question, prepared)
        }
      } catch { /* API 已显示错误 */ }
      finally { this.taskStarting = false }
    },
    refreshSessions() { return this.$refs.sessionList && this.$refs.sessionList.load() },
    async loadNotifications() {
      const res = await api.getNotifications({ page: 1, limit: 20 })
      this.notifications = res.notifications
      this.unread = res.unread_count
    },
    async markRead(n) { await api.updateNotification(n.id, { is_read: true }); this.loadNotifications() },
    async markAllRead() { await api.readAllNotifications(); this.loadNotifications() },
    async removeNotification(n) {
      await this.$confirm('确定删除该通知？', '提示', { type: 'warning' }).catch(() => null)
        .then((ok) => ok && api.deleteNotification(n.id).then(() => this.loadNotifications()))
    },
    onUserCommand(cmd) {
      if (cmd === 'settings') this.$router.push('/settings')
      else if (cmd === 'logout') { this.$store.dispatch('logout'); this.$router.replace('/auth') }
    },
  },
}
</script>

<style scoped>
.dash { height: 100%; }
.topbar { display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #ebeef5; }
.brand { font-size: 18px; font-weight: 600; color: #409eff; }
.actions { display: flex; align-items: center; gap: 12px; }
.user { cursor: pointer; color: #606266; }
.body { height: calc(100% - 60px); }
.sidebar { background: #fff; border-right: 1px solid #ebeef5; display: flex; flex-direction: column; padding: 12px; overflow: hidden; }
.main { padding: 0; height: 100%; display: flex; flex-direction: column; }
.main > .chat-panel { min-height: 0; flex: 1; }
.setup-bar { padding: 10px 16px; background: #fdf6ec; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; color: #8a6d3b; }
.setup-note { color: #909399; }
.placeholder { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #c0c4cc; }
.placeholder i { font-size: 64px; margin-bottom: 12px; }
.notif-head { display: flex; justify-content: space-between; margin-bottom: 8px; font-weight: 600; }
.notif-list { max-height: 360px; overflow-y: auto; }
.notif-item { padding: 8px; border-bottom: 1px solid #f0f0f0; }
.notif-item.unread { background: #ecf5ff; }
.notif-title { font-weight: 600; display: flex; justify-content: space-between; }
.notif-title .el-icon-close { cursor: pointer; color: #c0c4cc; }
.notif-content { font-size: 12px; color: #909399; margin: 4px 0; }
.empty { text-align: center; color: #c0c4cc; padding: 20px; }
.bell { margin-top: 4px; }
</style>

<template>
  <el-container class="dash">
    <el-header class="topbar">
      <div class="brand"><i class="el-icon-chat-dot-round"></i> 问答机器人</div>
      <div class="actions">
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
            <el-button icon="el-icon-bell" circle size="small" />
          </el-badge>
        </el-popover>

        <el-button icon="el-icon-setting" circle size="small" @click="$router.push('/settings')" />
        <el-dropdown @command="onUserCommand">
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
        <db-config-panel v-model="activeDbId" />
        <session-list ref="sessionList" :active-id="activeSessionId" @select="selectSession" />
      </el-aside>

      <el-main class="main">
        <chat-panel
          v-if="activeSessionId"
          :session-id="activeSessionId"
          :db-config-id="activeDbId"
          @updated="refreshSessions"
        />
        <div v-else class="placeholder">
          <i class="el-icon-chat-line-round"></i>
          <p>请选择或新建一个会话开始提问</p>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script>
import * as api from '../api'
import DbConfigPanel from '../components/db/DbConfigPanel.vue'
import SessionList from '../components/session/SessionList.vue'
import ChatPanel from '../components/chat/ChatPanel.vue'

export default {
  name: 'DashboardView',
  components: { DbConfigPanel, SessionList, ChatPanel },
  data() {
    return { activeDbId: '', activeSessionId: '', notifications: [], unread: 0 }
  },
  computed: {
    username() { return (this.$store.state.user && this.$store.state.user.username) || '用户' },
  },
  mounted() { this.loadNotifications() },
  methods: {
    selectSession(id) { this.activeSessionId = id },
    refreshSessions() { this.$refs.sessionList && this.$refs.sessionList.load() },
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
.main { padding: 0; height: 100%; }
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

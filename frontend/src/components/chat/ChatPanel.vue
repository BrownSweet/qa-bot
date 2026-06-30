<template>
  <div class="chat-panel">
    <div class="chat-head">
      <span class="title">问答</span>
      <div>
        <el-button size="mini" icon="el-icon-download" @click="exportData('excel')">导出Excel</el-button>
        <el-button size="mini" icon="el-icon-document" @click="exportData('csv')">导出CSV</el-button>
      </div>
    </div>

    <message-list
      :messages="messages"
      :typing="typing"
      :typing-content="typingContent"
      :status="status"
      @retry="retry"
      @stop="stop"
    />

    <chat-input :disabled="typing" :db-selected="!!dbConfigId" :status="status" @send="send" @stop="stop" />
  </div>
</template>

<script>
import * as api from '../../api'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'

export default {
  name: 'ChatPanel',
  components: { MessageList, ChatInput },
  props: { sessionId: String, dbConfigId: String },
  data() {
    return { messages: [], typing: false, typingContent: '', status: '', controller: null, lastQuestion: '' }
  },
  watch: { sessionId: { immediate: true, handler() { this.loadMessages() } } },
  methods: {
    async loadMessages() {
      if (!this.sessionId) return
      const res = await api.getMessages(this.sessionId)
      this.messages = res.messages
    },
    send(question) {
      if (!this.dbConfigId) return this.$message.warning('请先在左侧选择数据源')
      if (this.typing) return
      this.lastQuestion = question
      this.messages.push({ id: 'u-' + Date.now(), role: 'user', content: question, status: 'completed' })
      this.typing = true
      this.typingContent = ''
      this.status = 'connecting'

      this.controller = api.chatStream(
        { session_id: this.sessionId, db_config_id: this.dbConfigId, question },
        {
          onStatus: (d) => { this.status = d.status; if (d.status === 'error') this.typingContent = d.message },
          onMessage: (d) => { this.typingContent += d.content; this.status = 'outputting' },
          onComplete: (d) => {
            this.messages.push({ id: d.message_id, role: 'assistant', content: this.typingContent, status: d.status })
            this.resetTyping()
            this.$emit('updated')
          },
          onError: (e) => {
            this.messages.push({ id: 'e-' + Date.now(), role: 'assistant', content: e.message || '请求失败', status: 'error' })
            this.resetTyping()
          },
        }
      )
    },
    stop() {
      if (this.controller) this.controller.abort()
      if (this.typing) {
        const content = this.typingContent || '（已停止）'
        this.messages.push({ id: 's-' + Date.now(), role: 'assistant', content, status: 'completed' })
        this.$message.info('已停止回答')
      }
      this.resetTyping()
    },
    retry() {
      const lastUser = [...this.messages].reverse().find((m) => m.role === 'user')
      const q = (lastUser && lastUser.content) || this.lastQuestion
      if (q) this.send(q)
    },
    resetTyping() { this.typing = false; this.typingContent = ''; this.status = ''; this.controller = null },
    async exportData(format) {
      try { await api.exportSession(this.sessionId, format) }
      catch (e) { this.$message.error('导出失败') }
    },
  },
}
</script>

<style scoped>
.chat-panel { height: 100%; display: flex; flex-direction: column; }
.chat-head { display: flex; justify-content: space-between; align-items: center;
  padding: 10px 16px; background: #fff; border-bottom: 1px solid #ebeef5; }
.chat-head .title { font-weight: 600; }
</style>

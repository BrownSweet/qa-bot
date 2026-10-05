<template>
  <div class="chat-panel">
    <div class="chat-head">
      <span class="title">问答 <small>{{ sourceName }}</small></span>
      <div>
        <el-button size="mini" :disabled="typing || loading" icon="el-icon-refresh" @click="loadMessages">刷新记录</el-button>
        <el-button size="mini" :disabled="typing" icon="el-icon-download" @click="exportData('excel')">会话记录 Excel</el-button>
        <el-button size="mini" :disabled="typing" icon="el-icon-document" @click="exportData('csv')">会话记录 CSV</el-button>
      </div>
    </div>

    <div v-if="loading" class="load-state"><i class="el-icon-loading"></i> 正在加载会话…</div>
    <div v-else-if="loadError" class="load-state">
      <p>{{ loadError }}</p>
      <el-button size="small" @click="loadMessages">重新加载</el-button>
    </div>
    <message-list
      v-else
      :messages="messages"
      :typing="typing"
      :typing-content="typingContent"
      :status="status"
      :typing-evidence="typingEvidence"
      :typing-source="typingSource"
      @retry="retry"
      @save-task="saveTask"
      @stop="stop"
    />

    <chat-input :disabled="typing || loading || !!loadError || !aiReady" :generating="typing" :db-selected="!!dbConfigId" :status="status" @send="send" @stop="stop" />
  </div>
</template>

<script>
import * as api from '../../api'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import { questionForReply } from '../../utils/chat.mjs'
import { sameSourceIdentity } from '../../utils/analysis.mjs'

export default {
  name: 'ChatPanel',
  components: { MessageList, ChatInput },
  props: { sessionId: String, dbConfigId: String, configs: { type: Array, default: () => [] }, aiReady: { type: Boolean, default: true } },
  data() {
    return {
      messages: [], typing: false, typingContent: '', status: '', controller: null,
      loading: false, loadError: '', loadSequence: 0, streamSequence: 0, errorMessage: '',
      typingEvidence: null, typingSource: null, typingMessageId: '',
    }
  },
  computed: {
    sourceName() {
      const source = this.configs.find((item) => item.id === this.dbConfigId)
      return source ? `· ${source.name}` : '· 请选择数据源'
    },
  },
  watch: {
    typing(value) { this.$emit('busy', value) },
    sessionId: { immediate: true, handler() {
      this.cancelStream()
      this.messages = []
      this.loadMessages()
    } },
  },
  beforeDestroy() {
    this.loadSequence++
    this.cancelStream()
  },
  methods: {
    async loadMessages() {
      const sessionId = this.sessionId
      const sequence = ++this.loadSequence
      this.loadError = ''
      if (!sessionId) { this.loading = false; return }
      this.loading = true
      try {
        const res = await api.getMessages(sessionId)
        if (sequence === this.loadSequence && sessionId === this.sessionId) this.messages = res.messages
      } catch (e) {
        if (sequence === this.loadSequence) this.loadError = e.message || '会话加载失败'
      } finally {
        if (sequence === this.loadSequence) this.loading = false
      }
    },
    send(question, options = {}) {
      const dbConfigId = options.db_config_id || this.dbConfigId
      if (!dbConfigId) return this.$message.warning('请先在左侧选择数据源')
      if (this.typing || this.loading || this.loadError || !this.sessionId || !this.aiReady) return
      const sessionId = this.sessionId
      const sequence = ++this.streamSequence
      const isCurrent = () => sequence === this.streamSequence && sessionId === this.sessionId
      const userMessage = { id: 'u-' + Date.now(), role: 'user', content: question, status: 'completed',
        db_config_id: dbConfigId, source: this.configs.find(item => item.id === dbConfigId) || null }
      this.messages.push(userMessage)
      this.typing = true
      this.typingContent = ''
      this.errorMessage = ''
      this.typingEvidence = null
      this.typingSource = this.configs.find((item) => item.id === dbConfigId) || null
      this.typingMessageId = ''
      this.status = 'connecting'

      this.controller = api.chatStream(
        { session_id: this.sessionId, db_config_id: dbConfigId, question, ...(options.analysis_task_id ? {
          analysis_task_id: options.analysis_task_id, analysis_task_parameters: options.analysis_task_parameters || {},
        } : {}) },
        {
          onStatus: (d) => {
            if (!isCurrent()) return
            this.status = d.status
            if (d.status === 'error') this.errorMessage = d.message
          },
          onMessage: (d) => {
            if (!isCurrent()) return
            this.typingContent += d.content || ''
            if (this.status !== 'error') this.status = 'outputting'
          },
          onResult: (d) => {
            if (!isCurrent()) return
            this.typingEvidence = d.evidence
            this.typingSource = d.source || this.typingSource
            this.typingMessageId = d.message_id
          },
          onComplete: (d) => {
            if (!isCurrent()) return
            if (d.question_message_id) userMessage.id = d.question_message_id
            userMessage.source = d.source || userMessage.source
            this.messages.push({ id: d.message_id, role: 'assistant', content: this.typingContent || this.errorMessage || '（无内容）', status: d.status,
              db_config_id: d.db_config_id || dbConfigId, source: d.source || this.typingSource,
              question_message_id: d.question_message_id, analysis_task_id: d.analysis_task_id || options.analysis_task_id || null,
              evidence: this.typingEvidence, generation_snapshot: d.generation_snapshot || null })
            this.resetTyping()
            this.$emit('updated')
          },
          onError: (e) => {
            if (!isCurrent()) return
            const message = e.message || '请求失败'
            const content = this.typingContent ? `${this.typingContent}\n\n${message}` : message
            this.messages.push({ id: this.typingMessageId || 'e-' + Date.now(), role: 'assistant', content, status: 'error',
              db_config_id: dbConfigId, source: this.typingSource, evidence: this.typingEvidence, local_pending: true })
            this.resetTyping()
          },
        }
      )
    },
    stop() {
      if (!this.typing) return
      const content = this.typingContent || '（已停止）'
      const partial = { id: this.typingMessageId || 's-' + Date.now(), role: 'assistant', content, status: 'cancelled',
        db_config_id: this.typingSource && this.typingSource.id, source: this.typingSource, evidence: this.typingEvidence, local_pending: true }
      this.cancelStream()
      this.messages.push(partial)
      this.$message.info('已停止回答')
      this.$emit('updated')
    },
    cancelStream() {
      this.streamSequence++
      if (this.controller) this.controller.abort()
      this.resetTyping()
    },
    async retry(messageId) {
      const sessionId = this.sessionId
      const message = this.messages.find((item) => item.id === messageId)
      const q = questionForReply(this.messages, messageId)
      if (!q) return this.$message.warning('未找到原问题，请重新输入')
      if (!message.db_config_id) return this.$message.warning('此历史回答未记录来源，请选择数据源后重新提问')
      const source = this.configs.find((item) => item.id === message.db_config_id)
      if (!source) return this.$message.warning('原数据源已被删除，请重新关联后提问')
      if (!sameSourceIdentity(message.source, source)) {
        try { await this.$confirm('此数据源的连接位置或账号已更改，或原来源记录不完整。将按当前配置重新查询，结果可能无法与原回答直接比较。', '确认来源变更', { type: 'warning' }) }
        catch { return }
      } else if (message.db_config_id !== this.dbConfigId) {
        try { await this.$confirm(`将使用原数据源「${message.source?.name || '历史数据源'}」重试，并恢复为此会话的数据源。`, '确认重试来源') }
        catch { return }
      }
      if (sessionId !== this.sessionId || this.typing || this.loading) return
      this.$emit('source-restored', message.db_config_id)
      this.send(q, { db_config_id: message.db_config_id })
    },
    saveTask(messageId) {
      const message = this.messages.find((item) => item.id === messageId)
      const question = questionForReply(this.messages, messageId)
      if (message && question) this.$emit('save-task', { question, db_config_id: message.db_config_id })
    },
    resetTyping() { this.typing = false; this.typingContent = ''; this.status = ''; this.controller = null; this.errorMessage = ''; this.typingEvidence = null; this.typingSource = null; this.typingMessageId = '' },
    async exportData(format) {
      try { await api.exportSession(this.sessionId, format) }
      catch (e) { this.$message.error(e.message || '导出失败') }
    },
  },
}
</script>

<style scoped>
.chat-panel { height: 100%; display: flex; flex-direction: column; }
.chat-head { display: flex; justify-content: space-between; align-items: center;
  padding: 10px 16px; background: #fff; border-bottom: 1px solid #ebeef5; }
.chat-head .title { font-weight: 600; }
.chat-head small { font-weight: normal; color: #909399; }
.load-state { flex: 1; padding: 32px; text-align: center; color: #909399; }
</style>

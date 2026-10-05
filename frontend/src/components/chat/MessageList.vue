<template>
  <div ref="scroll" class="msg-list" role="log" aria-label="问答记录" aria-live="polite">
    <div v-for="m in messages" :key="m.id" class="row" :class="m.role">
      <div class="bubble" :class="{ error: m.status === 'error' }">
        <div v-if="m.role === 'assistant'" class="md" v-html="render(m.content)"></div>
        <div v-else class="text">{{ m.content }}</div>
        <div v-if="m.role === 'assistant' && m.source" class="message-state">来源：{{ m.source.name }} · {{ m.source.type }}</div>
        <query-evidence v-if="m.role === 'assistant' && m.evidence" :evidence="m.evidence" :source="m.source" :message-id="m.id" :pending="m.local_pending" :generation-snapshot="m.generation_snapshot" />
        <generation-snapshot v-else-if="m.role === 'assistant' && !m.local_pending" :snapshot="m.generation_snapshot" />
        <div v-if="m.role === 'assistant' && m.status === 'cancelled'" class="message-state">已停止回答</div>
        <div v-if="m.role === 'assistant' && m.status === 'pending'" class="message-state">回答尚未完成，可重新提问</div>
        <div v-if="m.local_pending" class="message-state">当前显示本次连接保留的内容，可点击“刷新记录”核对后台保存状态。</div>
        <div class="msg-actions">
          <el-button type="text" size="mini" icon="el-icon-document-copy" @click="copy(m.content)">复制</el-button>
          <el-button v-if="m.role === 'assistant'" type="text" size="mini" icon="el-icon-refresh"
                     :disabled="typing" @click="$emit('retry', m.id)">重试</el-button>
          <el-button v-if="m.role === 'assistant'" type="text" size="mini" icon="el-icon-star-off"
                     :disabled="typing || !m.db_config_id" @click="$emit('save-task', m.id)">收藏为分析任务</el-button>
        </div>
      </div>
    </div>

    <!-- 正在生成 -->
    <div v-if="typing" class="row assistant">
      <div class="bubble">
        <div class="status-indicator">
          <i class="el-icon-loading"></i> {{ statusText }}
          <el-button type="text" size="mini" class="stop-btn" @click="$emit('stop')">停止</el-button>
        </div>
        <div v-if="typingContent" class="md" v-html="render(typingContent)"></div>
        <query-evidence v-if="typingEvidence" :evidence="typingEvidence" :source="typingSource" pending />
      </div>
    </div>
  </div>
</template>

<script>
import { renderMarkdown } from '../../utils/markdown'
import { desktop, isDesktop } from '../../desktop'
import QueryEvidence from './QueryEvidence.vue'
import GenerationSnapshot from '../analysis/GenerationSnapshot.vue'

const STATUS_MAP = {
  connecting: '正在连接数据库...',
  scanning: '正在扫描数据表...',
  analyzing: '正在分析数据...',
  outputting: '正在生成回答...',
  error: '出错了',
}

export default {
  name: 'MessageList',
  components: { QueryEvidence, GenerationSnapshot },
  props: {
    messages: { type: Array, default: () => [] },
    typing: Boolean,
    typingContent: String,
    status: String,
    typingEvidence: Object,
    typingSource: Object,
  },
  computed: {
    statusText() { return STATUS_MAP[this.status] || '处理中...' },
  },
  watch: {
    messages() { this.scrollToBottom() },
    typingContent() { this.scrollToBottom() },
  },
  methods: {
    render: renderMarkdown,
    async copy(text) {
      try {
        if (isDesktop) await desktop.copyText(text)
        else await navigator.clipboard.writeText(text)
        this.$message.success('已复制')
      } catch (e) {
        this.$message.error(e.message || '复制失败')
      }
    },
    scrollToBottom() {
      this.$nextTick(() => {
        const el = this.$refs.scroll
        if (el) el.scrollTop = el.scrollHeight
      })
    },
  },
}
</script>

<style scoped>
.msg-list { flex: 1; overflow-y: auto; padding: 16px; background: #f5f7fa; }
.row { display: flex; margin-bottom: 16px; }
.row.user { justify-content: flex-end; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 8px; background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.row.user .bubble { background: #409eff; color: #fff; }
.bubble.error { background: #fef0f0; border: 1px solid #fbc4c4; color: #f56c6c; }
.text { white-space: pre-wrap; word-break: break-word; }
.msg-actions { margin-top: 4px; border-top: 1px dashed rgba(0,0,0,0.06); padding-top: 2px; }
.row.user .msg-actions { border-color: rgba(255,255,255,0.3); }
.row.user .msg-actions .el-button { color: #fff; }
.status-indicator { color: #409eff; font-size: 13px; margin-bottom: 6px; }
.stop-btn { color: #f56c6c; margin-left: 8px; }
.message-state { margin-top: 8px; color: #909399; font-size: 12px; }
</style>

<!-- 非 scoped：用于渲染后的 Markdown 内容（v-html 注入，不带 scoped 属性） -->
<style>
.md pre { background: #282c34; color: #abb2bf; padding: 12px; border-radius: 6px; overflow-x: auto; }
.md code { font-family: 'SFMono-Regular', Consolas, monospace; }
.md table { border-collapse: collapse; width: 100%; margin: 8px 0; }
.md th, .md td { border: 1px solid #dcdfe6; padding: 6px 10px; }
.md th { background: #f5f7fa; }
.md p { margin: 4px 0; }
</style>

<template>
  <div ref="scroll" class="msg-list">
    <div v-for="m in messages" :key="m.id" class="row" :class="m.role">
      <div class="bubble" :class="{ error: m.status === 'error' }">
        <div v-if="m.role === 'assistant'" class="md" v-html="render(m.content)"></div>
        <div v-else class="text">{{ m.content }}</div>
        <div class="msg-actions">
          <el-button type="text" size="mini" icon="el-icon-document-copy" @click="copy(m.content)">复制</el-button>
          <el-button v-if="m.role === 'assistant'" type="text" size="mini" icon="el-icon-refresh"
                     @click="$emit('retry')">重试</el-button>
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
      </div>
    </div>
  </div>
</template>

<script>
import { marked } from 'marked'

const STATUS_MAP = {
  connecting: '正在连接数据库...',
  scanning: '正在扫描数据表...',
  analyzing: '正在分析数据...',
  outputting: '正在生成回答...',
  error: '出错了',
}

export default {
  name: 'MessageList',
  props: {
    messages: { type: Array, default: () => [] },
    typing: Boolean,
    typingContent: String,
    status: String,
  },
  computed: {
    statusText() { return STATUS_MAP[this.status] || '处理中...' },
  },
  watch: {
    messages() { this.scrollToBottom() },
    typingContent() { this.scrollToBottom() },
  },
  methods: {
    render(text) {
      const html = marked.parse(text || '', { breaks: true })
      return html.replace(/<script[\s\S]*?<\/script>/gi, '')
    },
    copy(text) {
      navigator.clipboard.writeText(text).then(
        () => this.$message.success('已复制'),
        () => this.$message.error('复制失败')
      )
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

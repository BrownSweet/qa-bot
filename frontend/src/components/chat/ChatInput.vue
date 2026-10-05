<template>
  <div class="chat-input">
    <el-input
      v-model="text"
      type="textarea"
      :rows="3"
      resize="none"
      :placeholder="placeholder"
      @keydown.native.enter="onEnter"
    />
    <div class="input-actions">
      <span class="tip">Enter 发送 / Shift+Enter 换行</span>
      <div>
        <el-button v-if="generating" type="danger" size="small" icon="el-icon-video-pause" @click="$emit('stop')">停止</el-button>
        <el-button type="primary" size="small" icon="el-icon-position" :disabled="disabled || !dbSelected || !text.trim()" @click="submit">
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script>
import { shouldSendOnEnter } from '../../utils/chat.mjs'

export default {
  name: 'ChatInput',
  props: { disabled: Boolean, generating: Boolean, dbSelected: Boolean, status: String },
  data() { return { text: '' } },
  computed: {
    placeholder() {
      return this.dbSelected ? '请输入你的问题，例如：查询上个月的销售额' : '请先在左侧选择数据源…'
    },
  },
  methods: {
    onEnter(e) {
      if (!shouldSendOnEnter(e)) return
      e.preventDefault()
      this.submit()
    },
    submit() {
      const q = this.text.trim()
      if (!q || this.disabled) return
      if (!this.dbSelected) return this.$message.warning('请先在左侧选择数据源')
      if (q.length > 1000) return this.$message.warning('问题长度不能超过1000字符')
      this.$emit('send', q)
      this.text = ''
    },
  },
}
</script>

<style scoped>
.chat-input { padding: 12px 16px; background: #fff; border-top: 1px solid #ebeef5; }
.input-actions { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
.tip { font-size: 12px; color: #c0c4cc; }
</style>

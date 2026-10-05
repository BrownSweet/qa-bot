<template>
  <details class="generation-snapshot">
    <summary>当次生成上下文<span v-if="!snapshot"> · 未记录</span></summary>
    <p v-if="!snapshot" class="missing">此历史记录未保存生成上下文，无法用当前模型、业务口径或 Schema 还原当次输入。</p>
    <template v-else>
      <p class="note">以下内容是当次生成 SQL 时使用的输入快照。AI 服务仅显示域名或地址指纹，不展示凭证、路径和参数。</p>
      <dl>
        <dt>模型</dt><dd>{{ snapshot.model || '未记录' }}</dd>
        <dt>AI 服务</dt><dd>{{ providerIdentityForDisplay(snapshot) }}</dd>
        <dt>SQL 方言</dt><dd>{{ snapshot.dialect || '未记录' }}</dd>
      </dl>
      <div class="section"><strong>当次问题</strong><pre>{{ snapshot.question || '未记录' }}</pre></div>
      <div v-if="snapshot.task" class="section">
        <strong>当次分析任务</strong>
        <dl><dt>任务名称</dt><dd>{{ snapshot.task.name || '未记录' }}</dd><dt>任务 ID</dt><dd>{{ snapshot.task.id || '未记录' }}</dd></dl>
        <p class="label">问题模板</p><pre>{{ snapshot.task.question_template || '未记录' }}</pre>
        <p class="label">运行参数</p>
        <dl v-if="parameters.length"><template v-for="entry in parameters"><dt :key="entry.name + '-name'">{{ entry.name }}</dt><dd :key="entry.name + '-value'">{{ entry.value }}</dd></template></dl>
        <p v-else class="note">此任务没有参数。</p>
      </div>
      <details class="section"><summary>当次业务口径</summary><pre>{{ snapshot.business_context || '（未配置）' }}</pre></details>
      <details class="section"><summary>当次 Schema</summary><pre>{{ snapshot.schema || '未记录' }}</pre></details>
      <details class="section">
        <summary>当次历史对话（{{ history.length }} 条）</summary>
        <p v-if="!history.length" class="note">本次未使用历史对话。</p>
        <div v-for="(item, index) in history" :key="index" class="history-item">
          <strong>{{ item.role === 'assistant' ? 'AI 回答' : '用户问题' }}</strong>
          <pre>{{ item.content }}</pre>
        </div>
      </details>
    </template>
  </details>
</template>

<script>
import { providerIdentityForDisplay, snapshotParameters } from '../../utils/generationSnapshot.mjs'

export default {
  name: 'GenerationSnapshot',
  props: { snapshot: { type: Object, default: null } },
  computed: {
    parameters() { return snapshotParameters(this.snapshot) },
    history() { return Array.isArray(this.snapshot?.history) ? this.snapshot.history : [] },
  },
  methods: { providerIdentityForDisplay },
}
</script>

<style scoped>
.generation-snapshot { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #dcdfe6; color: #606266; font-size: 12px; min-width: 0; }
summary { cursor: pointer; color: #409eff; font-weight: 600; }
.missing, .note { color: #909399; line-height: 1.6; }
.section { margin-top: 10px; min-width: 0; }
.section > summary { margin-bottom: 5px; }
dl { display: grid; grid-template-columns: minmax(65px, max-content) minmax(0, 1fr); gap: 5px 12px; line-height: 1.6; }
dt { color: #909399; }
dd { margin: 0; overflow-wrap: anywhere; }
.label { margin: 7px 0 3px; color: #909399; }
pre { box-sizing: border-box; max-width: 100%; max-height: 240px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f7fa; padding: 9px; border-radius: 4px; font: inherit; line-height: 1.6; }
.history-item { border-top: 1px solid #ebeef5; padding-top: 7px; }
.history-item pre { margin-top: 4px; }
</style>

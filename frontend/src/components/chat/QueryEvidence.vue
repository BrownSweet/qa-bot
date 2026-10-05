<template>
  <details v-if="evidence" class="query-evidence">
    <summary>查询依据 · {{ sourceName }} · {{ coverage.returned_rows || 0 }} 行<span v-if="truncated"> · 已截断</span></summary>
    <dl>
      <dt>数据来源</dt><dd>{{ sourceName }} <span v-if="source">({{ source.type }}) · {{ source.file_path || source.database || source.host }}</span></dd>
      <dt>查询时间</dt><dd>{{ evidence.executed_at ? new Date(evidence.executed_at).toLocaleString() : '未记录' }}</dd>
      <dt>读取范围</dt><dd>返回 {{ coverage.returned_rows || 0 }} 行，行数上限 {{ coverage.row_limit || '—' }}；送入 AI {{ coverage.analyzed_rows || 0 }} 行；保存 {{ evidence.rows ? evidence.rows.length : 0 }} 行预览。<span v-if="coverage.analysis_result_byte_limit"> AI 结果输入预算 {{ formatBytes(coverage.analysis_result_byte_limit) }}，不等于已完整分析所有返回内容。</span></dd>
      <template v-if="scopeNotes.length"><dt>范围说明</dt><dd><p v-for="note in scopeNotes" :key="note" class="scope-note">{{ note }}</p></dd></template>
    </dl>
    <el-alert v-if="truncated" title="此处仅展示已保存的查询样本，不能据此推断全部数据。导出同样限于已保存结果。" type="warning" :closable="false" />
    <p class="sql-label">执行 SQL</p>
    <pre>{{ evidence.sql }}</pre>
    <result-table :columns="evidence.columns || []" :rows="evidence.rows || []" />
    <generation-snapshot v-if="!pending" :snapshot="generationSnapshot" />
    <div class="evidence-actions">
      <el-button size="mini" :disabled="!messageId || pending || !!exporting" :loading="exporting === 'excel'" @click="exportData('excel')">导出结果预览 Excel</el-button>
      <el-button size="mini" :disabled="!messageId || pending || !!exporting" :loading="exporting === 'csv'" @click="exportData('csv')">导出结果预览 CSV</el-button>
    </div>
  </details>
</template>

<script>
import * as api from '../../api'
import ResultTable from './ResultTable.vue'
import GenerationSnapshot from '../analysis/GenerationSnapshot.vue'

export default {
  name: 'QueryEvidence',
  components: { ResultTable, GenerationSnapshot },
  props: { evidence: Object, source: Object, messageId: String, pending: Boolean, generationSnapshot: Object },
  data() { return { exporting: '' } },
  computed: {
    sourceName() { return (this.source && this.source.name) || '来源未记录' },
    coverage() { return (this.evidence && this.evidence.coverage) || {} },
    truncated() {
      return ['analysis_truncated', 'evidence_truncated', 'has_more', 'cells_truncated', 'result_truncated', 'analysis_bytes_truncated']
        .some(key => !!this.coverage[key])
    },
    scopeNotes() {
      const coverage = this.coverage
      const notes = []
      if (coverage.has_more) notes.push('数据源仍有未返回的后续行。')
      if (coverage.cells_truncated) notes.push(`${coverage.truncated_cell_count || '部分'} 个单元格内容因大小限制已截断，显示与分析均不包含完整内容。`)
      if (coverage.result_truncated) notes.push(`查询结果达到${coverage.result_byte_limit ? ' ' + this.formatBytes(coverage.result_byte_limit) + ' ' : ''}字节上限，后续行未返回。`)
      if (coverage.analysis_bytes_truncated) notes.push(`AI 输入达到${coverage.analysis_result_byte_limit ? ' ' + this.formatBytes(coverage.analysis_result_byte_limit) + ' ' : ''}大小预算，实际仅送入前 ${coverage.analyzed_rows || 0} 行，其余已返回行未进入本次分析。`)
      else if (coverage.analysis_truncated && coverage.returned_rows > coverage.analyzed_rows) notes.push(`AI 仅使用已返回结果的前 ${coverage.analyzed_rows || 0} 行。`)
      if (coverage.evidence_truncated) notes.push('保存和导出的结果预览也有截断，不能视为完整查询结果。')
      return notes
    },
  },
  methods: {
    formatBytes(bytes) { return bytes >= 1024 ? `${Math.round(bytes / 1024 * 10) / 10} KiB` : `${bytes} B` },
    async exportData(format) {
      this.exporting = format
      try { await api.exportResult(this.messageId, format) }
      catch (error) { this.$message.error(error.message || '导出失败') }
      finally { this.exporting = '' }
    },
  },
}
</script>

<style scoped>
.query-evidence { margin-top: 12px; border-top: 1px solid #ebeef5; padding-top: 8px; color: #606266; font-size: 12px; }
summary { cursor: pointer; color: #409eff; font-weight: 600; }
dl { display: grid; grid-template-columns: 62px 1fr; gap: 5px; line-height: 1.6; }
dt { color: #909399; } dd { margin: 0; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f7fa; padding: 10px; border-radius: 4px; }
.sql-label { margin-bottom: 4px; font-weight: 600; }
.evidence-actions { margin-top: 8px; }
.scope-note { margin: 0 0 4px; }
</style>

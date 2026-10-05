<template>
  <div class="result-table">
    <div v-if="columns.length" class="table-scroll">
      <table>
        <thead><tr><th v-for="(column, index) in columns" :key="index">{{ label(column) }}</th></tr></thead>
        <tbody>
          <tr v-for="(row, rowIndex) in pageRows" :key="rowIndex">
            <td v-for="(column, columnIndex) in columns" :key="columnIndex">{{ cell(Array.isArray(row) ? row[columnIndex] : row[label(column)]) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!rows.length" class="empty-result">查询结果为空。</p>
    <el-pagination v-if="rows.length > pageSize" small layout="prev, pager, next" :total="rows.length"
                   :page-size="pageSize" :current-page.sync="page" />
  </div>
</template>

<script>
export default {
  name: 'ResultTable',
  props: { columns: { type: Array, default: () => [] }, rows: { type: Array, default: () => [] } },
  data() { return { page: 1, pageSize: 20 } },
  computed: { pageRows() { return this.rows.slice((this.page - 1) * this.pageSize, this.page * this.pageSize) } },
  watch: { rows() { this.page = 1 } },
  methods: {
    label(column) { return typeof column === 'string' ? column : column.name },
    cell(value) {
      if (value === null || value === undefined) return 'NULL'
      return typeof value === 'object' ? JSON.stringify(value) : String(value)
    },
  },
}
</script>

<style scoped>
.table-scroll { overflow: auto; max-height: 330px; border: 1px solid #dcdfe6; }
table { border-collapse: collapse; width: 100%; font-size: 12px; background: #fff; }
th, td { border: 1px solid #ebeef5; padding: 6px 10px; text-align: left; white-space: pre-wrap; min-width: 90px; max-width: 380px; overflow-wrap: anywhere; }
th { position: sticky; top: 0; background: #f5f7fa; }
.empty-result { color: #909399; font-size: 12px; }
</style>

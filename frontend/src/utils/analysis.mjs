export function templateParameters(template) {
  return [...new Set([...String(template || '').matchAll(/\{\{([A-Za-z][A-Za-z0-9_]{0,31})\}\}/g)].map(match => match[1]))]
}

export function templateError(template) {
  const remaining = String(template || '').replace(/\{\{([A-Za-z][A-Za-z0-9_]{0,31})\}\}/g, '')
  return remaining.includes('{{') || remaining.includes('}}')
    ? '模板变量须写成 {{name}}，以英文字母开头，仅含字母、数字或下划线，且不超过 32 字符' : ''
}

export function sameSourceIdentity(original, current) {
  if (!original || !current) return false
  return ['type', 'host', 'port', 'database', 'file_path', 'username'].every(key =>
    String(original[key] ?? '') === String(current[key] ?? ''))
}

export function compareTaskRuns(runs) {
  if (!Array.isArray(runs) || runs.length !== 2) return null
  const [first, second] = runs
  const reasons = []
  if (first.status !== 'completed' || second.status !== 'completed') {
    reasons.push('至少一次运行没有完整完成，不能比较回答结论')
  }
  if (!sameSourceIdentity(first.source, second.source)) {
    reasons.push('两次运行的数据源连接位置或账号不同')
  }
  const left = first.evidence
  const right = second.evidence
  if (!left || !right) {
    reasons.push('至少一次运行缺少查询依据')
  } else {
    if (!left.sql || !right.sql || left.sql !== right.sql) {
      reasons.push('执行 SQL 不同，须人工核对筛选条件与统计口径')
    }
    if (JSON.stringify(left.columns || []) !== JSON.stringify(right.columns || [])) {
      reasons.push('结果列不同，不能按相同指标逐列比较')
    }
    const a = left.coverage || {}
    const b = right.coverage || {}
    if (!a.row_limit || !b.row_limit || a.row_limit !== b.row_limit) {
      reasons.push('查询行数上限不同或未记录')
    }
    const incomplete = ['has_more', 'cells_truncated', 'result_truncated', 'evidence_truncated',
      'analysis_truncated', 'analysis_bytes_truncated']
    if (incomplete.some(key => a[key] || b[key])) {
      reasons.push('至少一次运行只保存或分析了部分结果')
    }
  }
  return { comparable: reasons.length === 0, reasons }
}

export function evaluationSummary(results) {
  const completed = Object.values(results).filter(Boolean)
  const review = completed.filter(run => run.baseline_changed).length
  const confirmed = completed.filter(run => !run.baseline_changed &&
    (run.status === 'mismatch' || (run.status === 'passed' && run.matched === true)))
  const passed = confirmed.filter(run => run.status === 'passed' && run.matched === true).length
  return { completed: completed.length, confirmed: confirmed.length, review,
    errors: completed.length - review - confirmed.length, passed, failed: confirmed.length - passed,
    percent: confirmed.length ? Math.round(passed / confirmed.length * 100) : null }
}

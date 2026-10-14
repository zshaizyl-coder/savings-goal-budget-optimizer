export const categoryLabels = {
  food: '餐饮', shopping: '购物', transport: '交通', entertainment: '娱乐',
  housing: '居住', life_services: '生活服务', health: '医疗健康', education: '教育',
  communications: '通讯', human_relations: '人情往来', other: '其他 / 待确认消费'
}

export const modeLabels = {
  goal_priority: '更快接近目标', balanced: '兼顾目标与生活（推荐）', life_stability: '尽量保持现有生活'
}

export const riskLabels = { LOW: '较低', MEDIUM: '需留意', HIGH: '较高', LOW_RISK: '较低', MEDIUM_RISK: '需留意', HIGH_RISK: '较高' }

export const planStatusLabels = { draft: '待确认', active: '执行中', superseded: '历史版本' }

export const reasonLabels = {
  initial_plan: '首次生成', manual_replan: '主动重新规划', unplanned_expense: '计划外支出',
  extra_income: '临时额外收入', income_decrease: '收入下降', income_increase: '收入增加',
  goal_amount_change: '储蓄目标调整', goal_date_change: '目标日期调整',
  budget_constraint_change: '预算底线调整'
}

export function categoryName(code) { return categoryLabels[code] || code || '未命名类别' }
export function localDate(value) {
  if (!value) return '暂无'
  const text = String(value)
  return text.includes('T') ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(text)) : text
}

export function workflowSteps(workflow = {}) {
  return [
    { title: '导入账单', path: '/bills', done: !!workflow.has_transactions },
    { title: '查看分析', path: '/analytics', done: !!workflow.has_transactions },
    { title: '设置目标', path: '/goals', done: !!workflow.has_goal && !!workflow.has_constraints },
    { title: '生成预算', path: '/budgets', done: !!workflow.has_active_plan },
    { title: '执行与调整', path: '/replan', done: false }
  ]
}

export const adjustabilityOptions = [
  { label: '不可调整', value: 'none' }, { label: '低', value: 'low' },
  { label: '中', value: 'medium' }, { label: '高', value: 'high' }
]

export function validateGoal(form) {
  if (!form.start_date || !form.target_date) return '请选择开始和目标日期'
  if (form.target_date <= form.start_date) return '目标日期必须晚于开始日期'
  if (!(Number(form.target_savings_minor) > Number(form.current_savings_minor))) return '目标存款应高于当前存款'
  return null
}

export const adjustabilityOptions = [
  { label: '必须保持不变', value: 'none' }, { label: '尽量少调整', value: 'low' },
  { label: '可以适度调整', value: 'medium' }, { label: '必要时可多调整', value: 'high' }
]

export function validateGoal(form) {
  if (!form.start_date || !form.target_date) return '请选择开始和目标日期'
  if (form.target_date <= form.start_date) return '目标日期必须晚于开始日期'
  if (!(Number(form.target_savings_minor) > Number(form.current_savings_minor))) return '目标存款应高于当前存款'
  return null
}

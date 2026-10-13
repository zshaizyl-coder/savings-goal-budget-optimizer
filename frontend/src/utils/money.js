export function formatMoney(minor, empty = '暂无数据') {
  if (minor === null || minor === undefined || Number.isNaN(Number(minor))) return empty
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency', currency: 'CNY', minimumFractionDigits: 2
  }).format(Number(minor) / 100)
}

export function yuanToMinor(value) {
  const text = String(value ?? '').trim()
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('金额最多保留两位小数')
  const [whole, decimal = ''] = text.split('.')
  return Number(whole) * 100 + Number(decimal.padEnd(2, '0'))
}

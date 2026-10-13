import { describe, expect, it } from 'vitest'
import { formatMoney, formatPercent, yuanToMinor } from './money.js'

describe('money utilities', () => {
  it('formats integer minor units consistently', () => expect(formatMoney(1234)).toContain('12.34'))
  it('keeps decimal conversion exact', () => expect(yuanToMinor('10.10')).toBe(1010))
  it('rejects excess decimal places', () => expect(() => yuanToMinor('1.001')).toThrow())
  it('formats basis points consistently', () => expect(formatPercent(8125)).toBe('81.3%'))
})

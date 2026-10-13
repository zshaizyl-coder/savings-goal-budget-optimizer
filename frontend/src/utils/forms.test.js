import { describe, expect, it } from 'vitest'
import { validateGoal } from './forms.js'

describe('goal form validation', () => {
  it('rejects reversed dates', () => expect(validateGoal({start_date:'2026-02-01',target_date:'2026-01-01',current_savings_minor:1,target_savings_minor:2})).toBeTruthy())
  it('accepts an increasing target and future date', () => expect(validateGoal({start_date:'2026-01-01',target_date:'2026-02-01',current_savings_minor:1,target_savings_minor:2})).toBeNull())
})

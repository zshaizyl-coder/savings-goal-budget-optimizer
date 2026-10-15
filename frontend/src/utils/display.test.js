import { describe, expect, it } from 'vitest'
import { categoryName, localDate, primaryNavigation, secondaryNavigation, workflowSteps } from './display.js'

describe('user-facing display rules', () => {
  it('does not expose OTHER as the primary label', () => {
    expect(categoryName('other')).toBe('其他 / 待确认消费')
  })

  it('uses localized category names', () => {
    expect(categoryName('food')).toBe('餐饮')
    expect(categoryName('shopping')).toBe('购物')
  })

  it('keeps workflow steps in the single supported order', () => {
    expect(workflowSteps({}).map(item => item.path)).toEqual(['/bills', '/analytics', '/goals', '/budgets', '/replan'])
  })

  it('keeps only the five required tasks in primary navigation', () => {
    expect(primaryNavigation.map(item => item.path)).toEqual(['/bills', '/analytics', '/goals', '/budgets', '/replan'])
  })

  it('moves experiment and history pages into secondary navigation', () => {
    expect(secondaryNavigation.map(item => item.path)).toEqual(['/history', '/forecast'])
  })

  it('marks only completed persisted milestones', () => {
    const steps = workflowSteps({ has_transactions: true, has_goal: true, has_constraints: false, has_active_plan: false })
    expect(steps.map(item => item.done)).toEqual([true, true, false, false, false])
  })

  it('renders ISO timestamps as local human-readable text', () => {
    expect(localDate('2026-10-14T08:00:00+08:00')).not.toContain('T')
  })
})

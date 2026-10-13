import { describe, expect, it } from 'vitest'
import { mapApiError } from './client.js'

describe('API error mapping', () => {
  it('keeps safe business error fields', () => {
    const error = mapApiError({ response: { data: { error: {
      code: 'INFEASIBLE_GOAL', message: '当前目标不可行', details: { gap: 1000 }
    } } } })
    expect(error.code).toBe('INFEASIBLE_GOAL')
    expect(error.message).toBe('当前目标不可行')
    expect(error.details.gap).toBe(1000)
  })

  it('does not expose an unknown technical exception', () => {
    const error = mapApiError(new Error('SQLAlchemy internal details'))
    expect(error.code).toBe('NETWORK_ERROR')
    expect(error.message).not.toContain('SQLAlchemy')
  })

  it('explains long-running bill imports without encouraging a retry', () => {
    const error = mapApiError({ code: 'ECONNABORTED' })
    expect(error.code).toBe('REQUEST_TIMEOUT')
    expect(error.message).toContain('不要重复上传')
  })
})

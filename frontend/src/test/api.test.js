// api.js: multimodalAudit must send the caller's consent, never hardcode it.
import { describe, expect, it, vi, beforeEach } from 'vitest'

const postMock = vi.fn()

vi.mock('axios', () => ({
  default: {
    create: () => ({
      request: (config) => {
        postMock(config)
        return Promise.resolve({ data: { ok: true }, headers: {} })
      },
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
  },
}))

import { api } from '../services/api'

describe('multimodalAudit consent', () => {
  beforeEach(() => {
    postMock.mockClear()
  })

  it('sends consent=false by default', async () => {
    await api.multimodalAudit('p1', {})
    const form = postMock.mock.calls[0][0].data
    expect(form.get('consent')).toBe('false')
  })

  it('sends consent=true only when the caller checked the box', async () => {
    await api.multimodalAudit('p1', {}, 'architecture_diagram', '', true)
    const form = postMock.mock.calls[0][0].data
    expect(form.get('consent')).toBe('true')
  })
})

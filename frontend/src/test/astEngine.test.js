import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeRepository } from '../services/astEngine'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AST Engine service', () => {
  it('sends repository parameters and extracts the score fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        overall_architect_score: 87.25,
        is_ai_flagged: false,
        analyzed_files: 12,
        file_breakdown: [],
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await analyzeRepository({
      owner: 'example',
      repo: 'sample',
      branch: 'feature/ast',
    })

    const requestUrl = new URL(fetchMock.mock.calls[0][0])
    expect(requestUrl.pathname).toBe('/analyze/repo')
    expect(requestUrl.searchParams.get('owner')).toBe('example')
    expect(requestUrl.searchParams.get('repo')).toBe('sample')
    expect(requestUrl.searchParams.get('branch')).toBe('feature/ast')
    expect(result).toEqual({
      overall_architect_score: 87.25,
      is_ai_flagged: false,
      analyzed_files: 12,
    })
  })

  it('reports an unavailable engine clearly', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(analyzeRepository({
      owner: 'example',
      repo: 'sample',
      branch: 'main',
    })).rejects.toThrow('Cannot reach the AST Engine')
  })
})

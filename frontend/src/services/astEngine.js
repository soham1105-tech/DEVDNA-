const AST_ENGINE_URL = import.meta.env.VITE_AST_ENGINE_URL || 'http://127.0.0.1:8001'

export async function analyzeRepository({ owner, repo, branch, signal }) {
  const url = new URL('/analyze/repo', AST_ENGINE_URL)
  url.search = new URLSearchParams({ owner, repo, branch }).toString()

  let response
  try {
    response = await fetch(url, { headers: { Accept: 'application/json' }, signal })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new Error('Cannot reach the AST Engine. Check that the service is running and VITE_AST_ENGINE_URL is correct.')
  }

  if (!response.ok) {
    let detail = `AST analysis failed (${response.status}).`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    throw new Error(detail)
  }

  const body = await response.json()
  return {
    overall_architect_score:
      typeof body.overall_architect_score === 'number' && Number.isFinite(body.overall_architect_score)
        ? body.overall_architect_score
        : null,
    is_ai_flagged: body.is_ai_flagged === true,
    analyzed_files: Number.isInteger(body.analyzed_files) ? body.analyzed_files : 0,
  }
}

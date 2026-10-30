import { motion } from 'framer-motion'
import { FileArchive, FolderOpen, Sparkles, UploadCloud } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAnalysisFlow } from '../hooks/useAnalysisFlow'
import AnalysisProgress from './AnalysisProgress'
import { Button, ErrorState, Modal } from './ui'

const TABS = [
  { id: 'sample', label: 'Demo project', icon: Sparkles },
  { id: 'zip', label: 'Upload ZIP', icon: UploadCloud },
  { id: 'local', label: 'Local folder', icon: FolderOpen },
]

export default function NewProjectModal({ open, onClose, initialTab = 'sample' }) {
  const [tab, setTab] = useState(initialTab)
  const [file, setFile] = useState(null)
  const [path, setPath] = useState('')
  const [projectName, setProjectName] = useState('')
  const [githubUrl, setGithubUrl] = useState('')
  const [githubBranch, setGithubBranch] = useState('main')
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)
  const flow = useAnalysisFlow()
  const navigate = useNavigate()

  useEffect(() => {
    if (open) {
      setTab(initialTab)
      setFile(null)
      setPath('')
      setProjectName('')
      setGithubUrl('')
      setGithubBranch('main')
      flow.reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialTab])

  const go = async () => {
    const kind = tab === 'zip' && !file ? 'github' : tab
    const payload =
      tab === 'zip'
        ? file
          ? { file, githubUrl, githubBranch }
          : { github_url: githubUrl.trim(), github_branch: githubBranch.trim() || 'main' }
        : tab === 'local'
        ? {
            path: path.trim(),
            name: projectName.trim() || undefined,
            github_url: githubUrl.trim() || undefined,
            github_branch: githubUrl.trim() ? githubBranch.trim() || undefined : undefined,
          }
        : undefined
    const res = await flow.start(kind, payload)
    if (res) {
      setTimeout(() => {
        onClose()
        navigate('/')
      }, 900)
    }
  }

  const running = flow.busy || flow.run
  return (
    <Modal
      open={open}
      onClose={flow.busy ? () => {} : onClose}
      title={running ? 'Analysing repository' : 'New project'}
      subtitle={running ? 'Real static analysis in progress' : 'DevDNA analyses Python source code statically. Nothing is executed during analysis.'}
      footer={
        !running || flow.error ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={go}
              disabled={(tab === 'zip' && !file && !githubUrl.trim()) || (tab === 'local' && !path.trim())}
            >
              {tab === 'sample' ? 'Load demo project' : 'Analyse'}
            </Button>
          </>
        ) : null
      }
    >
      {running && !flow.error ? (
        <AnalysisProgress run={flow.run} upload={flow.upload} />
      ) : (
        <>
          <div className="mb-4 flex gap-1 rounded-lg bg-ink-900/70 p-1" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`relative flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium ${tab === t.id ? 'text-ink-100' : 'text-ink-300 hover:text-ink-100'}`}
              >
                {tab === t.id && <motion.span layoutId="np-tab" className="absolute inset-0 rounded-md bg-ink-700" />}
                <t.icon className="relative h-3.5 w-3.5" />
                <span className="relative">{t.label}</span>
              </button>
            ))}
          </div>
          {tab === 'sample' && (
            <div className="text-sm text-ink-300">
              <p>
                <span className="font-medium text-ink-100">ShopLite</span> is a small bundled FastAPI shop with HTTP routes, a service layer, SQLite repositories, an
                external shipping API (mocked locally), a background worker and a real pytest suite.
              </p>
              <p className="mt-2">It needs no network or API key, and it is the only project allowed to run experiments in the local restricted harness.</p>
            </div>
          )}
          {tab === 'zip' && (
            <div>
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDrag(true)
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDrag(false)
                  setFile(e.dataTransfer.files?.[0] || null)
                }}
                onClick={() => inputRef.current?.click()}
                className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-4 py-8 text-center transition-colors ${drag ? 'border-violet-400 bg-violet-400/5' : 'border-ink-500 hover:border-ink-400'}`}
              >
                <FileArchive className="h-6 w-6 text-violet-300" />
                <div className="mt-2 text-sm text-ink-100">{file ? file.name : 'Drop a .zip of your repository or click to browse'}</div>
                <div className="mt-1 text-xs text-ink-400">Max 150 MB. Or enter a public GitHub URL below to import it directly.</div>
                <input ref={inputRef} type="file" accept=".zip,application/zip" className="hidden" onChange={(e) => setFile(e.target.files?.[0] || null)} />
              </div>
              <p className="mt-2 text-xs text-ink-400">Repository code is untrusted: it is analysed statically, and experiments only run inside the Docker sandbox.</p>
              <GitHubRepositoryFields
                allowImport
                githubUrl={githubUrl}
                setGithubUrl={setGithubUrl}
                githubBranch={githubBranch}
                setGithubBranch={setGithubBranch}
              />
            </div>
          )}
          {tab === 'local' && (
            <div className="space-y-3">
              <div>
                <label className="text-xs text-ink-300" htmlFor="local-path">
                  Absolute path to local Python repository
                </label>
                <input
                  id="local-path"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="C:\\Users\\...\\my-python-project"
                  className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 font-mono text-sm text-ink-100 outline-none focus:border-violet-400"
                />
              </div>

              <div>
                <label className="text-xs text-ink-300" htmlFor="project-name">
                  Project name (optional)
                </label>
                <input
                  id="project-name"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="Defaults to folder name"
                  className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-ink-100 outline-none focus:border-violet-400"
                />
              </div>

              {(path.startsWith('http://') || path.startsWith('https://') || path.startsWith('git@')) && (
                <div className="rounded-lg border border-amber-400/20 bg-amber-400/10 p-2.5 text-xs text-amber-200">
                  <strong>Git URL detected:</strong> To analyze a remote repository, please download it as a ZIP (GitHub: <em>Code &gt; Download ZIP</em>) and use the <strong>Upload ZIP</strong> tab, or run <code className="font-mono">git clone</code> locally and enter that folder path.
                </div>
              )}
              <GitHubRepositoryFields
                githubUrl={githubUrl}
                setGithubUrl={setGithubUrl}
                githubBranch={githubBranch}
                setGithubBranch={setGithubBranch}
              />

              <p className="text-xs text-ink-400">
                Folders inside <code className="font-mono text-ink-300">DEVDNA_ALLOWED_LOCAL_ROOTS</code> (configured in <code className="font-mono text-ink-300">backend/.env</code>) can be analysed. Analysis is strictly read-only and static.
              </p>
            </div>
          )}
          {flow.error && (
            <div className="mt-4 space-y-2">
              <ErrorState title="Could not analyse" error={flow.error} />
              {flow.error?.code === 'too_large' && (
                <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 p-3 text-xs text-cyan-200">
                  <strong>Why did this happen?</strong> Python project ZIPs often contain virtual environments (<code className="font-mono">.venv/</code> or <code className="font-mono">venv/</code>) or <code className="font-mono">node_modules/</code> which contain hundreds of megabytes of compiled binaries.
                  <br /><br />
                  <strong>How to fix:</strong>
                  <ul className="mt-1 list-disc pl-4 space-y-1 text-ink-200">
                    <li>Use the <strong>Local folder</strong> tab above and paste the directory path directly &mdash; DevDNA automatically ignores <code className="font-mono">.venv</code>, caches, and node_modules on the fly without any file size limits!</li>
                    <li>Or delete the <code className="font-mono">.venv</code> or <code className="font-mono">node_modules</code> folder before creating your ZIP archive.</li>
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Modal>
  )
}

function GitHubRepositoryFields({ githubUrl, setGithubUrl, githubBranch, setGithubBranch, allowImport = false }) {
  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
      <div>
        <label className="text-xs text-ink-300" htmlFor="github-repository-url">
          GitHub repository URL (optional)
        </label>
        <input
          id="github-repository-url"
          type="url"
          value={githubUrl}
          onChange={(e) => setGithubUrl(e.target.value)}
          placeholder="https://github.com/owner/repository"
          className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-ink-100 outline-none focus:border-violet-400"
        />
      </div>
      <div>
        <label className="text-xs text-ink-300" htmlFor="github-repository-branch">
          Branch
        </label>
        <input
          id="github-repository-branch"
          value={githubBranch}
          onChange={(e) => setGithubBranch(e.target.value)}
          placeholder="main"
          disabled={!githubUrl.trim()}
          className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2 text-sm text-ink-100 outline-none focus:border-violet-400 disabled:opacity-50"
        />
      </div>
      <p className="text-xs text-ink-400 sm:col-span-2">
        {allowImport
          ? 'Enter a public repository URL to import and analyze it directly. Private repositories are not supported.'
          : 'Link a public GitHub repository to display its AST authenticity score on the Overview dashboard.'}
      </p>
    </div>
  )
}

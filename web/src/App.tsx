import { useEffect, useState } from 'react'
import { Hero, Nav } from './sections/Top'
import Problem from './sections/Problem'
import Idea from './sections/Idea'
import Checker from './sections/Checker'
import Explorer from './sections/Explorer'
import Results from './sections/Results'
import { Cite, Footer, Roadmap } from './sections/Roadmap'
import Services from './services/Services'
import { SUBTABS, type Sub } from './services/subtabs'

type Theme = 'light' | 'dark'
export type View = { page: 'project' } | { page: 'services'; sub: Sub }

function initialTheme(): Theme {
  const t = document.documentElement.dataset.theme
  return t === 'dark' ? 'dark' : 'light'
}

function parseHash(hash: string): View {
  const m = hash.match(/^#\/services(?:\/([\w-]+))?/)
  if (!m) return { page: 'project' }
  const sub = SUBTABS.find(([id]) => id === m[1])?.[0] ?? 'status'
  return { page: 'services', sub }
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const [view, setView] = useState<View>(() => parseHash(window.location.hash))

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('autom2m-theme', theme)
    } catch {
      /* storage may be unavailable */
    }
  }, [theme])

  useEffect(() => {
    const onHash = () => {
      const next = parseHash(window.location.hash)
      setView((prev) => {
        if (prev.page !== next.page) {
          // the target section mounts on the next render; scroll to it then
          const id = window.location.hash.slice(1)
          requestAnimationFrame(() => {
            const el = next.page === 'project' && id ? document.getElementById(id) : null
            if (el) el.scrollIntoView()
            else window.scrollTo(0, 0)
          })
        }
        return next
      })
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    document.title = view.page === 'services' ? 'AutoM2M Services' : 'AutoM2M: Checked Composition for LLM Agent Teams'
  }, [view.page])

  const goSub = (sub: Sub) => {
    window.location.hash = `#/services/${sub}`
  }

  return (
    <>
      <a className="skip" href={view.page === 'project' ? '#problem' : '#services'}>
        Skip to content
      </a>
      <Nav view={view} onTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))} />
      <main>
        {view.page === 'project' ? (
          <>
            <Hero />
            <Problem />
            <Idea />
            <Checker />
            <Explorer />
            <Results />
            <Roadmap />
            <Cite />
          </>
        ) : (
          <Services sub={view.sub} go={goSub} />
        )}
      </main>
      <Footer />
    </>
  )
}

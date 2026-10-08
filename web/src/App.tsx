import { useEffect, useState } from 'react'
import { Hero, Nav } from './sections/Top'
import Problem from './sections/Problem'
import Idea from './sections/Idea'
import Checker from './sections/Checker'
import Explorer from './sections/Explorer'
import Results from './sections/Results'
import { Cite, Footer, Roadmap } from './sections/Roadmap'

type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  const t = document.documentElement.dataset.theme
  return t === 'dark' ? 'dark' : 'light'
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('autom2m-theme', theme)
    } catch {
      /* storage may be unavailable */
    }
  }, [theme])
  return (
    <>
      <a className="skip" href="#problem">Skip to content</a>
      <Nav onTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))} />
      <main>
        <Hero />
        <Problem />
        <Idea />
        <Checker />
        <Explorer />
        <Results />
        <Roadmap />
        <Cite />
      </main>
      <Footer />
    </>
  )
}

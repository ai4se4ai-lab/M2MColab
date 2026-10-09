import { useEffect, useState } from 'react'
import { Article, GithubLogo, Graph, Quotes, CircleHalf, Plugs } from '@phosphor-icons/react'
import type { View } from '../App'

export const REPO = 'https://github.com/ai4se4ai-lab/M2MColab'

const links = [
  ['problem', 'Problem'],
  ['idea', 'Idea'],
  ['checker', 'Checker'],
  ['explorer', 'Explorer'],
  ['results', 'Results'],
  ['roadmap', 'Roadmap'],
  ['cite', 'Cite'],
] as const

export function Nav({ onTheme, view }: { onTheme: () => void; view: View }) {
  const [current, setCurrent] = useState<string>('')
  const onProject = view.page === 'project'
  useEffect(() => {
    if (!onProject) return
    const els = links.map(([id]) => document.getElementById(id)).filter(Boolean) as HTMLElement[]
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (vis[0]) setCurrent(vis[0].target.id)
      },
      { rootMargin: '-45% 0px -50% 0px' },
    )
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [onProject])
  return (
    <header className="nav">
      <div className="wrap">
        <a className="brand" href="#top">
          AutoM2M
        </a>
        <div className="view-switch" role="tablist" aria-label="Site areas">
          <a href="#top" role="tab" aria-selected={onProject}>
            Project
          </a>
          <a href="#/services/status" role="tab" aria-selected={!onProject}>
            <Plugs size={14} /> Services
          </a>
        </div>
        {onProject ? (
          <nav className="nav-links" aria-label="Sections">
            {links.map(([id, label]) => (
              <a key={id} href={`#${id}`} aria-current={current === id ? 'true' : undefined}>
                {label}
              </a>
            ))}
          </nav>
        ) : (
          <span className="nav-spacer" />
        )}
        <button className="theme-btn" onClick={onTheme} aria-label="Toggle light and dark theme">
          <CircleHalf size={14} weight="fill" /> Theme
        </button>
      </div>
    </header>
  )
}

export function Hero() {
  return (
    <section className="hero" id="top">
      <div className="wrap">
        <p className="eyebrow">When the team writes itself</p>
        <h1>
          AutoM2M: Checked Composition for <br className="br-lg" />
          Automatically Assembled LLM Agent Teams
        </h1>
        <p className="lede">
          Team builders describe every agent in prose, so nothing checks that the parts fit. AutoM2M makes the builder hand
          over a <em>typed team</em>, and lets a deterministic checker decide whether it may run.
        </p>
        <div className="authors">
          Author list forthcoming <span className="ph-tag">placeholder</span>
        </div>
        <div className="affils">
          <span>
            <sup>1</sup>AI4SE4AI Lab
          </span>
        </div>
        <p className="note">
          Builds on AgentM2M: M2M Transformations for Agentic Collaborations in Software Engineering.
        </p>
        <div className="btn-row">
          <a className="btn primary" href="#cite" aria-label="Paper, forthcoming">
            <Article size={18} /> Paper <span className="soon">soon</span>
          </a>
          <a className="btn" href={REPO} target="_blank" rel="noreferrer">
            <GithubLogo size={18} /> Code
          </a>
          <a className="btn" href="#explorer">
            <Graph size={18} /> Pipeline explorer
          </a>
          <a className="btn" href="#/services/status">
            <Plugs size={18} /> Use the service
          </a>
          <a className="btn" href="#cite">
            <Quotes size={18} /> BibTeX
          </a>
        </div>
        <div className="stats">
          <div className="stat">
            <div className="num">
              0/356<small>roles</small>
            </div>
            <p>auto-generated role prompts that name a teammate or say what they must hand over (Who&amp;When audit)</p>
          </div>
          <div className="stat">
            <div className="num">
              6<small>conditions</small>
            </div>
            <p>W1-W6, decided on the team blueprint alone: no task data, no LLM call, no execution</p>
          </div>
          <div className="stat">
            <div className="num">
              5<small>defects</small>
            </div>
            <p>composition defects a team builder can introduce, each ruled out by one condition</p>
          </div>
          <div className="stat">
            <div className="num pending">
              TBD<small>pass rate</small>
            </div>
            <p>
              end-to-end success against free-form builders <span className="ph-tag">results pending</span>
            </p>
          </div>
        </div>
      </div>
    </section>
  )
}

import Status from './Status'
import ApiKeys from './ApiKeys'
import Playground from './Playground'
import ApiDocs from './ApiDocs'
import Pricing from './Pricing'

import { SUBTABS, type Sub } from './subtabs'

const TITLES: Record<Sub, [string, string]> = {
  status: ['Service status', 'Is the AutoM2M service up? The web app, the REST API and the MCP endpoint run in one process.'],
  keys: ['API keys', 'One key gives your MCP client and scripts access to every AutoM2M and AgentM2M operation.'],
  playground: ['Checker playground', 'Run the W1–W6 admission checker on a typed team in your browser.'],
  docs: ['API documentation', 'REST endpoints mirror the MCP tools one to one.'],
  pricing: ['Pricing', 'Free during the research preview. Paid plans will add server-side LLM work.'],
}

export default function Services({ sub, go }: { sub: Sub; go: (s: Sub) => void }) {
  const [title, lede] = TITLES[sub]
  return (
    <section className="section svc" id="services">
      <div className="wrap">
        <p className="kicker">
          <b>Services</b>
        </p>
        <h1 className="svc-title">{title}</h1>
        <p className="lead">{lede}</p>
        <nav className="tabs mt" role="tablist" aria-label="Services">
          {SUBTABS.map(([id, label]) => (
            <a
              key={id}
              href={`#/services/${id}`}
              className="tab"
              role="tab"
              aria-selected={sub === id}
              onClick={(e) => {
                e.preventDefault()
                go(id)
              }}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="mt">
          {sub === 'status' && <Status />}
          {sub === 'keys' && <ApiKeys />}
          {sub === 'playground' && <Playground />}
          {sub === 'docs' && <ApiDocs />}
          {sub === 'pricing' && <Pricing onKeys={() => go('keys')} />}
        </div>
      </div>
    </section>
  )
}

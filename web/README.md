# AutoM2M project website

React + Vite + [React Flow](https://reactflow.dev) site for AutoM2M. Its visual language follows the
RRSI project page (regularized-rsi.com). The content comes from `docs/autom2m-explained-v1.tex` and
`docs/agenthot-autom2m.md`.

```bash
npm install
npm run dev       # http://localhost:5173
npm run build     # static site in dist/
```

- `src/sections/` holds the page sections: hero, problem, idea, checker, explorer, results, roadmap, cite.
- `src/flow/` holds the three React Flow diagrams: `RuntimeFlow` (the DevTeam on the AgentHOT engine),
  `W4Flow` (the anchored-coverage data-flow graphs) and `PipelineExplorer` (Algorithm 1, steps A to F).
- `src/data/scenarios.ts` holds the explorer walkthroughs. Edit it to change or add a scenario.
- Results are placeholders (`TBD`). Fill them in `src/sections/Results.tsx`, the hero stat card in
  `src/sections/Top.tsx`, and the `TBD` audit rows in `src/sections/Problem.tsx` once the numbers are final.

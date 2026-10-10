"""AutoM2M: checking LLM-assembled agent teams with model transformations.

The team level of the paper: Lifter (`lift`), Builder (`prompts`,
`examples`, `loop`), typed teams (`typed_team`), the admission Checker
W1-W6 (`checker`, `vlib`), Attribution (`attribution`) and checked Repair
(`repair`). Admitted teams are compiled and run by AgentHOT (`agenthot`).
LLMs propose; programs decide.
"""

__version__ = "0.4.0"

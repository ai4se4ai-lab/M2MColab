"""AgentHOT (Agent Hand-Off Transformations): the hand-off level of AutoM2M.

A compiler (`agenthot.compiler`, a synthesis higher-order transformation from
a typed team to metamodels and rule modules) and a runtime (`engine`, `team`,
`session`) that executes hybrid hand-offs: a deterministic engine fixes the
structure of every hand-off (matching, object creation, reference resolution,
trace links) and LLMs fill only the values no expression can compute, each
from a declared footprint and behind a validator (paper Sec. 3.2, Alg. 2).
"""

__version__ = "0.4.0"

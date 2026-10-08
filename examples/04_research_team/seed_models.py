"""Seed Literature model for examples/04_research_team: two papers, four
claims across three topics. One topic -- "latency" -- is deliberately
shared by two claims from two *different* papers, so the n:m hand-off in
rules/ExpLit2Report.agentm2m has a genuine many-to-many match to make (2
plans x 2 claims = 4 sections for that topic), not just a disguised 1:1
pairing.
"""
from __future__ import annotations

from agentm2m.metamodel import MetamodelBuilder


def build_seed_literature_model(lit_mm: MetamodelBuilder):
    root = lit_mm.get("LiteratureModel")()

    p1 = lit_mm.new("Paper", id="P1", title="Efficient Retrieval-Augmented Generation")
    p1.claims.append(lit_mm.new("Claim", id="C1", topic="hallucination",
                                 text="RAG reduces hallucination rates in QA tasks"))
    p1.claims.append(lit_mm.new("Claim", id="C2", topic="latency",
                                 text="Chunk size significantly affects retrieval latency"))

    p2 = lit_mm.new("Paper", id="P2", title="Scaling Multi-Agent Coordination")
    p2.claims.append(lit_mm.new("Claim", id="C3", topic="coordination",
                                 text="Structured hand-offs reduce coordination failures compared to free text"))
    p2.claims.append(lit_mm.new("Claim", id="C4", topic="latency",
                                 text="Batch inference caching improves latency under load"))

    root.papers.append(p1)
    root.papers.append(p2)
    return root

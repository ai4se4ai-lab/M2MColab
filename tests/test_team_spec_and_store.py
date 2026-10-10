"""team.yaml loading (agenthot.team.spec) and the JSON model store
(agenthot.store): the two pieces that let a team be declared as data and
persisted across processes."""
from __future__ import annotations

import pytest
import yaml

from agenthot.engine.executor import _TARGET_KEY_ATTR
from agenthot.llm.mock_backend import MockBackend
from agenthot.store import dump_models, load_models
from agenthot.team.spec import (
    SpecError,
    build_view_metamodel,
    index_elements,
    load_spec_file,
    order_views,
    resolve_refs,
    seed_root,
)
from agenthot.workspace import Workspace, list_templates


def _views(yaml_text: str) -> dict:
    return yaml.safe_load(yaml_text)["views"]


def _build_all(views: dict):
    mms = {}
    for v in order_views(views):
        mms[v] = build_view_metamodel("t", v, views[v], mms)
    return mms


def test_types_extends_and_cross_view_references():
    views = _views("""
views:
  B:
    classes:
      Item:
        attributes: [name, {count: int}, {done: boolean}, {tags: {type: string, many: true}}]
        references: {ref: A.Base}
    root: {slots: {items: Item}}
  A:
    classes:
      Base: {attributes: [id]}
      Special: {extends: Base, attributes: [extra]}
    root: {class: AModel, slots: {bases: Base}}
""")
    assert order_views(views) == ["A", "B"]  # dependency order, not file order
    mms = _build_all(views)
    item = mms["B"].get("Item")
    feats = {f.name: f for f in item.eAllStructuralFeatures()}
    assert feats["count"].eType.name == "EInt" and feats["done"].eType.name == "EBoolean"
    assert feats["tags"].many and feats["ref"].eType is mms["A"].get("Base")
    special = mms["A"].get("Special")
    assert {f.name for f in special.eAllStructuralFeatures()} == {"id", "extra"}
    assert mms["B"].root_name == "BModel"  # default root class name

    refs: list = []
    roots = {
        "A": seed_root(mms["A"], "A", {"bases": [{"id": "b1"}, {"type": "Special", "id": "s1", "extra": "x"}]}, refs),
        "B": seed_root(mms["B"], "B", {"items": [{"name": "i1", "count": "3", "done": "yes", "tags": ["p", "q"], "ref": "Special#s1"}]}, refs),
    }
    resolve_refs(refs, index_elements(roots))
    it = roots["B"].items[0]
    assert it.count == 3 and it.done is True and list(it.tags) == ["p", "q"]
    assert it.ref.eClass.name == "Special" and it.ref.extra == "x"


@pytest.mark.parametrize("snippet,err", [
    ("A: {classes: {X: {attributes: [id]}}}", "root needs 'slots'"),
    ("A: {classes: {}, root: {slots: {xs: X}}}", "at least one class"),
    ("A: {classes: {X: {attributes: [{id: float}]}}, root: {slots: {xs: X}}}", "unsupported attribute type"),
    ("A: {classes: {X: {references: {r: Nope}}}, root: {slots: {xs: X}}}", "unknown class"),
    ("A: {classes: {X: {references: {r: Z.Y}}}, root: {slots: {xs: X}}}", "unknown views"),
    ("A: {classes: {X: {attributes: [id]}}, root: {slots: {xs: Nope}}}", "unknown class"),
    ("A: {classes: {X: {extends: Q}}, root: {slots: {xs: X}}}", "extends"),
    ("A: {classes: {X: {references: {r: {many: true}}}}, root: {slots: {xs: X}}}", "needs a type"),
])
def test_spec_errors_are_explained(snippet, err):
    views = yaml.safe_load(snippet)
    with pytest.raises(SpecError, match=err):
        _build_all(views)


def test_cyclic_cross_view_references_rejected():
    views = _views("""
views:
  A: {classes: {X: {attributes: [id], references: {r: B.Y}}}, root: {slots: {xs: X}}}
  B: {classes: {Y: {attributes: [id], references: {r: A.X}}}, root: {slots: {ys: Y}}}
""")
    with pytest.raises(SpecError, match="cyclic"):
        order_views(views)


def test_seed_reference_errors():
    views = _views("""
views:
  A: {classes: {X: {attributes: [id], references: {r: X}}}, root: {slots: {xs: X}}}
""")
    mm = _build_all(views)["A"]
    refs: list = []
    root = seed_root(mm, "A", {"xs": [{"id": "1", "r": "X#404"}]}, refs)
    with pytest.raises(SpecError, match="no element with key"):
        resolve_refs(refs, index_elements({"A": root}))
    with pytest.raises(SpecError, match="no feature"):
        seed_root(mm, "A", {"xs": [{"id": "1", "bogus": 1}]}, [])


def test_load_spec_file_errors(tmp_path):
    p = tmp_path / "team.yaml"
    p.write_text("- just a list")
    with pytest.raises(SpecError, match="mapping"):
        load_spec_file(p)
    p.write_text("views: [1]")
    with pytest.raises(SpecError, match="views"):
        load_spec_file(p)
    p.write_text("name: x\nviews: {a: [}")
    with pytest.raises(SpecError, match="invalid YAML"):
        load_spec_file(p)


@pytest.mark.parametrize("template", list_templates())
def test_store_roundtrip_every_template(tmp_path, template):
    ws = Workspace(tmp_path, llm=MockBackend())
    ws.init(template)
    ws.run()
    loaded = ws._require()
    first = dump_models(loaded.team.roots)
    roots = load_models(first, loaded.team.views)
    assert dump_models(roots) == first  # lossless, incl. cross-view refs and target keys
    owned = [el for r in roots.values() for el in r.eAllContents() if getattr(el, _TARGET_KEY_ATTR, None)]
    assert owned, "engine-created elements must keep their target keys"


def test_workspace_spec_errors_surface(tmp_path):
    from agenthot.workspace import WorkspaceError

    ws = Workspace(tmp_path, llm=MockBackend())
    ws.init("devteam")
    spec = ws.spec_path.read_text()
    ws.spec_path.write_text(spec.replace("    owner: Architect\n", ""))
    with pytest.raises(WorkspaceError, match="needs an 'owner'"):
        ws.status()
    ws.spec_path.write_text(spec.replace("rules/Req2Arch.agenthot", "rules/Missing.agenthot"))
    with pytest.raises(WorkspaceError, match="not found"):
        ws.status()
    ws.spec_path.write_text(spec.replace("rules/Req2Arch.agenthot", "../../outside.agenthot"))
    with pytest.raises(WorkspaceError, match="escapes"):
        ws.status()
    ws.spec_path.write_text(spec)
    res = ws.validate()
    assert res["ok"] is True and {h["name"] for h in res["handoffs"]} == {"Req2Arch", "Arch2Code", "Req2Test"}
    # a rule creating a class the view doesn't declare is reported by validate
    rule = ws.dir / "rules/Arch2Code.agenthot"
    rule.write_text(rule.read_text().replace("Code!CodeEdit", "Code!Patch"))
    res = ws.validate()
    assert res["ok"] is False and "Arch2Code" in res["errors"][0]

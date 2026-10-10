"""Benchmark tasks (ClassEval, HumanEval+) in one shape, plus code helpers.

Only `prompt`, `methods` (signature, docstring, public doctest examples) and
`imports` are ever shown to a team. `hidden_test` is used exclusively by
`score()`; no condition, validator or builder can read it.

The task model and the public checks live in `autom2m.pywork` (the
library's Python-methods domain); this module adds the benchmark loaders,
ClassEval's assembly frame and hidden scoring.
"""
from __future__ import annotations

import ast
import json
import re
import textwrap
from functools import lru_cache

from autom2m.pywork import (  # noqa: F401  (re-exported for the evaluation code)
    MethodSpec,
    Task,
    _parses,
    _strip_self_imports,
    extract_code,
    extract_function,
    run_examples,
    run_tests,
    splice_class,
    stub_method,
)
from autom2m.pywork import assemble as _pw_assemble
from autom2m.pywork import examples_of as _examples_of
from autom2m.pywork import split_signature as _split_signature

from .sandbox import RESULT_MARK, run_python_json


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_classeval() -> list[Task]:
    from datasets import load_dataset

    ds = load_dataset("FudanSELab/ClassEval", split="test")
    tasks = []
    for r in ds:
        methods = []
        for mi in r["methods_info"]:
            sig, doc = _split_signature(mi["method_description"])
            methods.append(MethodSpec(mi["method_name"], sig, doc, _examples_of(doc)))
        tasks.append(
            Task(
                task_id=r["task_id"], bench="classeval", kind="class", entry=r["class_name"],
                prompt=r["skeleton"], imports="\n".join(r["import_statement"]),
                description=textwrap.dedent(r["class_description"]).strip().strip('"').strip(),
                methods=methods, constructor=r["class_constructor"], hidden_test=r["test"],
                canonical=r["solution_code"], test_classes=list(r["test_classes"]),
            )
        )
    return tasks


@lru_cache(maxsize=1)
def load_humanevalplus() -> list[Task]:
    from datasets import load_dataset

    ds = load_dataset("evalplus/humanevalplus", split="test")
    tasks = []
    for r in ds:
        prompt = r["prompt"]
        tree_sig = re.search(rf"(def\s+{re.escape(r['entry_point'])}\s*\(.*?\)\s*(->\s*[^:]+)?:)", prompt, re.S)
        sig = tree_sig.group(1) if tree_sig else f"def {r['entry_point']}(...):"
        doc_m = re.search(r'("""|\'\'\')(.*?)(\1)', prompt[prompt.find(sig):] if tree_sig else prompt, re.S)
        doc = textwrap.dedent(doc_m.group(2)).strip() if doc_m else ""
        imports = "\n".join(l for l in prompt.splitlines() if l.startswith(("import ", "from ")))
        tasks.append(
            Task(
                task_id=r["task_id"], bench="humanevalplus", kind="function", entry=r["entry_point"],
                prompt=prompt, imports=imports, description=doc,
                methods=[MethodSpec(r["entry_point"], sig, doc, _examples_of(doc))],
                hidden_test=r["test"], canonical=prompt + r["canonical_solution"],
            )
        )
    return tasks


def load(bench: str) -> list[Task]:
    return {"classeval": load_classeval, "humanevalplus": load_humanevalplus}[bench]()


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


@lru_cache(maxsize=256)
def _frame(task_id: str) -> tuple[str, tuple[str, ...]]:
    """Assembly frame of a ClassEval task: imports, class header, class-level
    constants and __init__ (the public constructor), plus each method's
    decorators. Taken from the reference solution because ~20% of the raw
    skeletons/constructors do not parse; no method body is ever copied."""
    task = next(t for t in load_classeval() if t.task_id == task_id)
    tree = ast.parse(task.canonical)
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == task.entry)
    body = []
    if cls.body and isinstance(cls.body[0], ast.Expr) and isinstance(getattr(cls.body[0], "value", None), ast.Constant):
        body.append(cls.body[0])
    decos = {}
    for n in cls.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            body.append(n)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name == "__init__":
                body.append(n)
            else:
                decos[n.name] = [ast.unparse(d) for d in n.decorator_list]
    cls.body = body or [ast.Pass()]
    cls.decorator_list = []
    frame = ast.Module(body=keep + [cls], type_ignores=[])
    order = tuple(f"{m.name}|{','.join(decos.get(m.name, []))}" for m in task.methods)
    return ast.unparse(frame), order


def assemble_class(task: Task, bodies: dict[str, str]) -> str:
    """Splice method sources into the class frame; missing -> stub."""
    frame_src, order = _frame(task.task_id)
    return splice_class(frame_src, [(item.split("|", 1)[0], [d for d in item.split("|", 1)[1].split(",") if d])
                                    for item in order], bodies)


def assemble_function(task: Task, src: str | None) -> str:
    body = src or stub_method(task.methods[0])
    return f"{task.imports}\n\n{body}\n"


def assemble(task: Task, bodies: dict[str, str]) -> str:
    if task.frame:  # a user task lifted by pywork.task_from_source
        return _pw_assemble(task, bodies)
    if task.kind == "class":
        return assemble_class(task, bodies)
    return assemble_function(task, bodies.get(task.entry))


# --------------------------------------------------------------------------
# hidden scoring (never visible to teams)
# --------------------------------------------------------------------------

_CE_SCORE = r'''
import json, unittest, io
SRC = {src!r}
TEST = {test!r}
NAMES = {names!r}
g = {{"__name__": "solution", "__file__": __file__}}
out = {{"load_error": None, "classes": {{}}}}
try:
    exec(compile(SRC + "\n\n" + TEST, "solution_and_tests.py", "exec"), g)
except BaseException as e:
    out["load_error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
else:
    for n in NAMES:
        c = g.get(n)
        if c is None: continue
        suite = unittest.TestLoader().loadTestsFromTestCase(c)
        r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        out["classes"][n] = [r.testsRun, max(0, r.testsRun - len(r.errors) - len(r.failures))]
print("{mark}" + json.dumps(out))
'''


def score(task: Task, code: str) -> dict:
    """Hidden-test score. ClassEval: per-test pass rate and class success
    (all test classes pass). HumanEval+: base+plus inputs pass."""
    if not code or not code.strip():
        return {"success": False, "test_pass_rate": 0.0, "error": "no code"}
    if task.bench == "classeval":
        script = _CE_SCORE.format(src=code, test=task.hidden_test, names=[c.strip() for c in task.test_classes], mark=RESULT_MARK)
        res, raw = run_python_json(script, timeout=60)
        if res is None or res.get("load_error"):
            return {"success": False, "test_pass_rate": 0.0,
                    "error": (res or {}).get("load_error") or raw.tail(300)}
        run = sum(v[0] for v in res["classes"].values())
        ok = sum(v[1] for v in res["classes"].values())
        total_expected = max(run, 1)
        methods_ok = sum(1 for v in res["classes"].values() if v[0] > 0 and v[0] == v[1])
        return {
            "success": run > 0 and ok == run and len(res["classes"]) == len(task.test_classes),
            "test_pass_rate": ok / total_expected,
            "tests_run": run,
            "tests_passed": ok,
            "test_classes_passed": methods_ok,
            "test_classes": len(task.test_classes),
        }
    # HumanEval+
    script = f"{code}\n\n{task.hidden_test}\n\ncheck({task.entry})\nprint({RESULT_MARK!r} + '{{}}')\n"
    res, raw = run_python_json(script, timeout=120)
    ok = res is not None
    return {"success": ok, "test_pass_rate": 1.0 if ok else 0.0, "error": None if ok else raw.tail(300)}


def dumps_task_view(task: Task) -> str:
    return json.dumps({"id": task.task_id, "entry": task.entry, "methods": [m.name for m in task.methods]})

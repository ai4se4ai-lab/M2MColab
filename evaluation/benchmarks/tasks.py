"""Benchmark tasks (ClassEval, HumanEval+) in one shape, plus code helpers.

Only `prompt`, `methods` (signature, docstring, public doctest examples) and
`imports` are ever shown to a team. `hidden_test` is used exclusively by
`score()`; no condition, validator or builder can read it.
"""
from __future__ import annotations

import ast
import doctest
import json
import re
import textwrap
from dataclasses import dataclass, field
from functools import lru_cache

from .sandbox import RESULT_MARK, run_python, run_python_json


@dataclass
class MethodSpec:
    name: str
    signature: str  # "def add(self, a, b):"
    docstring: str
    examples: str  # doctest source lines (">>> ..." with expected output)


@dataclass
class Task:
    task_id: str
    bench: str  # "classeval" | "humanevalplus"
    kind: str  # "class" | "function"
    entry: str  # class name or function name
    prompt: str  # skeleton (ClassEval) or prompt (HumanEval+): what the team sees
    imports: str
    description: str
    methods: list[MethodSpec] = field(default_factory=list)
    constructor: str = ""
    hidden_test: str = field(default="", repr=False)
    canonical: str = field(default="", repr=False)
    test_classes: list[str] = field(default_factory=list, repr=False)

    def public_examples(self) -> str:
        return "\n".join(m.examples for m in self.methods if m.examples)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _examples_of(docstring: str) -> str:
    parser = doctest.DocTestParser()
    try:
        exs = parser.get_examples(docstring)
    except ValueError:
        return ""
    out = []
    for e in exs:
        src = e.source.rstrip("\n").replace("\n", "\n... ")
        out.append(f">>> {src}" + (f"\n{e.want.rstrip()}" if e.want.strip() else ""))
    return "\n".join(out)


def _split_signature(method_description: str) -> tuple[str, str]:
    m = re.search(r"((?:^\s*@[\w.]+\s*\n)*)\s*(def\s+\w+\s*\(.*?\)\s*(->\s*[^:]+)?:)", method_description, re.S | re.M)
    if m:
        decos = "\n".join(l.strip() for l in m.group(1).splitlines() if l.strip())
        sig = (decos + "\n" if decos else "") + m.group(2).strip()
    else:
        sig = method_description.splitlines()[0].strip()
    doc_m = re.search(r'"""(.*?)"""', method_description, re.S)
    return sig, textwrap.dedent(doc_m.group(1)).strip() if doc_m else ""


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
# code extraction and assembly
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:python|py|Python)?\s*\n(.*?)```", re.S)


def extract_code(text: str) -> str:
    """Last fenced Python block, or the text itself if it parses."""
    blocks = _FENCE.findall(text or "")
    if blocks:
        # prefer the longest of the blocks that parse
        parsing = [b for b in blocks if _parses(b)]
        return max(parsing or blocks, key=len).strip("\n")
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
    return t


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


def extract_function(text: str, name: str) -> str | None:
    """Return source of `def name(...)` found in `text` (dedented), or None.
    Module-level imports of the answer are moved into the function body, so
    the method stays self-contained when spliced into the class."""
    code = extract_code(text)
    for candidate in (code, textwrap.dedent(code)):
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            continue
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                if imports:
                    has_doc = (node.body and isinstance(node.body[0], ast.Expr)
                               and isinstance(getattr(node.body[0], "value", None), ast.Constant)
                               and isinstance(node.body[0].value.value, str))
                    at = 1 if has_doc else 0
                    node.body[at:at] = imports
                return ast.unparse(node)
    return None


def stub_method(m: MethodSpec) -> str:
    return f"{m.signature}\n    raise NotImplementedError('{m.name} pending')"


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
    tree = ast.parse(frame_src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    for item in order:
        name, decos = item.split("|", 1)
        src = bodies.get(name)
        m = next((x for x in task.methods if x.name == name), None)
        if src is None:
            src = f"def {name}(*args, **kwargs):\n    raise NotImplementedError('{name} pending')"
        try:
            fn = ast.parse(textwrap.dedent(src)).body[0]
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise SyntaxError("not a function")
        except SyntaxError:
            fn = ast.parse(f"def {name}(*a, **k):\n    raise SyntaxError('invalid generated code')").body[0]
        have = {ast.unparse(d) for d in fn.decorator_list}
        for d in [x for x in decos.split(",") if x]:
            if d not in have:
                fn.decorator_list.append(ast.parse(d, mode="eval").body)
        _ = m
        cls.body.append(fn)
    return ast.unparse(tree)


def assemble_function(task: Task, src: str | None) -> str:
    body = src or stub_method(task.methods[0])
    return f"{task.imports}\n\n{body}\n"


def assemble(task: Task, bodies: dict[str, str]) -> str:
    if task.kind == "class":
        return assemble_class(task, bodies)
    return assemble_function(task, bodies.get(task.entry))


# --------------------------------------------------------------------------
# public checks (doctest examples) -- visible to every condition alike
# --------------------------------------------------------------------------

# Executed only inside the rlimited sandbox subprocess (sandbox.run_python):
# eval/exec here is how doctest examples run, by design.
_EX_RUNNER = r'''
import doctest, json, sys, io, contextlib
SRC = {src!r}
EXAMPLES = {examples!r}
g = {{"__name__": "__main__", "__file__": __file__}}
res = {{"ok": True, "n": 0, "failed": [], "pending": 0, "error": None}}
try:
    exec(compile(SRC, "solution.py", "exec"), g)
except BaseException as e:
    res.update(ok=False, error="module failed to load: %s: %s" % (type(e).__name__, e))
else:
    parser = doctest.DocTestParser(); checker = doctest.OutputChecker()
    for name, text in EXAMPLES:
        try:
            exs = parser.get_examples(text)
        except ValueError:
            continue
        loc = dict(g)
        for ex in exs:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    try:
                        val = eval(compile(ex.source, "<ex>", "eval"), loc)
                        if val is not None: print(repr(val))
                    except SyntaxError:
                        exec(compile(ex.source, "<ex>", "exec"), loc)
                got = buf.getvalue()
            except NotImplementedError as e:
                res["pending"] += 1; break
            except BaseException as e:
                got = "EXC %s: %s\n" % (type(e).__name__, e)
            if ex.want.strip():
                res["n"] += 1
                if not checker.check_output(ex.want, got, doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE):
                    res["ok"] = False
                    res["failed"].append({{"method": name, "example": ex.source.strip()[:200],
                                          "expected": ex.want.strip()[:200], "got": got.strip()[:200]}})
print("{mark}" + json.dumps(res))
'''


def run_examples(task: Task, code: str, only: list[str] | None = None, timeout: float = 15.0) -> dict:
    exs = [(m.name, m.docstring) for m in task.methods if only is None or m.name in only]
    script = _EX_RUNNER.format(src=code, examples=exs, mark=RESULT_MARK)
    res, raw = run_python_json(script, timeout=timeout)
    if res is None:
        return {"ok": False, "n": 0, "failed": [], "pending": 0, "error": raw.tail(400) or "crashed"}
    return res


_TEST_RUNNER = r'''
import json, unittest, sys
SRC = {src!r}
TESTS = {tests!r}
g = {{"__name__": "solution", "__file__": __file__}}
res = {{"ok": False, "n": 0, "passed": 0, "pending": 0, "errors": [], "error": None}}
try:
    exec(compile(SRC, "solution.py", "exec"), g)
    t = dict(g); exec(compile(TESTS, "tests.py", "exec"), t)
except BaseException as e:
    res["error"] = "load failed: %s: %s" % (type(e).__name__, e)
else:
    import inspect
    loader = unittest.TestLoader(); suite = unittest.TestSuite()
    funcs = []
    for k, v in list(t.items()):
        if inspect.isclass(v) and issubclass(v, unittest.TestCase) and v is not unittest.TestCase:
            suite.addTests(loader.loadTestsFromTestCase(v))
        elif callable(v) and k.startswith("test") and inspect.isfunction(v):
            funcs.append(v)
    class R(unittest.TextTestResult): pass
    import io
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    n = r.testsRun; bad = []
    pend = 0
    for case, tb in r.errors + r.failures:
        if "NotImplementedError" in tb and "pending" in tb:
            pend += 1
        else:
            bad.append(tb.strip().splitlines()[-1][:240])
    for f in funcs:
        n += 1
        try:
            f()
        except NotImplementedError as e:
            if "pending" in str(e): pend += 1
            else: bad.append("NotImplementedError")
        except BaseException as e:
            bad.append("%s: %s" % (type(e).__name__, str(e)[:200]))
    res.update(n=n, pending=pend, passed=n - len(bad) - pend, errors=bad[:8], ok=(not bad and n - pend > 0))
print("{mark}" + json.dumps(res))
'''


def _strip_self_imports(code: str, tests: str) -> str:
    """Drop test lines that import names the solution already defines
    (`from solution import Calculator`): the code under test is in scope."""
    try:
        defined = {n.name for n in ast.parse(code).body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    except SyntaxError:
        return tests
    out = []
    for line in tests.splitlines():
        m = re.match(r"\s*from\s+[\w.]+\s+import\s+(.+)$", line)
        if m and {x.strip().split(" as ")[0] for x in m.group(1).strip("() ").split(",")} & defined:
            continue
        out.append(line)
    return "\n".join(out)


def run_tests(code: str, tests: str, timeout: float = 30.0) -> dict:
    """Run unittest classes and/or bare test_* functions in `tests` against `code`."""
    tests = _strip_self_imports(code, tests)
    script = _TEST_RUNNER.format(src=code, tests=tests, mark=RESULT_MARK)
    res, raw = run_python_json(script, timeout=timeout)
    if res is None:
        return {"ok": False, "n": 0, "passed": 0, "pending": 0, "errors": [], "error": raw.tail(400) or "crashed"}
    return res


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

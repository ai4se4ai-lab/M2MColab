"""Python-methods domain: the task model, code helpers and the Workbench that
AutoM2M validators execute against.

A task is a Python class (each method to implement is one goal `Method`) or
a single function. `task_from_source` lifts a user's skeleton into a task;
the evaluation harness builds the same `Task` from ClassEval / HumanEval+.
Only `prompt`, `methods` (signature, docstring, public doctest examples) and
`imports` are ever shown to a team; `hidden_test` is for scoring only.
"""
from __future__ import annotations

import ast
import doctest
import re
import textwrap
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .sandbox import RESULT_MARK, run_python_json


@dataclass
class MethodSpec:
    name: str
    signature: str  # "def add(self, a, b):"
    docstring: str
    examples: str  # doctest source lines (">>> ..." with expected output)


@dataclass
class Task:
    task_id: str
    bench: str  # "classeval" | "humanevalplus" | "user"
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
    # assembly frame for user tasks: the skeleton without the methods to
    # implement, and each such method's decorators (benchmark tasks derive
    # theirs from the reference solution instead)
    frame: str = field(default="", repr=False)
    decorators: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def public_examples(self) -> str:
        return "\n".join(m.examples for m in self.methods if m.examples)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        d = dict(d)
        d["methods"] = [MethodSpec(**m) for m in d.get("methods", [])]
        return cls(**d)


# --------------------------------------------------------------------------
# docstrings
# --------------------------------------------------------------------------


def examples_of(docstring: str) -> str:
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


def split_signature(method_description: str) -> tuple[str, str]:
    m = re.search(r"((?:^\s*@[\w.]+\s*\n)*)\s*(def\s+\w+\s*\(.*?\)\s*(->\s*[^:]+)?:)", method_description, re.DOTALL | re.MULTILINE)
    if m:
        decos = "\n".join(l.strip() for l in m.group(1).splitlines() if l.strip())
        sig = (decos + "\n" if decos else "") + m.group(2).strip()
    else:
        sig = method_description.splitlines()[0].strip()
    doc_m = re.search(r'"""(.*?)"""', method_description, re.DOTALL)
    return sig, textwrap.dedent(doc_m.group(1)).strip() if doc_m else ""


# --------------------------------------------------------------------------
# lifting a user's skeleton
# --------------------------------------------------------------------------


class TaskSourceError(ValueError):
    pass


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A method to implement: its body (after the docstring) is empty, `pass`,
    `...`, or `raise NotImplementedError`."""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    s = body[0]
    if isinstance(s, ast.Pass):
        return True
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
        return True
    if isinstance(s, ast.Raise) and s.exc is not None:
        exc = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    import copy

    head = copy.copy(fn)
    head.body = [ast.Pass()]
    return ast.unparse(head).rsplit("\n", 1)[0].strip()


def _spec(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> MethodSpec:
    doc = ast.get_docstring(fn) or ""
    return MethodSpec(fn.name, _signature(fn), doc, examples_of(doc))


def task_from_source(source: str, *, task_id: str = "user", description: str = "") -> Task:
    """Lift a Python skeleton into a task.

    A class: every stub method (body is a docstring plus nothing, `pass`,
    `...` or `raise NotImplementedError`) becomes a goal `Method`; already
    implemented methods (typically `__init__`) stay in the assembly frame.
    Otherwise the first stub top-level function is the task."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise TaskSourceError(f"the task source does not parse: {exc}") from None
    imports = "\n".join(ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if cls is not None:
        todo = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_stub(n)]
        if not todo:
            raise TaskSourceError(f"class {cls.name} has no method to implement (stub methods have a docstring "
                                  "and `pass`, `...` or `raise NotImplementedError`)")
        names = {n.name for n in todo}
        decorators = {n.name: [ast.unparse(d) for d in n.decorator_list] for n in todo}
        cls.body = [n for n in cls.body if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                 and n.name in names)] or [ast.Pass()]
        return Task(
            task_id=task_id, bench="user", kind="class", entry=cls.name, prompt=source.strip("\n") + "\n",
            imports=imports, description=description or (ast.get_docstring(cls) or "").strip(),
            methods=[_spec(n) for n in todo], frame=ast.unparse(tree), decorators=decorators,
        )
    fns = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    fn = next((n for n in fns if _is_stub(n)), None)
    if fn is None:
        raise TaskSourceError("no class and no stub function found in the task source")
    spec = _spec(fn)
    tree.body = [n for n in tree.body if n is not fn]
    return Task(
        task_id=task_id, bench="user", kind="function", entry=fn.name, prompt=source.strip("\n") + "\n",
        imports=imports, description=description or spec.docstring, methods=[spec], frame=ast.unparse(tree),
    )


# --------------------------------------------------------------------------
# code extraction and assembly
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:python|py|Python)?\s*\n(.*?)```", re.DOTALL)


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


def _reindent_docstring(fn: ast.FunctionDef | ast.AsyncFunctionDef, indent: int) -> None:
    """A method written at column 0 keeps its docstring's continuation lines at
    that depth; re-indent them for where the function is spliced."""
    if not (fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        return
    import inspect

    doc = inspect.cleandoc(fn.body[0].value.value)
    if "\n" in doc:
        pad = " " * indent
        doc = "\n".join((pad + line) if (i and line) else line for i, line in enumerate(doc.split("\n"))) + "\n" + pad
    fn.body[0].value.value = doc


def splice_class(frame_src: str, order: list[tuple[str, list[str]]], bodies: dict[str, str]) -> str:
    """Splice method sources into a class frame, in `order` (name, decorators);
    a missing method becomes a pending stub."""
    tree = ast.parse(frame_src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    for name, decos in order:
        src = bodies.get(name)
        if src is None:
            src = f"def {name}(*args, **kwargs):\n    raise NotImplementedError('{name} pending')"
        try:
            fn = ast.parse(textwrap.dedent(src)).body[0]
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise SyntaxError("not a function")
        except SyntaxError:
            fn = ast.parse(f"def {name}(*a, **k):\n    raise SyntaxError('invalid generated code')").body[0]
        _reindent_docstring(fn, 8)
        have = {ast.unparse(d) for d in fn.decorator_list}
        for d in decos:
            if d and d not in have:
                fn.decorator_list.append(ast.parse(d, mode="eval").body)
        if len(cls.body) == 1 and isinstance(cls.body[0], ast.Pass):
            cls.body = []
        cls.body.append(fn)
    return ast.unparse(tree)


def assemble_function(task: Task, src: str | None) -> str:
    body = src or stub_method(task.methods[0])
    try:
        tree = ast.parse(textwrap.dedent(body))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _reindent_docstring(node, 4)
        body = ast.unparse(tree)
    except SyntaxError:
        pass
    return f"{task.frame or task.imports}\n\n{body}\n"


def assemble(task: Task, bodies: dict[str, str]) -> str:
    """Assemble a user task (one with a frame) into runnable source."""
    if task.kind == "class":
        if not task.frame:
            raise ValueError(f"task {task.task_id} has no assembly frame")
        return splice_class(task.frame, [(m.name, task.decorators.get(m.name, [])) for m in task.methods], bodies)
    return assemble_function(task, bodies.get(task.entry))


# --------------------------------------------------------------------------
# public checks (doctest examples, given tests)
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
                        # statements, e.g. `s.push(2); s.pop()`: 'single' echoes expression
                        # values through sys.displayhook, exactly as doctest does
                        exec(compile(ex.source, "<ex>", "single"), loc)
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
# the Workbench
# --------------------------------------------------------------------------


class PyWorkbench:
    """`vlib.Workbench` for Python tasks. `assemble` defaults to the task's own
    frame; the evaluation harness passes the benchmark's assembler."""

    def __init__(self, task: Task, bodies: Callable[[], dict[str, str]] | None = None,
                 assembler: Callable[[Task, dict[str, str]], str] | None = None) -> None:
        self.task = task
        self._bodies = bodies or (dict)
        self._assemble = assembler or assemble

    def bind(self, bodies: Callable[[], dict[str, str]]) -> None:
        self._bodies = bodies

    def method_names(self) -> list[str]:
        return [m.name for m in self.task.methods]

    def current_bodies(self) -> dict[str, str]:
        return self._bodies()

    def assemble(self, bodies: dict[str, str]) -> str:
        return self._assemble(self.task, bodies)

    def run_examples(self, code: str, only: list[str]) -> dict:
        return run_examples(self.task, code, only)

    def run_tests(self, code: str, tests: str) -> dict:
        return run_tests(code, tests)

    def extract_function(self, text: str, name: str) -> str | None:
        return extract_function(text, name)

    def extract_code(self, text: str) -> str:
        return extract_code(text)


def task_text(task: Task) -> str:
    """What the builder sees of a task (the same public material as the Lift)."""
    return task.prompt

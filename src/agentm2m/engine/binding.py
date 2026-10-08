"""Structural and stochastic binding evaluation (Algorithm 1, lines 5-10).

Structural bindings are ordinary OCL-subset expressions; when a structural
expression evaluates to a *source* model element (or a list of them), it is
resolved to the corresponding *target* element through the hand-off's trace
-- exactly ATL's implicit target-resolution for reference bindings
(`component <- s.epic` yields the Component generated from s.epic).

Stochastic bindings sample v ~ D(prompt (+) footprint) and only commit the
value once its `@check` validator accepts it; the resample loop is bounded
by `k` and never loops past it (Proposition 3): on exhaustion the binding
is escalated, not retried forever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyecore.ecore import EcoreUtils

from ..llm.base import LLMBackend, LLMError, PendingSample
from ..rules.ast import StochasticBinding, StructuralBinding, TargetPattern
from .expr import Helpers, eval_expr
from .lift import LIFT_BINDING_NAME, LiftRejected, lift_json_into_element
from .matcher import Match
from .trace import TraceLink, TraceModel, digest, element_key
from .validators import Rejected

_FEEDBACK_SNIPPET_CHARS = 400


def _retry_prompt(prompt: str, rejected: str, reason: str) -> str:
    """Prompt for a resample after a *content* rejection. Re-sending the
    identical prompt at low temperature mostly reproduces the identical
    rejected answer (observed: devstral returning the same class-qualified
    signature twice), wasting the resample budget; telling the model what was
    rejected is what makes the next sample differ. It adds only the model's
    own previous output -- never source data outside the footprint."""
    snippet = (rejected or "").strip()
    if len(snippet) > _FEEDBACK_SNIPPET_CHARS:
        snippet = snippet[:_FEEDBACK_SNIPPET_CHARS] + " ..."
    return (
        f"{prompt}\n\nYour previous answer was rejected ({reason}):\n{snippet or '(empty answer)'}\n"
        "Answer again, following the requested output format exactly."
    )


@dataclass
class Escalation:
    target_key: str
    binding: str
    rule: str
    reason: str


def _find_feature(target_obj: Any, name: str):
    for f in target_obj.eClass.eAllStructuralFeatures():
        if f.name == name:
            return f
    return None


def resolve_structural_value(value: Any, expected_type: Any, trace: TraceModel, target_registry: dict[str, Any]) -> Any:
    """ATL's implicit target resolution: a structural binding value that is
    itself a *source* model element is translated to the corresponding
    *target* element via the trace -- but only when it doesn't already
    conform to the target feature's declared type (e.g. `component <-
    s.epic` needs resolving; `operation <- op` in a hand-off that targets
    Arch!Operation directly by reference does not: op already fits)."""
    if isinstance(value, list):
        return [resolve_structural_value(v, expected_type, trace, target_registry) for v in value]
    if hasattr(value, "eClass"):
        if expected_type is not None and EcoreUtils.isinstance(value, expected_type):
            return value
        try:
            key = element_key(value)
        except Exception:
            return value
        link = trace.target_for_source(key)
        if link is not None and link.target_key in target_registry:
            return target_registry[link.target_key]
        return value
    return value


def apply_structural_bindings(
    target_pattern: TargetPattern,
    target_obj: Any,
    match: Match,
    trace: TraceModel,
    target_registry: dict[str, Any],
    helpers: Helpers,
) -> None:
    for b in target_pattern.bindings:
        if isinstance(b, StructuralBinding):
            raw = eval_expr(b.expr, match.bindings, helpers)
            feature = _find_feature(target_obj, b.name)
            expected_type = getattr(feature, "eType", None) if feature is not None else None
            setattr(target_obj, b.name, resolve_structural_value(raw, expected_type, trace, target_registry))


def _footprint_to_text(value: Any) -> str:
    if hasattr(value, "eClass"):
        feats = {f.name: getattr(value, f.name) for f in value.eClass.eAllStructuralFeatures()}
        return f"{value.eClass.name}({feats})"
    if isinstance(value, (str, bytes)):
        return str(value)
    if isinstance(value, (list, tuple)) or hasattr(value, "__iter__"):
        # Covers plain lists/tuples *and* pyecore's own collection types for
        # many-valued references (e.g. EOrderedSet from `s.criteria`, many=True)
        # -- these are not `list` instances, so an `isinstance(value, list)`-only
        # check silently falls through to `str(value)` below and renders as an
        # opaque "EOrderedSet([<pyecore.ecore.Criterion object at 0x...>])"
        # instead of the element's actual content, starving every stochastic
        # binding whose footprint is a many-valued reference of real
        # information (this footprint-rendering path is the *only* thing the
        # LLM ever sees of the source model -- Sec III-C's footprint-bounded
        # prompting -- so this silently defeated it for every such binding).
        return "\n".join(f"- {_footprint_to_text(v)}" for v in value)
    return str(value)


def accept_sample(
    binding: StochasticBinding,
    target_var: str,
    target_obj: Any,
    match: Match,
    trace_link: TraceLink,
    raw: str,
    helpers: Helpers,
    *,
    footprint: Any,
    fp_digest: str,
) -> tuple[bool, str]:
    """Judge one sampled value `raw` for `binding` and commit it if accepted.

    Shared by the in-engine resample loop (`apply_stochastic_binding`) and by
    host mode (`TeamRuntime.submit_binding`), so a value typed by Claude Code
    passes exactly the same @check / Lift conformance / stamping path as one
    sampled from a backend. Returns (accepted, rejection_reason).

    Algorithm 1, line 10: "if accepted: t.f_b <- v" -- the target is only
    written once a sample is accepted. Lift already satisfies this
    (lift_json_into_element raises *before* writing any EAttribute if the
    JSON is rejected); for an ordinary binding `raw` is not committed to
    target_obj until any @check has passed. @check itself never needs the
    premature write: check_scope supplies the sampled value under
    `binding.name` directly.
    """
    reason = ""
    ok: bool
    if binding.name == LIFT_BINDING_NAME:
        try:
            lift_json_into_element(raw, target_obj)
        except LiftRejected as exc:
            ok, reason = False, str(exc)
        else:
            ok = True
    else:
        ok = True

    if ok and binding.check_expr is not None:
        check_scope = {**match.bindings, target_var: target_obj, binding.name: raw}
        try:
            verdict = eval_expr(binding.check_expr, check_scope, helpers)
            ok = bool(verdict)
            if not ok:
                reason = (
                    verdict.reason
                    if isinstance(verdict, Rejected)
                    else "it failed the validator: wrong format, or not what was asked"
                )
        except Exception as exc:  # noqa: BLE001 - a failing @check is a rejection, not a crash
            ok, reason = False, f"@check raised: {exc}"

    if ok:
        if binding.name != LIFT_BINDING_NAME:
            setattr(target_obj, binding.name, raw)
        trace_link.stamps[binding.name] = fp_digest
        trace_link.footprints[binding.name] = footprint
        trace_link.failed_stamps.pop(binding.name, None)
        trace_link.attempts.pop(binding.name, None)
        trace_link.rejections.pop(binding.name, None)
    return ok, reason


def build_prompt(binding: StochasticBinding, match: Match, helpers: Helpers, footprint: Any) -> str:
    """pi = prompt_b (+) den(e_b)_m: the only text a sampler ever sees."""
    prompt_head = eval_expr(binding.prompt_expr, match.bindings, helpers)
    return f"{prompt_head}\n\nContext (footprint only):\n{_footprint_to_text(footprint)}"


def _footprint_blocked(footprint: Any, incomplete: set[str]) -> bool:
    """Host mode: is this footprint still waiting on an upstream binding?

    In backend mode hand-offs run synchronously in registration order, so an
    upstream value is always sampled before a downstream footprint reads it.
    In host mode sampling is deferred to Claude Code, so a downstream
    footprint can reach an upstream element whose stochastic value does not
    exist yet (e.g. CodeEdit.body reading a not-yet-filled
    Operation.signature). Offering such a binding would ask for a value from
    an empty footprint, so it is reported as *blocked* instead.
    """
    if footprint is None or footprint == "":
        return True
    if hasattr(footprint, "eClass"):
        return getattr(footprint, "_amt_target_key", None) in incomplete
    if isinstance(footprint, (str, bytes, int, float, bool)):
        return False
    if hasattr(footprint, "__iter__"):
        return any(_footprint_blocked(v, incomplete) for v in footprint)
    return False


def apply_stochastic_binding(
    binding: StochasticBinding,
    target_var: str,
    target_obj: Any,
    match: Match,
    trace_link: TraceLink,
    llm: LLMBackend,
    helpers: Helpers,
    *,
    max_resamples: int,
    temperature: float,
) -> tuple[bool, Escalation | None]:
    """Returns (value_changed_this_run, escalation_or_None).

    With a deferred backend (`llm.deferred`, i.e. host mode) no sample is
    drawn here: a stale binding raises `PendingSample` carrying its prompt,
    and the value arrives later through `TeamRuntime.submit_binding`.
    """
    footprint = eval_expr(binding.footprint_expr, match.bindings, helpers)
    fp_digest = digest(footprint)
    if trace_link.stamps.get(binding.name) == fp_digest:
        return (False, None)  # footprint unchanged since acceptance -> no re-invocation
    if trace_link.failed_stamps.get(binding.name) == fp_digest:
        # Already escalated on this exact footprint: re-sampling the same
        # prompt would just burn another k samples for the same outcome, so
        # re-report the (still open) escalation without invoking the LLM.
        # A footprint change clears this and earns a fresh budget.
        prev = trace_link.rejections.get(binding.name) or {}
        prev_reason = prev.get("reason") if prev.get("digest") == fp_digest else None
        return (
            False,
            Escalation(
                target_key=trace_link.target_key,
                binding=binding.name,
                rule=trace_link.rule,
                reason=prev_reason or "escalated earlier on an unchanged footprint; not re-sampled",
            ),
        )

    if getattr(llm, "deferred", False):
        incomplete = getattr(llm, "incomplete", set())
        blocked = _footprint_blocked(footprint, incomplete)
        prompt = "" if blocked else build_prompt(binding, match, helpers, footprint)
        attempts = 0
        rejection = trace_link.rejections.get(binding.name)
        if rejection and rejection.get("digest") == fp_digest:
            attempts = trace_link.attempts.get(binding.name, 0)
            if not blocked:
                prompt = _retry_prompt(prompt, rejection.get("value", ""), rejection.get("reason", ""))
        raise PendingSample(
            prompt=prompt,
            fp_digest=fp_digest,
            attempts=attempts,
            blocked=blocked,
            stale=binding.name in trace_link.stamps,
        )

    prompt = build_prompt(binding, match, helpers, footprint)

    last_reason = "no attempts made"
    any_sample_rejected = False
    attempt_prompt = prompt
    for _attempt in range(max_resamples):
        try:
            raw = llm.generate(attempt_prompt, temperature=temperature)
        except LLMError as exc:
            last_reason = str(exc)
            continue

        ok, reason = accept_sample(
            binding, target_var, target_obj, match, trace_link, raw, helpers,
            footprint=footprint, fp_digest=fp_digest,
        )
        if ok:
            return (True, None)
        last_reason = reason
        any_sample_rejected = True
        attempt_prompt = _retry_prompt(prompt, raw, last_reason)

    # Only a *content* rejection is cached: if every attempt was a transport
    # failure (LLMError: timeout, connection), the footprint was never
    # actually judged, so the next pass should still try it.
    if any_sample_rejected:
        trace_link.failed_stamps[binding.name] = fp_digest
        trace_link.rejections[binding.name] = {"value": (raw or "")[:400], "reason": last_reason, "digest": fp_digest}
    return (
        False,
        Escalation(target_key=trace_link.target_key, binding=binding.name, rule=trace_link.rule, reason=last_reason),
    )

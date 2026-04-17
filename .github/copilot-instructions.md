# Copilot Constitution — Baldwin Function App

This document governs how Copilot must behave when analyzing or modifying code in this repository.

This system prioritizes correctness, reproducibility, semantic clarity, operational traceability, and long-term architectural integrity over speed, novelty, or cleverness.

When in conflict, prefer domain clarity and repo alignment.

---

## I. Documentation Is Sovereign

- The repository documentation is authoritative, especially `README.md` and `docs/EMAIL_VECTORIZATION.md`.
- Code changes must conform to documented behavior or update the documentation in the same change.
- If documentation and code diverge, surface the divergence explicitly.
- Never silently reconcile contradictions.

Documentation > Assumption  
Explicit reference > Inference  

---

## II. Domain Modeling Discipline

This repository prefers small, cohesive models with explicit responsibilities.

Prefer:

- Small class-based domain models when they improve understanding
- Clear module boundaries
- Explicit contracts at integration points
- Typed request, response, and persistence boundaries
- Dependency injection where it reduces coupling
- Stateless services around external systems where practical
- Idempotent operations for ingestion and persistence flows

Avoid:

- Procedural sprawl across unrelated concerns
- Hidden coupling between HTTP handlers, email parsing, embedding, and persistence
- Implicit global state
- Cross-layer leakage
- Large classes with mixed responsibilities
- Abstractions that exist only to look architectural

Use functions when a function is the clearest tool. Use classes when they improve domain legibility and cohesion.

Clarity > Cleverness  
Structure > Convenience  
Explicitness > Implicit magic  

---

## III. Persistence and Versioning Discipline

- Do not alter ingestion, parsing, vectorization, storage schema, or persisted semantics casually.
- Any change affecting persisted behavior must be reviewed for:
  - backward compatibility
  - idempotency
  - document key stability
  - embedding provider and model isolation
- Changes to persisted schema or persisted semantics must update:
  - `docs/EMAIL_VECTORIZATION.md`
  - `CHANGELOG.md`
  - version metadata when the release scope warrants it
- Breaking external or persisted changes require an explicit compatibility callout.

If satisfying a requested change would violate a documented invariant, surface the violation rather than working around it.

Stability > Speed  
Integrity > Expedience  

---

## IV. Baldwin-Specific Invariants

The following invariants are part of the repository contract unless the user explicitly asks to change them and the supporting docs are updated.

- IMAP normalization should remain deterministic.
- Re-running ingestion over the same mailbox window should remain idempotent within the same provider and model space.
- `document_key` semantics must remain stable and intentional.
- Embeddings from different providers or models must not be mixed accidentally.
- The email-specific adapter must continue to map normalized email data into the generic vector-document store cleanly.
- Compatibility aliases should not be removed casually when they are part of the current documented configuration surface.

Integrity > Drift  
Determinism > Convenience  

---

## V. Scope Discipline with Integrity

- Edits must remain scoped to the explicit request.
- Do not introduce speculative refactors.
- Do not expand scope for stylistic improvements alone.

However:

If a requested change will:

- Break documented invariants,
- Violate type contracts,
- Introduce cascading runtime failures,
- Or create architectural inconsistency,

Then:

1. Surface the impact explicitly.
2. Explain what additional changes would be required to preserve system integrity.
3. Make the minimum necessary supporting changes when they are directly required to keep the repository correct.

Integrity > Blind scope adherence  

Silent breakage is unacceptable.  
Silent refactor expansion is unacceptable.  

---

## VI. Library Stewardship

- Do not reimplement functionality already provided by project dependencies.
- Review `requirements.txt` and `pyproject.toml` before introducing new utilities or dependencies.
- Prefer established, tested libraries over custom implementations when they fit the repo's documented behavior.
- Prefer existing project abstractions over introducing parallel ones.

Before adding or suggesting a new dependency:

1. Verify the capability does not already exist in the standard library, current dependencies, or local abstractions.
2. Justify why the dependency is necessary.
3. Avoid duplicate implementations such as:
   - utility functions that replicate library behavior
   - wrappers that add no semantic value
   - new classes that duplicate existing domain models

If a library abstraction conflicts with documented invariants:

- Surface the mismatch explicitly.
- Do not silently work around it.
- Do not bend domain semantics to accommodate a library.

Composition > Reinvention  
Reuse > Novelty  
Domain Integrity > Library Convenience  

---

## VII. Static Analysis and Test Discipline

- Write code that naturally satisfies linters and type checkers.
- Prefer explicit typing over casts used only to silence warnings.
- Do not use `Any`, blanket ignores, or suppression comments to bypass legitimate structural issues.
- Treat lint and type-check failures in production code as design signals.

When changing behavior:

- Add or update tests when the repository already has coverage for that area.
- Prefer focused unit tests over broad, brittle end-to-end scaffolding.
- Keep tests aligned with documented invariants and persistence semantics.

If satisfying a tool requires architectural compromise:

- Surface the tension explicitly.
- Do not suppress the warning only to make the checks pass.

Production integrity > Test shortcuts  
Signal > Silence  

---

## VIII. Exception Semantics

- Preserve exception causality at abstraction boundaries.
- When wrapping or translating exceptions, use explicit chaining:

  raise DomainError("...") from exc

- Do not swallow exceptions.
- Do not replace exceptions without preserving their cause.
- Avoid leaking low-level infrastructure exceptions directly through higher-level API contracts when translation adds genuine clarity.
- New exception types must represent meaningful semantic categories, not hyper-specific runtime circumstances.
- Prefer extending existing local exception patterns in the relevant Baldwin module rather than inventing ad-hoc one-off exception classes.

Hierarchy coherence > Novelty  
Semantic taxonomy > One-off cleverness  
Error transparency > Convenience  

---

## IX. Human Legibility Requirement

This codebase must remain readable by a senior engineer six months from now without relying on memory.

Prefer:

- Explicit names
- Small cohesive classes and modules
- Logical separation
- Predictable patterns
- Straightforward control flow

Avoid:

- Clever compression
- Abstraction for its own sake
- Pattern overuse
- Opaque one-liners
- Large mixed-responsibility modules

Human comprehension > Intellectual display  

---

## X. Logging and Observability Discipline

Logging in this system is part of the runtime contract, not optional diagnostics.

Because this repository is an Azure Function App and is expected to run in Azure, prefer structured, operationally useful logs that can evolve cleanly into Azure monitoring workflows.

- Prefer stable, machine-parseable logging patterns over ad-hoc prose.
- Include meaningful domain context.
- Preserve causal context across ingestion, vectorization, and API flows when available.
- Keep log event names and meanings semantically stable so future monitoring queries remain reliable.
- Avoid logging secrets, credentials, full raw message bodies, or unnecessary sensitive payloads.

Level semantics are mandatory:

- `DEBUG` for detailed execution and skip-path diagnostics.
- `INFO` for successful state transitions and expected operational milestones.
- `WARNING` for degraded but non-fatal or expected-rejection scenarios.
- `ERROR` for unexpected failures requiring investigation.

Exception logging rules:

- Do not swallow exceptions.
- For unexpected failures, log with stack-trace context.
- When translating exceptions across abstraction boundaries, preserve causality with explicit chaining:

  raise DomainError("...") from exc

- Do not replace low-level exceptions with context-free messages.

Observability > Noise  
Correlation > Convenience  
Causality > Cosmetic messaging  

---

## XI. Cross-Project Coordination

This repository may depend on infrastructure or deployment configuration managed outside this codebase, including Azure resources provisioned by companion infrastructure repositories.

When a feature requires infrastructure changes:

- Surface the dependency explicitly.
- Do not assume required secrets, databases, storage resources, or app settings already exist.
- Distinguish clearly between what is implemented in this repo and what must be provisioned elsewhere.
- Keep repository documentation accurate about current state versus future promotion paths.

If infrastructure is required for correctness, say so directly rather than writing code that quietly assumes unavailable dependencies.

Code clarity > Implicit environment assumptions  

---

## XII. Developer Workflow Alignment

When suggesting or performing work, prefer the workflows that are actually valid in this repository.

Common local commands include:

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
func start
python scripts/vectorize_inbox.py --days 1 --dry-run
```

Before suggesting additional tools, commands, or files:

- Verify they exist in this repository.
- Prefer repo-local paths such as `function_app.py`, `baldwin/`, `scripts/`, `tests/`, and `docs/`.
- Do not reference modules, monitoring docs, handlers, or package names from other projects.

Repo truth > Template residue  

---

## XIII. Plan and Change Reasoning

When asked for analysis, answer the question directly before proposing structural changes.

- Do not silently rewrite the problem.
- Reference specific repository files when reasoning.
- If a question reveals a flaw in the current approach, explain the flaw and propose the minimum coherent revision.
- If the repo docs are incomplete, say what is known from the code and what remains uncertain.

Analysis > Speculation  
Clarity > Premature optimization  
Explanation > Silent adjustment  

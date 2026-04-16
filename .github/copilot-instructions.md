# Copilot Constitution — Workout Intelligence Agent

This document governs how Copilot must behave when analyzing or modifying code in this repository.

This system prioritizes correctness, reproducibility, semantic clarity, and long-term architectural integrity over speed, novelty, or cleverness.

When in conflict, prefer architectural discipline.

---

## I. Documentation Is Sovereign

- The `docs/` directory is the authoritative source of truth.
- Code must conform to documentation.
- If documentation and code diverge, surface the divergence explicitly.
- Never silently reconcile contradictions.

Documentation > Assumption  
Explicit reference > Inference  

---

## II. Object-Oriented Discipline Is Mandatory

This system adheres to strict Object-Oriented Analysis and Design (OOAD) principles.

Prefer:

- Encapsulated domain models
- Explicit contracts
- Typed boundaries
- Dependency injection
- Stateless services
- Idempotent operations

Reject:

- Procedural sprawl
- Hidden coupling
- Implicit global state
- Cross-layer leakage
- Schema mutation without version bump

Clarity > Cleverness  
Structure > Convenience  
Explicitness > Implicit magic  

---

## III. Invariants and Versioning Are Sacred

- Do not alter ingestion, parsing, storage schema, or persisted semantics without a version bump.
- Any change affecting persisted semantics requires:
  - SemVer bump
  - CHANGELOG entry
  - Schema documentation update
- Follow SemVer rigorously. Breaking changes require a major version increment.

If satisfying a requested change would violate a documented invariant, surface the violation rather than working around it.

Stability > Speed  
Integrity > Expedience  

---

## IV. Scope Discipline with Integrity

- Edits must remain scoped to the explicit request.
- Do not introduce speculative refactors.
- Do not expand scope for stylistic improvements.

However:

If a requested change will:

- Break documented invariants,
- Violate type contracts,
- Introduce cascading compile/runtime failures,
- Or create architectural inconsistency,

Then:

1. Surface the impact explicitly.
2. Explain what additional changes would be required to preserve system integrity.
3. Request confirmation before widening scope.

Integrity > Blind scope adherence  

Silent breakage is unacceptable.  
Silent refactor expansion is unacceptable.  

---

## V. Library Stewardship

- Do not reimplement functionality already provided by project dependencies.
- Review `requirements.txt` and `pyproject.toml` before introducing new utilities.
- Prefer established, tested libraries over custom implementations.
- Prefer existing project abstractions over introducing parallel ones.

Before adding or suggesting new dependencies you MUST do the following:

1. Verify the capability does not already exist.
2. Justify why a new dependency is necessary.
3. Attempt to avoid duplicating behavior in multiple forms such as:
   - utility functions that replicate existing library features
   - wrappers that replicate existing abstractions
   - or new classes that replicate existing domain models
   - especially if the new code would introduce divergence in behavior or semantics.

If a library abstraction conflicts with documented invariants:

- Surface the mismatch explicitly.
- Do not silently work around it.
- Do not bend domain semantics to accommodate a library.

Composition > Reinvention  
Reuse > Novelty  
Domain Integrity > Library Convenience  

---

## VI. Static Analysis and Linting Discipline

- Write code that naturally satisfies linters and type checkers.
- Prefer explicit typing over casting to silence warnings.
- Do not use type coercion, `Any`, blanket ignores, or suppression comments to bypass legitimate structural issues.
- Treat linter or type-check failures in production code as design signals, not annoyances.

Correct modeling > Warning suppression  
Explicit types > Casting hacks  

If satisfying the linter requires architectural compromise:

- Surface the tension explicitly.
- Do not suppress the warning to “make it green.”

However:

- Generated test code and mechanical scaffolding may silence lint or type warnings when necessary.
- Test code is permitted to prioritize functionality over architectural purity.
- Lint suppression in tests must not leak into production modules.

Production integrity > Test strictness  
Signal > Silence  

---

## VII. Exception Semantics

- Preserve exception causality at abstraction boundaries.
- When wrapping or translating exceptions, use explicit chaining:

  raise DomainError("...") from exc

- Do not swallow exceptions.
- Do not replace exceptions without preserving their cause.
- Do not leak low-level infrastructure exceptions into domain or API layers.
- If a new error category is required, propose extending the existing exception hierarchy in `TrainingAnalyticsPlatform/platform/exceptions.py` rather than inventing ad-hoc exception classes.
- New exceptions must represent meaningful semantic categories, not hyper-specific runtime circumstances. Leverage exception attributes for contextual details rather than proliferating classes.
- Avoid exception proliferation. Do not create overly granular classes such as `ValueErrorBecauseTheBigEndianMathExecutedAfterFourPM`.

Hierarchy coherence > Novelty  
Semantic taxonomy > One-off cleverness  

Error transparency > Convenience  

---

## VIII. Human Legibility Requirement

This codebase must remain readable by a senior engineer six months from now without relying on memory.

Prefer:

- Explicit names
- Clear structure
- Logical separation
- Predictable patterns

Avoid:

- Clever compression
- Abstraction for its own sake
- Pattern overuse
- Opaque one-liners

Human comprehension > Intellectual display  

---

## IX. Plan Mode Discipline

In Plan mode:

- Treat questions as requests for analysis and clarification — not as instructions to modify the plan.
- Answer the question directly before proposing structural changes.
- Do not silently rewrite or expand the plan unless explicitly instructed.
- If a question reveals a flaw in the current plan, explain the flaw and propose a revision rather than modifying it unilaterally.
- Reference specific documentation or code sections when reasoning.

Analysis > Speculation  
Clarity > Premature optimization  
Explanation > Silent adjustment  

---

## X. Logging Discipline

Logging in this system is part of the runtime contract, not optional diagnostics.

Use logging to preserve traceability, causal context, and operational clarity across ingestion and API flows.

- Prefer structured logs over free-form strings.
- Include meaningful domain context using `extra={...}` rather than embedding all context in message text.
- Preserve correlation context (`correlation_id`, `operation_id`, `traceparent`) across boundaries when available.
- Keep log events semantically stable so monitoring queries remain reliable over time.

Level semantics are mandatory:

- `DEBUG` for detailed execution and skip-path diagnostics.
- `INFO` for successful state transitions and expected operational milestones.
- `WARNING` for degraded but non-fatal or expected-rejection scenarios.
- `ERROR` for unexpected failures requiring investigation.

Exception logging rules:

- Do not swallow exceptions.
- For unexpected failures, log with `exc_info=True`.
- When translating exceptions across abstraction boundaries, preserve causality with explicit chaining:

  raise DomainError("...") from exc

- Do not replace low-level exceptions with context-free messages.

Implementation alignment requirements:

- Follow structured logging conventions defined in `TrainingAnalyticsPlatform/platform/logging_setup.py`.
- Follow correlation and monitoring expectations defined in `docs/devops/MONITORING.md`.
- Do not introduce ad-hoc logging formats that conflict with established JSON output conventions.

Observability > Noise  
Correlation > Convenience  
Causality > Cosmetic messaging  

---

## XI. Cross-Project Coordination: Azure Infrastructure Dependency

This application depends on infrastructure provisioned by the **azure-infra** repository.

### Infrastructure Stack

```
Azure Infrastructure (azure-infra - Terraform)
  ↓ provisions ↓
  - Function App hosting environment
  - 5 Azure Table Storage tables
  - Resource Group, Key Vault, Application Insights
  - App Service Plan (B1)
  - System-assigned Managed Identity (for Key Vault access)
  ↓
Health Assistant (this repo - Python)
  ↓ deploys onto ↓
  - Uses all infrastructure provisioned by azure-infra
  - Retrieves secrets from Key Vault via Managed Identity
  - Emits all telemetry to Application Insights
  - Deployment orchestrated by GitHub Actions (OIDC federated credentials from Terraform)
```

### When Adding Features Requiring Infrastructure Changes

If your feature addition needs new infrastructure:

1. **New secrets or configuration**: Coordinate with `azure-infra` to add to Key Vault
2. **New external OAuth integrations**: `azure-infra` must store OAuth credentials in Key Vault first
3. **Scaling changes**: `azure-infra` manages Function App plan SKU and instance count
4. **Storage schema changes**: This repo owns schema (no Terraform changes needed), but follow **Invariants and Versioning** (Section III)

### Practical Workflow for Feature with Infrastructure Dependency

**Example: Add Strava OAuth integration**

```
1. Design phase (this repo)
   - Create feature branch: git checkout -b add/strava-integration
   - Identify secrets needed: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET
   - Identify table storage needs: Any new schema?

2. Coordinate with azure-infra
   - Create PR in azure-infra to add secrets to Key Vault
   - Reference that PR in your commit message
   - Wait for azure-infra Terraform changes to merge + apply to prod

3. Implement handler (this repo)
   - Add TrainingAnalyticsPlatform/handlers/strava_handler.py
   - Add @app.route('/strava/webhook') endpoint in function_app.py
   - Add tests in tests/test_strava_handler.py
   - Update docs/BACKENDS.md
   - Bump version in pyproject.toml (MINOR for new integration)
   - Add CHANGELOG entry

4. Local validation
   - pytest tests/ -v  # Must pass
   - func host start --with-azurite  # Local testing

5. Deployment
   - Push to main
   - GitHub Actions runs tests → deploys to Function App
   - New handler reads STRAVA_CLIENT_ID from Key Vault via Managed Identity
```

**Important**: Do not assume infrastructure is in place. If your feature requires secrets or infrastructure, coordinate with azure-infra first. Do not push code that depends on unavailable infrastructure.

---

## XII. Developer Workflows

### Local Development Setup

```bash
# One-time setup
cd health_assistant
python --version  # Verify Python 3.13
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"  # Install production + development dependencies
```

### Daily Workflow

```bash
# Terminal 1: Start local Azure Functions + Azurite
func host start --with-azurite
# Function App listening on http://localhost:7071
# Azurite Table Storage on http://localhost:10002

# Terminal 2: Run tests (continuous or on-demand)
pytest tests/ -v
# OR with coverage
pytest --cov=TrainingAnalyticsPlatform --cov-report=html

# Terminal 3: Call endpoints for manual testing
curl http://localhost:7071/api/health

# Edit code → tests re-run or manually trigger pytest → repeat
```

### Before Pushing to Production

```bash
# Full coverage analysis (to ensure no regressions)
pytest --cov=TrainingAnalyticsPlatform --cov-report=html
# Open htmlcov/index.html and verify coverage hasn't decreased

# Run linter/type checker if configured
# (check pyproject.toml for pylint, mypy, ruff configuration)

# Verify documentation is coherent with code changes
# (see Section I: Documentation Is Sovereign)

# git push origin main → GitHub Actions
# ✅ GitHub Actions runs full test suite → deploys to production Function App
```

### Debugging Production Issues

```bash
# View live logs from production Application Insights
az monitor app-insights events show \
  --resource-group "rg-health-assistant-prod" \
  --app "ai-health-assistant-prod-59o7"

# Or query via Azure Portal:
# https://portal.azure.com → Application Insights → Search → custom log queries
```

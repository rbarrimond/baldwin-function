# Baldwin Semantic IMAP Design

## Purpose

Baldwin is designed to transform email from an unstructured inbox into an executive cognitive operating system grounded in GTD principles, semantic classification, and reversible workflow state.

The system uses:

- IMAP folders for broad structural routing
- IMAP flags for workflow urgency/state
- IMAP keywords for semantic meaning
- Things Areas of Responsibility (AoR) as the canonical organizational model
- Embeddings/vector search for retrieval and reasoning
- Human review as the final authority

This document captures the intentional semantics of the system.

---

# Core Design Principles

## 1. Email is Input, Not Storage

Email is treated as an inbound event stream rather than a permanent workspace.

Baldwin extracts meaning from messages and stores semantic state externally while preserving interoperability with standard IMAP clients.

---

## 2. Semantic Meaning Must Be Visible

The system intentionally encodes meaning into IMAP-visible artifacts so that:

- Apple Mail
- iPhone/iPad Mail
- Other IMAP clients
- Scripts
- AI systems

can all understand workflow state without proprietary lock-in.

---

## 3. State Must Be Reversible

Baldwin never mutates state in irreversible ways.

All transitions must be:

- explainable
- auditable
- reversible
- confidence-scored

This aligns with the Baldwin Prime Invariant:

> The system must never lie by hallucination or omission.

---

# IMAP Semantic Layers

The system uses multiple overlapping semantic layers.

| Layer | Purpose | Example |
|---|---|---|
| Folder | Structural routing | `Bulk`, `Alum`, `Family` |
| Flag | Workflow urgency/state | Red Flag = Tier 0 |
| Keyword | Semantic classification | `$AOR_Family` |
| Embedding | Semantic retrieval | Thread similarity |
| AI Summary | Executive synthesis | “Action required by Friday” |

These layers are intentionally orthogonal.

---

# Folder Strategy

Folders represent broad structural routing rather than precise workflow state.

Examples:

| Folder | Purpose |
|---|---|
| Inbox | Active intake |
| Bulk | Low-value/high-volume email |
| Alum | MIT/UPenn alumni traffic |
| Receipts | Purchases and invoices |
| Family | Family-related communication |
| Archive | Long-term searchable storage |
| @SaneLater | Deferred review |
| @SaneNews | Newsletters |
| @SaneCC | “To Me” mail requiring caution |

Folders should remain human-legible and relatively stable.

---

# Flags as Workflow State

Flags represent operational urgency and triage priority.

The system intentionally uses Apple Mail colored flags because they are:

- visible across Apple devices
- IMAP-backed
- human-readable
- script-accessible

## Proposed Flag Semantics

| Flag Color | Meaning |
|---|---|
| Red | Tier 0 immediate attention |
| Orange | Important but not urgent |
| Yellow | Pending review |
| Green | Waiting on external party |
| Blue | Informational/reference |
| Gray | Political/news/opinion tracking |
| Unflagged | Unprocessed/default |

Flags represent workflow state rather than topic.

---

# IMAP Keywords as Semantic Metadata

Keywords provide machine-readable semantic classification.

Unlike folders, a message can contain multiple keywords simultaneously.

This is the core semantic layer.

## Keyword Categories

### Areas of Responsibility (AoR)

Examples:

- `$AOR_Family`
- `$AOR_Health`
- `$AOR_Wealth`
- `$AOR_Faith`
- `$AOR_WBMBAA`
- `$AOR_RideAsOne`

These map directly to Things Areas of Responsibility.

---

### Workflow Keywords

Examples:

- `$Action`
- `$Waiting`
- `$Someday`
- `$Reference`
- `$ReadReview`
- `$Escalated`

These mirror GTD concepts.

---

### Disposition Keywords

Examples:

- `$Pay`
- `$Legal`
- `$Medical`
- `$Travel`
- `$School`
- `$Finance`

These help downstream AI summarization and retrieval.

---

### AI Confidence Keywords

Examples:

- `$AI_HighConfidence`
- `$AI_LowConfidence`
- `$HumanReviewed`

These provide explainability and trust calibration.

---

# Things Integration Model

Things is treated as the canonical task management layer.

Email classification maps naturally into Things concepts.

## Mapping

| Email Semantic | Things Concept |
|---|---|
| AoR keyword | Area of Responsibility |
| Action keyword | To-Do |
| Thread cluster | Project |
| Structured subtasks | Checklist |
| Waiting flag | Waiting state |

---

# Example Workflow

## Incoming Email

A message arrives from school regarding a tuition deadline.

### Folder

- `Inbox`

### Flags

- Orange

### Keywords

- `$AOR_Family`
- `$School`
- `$Pay`
- `$Action`

### AI Summary

> Tuition payment due Friday. Requires parent portal login.

### Things Mapping

AoR:

- Family

Project:

- Children Education

Task:

- Pay tuition invoice

---

# SaneBox Interaction Model

SaneBox remains responsible for coarse contact-centric filtering.

Baldwin adds semantic intelligence on top.

## Important Constraint

Messages in `@SaneCC` should not be aggressively moved because SaneBox interprets this mailbox specially.

Baldwin should instead:

- summarize
- classify
- flag
- prioritize

while minimizing folder disruption.

---

# Embeddings and Semantic Retrieval

Threads become the primary semantic unit.

Each thread stores:

- embedding vectors
- AI summaries
- workflow state
- semantic tags
- timestamps
- confidence metadata

## Why Threads?

Threads preserve:

- conversational context
- decision history
- action chains
- accountability

Single-message threads are valid degenerate cases.

---

# Escalation Logic

Workflow state can evolve over time.

Example:

| Condition | Action |
|---|---|
| No response after 3 days | escalate flag |
| Payment overdue | promote to Red |
| Repeated ignored thread | summarize daily |
| High-value sender | preserve Inbox visibility |

This creates an executive attention surface rather than a passive archive.

---

# Human Authority

AI assists.

Humans decide.

The system intentionally avoids autonomous destructive behavior.

## AI Responsibilities

- summarize
- classify
- rank
- cluster
- suggest

## Human Responsibilities

- approve
- reject
- override
- contextualize

---

# Future Enhancements

## Planned Capabilities

- Thread-level embeddings
- Semantic deduplication
- Priority prediction
- Relationship graphs
- Daily executive briefings
- Natural language retrieval
- Things mutation queue
- Apple Mail extension support
- Voice-driven triage

---

# Architectural Philosophy

Baldwin is not merely an email classifier.

It is an executive cognitive operating system designed to reduce cognitive surface area while preserving human judgment, traceability, and trust.

The design intentionally combines:

- GTD workflow theory
- semantic search
- AI summarization
- IMAP interoperability
- Apple ecosystem ergonomics
- reversible operational semantics

to create a system where meaning is visible, durable, and explainable.

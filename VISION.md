# Codex Agentic OS Vision

## Purpose

Codex Agentic OS is a local-first, provider-neutral control plane that turns AI models into reliable, observable, resumable workers. It gives agents a durable home in which they can receive goals, plan and coordinate work, execute tools safely, survive interruption, retain decisions, and produce auditable results without depending on one model vendor or orchestration framework.

Models are interchangeable reasoning engines. Codex Agentic OS supplies the operating layer around them: durable identity and state, scheduling and coordination, sandboxed execution, policy and approvals, memory and provenance, provider routing, recovery, and operator control.

## End-state operator experience

An operator can submit a meaningful objective and trust the system to:

1. Create a durable run owned by an observable agent identity.
2. Decompose the objective into ordered, inspectable, resumable steps.
3. Select configured hosted or local model providers without changing the workflow contract.
4. Execute tools in an explicit Docker or Podman sandbox with deliberate filesystem, environment, resource, working-directory, and network policy.
5. Persist every claim, transition, result, failure, decision, approval, and artifact with enough provenance to explain what happened.
6. Recover safely after crashes, timeouts, cancellation, or worker replacement without silently duplicating or losing work.
7. Coordinate multiple agents without conflicting ownership or hidden state mutation.
8. Pause for operator approval when policy requires it and expose useful intervention controls.
9. Present progress, costs, model/tool activity, outputs, and failure reasons through coherent CLI, API, and operator interfaces.

## Product capabilities

The project converges on these connected capabilities:

- **Durable orchestration:** goals, plans, runs, steps, queues, claims, lifecycle transitions, retries, cancellation, recovery, and scheduling.
- **Agent identity and coordination:** durable identities, liveness, ownership, concurrency control, delegation, and multi-agent handoffs.
- **Safe execution:** sandboxed commands and tools with explicit capabilities, resource limits, network policy, approvals, and auditable boundaries.
- **Provider-neutral intelligence:** consistent model contracts across hosted providers, local servers, and arbitrary compatible endpoints, with routing based on capability and operator policy.
- **Memory and context:** durable decisions, working memory, artifacts, repository intelligence, provenance, and deliberate context retrieval across sessions.
- **Observability and governance:** structured events, logs, status, cost and usage evidence, policy decisions, approvals, and post-run explanation.
- **Operator surfaces:** composable library and API contracts, a complete CLI, and an eventual interface for creating, supervising, inspecting, and recovering agent work.

## Architectural principles

- Keep durable state explicit, versioned, inspectable, and safe under concurrent workers.
- Treat sandboxing and policy as core runtime boundaries, not optional integrations.
- Keep provider and orchestration abstractions small until concrete use earns additional complexity.
- Support local-only operation and credential-free providers as first-class paths.
- Prefer vertical operator outcomes over disconnected infrastructure work.
- Require deterministic behavior at coordination boundaries and explicit handling of ambiguity and failure.
- Preserve human authority over credentials, external side effects, destructive actions, and policy-sensitive execution.
- Record durable rationale in `.decisions/` and implementation structure in `.plan/`.

## Roadmap contract

GitHub milestones are ordered vertical sprints. Each sprint must deliver one demonstrable operator capability that composes with what already exists and closes a specific gap toward this vision.

Maintain a rolling horizon of three open milestones: one active sprint and two ordered future sprints. Detail executable issues only for the active sprint; future milestones carry an objective, user-visible exit criteria, scope boundary, dependencies, and evidence-based rationale until they become active.

Milestones must not be generic themes such as “improve architecture,” speculative cleanup, or arbitrary queue maintenance. Retrospective evidence may introduce a remediation sprint ahead of planned work. New product sprints must respect such remediation and must be revised when completed work invalidates their assumptions.

## Definition of success

The project has reached its intended product state when an operator can delegate a non-trivial, multi-step objective to one or more agents; run it across interruption and worker replacement inside enforceable execution policy; inspect and influence it while active; and later reconstruct what models, tools, decisions, approvals, state transitions, and artifacts produced the result.

The system must demonstrate that workflow across both hosted and local model paths without making its durable runtime semantics depend on either.

## Non-goals

- Training or owning a foundation model.
- Hiding irreversible or externally consequential actions from the operator.
- Coupling all workflows to one vendor, SDK, container engine, or orchestration framework.
- Treating chat alone as the product.
- Maximizing autonomous behavior at the expense of recoverability, provenance, or control.

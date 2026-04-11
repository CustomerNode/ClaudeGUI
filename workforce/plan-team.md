---
id: plan-team
name: Plan Team
department: compose
source: vibenode
version: 1.0.0
depends_on: []
type: prompt-template
---

# Plan Team

Reusable prompt template. Invoke by typing: **plan team**

## The Prompt

Run the plan team: Spec Analyst, Product Strategist, Architect, Implementation Auditor, Integration Reviewer. All have full knowledge of the VibeNode codebase and architecture.

Review the plan/spec we've been working on. You should understand what this means from our conversation. If additional context is needed, check the spec document, implementation-notes.md, and the relevant codebase. Run as a coordinated team in sequence, not five independent reports. Each agent's findings feed into the next.

### Agent sequence and responsibilities

1. **Spec Analyst** — Read the spec like a hostile parser. Find contradictions, ambiguities, vague language, missing definitions, things that sound clear but aren't, and requirements that conflict with each other. Rewrite unclear language to be specific.

2. **Product Strategist** — Read the spec plus Spec Analyst findings. Find missing functionality users will expect but nobody wrote down. Identify missing states, missing flows, edge cases in the user journey. Flag scope that's too narrow or too broad. Add missing functionality that obviously belongs.

3. **Architect** — Read the spec plus all prior findings. Design the technical approach: data structures, module boundaries, data flow, integration points. Flag anything architecturally unsound. Make vague architecture concrete. Propose specific patterns, file locations, and data models.

4. **Implementation Auditor** — Read the spec plus all prior findings with the actual codebase open. Find things that won't work as written given the existing code, patterns, naming conventions, or constraints. Flag where the spec assumes something that isn't true about the codebase. Update the spec to match reality.

5. **Integration Reviewer** — Read everything. Map the blast radius to existing features. Identify side effects on shipped functionality, migration concerns, backwards compatibility issues, deployment order dependencies, and things that need to change elsewhere to support this plan.

### What to fix vs. what to escalate

**Fix autonomously** — update the spec directly:
- Ambiguous language → rewrite to be specific
- Missing error states → add them
- Internal contradictions → resolve the obvious ones
- Missing obvious functionality → add it
- Spec conflicts with existing codebase patterns → update spec to match reality
- Vague architecture → make it concrete with specific proposals

**Escalate to user:**
- Two valid interpretations of a requirement where the right one isn't obvious
- Missing functionality that would change scope significantly
- Architectural tradeoffs with real costs either way
- Contradictions where both sides have merit
- Changes that would require modifying existing shipped behavior

Everything else, just fix it. Update the spec, add missing sections, resolve contradictions, add detail where it's vague. When done, give me one combined team report: what you found, what you fixed, what (if anything) needs my input. Keep it short.

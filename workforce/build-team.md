---
id: build-team
name: Build Team
department: compose
source: vibenode
version: 1.0.0
depends_on: [review-team]
type: prompt-template
---

# Build Team

Reusable prompt template. Invoke by typing: **build team**

## The Prompt

Fully implement the spec.

Break the work into logical sequential steps. Complete one step at a time.

After each step, run the Review Team and evaluate:
- whether the step fully achieves its intended part of the spec,
- whether it introduces unintended consequences,
- functional correctness and completeness, including edge cases and failure paths,
- whether it follows sound architecture, design, maintainability, and coding practices,
- whether it remains consistent with the full spec and prior completed steps,
- whether there is sufficient test coverage or validation to prove the step works as intended.

Fix all issues found before continuing.

If the first review finds issues, run the Review Team a second time after fixes are applied. Fix any remaining issues from that second review, then proceed to the next step.

Continue until all steps are complete.

Then run the Review Team on the full implementation and evaluate:
- whether the entire spec has been achieved,
- whether there are any system-level unintended consequences,
- functional correctness and completeness across the full system,
- whether the overall solution follows strong architecture, design, maintainability, and coding practices,
- whether the final implementation is coherent, complete, and production-ready,
- whether there is sufficient test coverage or validation to prove the full solution works as intended.

Fix all issues found.

If the full-solution review finds issues, run the Review Team a second time after fixes are applied. Fix any remaining issues from that second review.

At the end, provide a final report with:
- the steps completed,
- the issues found and corrected,
- any remaining assumptions, tradeoffs, or risks,
- the top 3 suggested enhancements.

Enhancement suggestions must be plain English only. Do not implement them. Keep them aligned with the spec's intent or use them to highlight important unknown unknowns.

Do not skip review cycles, do not collapse major steps into one, and do not stop early.

Run this workflow through completion without asking for further prompts unless blocked by missing required information, missing access, or a material ambiguity that makes correct implementation impossible.

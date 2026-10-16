# LinkedIn package: support-copilot

Ready for your review. Nothing here has been published.

| File | What it is |
|---|---|
| [post-en.md](post-en.md) | Recommended post, English |
| [post-pt-pt.md](post-pt-pt.md) | Optional PT-PT version |
| [project-summary.md](project-summary.md) | Short summary for a profile Featured section, a message or a CV line |

## Images, in order

Upload as a multi-image post (LinkedIn shows the first image in the feed, so it carries the message).

| # | File | Why it is there | Suggested alt text |
|---|---|---|---|
| 1 | `docs/social-preview.png` (1280 × 640) | The idea in one line, with a genuine product crop | support-copilot: AI-assisted support, human-approved decisions |
| 2 | `docs/review-queue.png` | The human decision, clearly separated from the AI's suggestion | The review page with the ticket, the AI's triage and draft, and a separate Your decision panel |
| 3 | `docs/guardrails.png` | Masking and the promise warning | A reply promising a refund is flagged before approval |
| 4 | `docs/incident-spike.png` | Product thinking: an outage becomes one incident | Four tickets grouped as a possible incident, answered with one click |
| 5 | `docs/architecture.png` | Which steps are AI, which are plain rules, where the human sits | Flow from ticket through masking, triage, drafting, checks and human approval to the customer |
| 6 (optional) | `docs/insights.png` | The feedback loop, with synthetic demo data | Clean rate per category with a confidence lower bound |

The post is written to work with images 1 to 5. Images 2 and 3 are tall, so check the crop in LinkedIn's preview.

## Product decisions worth highlighting

1. **The AI suggests, a person decides.** The approval step is the product, not an afterthought, and the UI keeps ticket, AI suggestion and human decision visibly apart.
2. **Deterministic checks around the model.** Masking before, promise detection after, as readable rules that can be tested.
3. **Visible failure.** Fallback to rules is labelled, never silent.
4. **Evidence before autonomy.** Per-category clean rate with a Wilson lower bound; "automation candidate" is informational.
5. **Incidents, not just tickets.** Grouping near-identical tickets and one-click answers for a whole outage.
6. **Honest boundaries.** Fakes, baselines and guardrail gaps are stated up front, in the README and in the post.

## Hashtags

Used in the post: `#ProductManagement #AppliedAI #HumanInTheLoop #ResponsibleAI #CustomerSupport #Automation`
Alternatives: `#ProductThinking #AIProduct #UX #Python #FastAPI #LLM`. Three to six is plenty.

## Before posting (manual)

- Merge the branch and make sure the images in `docs/` are on `main`, so the repository link shows them.
- Set the GitHub social preview (see [docs/presentation.md](../../docs/presentation.md)), so the link card looks right.
- Re-read the claims against the repository on the day: the test count (332) changes whenever tests are added.
- Add the link in the post or in the first comment, then publish it yourself.

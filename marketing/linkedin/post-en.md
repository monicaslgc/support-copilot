# LinkedIn post (English)

Recommended. About 2,300 characters, under LinkedIn's 3,000 limit. Structure follows the kit: hook, context, work, learning, limitations, call to action. Attach the images in the order given in [README.md](README.md).

---

AI proposes, deterministic rules decide, humans approve. I wanted to see what that looks like in a support queue.

Most AI support demos show what a model can write. The harder question is what it takes to let that text anywhere near a customer.

So I built support-copilot, an independent concept project with synthetic data. The AI prepares each ticket: triage, a draft reply in the customer's language, a warning if the draft promises something it shouldn't. A person approves or rejects every reply. Nothing reaches the customer before that.

Four decisions I would defend:

1. The model never sees raw private data. Emails, cards, IBANs, phones and passwords are masked by plain rules first, with Luhn and mod-97 checks so an order number is left alone.

2. Promises are checked by rules, not by asking the model to behave. A model-written draft that promises a refund is replaced by the standard reply. If a reviewer types one, the page warns and asks for a second click.

3. Failure is visible. If the model refuses, is cut off or is down, the ticket is still triaged by rules, and the screen says so.

4. Human decisions become data. Per category, the app shows how often the AI was right, with a confidence bound: three perfect reviews are not evidence. "Automation candidate" is informational. Nothing is automated.

When an outage hits, near-identical tickets are grouped into one possible incident and answered with one click, still as individual reviews.

What I took from it: a useful AI system needs more than good outputs. It needs clear boundaries, deterministic checks where they fit, human judgement and behaviour you can test. That is why it has 379 offline tests, with each user story as an executable acceptance test.

What it is not: the Zammad and Zendesk adapters are tested against fakes, the Claude path against a fake SDK, and the guardrails have documented gaps (names and addresses are not masked; an oddly worded promise gets through). A person still reads every reply. I would rather say that plainly.

Code, screenshots and the limitations page: https://github.com/monicaslgc/support-copilot

#AI #ProductManagement #Automation #OpenSource #BusinessAnalysis

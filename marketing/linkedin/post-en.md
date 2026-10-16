# LinkedIn post (English)

Recommended. About 1,900 characters, under LinkedIn's 3,000 limit. Attach the images in the order given in [README.md](README.md). Put the repository link in the first comment or at the end of the post, as you prefer.

---

Most AI support demos show what the model can write. I wanted to explore what it takes to let that output anywhere near a customer.

So I built support-copilot, an independent concept project with synthetic data. The AI prepares the ticket: triage, a draft reply in the customer's language, a warning if the draft promises something it shouldn't. A person approves or rejects every reply. Nothing reaches the customer before that.

Four decisions I would defend:

1. The model never sees raw private data. Emails, cards, IBANs, phones and passwords are masked by plain rules first (with Luhn and mod-97 checks, so an order number is left alone).

2. Promises are checked by rules, not by asking the model to behave. If a model-written draft promises a refund, it is replaced by the standard reply. If a reviewer types one, the page warns and asks for a second click.

3. Failure is visible. If the model refuses, is cut off or is down, the ticket is still triaged by rules, and the screen says so.

4. Human decisions become data. Per category, the app shows how often the AI was right with a confidence bound: three perfect reviews are not evidence. "Automation candidate" is informational only. Nothing is automated.

When an outage hits, near-identical tickets are grouped into one possible incident and answered with one click, still as individual reviews.

Testing: 332 offline tests, with each user story as an executable acceptance test. I also broke the code on purpose in dozens of ways to check that a test failed each time.

What it is not: the Zammad and Zendesk adapters are tested against fakes, the Claude path against a fake SDK, and the guardrails have documented gaps (names and addresses are not masked; an oddly worded promise gets through). A person still reads every reply. I would rather say that plainly.

The point I keep coming back to: a useful AI system needs more than good outputs. It needs clear boundaries, deterministic checks where they fit, human judgement and behaviour you can test.

Code, screenshots and the limitations page: https://github.com/monicaslgc/support-copilot

#ProductManagement #AppliedAI #HumanInTheLoop #ResponsibleAI #CustomerSupport #Automation

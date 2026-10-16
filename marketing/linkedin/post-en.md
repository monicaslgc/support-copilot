# LinkedIn post (English)

Recommended. Under LinkedIn's 3,000 character limit. Structure follows the kit: hook, context, work, learning, limitations, call to action. Attach the images in the order given in [README.md](README.md).

---

AI proposes, deterministic rules decide, humans approve. I wanted to see how that fits inside the helpdesk a support team already uses, instead of next to it.

Most AI support demos are one more app. A support team already has a helpdesk, and nobody wants another tool to open.

So I built support-copilot as an add-on, an independent concept project with synthetic data. It listens to the helpdesk (Zendesk, Zammad or your own), triages each ticket, drafts a reply in the customer's language from your help articles, and writes the result back as an internal note and tags. The agent decides without leaving the ticket: add the tag ai_approve and the drafted reply goes to the customer, add ai_reject and they answer themselves. Nothing reaches the customer before that.

Four decisions I would defend:

1. Tags are the interface. Your own triggers and views can route on them, the names are yours to set, and the add-on never needs to know your groups or custom fields.

2. A decision is signed. Approving by tag sends a reply to a customer, so for a connected helpdesk it is only accepted when the webhook signature can be checked, and you can limit it to the people you list.

3. Plain rules sit around the model. Private data is masked before it, with Luhn and mod-97 checks so an order number is left alone. Promises are caught after it: a draft that promises a refund is replaced by the standard reply.

4. Human decisions become data. Per category, it shows how often the AI was right, with a confidence bound: three perfect reviews are not evidence. "Automation candidate" is informational. Nothing is automated.

What I took from it: a useful AI system needs clear boundaries, deterministic checks where they fit, human judgement and behaviour you can test. That is why it has 385 offline tests, with each of its twelve user stories as an executable acceptance test. It is a Python library with an optional FastAPI router and an optional review page, so a team can take only the part it needs.

What it is not: the Zammad and Zendesk adapters are tested against fakes, and the Claude path against a fake SDK. Nothing has run against a live helpdesk or the live API yet, including the payloads that carry the tags. The guardrails have documented gaps (names and addresses are not masked; an oddly worded promise gets through). A person still reads every reply. I would rather say that plainly.

Code, screenshots and the limitations page: https://github.com/monicaslgc/support-copilot

#AI #ProductManagement #Automation #OpenSource #BusinessAnalysis

# Visual identity and assets

How the project looks, where the images come from and how to regenerate them. Back to the [README](../README.md).

## Identity

The review page, diagrams and images use one palette, taken from the author's personal brand:

| Token | Hex | Used for |
|---|---|---|
| Cream | `#F6F2E9` | page and image backgrounds |
| Forest Green | `#263C32` | headings, the primary button, the human-decision panel |
| Sage | `#A8B29A` | AI suggestions, accents |
| Charcoal | `#353832` | body text |

Typography: a serif (Georgia, as in the project cover) for headings, Inter or DM Sans for text and JetBrains Mono for technical labels. The page asks for these by name with system fallbacks and loads nothing from the network, so on a machine without them it looks plainer, not broken. Rust and ochre appear only for warnings (promises, possible incidents), so they stand out.

In the review page the three kinds of content are kept apart on purpose: the **customer's ticket** (a quote with a charcoal rule), the **AI suggestion** (sage panels labelled `AI SUGGESTION` and `AI DRAFT`) and the **human decision** (a forest-green bordered panel labelled `HUMAN`). Text pairs keep at least 4.5:1 contrast, which `tests/test_ui.py` checks for light and dark mode.

## Images

| File | What it is | Made by |
|---|---|---|
| `review-queue.png`, `after-approval.png`, `approved-view.png`, `incident-spike.png`, `customer-context.png`, `insights.png`, `mobile-dark.png` | The running app with invented demo data | `scripts/capture_screenshots.py` |
| `guardrails.png` | The same, with a stub model so the masking notice shows without an API key; the promise is typed in by the reviewer | `scripts/capture_screenshots.py` |
| `architecture.png` | The flow, from `docs/src/architecture.html` | `scripts/render_assets.py` |
| `social-preview.png` | 1280 × 640 GitHub social preview | `scripts/render_assets.py` |

```bash
pip install playwright
python scripts/capture_screenshots.py   # needs Chromium; add --fonts DIR for the brand fonts
python scripts/render_assets.py
```

Neither script is part of CI. Screenshots were rendered with Inter and JetBrains Mono; where Georgia was not installed a similar serif (Newsreader) stood in for it.

## Social preview

![Social preview: support-copilot, AI-assisted support, human-approved decisions](social-preview.png)

GitHub does not let an API or a pull request set the social preview. In the repository, open **Settings → General → Social preview → Edit → Upload an image** and choose `docs/social-preview.png`.

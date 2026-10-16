# Images and how to regenerate them

Where the pictures in the README and the docs come from. Back to the [README](../README.md).

| File | What it is | Made by |
|---|---|---|
| `review-queue.png`, `after-approval.png`, `approved-view.png`, `incident-spike.png`, `customer-context.png`, `insights.png`, `mobile-dark.png` | The running review page with invented demo data | `scripts/capture_screenshots.py` |
| `guardrails.png` | The same, with a stub model so the masking notice shows without an API key; the promise is typed in by the reviewer | `scripts/capture_screenshots.py` |
| `architecture.png` | The flow of a ticket (`docs/src/architecture.py`) | `scripts/render_assets.py` |
| `social-preview.png` | 1280 x 640 GitHub social preview, also the README header | `scripts/render_assets.py` |

The announcement material (a LinkedIn post and its graphic) is in [`marketing/`](../marketing/linkedin/README.md). It is not part of the library.

```bash
pip install playwright
python scripts/capture_screenshots.py   # needs Chromium; CHROMIUM_PATH names a binary if Playwright's download is not installed
python scripts/render_assets.py --fonts support_copilot/static/fonts
```

Neither script is part of CI. Every screenshot shows synthetic data.

In the review page the three kinds of content are kept apart on purpose: the customer's ticket (a quote with a dark rule), the AI suggestion (tinted panels labelled `AI SUGGESTION` and `AI DRAFT`) and the human decision (a bordered panel labelled `HUMAN`). Colour is never the only signal: a decision is also written as text, and `tests/test_ui.py` checks that every text pair keeps at least 4.5:1 contrast in light and dark mode.

## Social preview

![Social preview: support-copilot, AI-assisted support, human-approved decisions](social-preview.png)

GitHub does not let an API or a pull request set the social preview. In the repository, open **Settings > General > Social preview > Edit > Upload an image** and choose `docs/social-preview.png`.

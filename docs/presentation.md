# Visual identity and assets

How the project looks, where the images come from and how to regenerate them. Back to the [README](../README.md).

## Identity

Everything uses the author's own brand kit (the LinkedIn Media Kit), not a new identity:

| Token | Hex | Used for |
|---|---|---|
| Cream | `#F4F1E8` | page and image backgrounds |
| Dark green | `#153D32` | headings, the primary button, the human-decision panel |
| Sage | `#A0B3A0` | AI suggestions, accents |
| Charcoal | `#202A27` | body text |
| Muted green | `#6F8876` | secondary and decorative elements |

Typography: DM Serif Display for headings, Inter for text and JetBrains Mono for technical labels. The page asks for them by name with system fallbacks and loads nothing from the network, so on a machine without them it looks plainer, not broken. The kit's muted green is too light for small text (about 3.3:1 on cream), so the review page uses a darker shade of it (`#4C6354`) for secondary text; `tests/test_ui.py` checks that every text pair keeps at least 4.5:1 in light and dark mode. Rust and ochre appear only for warnings (promises, possible incidents), so they stand out.

In the review page the three kinds of content are kept apart on purpose: the **customer's ticket** (a quote with a charcoal rule), the **AI suggestion** (sage panels labelled `AI SUGGESTION` and `AI DRAFT`) and the **human decision** (a dark-green bordered panel labelled `HUMAN`).

## Images

| File | What it is | Made by |
|---|---|---|
| `review-queue.png`, `after-approval.png`, `approved-view.png`, `incident-spike.png`, `customer-context.png`, `insights.png`, `mobile-dark.png` | The running app with invented demo data | `scripts/capture_screenshots.py` |
| `guardrails.png` | The same, with a stub model so the masking notice shows without an API key; the promise is typed in by the reviewer | `scripts/capture_screenshots.py` |
| `architecture.png` | The flow, drawn with the author's diagram kit (`docs/src/architecture.py`) | `scripts/render_assets.py` |
| `social-preview.png` | 1280 × 640 GitHub social preview, also the README header | `scripts/render_assets.py` |
| `../marketing/linkedin/images/support-copilot-4x5.png` | 1080 × 1350 LinkedIn graphic, in the kit's editorial template | `scripts/render_assets.py` |

```bash
pip install playwright
python scripts/capture_screenshots.py   # needs Chromium; add --fonts DIR for the brand fonts
python scripts/render_assets.py
```

Neither script is part of CI. Screenshots were rendered with Inter, JetBrains Mono and DM Serif Display.

## Kit checklist

What each image was checked against (from the author's LinkedIn Media Kit):

| Rule | How it is met |
|---|---|
| Official palette, no neon, purple, intense gradients or 3D | Cream, Sage, Dark green, Charcoal and Muted green only; rust and ochre only on warnings in the app |
| At most two families per piece | Social preview and LinkedIn graphic use DM Serif Display and Inter. The diagram and the app also use JetBrains Mono for technical labels, as the kit's diagram tool does |
| Formats | 4:5 (1080 × 1350) for the post graphic, 16:9 for the diagram, 2:1 (1280 × 640) for the GitHub social preview |
| One idea per image, short title, plenty of space | The graphic says one thing: AI prepares, a person decides |
| No dashes or arrows in the text of an image | Titles and labels use full stops, commas and middle dots. The diagram's flow lines are drawn, not typed |
| Nothing invented | Screenshots come from the running app with synthetic data; the graphic lists the README's eight capabilities; status labels say prototype, tested against fakes |
| No robots, digital brains or circuit decoration | Line icons of real concepts only (inbox, document, shield) |

## Social preview

![Social preview: support-copilot, AI-assisted support, human-approved decisions](social-preview.png)

GitHub does not let an API or a pull request set the social preview. In the repository, open **Settings → General → Social preview → Edit → Upload an image** and choose `docs/social-preview.png`.

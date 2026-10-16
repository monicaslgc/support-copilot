"""Tiny TF-IDF retrieval over markdown knowledge-base articles.

Pure Python on purpose (no heavy deps). Swap `search` for a pgvector query
when you move to embeddings; the interface stays the same.
"""
import math
import re
from collections import Counter
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "kb"
_STOP = set(
    "a o os as de do da dos das e em um uma para por com que se na no nos nas meu minha "
    "the of to and in is it my i not you your can could would should will how what when where why "
    "with for are was this that have has from but our any all been please hi hello thanks since".split()
)


def _stem(token: str) -> str:
    """Fold simple plurals (invoices -> invoice) so a question matches the article's wording.
    Applied to both sides, so odd results (e.g. 'process' keeps its s) still match consistently."""
    return token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"\w+", text.lower()) if t not in _STOP and len(t) > 2]


class KnowledgeBase:
    def __init__(self, directory: Path = KB_DIR):
        self.docs = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
            self.docs.append({"id": path.stem, "title": title, "text": text, "tf": Counter(_tokens(text))})
        n = max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in d["tf"])
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    def search(self, query: str, k: int = 3, min_score: float = 0.1, relative: float = 0.35) -> list[dict]:
        """Top-k articles. Besides the absolute floor, drop anything scoring under `relative` x the best match,
        so one clear hit is not listed next to weak coincidental ones."""
        q = Counter(_tokens(query))
        results = []
        for d in self.docs:
            score = sum(q[t] * self.idf.get(t, 0) * d["tf"][t] for t in q if t in d["tf"])
            norm = math.sqrt(sum((v * self.idf.get(t, 0)) ** 2 for t, v in d["tf"].items())) or 1
            score /= norm
            if score >= min_score:
                results.append({"id": d["id"], "title": d["title"], "score": round(score, 3), "text": d["text"]})
        results = sorted(results, key=lambda r: r["score"], reverse=True)
        if results:
            results = [r for r in results if r["score"] >= relative * results[0]["score"]]
        return results[:k]

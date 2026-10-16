"""Sentiment, language and customer history."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

from app import main, state
from app.classifier import apply_sentiment, classify, classify_rules
from app.drafting import _template, draft_reply
from app.models import Category, Priority, Sentiment, Triage
from app.text import detect_language, rule_sentiment
from tests.fakes import FakeZendesk
from tests.test_llm import GOOD, FakeAnthropic, block, reply


# ---------------- language ----------------
@pytest.mark.parametrize("text,lang", [
    ("Não consigo fazer login. Esqueci a palavra-passe e o link de reset não chega.", "pt"),
    ("I was charged twice this month and I want a refund for my invoice.", "en"),
    ("Hola, no puedo entrar y necesito ayuda con mi cuenta", "es"),
    ("Bonjour, je ne peux pas me connecter et mon compte est bloqué", "fr"),
    ("Ciao, non riesco ad accedere e sono molto preoccupato", "it"),
    ("Platform is down", "en"),
    ("Erro ao guardar contacto. A página bloqueou e deu erro ao gravar o contacto.", "pt"),
    ("12345 !!!", "und"), ("", "und"), ("asdf qwer zxcv", "und"),
])
def test_language_detection(text, lang):
    assert detect_language(text) == lang


# ---------------- sentiment ----------------
@pytest.mark.parametrize("text", [
    "This is unacceptable, it fails again!",
    "Cancel everything, this is ridiculous",
    "ISTO É UMA VERGONHA, NADA FUNCIONA, PÉSSIMO SERVIÇO",
    "Este serviço é péssimo e inaceitável",
    "C'est inacceptable, je veux annuler",
    "Es inaceptable, es la tercera vez!!",
    "THE EXPORT BUTTON DOES NOTHING, AGAIN",             # shouting plus a complaint word
    "I want to cancel!!",                                # a mild word plus emphatic punctuation
])
def test_upset_customers_are_detected(text):
    assert rule_sentiment(text) == "negative"


@pytest.mark.parametrize("text", [
    "How do I cancel my subscription?",                # one mild word is not anger
    "Thanks, how do I export a report?",
    "Can you add dark mode, please?",
    "Como posso cancelar a subscrição?",
    "", "ok",
    "Please send me the invoice again",
    "I want to cancel!",                                       # one exclamation mark is just punctuation
    "THE EXPORT BUTTON DOES NOTHING AND I NEED THIS TODAY",   # shouting alone could just be caps lock
])
def test_calm_questions_stay_neutral(text):
    assert rule_sentiment(text) == "neutral"


# ---------------- priority ----------------
def tri(priority, sentiment):
    return Triage(category=Category.BUG, priority=priority, team="x", confidence=0.5, rationale="r",
                  source="rules", sentiment=sentiment)


@pytest.mark.parametrize("before,after", [(Priority.P4, Priority.P3), (Priority.P3, Priority.P2),
                                          (Priority.P2, Priority.P2), (Priority.P1, Priority.P1)])
def test_an_upset_customer_raises_routine_tickets_one_level(before, after):
    t = apply_sentiment(tri(before, Sentiment.NEGATIVE))
    assert t.priority is after
    assert ("Raised from" in t.rationale) == (before is not after)


@pytest.mark.parametrize("s", [Sentiment.NEUTRAL, Sentiment.POSITIVE])
def test_other_sentiments_never_change_priority(s):
    assert apply_sentiment(tri(Priority.P3, s)).priority is Priority.P3


def test_rules_triage_applies_the_bump_and_records_the_signals():
    t = classify_rules("Export is useless", "How do I export the report? This is unacceptable!!")
    assert (t.sentiment, t.language) == (Sentiment.NEGATIVE, "en")
    assert t.priority is Priority.P3 and "Raised from P4" in t.rationale     # a how-to is P4, raised once


def test_the_outage_stays_p1_however_upset_the_customer_is():
    t = classify_rules("Platform is down", "Total outage, this is unacceptable!!")
    assert t.priority is Priority.P1 and t.sentiment is Sentiment.NEGATIVE


# ---------------- the model path ----------------
@pytest.fixture
def fake(monkeypatch):
    f = FakeAnthropic()
    monkeypatch.setitem(sys.modules, "anthropic", f)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    return f


def say(fake, **fields):
    fake.next_reply = reply(block("text", json.dumps({**GOOD, **fields})))


def test_model_sentiment_and_language_are_used(fake):
    say(fake, sentiment="positive", language="PT")
    t = classify("Ticket", "Obrigado, preciso de ajuda com a fatura")
    assert (t.source, t.sentiment, t.language) == ("llm", Sentiment.POSITIVE, "pt")


def test_the_prompt_asks_for_both_fields(fake):
    classify("Ticket", "text")
    system = fake.calls[0]["system"]
    assert '"sentiment"' in system and '"language"' in system and "ISO 639-1" in system


@pytest.mark.parametrize("fields", [{}, {"sentiment": "furious", "language": "Portuguese"},
                                    {"sentiment": None, "language": 7}])
def test_missing_or_invalid_extras_fall_back_to_rules_without_losing_the_triage(fake, fields):
    say(fake, **fields)
    t = classify("Não funciona", "Esqueci a palavra-passe e o link de reset não chega para mim")
    assert t.source == "llm" and t.category is Category.BILLING and t.rationale == "double charge"  # the model's triage
    assert (t.sentiment, t.language) == (Sentiment.NEUTRAL, "pt")                               # rules filled the gaps


def test_the_bump_also_applies_to_the_models_answer(fake):
    say(fake, priority="P3", sentiment="negative")
    t = classify("Ticket", "text")
    assert t.priority is Priority.P2 and "Raised from P3" in t.rationale


# ---------------- drafts ----------------
def triage_for(language, sentiment=Sentiment.NEUTRAL, priority=Priority.P3):
    return Triage(category=Category.BILLING, priority=priority, team="finance", confidence=0.6,
                  rationale="x", source="rules", sentiment=sentiment, language=language)


ARTS = [{"id": "invoices", "title": "Invoices and payments", "text": "t"}]


@pytest.mark.parametrize("lang,hello,bye", [
    ("en", "Hi Ana,", "Support Team"), ("pt", "Olá Ana,", "Equipa de Suporte"),
    ("es", "Hola Ana,", "Equipo de Soporte"), ("fr", "Bonjour Ana,", "L'équipe Support"),
    ("und", "Hi Ana,", "Support Team"), ("it", "Hi Ana,", "Support Team"),
])
def test_fallback_draft_speaks_the_customers_language(lang, hello, bye):
    out = _template("Ana", triage_for(lang), ARTS)
    assert out.startswith(hello) and out.rstrip().endswith(bye)


def test_an_upset_customer_gets_an_apology_in_their_language():
    assert "Lamentamos o incómodo." in _template("Rui", triage_for("pt", Sentiment.NEGATIVE), ARTS)
    assert "sorry" not in _template("Rui", triage_for("en"), ARTS)
    assert "We're sorry for the trouble." in _template("Rui", triage_for("en", Sentiment.NEGATIVE), ARTS)


def test_every_language_has_every_phrase():
    from app.drafting import _WORDS
    assert all(set(w) == set(_WORDS["en"]) for w in _WORDS.values())


def test_the_model_is_told_the_language_and_mood(fake):
    fake.next_reply = reply(block("text", "Olá Rui, lamentamos."))
    out = draft_reply("Fatura", "cobrança duplicada", triage_for("pt", Sentiment.NEGATIVE), ARTS, "Rui")
    assert out == "Olá Rui, lamentamos."
    user = fake.calls[0]["messages"][0]["content"]
    assert "<context>language: pt; sentiment: negative</context>" in user
    assert "language given in <context>" in fake.calls[0]["system"] and "apology" in fake.calls[0]["system"]


# ---------------- the flow ----------------
def test_tickets_expose_sentiment_and_language_and_the_system_gets_tags_and_a_note():
    fz = FakeZendesk()
    fz.seed(1)
    fz.seed(2)
    state.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    upset = fz.payload(1, "Isto é inaceitável", "Pagamento duplicado, péssimo serviço!!", "Rui")
    calm = fz.payload(2, "Duplicate charge", "I was charged twice, can you refund my invoice?", "Sam")
    c.post("/webhooks/zendesk", json=upset)
    c.post("/webhooks/zendesk", json=calm)

    assert {"ai_sentiment_negative", "ai_lang_pt"} <= fz.tags(1)
    assert "Sentiment: negative | Language: pt" in fz.internal(1)[0]
    assert "ai_sentiment_negative" not in fz.tags(2) and "ai_lang_en" in fz.tags(2)
    assert "Sentiment: neutral | Language: en" in fz.internal(2)[0]

    first = c.get("/tickets/1").json()["suggestion"]
    assert first["triage"]["sentiment"] == "negative" and first["triage"]["language"] == "pt"
    assert first["draft_reply"].startswith("Olá Rui,") and "Lamentamos" in first["draft_reply"]


def make(c, subject, body, customer):
    return c.post("/tickets", json={"subject": subject, "body": body, "customer": customer}).json()["id"]


def test_customer_history_lists_earlier_tickets_newest_first():
    c = TestClient(main.app)
    a = make(c, "Export fails", "export of the report fails", "Rui")
    make(c, "Other person", "login problem", "Ana")
    b = make(c, "Invoice question", "need my invoice", "rui")                    # case-insensitive match
    now = make(c, "Still broken", "export still fails", "Rui")
    c.post(f"/tickets/{b}/review", json={"action": "approved", "reviewer": "m"})

    h = c.get(f"/tickets/{now}").json()["history"]
    assert h["count"] == 2 and [x["id"] for x in h["recent"]] == [b, a]
    assert h["recent"][0]["status"] == "approved" and h["recent"][0]["subject"] == "Invoice question"
    assert c.get(f"/tickets/{a}").json()["history"] == {"count": 0, "recent": []}  # nothing before the first one
    listed = {t["id"]: t["history"]["count"] for t in c.get("/tickets").json()}
    assert listed[now] == 2 and listed[a] == 0


def test_history_is_limited_and_counts_everything():
    c = TestClient(main.app)
    for i in range(8):
        make(c, f"Question {i}", "how do I export", "Rui")
    last = make(c, "Question 8", "how do I export", "Rui")
    h = c.get(f"/tickets/{last}").json()["history"]
    assert h["count"] == 8 and len(h["recent"]) == 5


def test_unknown_customers_have_no_history():
    c = TestClient(main.app)
    make(c, "Question one", "how do I export", "unknown")
    t = make(c, "Question two", "how do I export", "unknown")
    assert c.get(f"/tickets/{t}").json()["history"]["count"] == 0


def test_repeat_customers_are_called_out_in_the_internal_note():
    fz = FakeZendesk()
    state.systems["zendesk"] = fz.make()
    c = TestClient(main.app)
    make(c, "Export fails", "export fails", "Rui")
    fz.seed(7)
    c.post("/webhooks/zendesk", json=fz.payload(7, "Still failing", "the export still fails", "Rui"))
    assert 'Repeat customer: 1 earlier ticket (latest: "Export fails", pending_review)' in fz.internal(7)[0]


def test_tickets_stored_before_this_feature_still_load():
    old = json.dumps({"triage": {"category": "bug", "priority": "P3", "team": "engineering", "confidence": 0.6,
                                 "rationale": "old", "source": "rules"}, "articles": [], "draft_reply": "Hi"})
    state.conn.execute("INSERT INTO tickets (created_at, subject, body, customer, suggestion) VALUES (?,?,?,?,?)",
                      ("2026-10-01T09:00:00+00:00", "Old ticket", "from an older version", "Ana", old))
    state.conn.commit()
    c = TestClient(main.app)
    t = c.get("/tickets").json()[0]
    assert t["suggestion"]["triage"].get("sentiment") is None and t["history"]["count"] == 0


# ---------------- Portuguese tickets are triaged and find their article without the model ----------------
@pytest.mark.parametrize("subject,body,category,article", [
    ("Exportação falha", "Não consigo exportar o relatório mensal para CSV.", Category.BUG, "export-report"),
    ("Fatura em falta", "Preciso da fatura de setembro, por favor.", Category.BILLING, "invoices"),
    ("Não consigo fazer login", "Esqueci a palavra-passe e o link de reset não chega.", Category.ACCESS,
     "reset-password"),
    ("A plataforma está em baixo", "Ninguém da equipa consegue aceder, está indisponível desde as 9h.", Category.BUG,
     "service-outage"),
    ("Erro ao guardar contacto", "A página bloqueou e deu erro ao gravar o contacto.", Category.BUG, "save-errors"),
])
def test_portuguese_tickets_are_classified_and_matched(subject, body, category, article):
    t = classify_rules(subject, body)
    assert t.category is category and t.language == "pt"
    assert state.kb.search(f"{subject} {body}")[0]["id"] == article


def test_the_portuguese_outage_is_p1():
    t = classify_rules("A plataforma está em baixo", "Ninguém consegue aceder, está indisponível.")
    assert t.priority is Priority.P1

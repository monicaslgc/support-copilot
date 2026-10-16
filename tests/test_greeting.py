"""The standard reply greets a person by name, and nobody by a placeholder or an email address."""
import pytest

from support_copilot.drafting import _template, greeting_name
from support_copilot.models import Category, Priority, Sentiment, Triage


def triage(language="en"):
    return Triage(category=Category.BILLING, priority=Priority.P3, team="finance", confidence=0.6, rationale="r",
                  source="rules", sentiment=Sentiment.NEUTRAL, language=language)


@pytest.mark.parametrize("customer,name", [
    ("Rui Costa <rui@example.com>", "Rui Costa"),       # what Zammad sends in article.from
    ('"Ana Silva" <ana@example.com>', "Ana Silva"),
    ("Sam Carter", "Sam Carter"),
    ("  Ana  ", "Ana"),
    ("unknown", ""), ("Unknown", ""), ("", ""),          # no customer is not a customer called "unknown"
    ("rui@example.com", ""),                             # a bare address is not what anyone is called
])
def test_who_is_greeted(customer, name):
    assert greeting_name(customer) == name


@pytest.mark.parametrize("language,named,nameless", [
    ("en", "Hi Rui,", "Hi,"), ("pt", "Olá Rui,", "Olá,"),
    ("es", "Hola Rui,", "Hola,"), ("fr", "Bonjour Rui,", "Bonjour,"),
])
def test_the_greeting_in_every_language_with_and_without_a_name(language, named, nameless):
    assert _template("Rui <rui@example.com>", triage(language), []).splitlines()[0] == named
    assert _template("unknown", triage(language), []).splitlines()[0] == nameless
    assert "unknown" not in _template("unknown", triage(language), []).lower()
    assert "{name}" not in _template("", triage(language), [])

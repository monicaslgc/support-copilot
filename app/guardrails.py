"""Safety checks around the model: what we send to it and what it writes back.

- `redact` masks private details (emails, cards, IBANs, phone numbers, passwords and keys) in ticket
  text before it goes to the model. The stored ticket keeps the original: the human reviewer needs it.
- `find_commitments` flags promises a person must decide on: refunds, credits, guarantees, money,
  deadlines. "hard" ones are never acceptable in a model-written draft; "soft" ones are shown as a
  warning because they may be copied correctly from the knowledge base.

Both are rules, not a model, so they are predictable and testable. They are a safety net with known gaps
(see the README), not a guarantee.
"""
import re

# ------------------------------------------------------------------ redaction
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|palavra-passe|senha|contraseña|mot de passe|token|api[ _-]?key|secret)\b"
    r"(\s*(?:is|é|es|est|:|=)\s*)(\S+)")
_KEY_LIKE = re.compile(r"\b(?:sk|pk|ghp|xox[bap])[-_][A-Za-z0-9_-]{16,}\b|\bBearer\s+[A-Za-z0-9._-]{20,}\b"
                       r"|\b[A-Fa-f0-9]{32,}\b")
_PLACEHOLDER = re.compile(r"\[(?:EMAIL|PHONE|CARD|IBAN|SECRET)\]")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){13,19}(?![\w-])")
_PHONE = re.compile(r"(?<![\w+])(?:\+|00)\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?![\w])"
                    r"|(?<!\d)9[1236]\d[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = re.sub(r"\s", "", raw)
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return the text with private details masked, and how many of each kind were masked.

    Cards are only masked when the Luhn check passes and IBANs when the mod-97 check passes, so order
    numbers and other long digit strings are left alone.
    """
    counts: dict[str, int] = {}

    def mask(kind: str, placeholder: str, valid=lambda m: True):
        def sub(m: re.Match) -> str:
            if not valid(m):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return placeholder
        return sub

    def secret_value(m: re.Match) -> str:
        if _PLACEHOLDER.fullmatch(m.group(3)):  # already masked: do not count it again
            return m.group(0)
        counts["secret"] = counts.get("secret", 0) + 1
        return f"{m.group(1)}{m.group(2)}[SECRET]"

    text = _SECRET_VALUE.sub(secret_value, text)
    text = _KEY_LIKE.sub(mask("secret", "[SECRET]"), text)
    text = _EMAIL.sub(mask("email", "[EMAIL]"), text)
    text = _IBAN.sub(mask("iban", "[IBAN]", lambda m: _iban_ok(m.group(0))), text)
    text = _CARD.sub(mask("card", "[CARD]",
                          lambda m: 13 <= len(re.sub(r"\D", "", m.group(0))) <= 19
                          and _luhn_ok(re.sub(r"\D", "", m.group(0)))), text)
    text = _PHONE.sub(mask("phone", "[PHONE]"), text)
    return text, counts


def redact_text(text: str) -> str:
    return redact(text)[0]


def describe(counts: dict[str, int]) -> str:
    """"1 email and 2 phone numbers", for the reviewer."""
    names = {"email": ("email", "emails"), "card": ("card number", "card numbers"), "iban": ("IBAN", "IBANs"),
             "phone": ("phone number", "phone numbers"), "secret": ("password or key", "passwords or keys")}
    parts = [f"{n} {names[k][0 if n == 1 else 1]}" for k, n in counts.items() if n]
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else ""


# ------------------------------------------------------------------ promises
_RULES = [
    # (kind, severity, regex)
    ("refund", "hard", r"\b(?:we|i)(?:'ll|'ve|\s+will|\s+have|\s+can)\s+(?:\w+\s+)?(?:refund|credit|reimburse)\w*"),
    ("refund", "hard", r"\byou(?:'ll|\s+will)\s+(?:\w+\s+)?(?:be\s+)?(?:refunded|credited|reimbursed"
                       r"|receive\s+(?:a\s+)?(?:full\s+)?(?:refund|credit|compensation))"),
    ("refund", "hard", r"\b(?:full|complete|total)\s+refund\b"),
    ("refund", "hard", r"\b(?:refund|credit)\s+(?:has\s+been|was|will\s+be)\s+(?:issued|processed|applied|sent)\b"),
    ("refund", "hard", r"\b(?:vamos|iremos|vou|irei|já)\s+(?:\w+\s+)?(?:reembolsar|devolver|creditar|compensar)\w*"),
    ("refund", "hard", r"\b(?:foi|será|serão)\s+(?:\w+\s+)?(?:reembolsad|devolvid|creditad)\w*"),
    ("refund", "hard", r"\breembolso\s+(?:total|integral|completo)\b"),
    ("refund", "hard", r"\bvai\s+receber\s+(?:um\s+)?(?:reembolso|crédito)\b"),
    ("refund", "hard", r"\b(?:reembolsaremos|devolveremos|acreditaremos)\w*"),
    ("refund", "hard", r"\bnous\s+(?:vous\s+)?rembourserons\b|\bremboursement\s+(?:total|intégral)\b"),
    ("guarantee", "hard", r"\b(?:we|i)\s+guarantee\b|\bgarantimos\b|\bgarantizamos\b|\bnous\s+garantissons\b"
                          r"|\b100\s?%\s+(?:sure|certain|safe|secure|guaranteed)\b"),
    ("benefit", "hard", r"\bfree\s+of\s+charge\b|\bfor\s+free\b|\bcompensation\b|\bdiscount\b|\bcoupon\b"
                        r"|\bgrátis\b|\bdesconto\b|\bcompensaç\w+|\bcompensación\b|\bgratuit\w*"),
    # an amount ends on a digit, so the full stop that ends the sentence is not part of it
    ("money", "soft", r"[€$£]\s?\d(?:[\d.,]*\d)?|\b\d(?:[\d.,]*\d)?\s?(?:€|£|\$|eur\b|euros?\b|usd\b|dollars?\b)"),
    ("deadline", "soft", r"\bwithin\s+(?:the\s+next\s+)?\d+(?:\s?-\s?\d+)?"
                         r"\s+(?:minute|hour|day|business\s+day|week)s?\b"
                         r"|\bem\s+(?:até\s+)?\d+(?:\s?-\s?\d+)?\s+(?:minuto|hora|dia|dias\s+úteis|semana)s?\b"
                         r"|\bby\s+(?:tomorrow|tonight|end\s+of\s+(?:the\s+)?day|monday|tuesday|wednesday|thursday"
                         r"|friday)\b|\b(?:até\s+amanhã|ainda\s+hoje|hoje\s+mesmo)\b"),
]
_COMPILED = [(kind, sev, re.compile(rx, re.IGNORECASE)) for kind, sev, rx in _RULES]


def find_commitments(text: str) -> list[dict]:
    """Promises in `text` a person should decide on: [{"kind", "severity", "text"}], each phrase once."""
    found, seen = [], set()
    for kind, severity, rx in _COMPILED:
        for m in rx.finditer(text):
            phrase = " ".join(m.group(0).split())
            if (kind, phrase.lower()) not in seen:
                seen.add((kind, phrase.lower()))
                found.append({"kind": kind, "severity": severity, "text": phrase})
    return found


def hard_commitments(flags: list[dict]) -> list[dict]:
    return [f for f in flags if f["severity"] == "hard"]


def explain(flags: list[dict]) -> str:
    """One line for a note or a banner: 'promises a refund ("we will refund"), mentions an amount ("€49")'."""
    words = {"refund": "promises a refund or credit", "guarantee": "gives a guarantee",
             "benefit": "offers compensation or something free", "money": "mentions an amount",
             "deadline": "commits to a deadline"}
    return ", ".join(f'{words[f["kind"]]} ("{f["text"]}")' for f in flags)

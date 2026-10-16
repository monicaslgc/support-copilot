"""Language and sentiment detection with plain rules.

This is the offline baseline and the fallback when the model's answer is missing or malformed.
It deliberately claims little: sentiment is only ever "negative" or "neutral" (a model may say
"positive"), and language is "und" (undetermined) rather than a guess.
"""
import re

# A handful of very common words per language. Words shared by several languages are left out
# of the lists where they would only add noise.
_STOPWORDS = {
    "en": "the and is are with for not can you my our this that have has from was we i it to of in on be want how",
    "pt": "não nao para com uma que é está estou meu minha por em já foi consigo preciso obrigado olá os as do da "
          "ao aos ou isto isso tem são ainda muito deu pelo pela dos nos nas ser",
    "es": "el los las del que para con una es está estoy mi por necesito puedo hola gracias pero muy",
    "fr": "le les des que pas pour avec une est mon ma je ne bonjour merci mais très suis",
    "it": "il gli di che non per con una è mio sono ciao grazie ma molto sto",
}
_LANG_WORDS = {lang: frozenset(words.split()) for lang, words in _STOPWORDS.items()}


def detect_language(text: str) -> str:
    """ISO 639-1 code of the ticket's language (en, pt, es, fr, it), or "und" when it is not clear."""
    words = re.findall(r"\w+", text.lower())
    scores = sorted(((sum(w in vocab for w in words), lang) for lang, vocab in _LANG_WORDS.items()), reverse=True)
    (best, lang), (second, _) = scores[0], scores[1]
    return lang if best >= 2 and best > second or best >= 1 and second == 0 else "und"


# weight 2: clearly upset on its own. weight 1: only counts together with something else
# ("how do I cancel my subscription" is a neutral question, "cancel everything, this is ridiculous" is not).
_STRONG = (
    r"unacceptable|ridiculous|furious|angry|terrible|awful|worst|useless|disgusted|fed up|incompetent|never again|"
    r"waste of (?:time|money)|inaceit\w+|rid[ií]cul\w+|furios\w+|p[eé]ssim\w+|horr[ií]vel|vergonha|in[uú]til|"
    r"incompet\w+|nunca mais|inaceptable|pésimo|vergüenza|inacceptable|furieux|catastrophique|honte|"
    r"inaccettabile|vergognoso"
)
_WEAK = (
    r"cancel\w*|manager|complain\w*|disappointed|frustrat\w*|still (?:not|no)|again|third time|exijo|reclama\w*|"
    r"cancelar|continua sem|outra vez|otra vez|annuler|encore|d[eé]cu\w*|annullare"
)


def rule_sentiment(text: str) -> str:
    """"negative" when the customer clearly sounds upset, else "neutral"."""
    low = text.lower()
    # a weak word counts once however often it is repeated: "Cancel" in the subject and "cancel" in the body
    # is one signal, not two
    weak = {m.group(0) for m in re.finditer(rf"\b(?:{_WEAK})\b", low)}
    score = 2 * len(re.findall(rf"\b(?:{_STRONG})\b", low)) + len(weak)
    letters = [c for c in text if c.isalpha()]
    if "!!" in text:
        score += 1
    if len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) > 0.6:  # SHOUTING
        score += 1
    return "negative" if score >= 2 else "neutral"

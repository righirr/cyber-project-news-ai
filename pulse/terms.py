"""Trending words and two-word expressions for the word cloud."""
import re
from collections import Counter

STOPWORDS = set('''
a about above across after again against all almost also am amid among an and another any are
around as at away back be because been before being below between big both but by can can't
could day days did do does doing don't done down during each even every few first for from further
get gets got had has have having he her here hers him his how however i if in including inside into
is it it's its itself just last later less like made make makes many may me might more most much
must my need needs never new news next no nor not now of off often on once one only or other our
out over own per put really report reports said same say says see she should since so some still such
take than that that's the their them then there these they this those though three through to today
too top two under until up upon us use used uses using very via vs want was way we week weeks well
were what what's when where whether which while who whom why will with within without would year
years yet you your
headline publisher published abstract feed item page expose description details full open source
exposed exposing company companies people new data time according
security cyber cybersecurity threat threats attack attacks attackers attacker systems months
targeting targets target organizations users business businesses warns warning
requires multiple legitimate customers code remote
'''.split())
_WORD = re.compile(r"[a-z][a-z0-9'’-]*[a-z0-9]|[a-z]")


def _tokens(text):
    return [w.strip("'’-") for w in _WORD.findall(text.lower())]


def _useful(word):
    return len(word) >= 3 and word not in STOPWORDS and not word.isdigit()


def trending_terms(docs, limit=28):
    """docs: iterable of (title, summary). Titles count double."""
    words, pairs = Counter(), Counter()
    for title, summary in docs:
        title_tokens = _tokens(title)
        for weight, tokens in ((2, title_tokens), (1, _tokens(summary or ''))):
            for w in tokens:
                if _useful(w):
                    words[w] += weight
        for a, b in zip(title_tokens, title_tokens[1:]):
            if _useful(a) and _useful(b):
                pairs[f'{a} {b}'] += 2
    scored = Counter({w: c for w, c in words.items() if c >= 3})
    for pair, count in pairs.items():
        if count >= 4:
            scored[pair] = count * 1.5
    # Drop single words that are almost always seen inside a stronger expression.
    for pair in [p for p in scored if ' ' in p]:
        for part in pair.split():
            if part in scored and scored[part] <= scored[pair]:
                del scored[part]
    return [{'term': t, 'score': round(s, 1)} for t, s in scored.most_common(limit)]

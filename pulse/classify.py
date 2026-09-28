"""Keyword-based topic classification and security-relevance filtering.

Used when Claude is not configured, and as the first pass when it is.
Title matches weigh three times as much as body matches.
"""
import re

TOPICS = ['AI Security', 'Vulnerability', 'Threat Intel', 'Supply Chain', 'SecOps']
DEFAULT_TOPIC = 'Threat Intel'

_PATTERNS = {
    'AI Security': [
        r'\bai\b', r'\bartificial intelligence\b', r'\bllms?\b', r'\bgen ?ai\b', r'\bchatgpt\b',
        r'\bopenai\b', r'\banthropic\b', r'\bclaude\b', r'\bgemini\b', r'\bcopilot\b',
        r'\bprompt injection\b', r'\bai agents?\b', r'\bagentic\b', r'\bmachine learning\b',
        r'\bdeepfakes?\b', r'\bchatbots?\b', r'\bmcp\b', r'\bmodel context protocol\b',
    ],
    'Supply Chain': [
        r'\bsupply[- ]chain\b', r'\bnpm\b', r'\bpypi\b', r'\bmalicious packages?\b',
        r'\bopen[- ]source packages?\b', r'\bdependenc(?:y|ies)\b', r'\bgithub\b', r'\bci/cd\b',
        r'\b(?:browser|vs ?code|chrome) extensions?\b', r'\bsdks?\b', r'\bthird[- ]party\b',
        r'\btrojani[sz]ed\b', r'\bsoftware updates?\b',
    ],
    'Vulnerability': [
        r'\bvulnerabilit(?:y|ies)\b', r'\bzero[- ]days?\b', r'\bcve-\d{4}-\d+', r'\bcvss\b',
        r'\bflaws?\b', r'\bexploit(?:s|ed|ation|ing)?\b', r'\bpatch(?:es|ed)?\b', r'\brce\b',
        r'\bremote code execution\b', r'\bprivilege escalation\b', r'\bbugs?\b',
        r'\bsecurity updates?\b', r'\bunpatched\b', r'\bkev\b',
    ],
    'SecOps': [
        r'\bsoc\b', r'\bsiem\b', r'\bsoar\b', r'\bxdr\b', r'\bedr\b', r'\bincident response\b',
        r'\bdetection engineering\b', r'\bthreat hunting\b', r'\bidentity\b', r'\bzero trust\b',
        r'\bmfa\b', r'\bcompliance\b', r'\bcisos?\b', r'\bsecurity operations\b', r'\bsase\b',
        r'\bsecurity teams?\b', r'\bresilience\b',
    ],
    'Threat Intel': [
        r'\bransomware\b', r'\bmalware\b', r'\bapt ?\d*\b', r'\bthreat actors?\b', r'\bbotnets?\b',
        r'\bphishing\b', r'\b\w*stealers?\b', r'\btrojans?\b', r'\bbackdoors?\b', r'\bespionage\b',
        r'\bhackers?\b', r'\bcampaigns?\b', r'\bextortion\b', r'\brats?\b', r'\bnation[- ]state\b',
        r'\bcybercrim\w*', r'\bspyware\b', r'\bloaders?\b', r'\bc2\b',
    ],
}
_COMPILED = {topic: [re.compile(p, re.I) for p in pats] for topic, pats in _PATTERNS.items()}
# Tie-break order: the more specific topics win over the generic Threat Intel bucket.
_PRIORITY = ['AI Security', 'Supply Chain', 'Vulnerability', 'SecOps', 'Threat Intel']

_SECURITY = re.compile(
    r'\b(?:security|secure|cyber\w*|hack\w*|breach\w*|attack\w*|privacy|surveillance|scam\w*|'
    r'fraud\w*|encrypt\w*|passwords?|credentials?|leak\w*|infosec|threats?|vulnerab\w*|malware|'
    r'ransomware|phish\w*|exploit\w*|zero[- ]day|cve-\d{4}|patch\w*|spyware|botnet\w*|backdoor\w*|'
    r'apt\d*|espionage|nation[- ]state|stealer\w*|trojan\w*|extort\w*|ddos|firewall\w*|'
    r'authenticat\w*|deepfakes?|prompt injection|sanction\w*|arrest\w*|indict\w*|cisa|nsa|fbi|'
    r'evasion|evad\w*|rooted|root access|defen[cs]es?|theft|stol\w*|steal\w*|sandbox\w*|'
    r'access controls?|permissions?|compromis\w*|intrusion\w*|takeover|hijack\w*|jailbreak\w*|'
    r'owasp|insider|spy\w*|forensic\w*|accounts? takeover|data loss|exfiltrat\w*)\b',
    re.I)


def _score(topic, title, body):
    return sum(3 * len(p.findall(title)) + len(p.findall(body)) for p in _COMPILED[topic])


def classify(title, body=''):
    scores = {topic: _score(topic, title, body) for topic in TOPICS}
    best = max(scores.values())
    if best == 0:
        return DEFAULT_TOPIC
    return next(topic for topic in _PRIORITY if scores[topic] == best)


def is_security_relevant(title, body=''):
    body = body[:1500]
    if _SECURITY.search(title) or _SECURITY.search(body):
        return True
    return any(p.search(title) or p.search(body) for pats in _COMPILED.values() for p in pats)

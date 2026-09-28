"""Deterministic lead briefing, used when Claude is not configured."""
import re
from collections import Counter

from .store import now_iso
from .terms import trending_terms

_EXPLOITED = re.compile(r'\b(actively exploited|exploited|in the wild|zero[- ]day|under attack|cve-\d{4}-\d+)', re.I)


def computed_briefing(articles):
    if not articles:
        return None
    topics = Counter(a['topic'] for a in articles)
    top_topic, top_count = topics.most_common(1)[0]
    in_topic = [a for a in articles if a['topic'] == top_topic]
    lead_article = next((a for a in in_topic if a['summary_kind'] != 'headline'), in_topic[0])
    terms = [t['term'] for t in trending_terms(((a['title'], a['summary']) for a in articles), limit=4)]
    exploited = [a for a in articles if _EXPLOITED.search(a['title'] + ' ' + a['summary'])]
    if exploited:
        action = {'title': f'Triage {len(exploited)} exploitation report{"s" if len(exploited) != 1 else ""}',
                  'text': 'Confirm exposure and patch status first for: '
                          + '; '.join(a['title'] for a in exploited[:2]) + '.',
                  'url': exploited[0]['url']}
    else:
        action = {'title': 'No active exploitation in recent reporting',
                  'text': 'Use the quiet window to review detections for the leading topic, '
                          f'{top_topic}, and validate patch cadence.'}
    return {
        'kind': 'computed',
        'generated_at': now_iso(),
        'lead': {'title': lead_article['title'],
                 'text': f'{top_topic} leads recent reporting with {top_count} of {len(articles)} stories. '
                         f'Latest from {lead_article["source_name"]}.',
                 'url': lead_article['url']},
        'watch': {'title': f'“{terms[0]}” is trending' if terms else 'Not enough reporting yet',
                  'text': ('Most-mentioned expressions across recent stories: ' + ', '.join(terms) + '.')
                  if terms else 'Collect more sources to surface trends.'},
        'action': action,
    }

import unittest

from pulse.classify import classify, is_security_relevant
from pulse.feeds import (discover_feeds, feed_candidates, google_news_url, parse_date, parse_feed, site_url,
                         strip_publisher_suffix)
from pulse.terms import trending_terms
from pulse.text import clean, extractive_summary, headline_summary, page_excerpt

from tests.fixtures import ATOM, HTML_WITH_FEED, LONG, rss


class FeedTests(unittest.TestCase):
    def test_rss_items(self):
        entries = parse_feed(rss([('Title one', 'https://ex.com/1', 1, LONG)]), 'https://ex.com/feed')
        self.assertEqual(entries[0]['title'], 'Title one')
        self.assertEqual(entries[0]['link'], 'https://ex.com/1')
        self.assertIsNotNone(entries[0]['published'])

    def test_atom_prefers_alternate_link_and_resolves_relative_urls(self):
        entry = parse_feed(ATOM, 'https://blog.example.com/feed.xml')[0]
        self.assertEqual(entry['title'], 'Atom & Friends: New Botnet Targets Routers')
        self.assertEqual(entry['link'], 'https://blog.example.com/p/1')

    def test_rejects_non_feeds(self):
        for body in [b'<html><body>nope</body></html>', b'not xml at all', b'<?xml version="1.0"?><note/>']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_feed(body, 'https://ex.com/')

    def test_non_http_links_are_dropped(self):
        entry = parse_feed(rss([('X', 'javascript:alert(1)', 1, LONG)]), 'https://ex.com/')[0]
        self.assertEqual(entry['link'], '')

    def test_discovers_advertised_feed(self):
        self.assertEqual(discover_feeds(HTML_WITH_FEED, 'https://ex.com/blog/'), ['https://ex.com/custom-feed.xml'])

    def test_feed_candidates_and_site_url(self):
        self.assertEqual(site_url('Example.com/blog'), 'https://example.com/blog')
        self.assertEqual(feed_candidates('https://example.com/blog/')[0], 'https://example.com/blog/feed/')
        self.assertIn('site%3Aexample.com%2Fblog', google_news_url('https://example.com/blog/', 3))

    def test_dates(self):
        self.assertEqual(parse_date('Tue, 10 Jun 2025 04:00:00 GMT').isoformat(), '2025-06-10T04:00:00+00:00')
        self.assertEqual(parse_date('2025-06-10T04:00:00Z').hour, 4)
        self.assertIsNone(parse_date('yesterday-ish'))

    def test_google_suffix(self):
        self.assertEqual(strip_publisher_suffix('Big breach - Dark Reading', 'Dark Reading'), 'Big breach')


class TextTests(unittest.TestCase):
    def test_clean_strips_markup_and_control_chars(self):
        self.assertEqual(clean('<p>Hi&nbsp;<b>there</b>\x02</p>'), 'Hi there')

    def test_extractive_summary(self):
        summary = extractive_summary('Gateway flaw exploited', LONG + ' More text follows here for context.')
        self.assertTrue(summary.startswith('Attackers are actively exploiting'))
        self.assertLessEqual(len(summary), 421)

    def test_summary_drops_wordpress_boilerplate_and_headings(self):
        raw = 'Introduction ' + LONG + ' The post Gateway flaw appeared first on Example Blog.'
        summary = extractive_summary('Gateway flaw', raw)
        self.assertNotIn('appeared first', summary)
        self.assertTrue(summary.startswith('Attackers'))

    def test_headline_only_description_is_rejected(self):
        self.assertIsNone(extractive_summary('Big breach at Acme Corp', 'Big breach at Acme Corp  Dark Reading'))

    def test_headline_fallback_is_honest(self):
        self.assertIn('without an abstract', headline_summary('X happened', 'Blog'))

    def test_page_excerpt(self):
        page = (b'<html><head><meta property="og:description" content="' + LONG.encode() + b'"></head>'
                b'<body><nav><p>' + b'menu ' * 30 + b'</p></nav><p>' + LONG.encode() + b'</p></body></html>')
        description, body = page_excerpt(page)
        self.assertEqual(description, LONG)
        self.assertNotIn('menu', body)


class ClassifyTests(unittest.TestCase):
    def test_topics(self):
        cases = {
            'Critical RCE flaw in Citrix NetScaler exploited as zero-day': 'Vulnerability',
            'Malicious npm packages steal developer credentials': 'Supply Chain',
            'Prompt injection lets attackers hijack AI agents': 'AI Security',
            'Lazarus hackers deploy new backdoor in espionage campaign': 'Threat Intel',
            'Why SOC teams are rethinking SIEM detection engineering': 'SecOps',
        }
        for title, topic in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify(title), topic)

    def test_no_false_ai_matches(self):
        self.assertNotEqual(classify('Attackers prompted users to reset passwords'), 'AI Security')
        self.assertNotEqual(classify('Chrome changes user agent string'), 'AI Security')

    def test_relevance(self):
        self.assertTrue(is_security_relevant('EDR Evasion Stack Helps Process Injection Slip Past Defenses'))
        self.assertTrue(is_security_relevant('Zero Trust for AI Agents Starts With Fixing Zero Visibility'))
        self.assertFalse(is_security_relevant('Best Party Speakers (2026): JBL, Sony, Marshall'))
        self.assertFalse(is_security_relevant('What’s the Best Pet DNA Test? We Tested the Most Popular Ones'))


class TermsTests(unittest.TestCase):
    def test_no_stopwords_and_phrases_win(self):
        docs = [('Oracle PeopleSoft flaw exploited', 'The attackers used the flaw.')] * 3
        terms = [t['term'] for t in trending_terms(docs)]
        self.assertIn('oracle peoplesoft', terms)
        for stop in ('the', 'and', 'for', 'used'):
            self.assertNotIn(stop, terms)


if __name__ == '__main__':
    unittest.main()

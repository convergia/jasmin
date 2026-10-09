"""Regression tests for update detection and duplicate suppression."""
import copy
import unittest
import urllib.parse
from unittest.mock import patch
from types import SimpleNamespace
import upstream_watch as watch


class FakeGitHub:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.writes = []

    def pages(self, path):
        return self.issues

    def request(self, path, data=None):
        self.writes.append(data)
        self.issues.append(dict(data, number=len(self.issues) + 1, state='open'))
        return {'html_url': 'https://github.com/convergia/jasmin/issues/1'}


CONFIG = {'upstream': 'jookies/jasmin', 'tracking_label': 'upstream-update'}


class MonitorTests(unittest.TestCase):
    def test_cli_transport_uses_utf8_and_json_stdin(self):
        with patch.object(watch.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='{"title":"\\u201d"}')) as run:
            result = watch.GitHub().request('repos/convergia/jasmin/issues', {'title': 'Review'})
            self.assertEqual(result['title'], '\u201d')
            self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
            self.assertEqual(run.call_args.kwargs['input'], '{"title": "Review"}')

    def test_api_failure_is_not_no_updates(self):
        with patch.object(watch.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                watch.GitHub().request('repos/example/missing')

    def test_initial_baseline_is_recorded_once(self):
        api = FakeGitHub()
        state = {'head': 'a' * 40, 'tags': []}
        self.assertTrue(watch.review(api, CONFIG, 'convergia/jasmin', state, True)['published'])
        self.assertFalse(watch.review(api, CONFIG, 'convergia/jasmin', state, True)['changed'])
        self.assertEqual(len(api.writes), 1)

    def test_closed_review_is_not_recreated(self):
        api = FakeGitHub()
        state = {'head': 'a' * 40}
        watch.review(api, CONFIG, 'convergia/jasmin', state, True)
        api.issues[0]['state'] = 'closed'
        self.assertFalse(watch.review(api, CONFIG, 'convergia/jasmin', state, True)['changed'])

    def test_upstream_reversion_is_a_new_event(self):
        api = FakeGitHub()
        for head in ('a', 'b', 'a'):
            self.assertTrue(watch.review(api, CONFIG, 'convergia/jasmin', {'head': head}, True)['changed'])
        self.assertEqual(len(api.writes), 3)

    def test_tag_move_detected_without_new_head(self):
        api = FakeGitHub()
        old = {'head': 'a' * 40, 'tags': [{'name': 'v1', 'sha': 'b' * 40}]}
        watch.review(api, CONFIG, 'convergia/jasmin', old, True)
        new = copy.deepcopy(old)
        new['tags'][0]['sha'] = 'c' * 40
        result = watch.review(api, CONFIG, 'convergia/jasmin', new, True)
        self.assertEqual(result['categories'], ['tags'])

    def test_pr_merge_and_advisory_updates_detected(self):
        for category in ('pull_requests', 'issues', 'releases', 'advisories', 'head'):
            api = FakeGitHub()
            old = {'head': 'a' * 40, category: 'old'}
            watch.review(api, CONFIG, 'convergia/jasmin', old, True)
            new = dict(old, **{category: 'new'})
            self.assertEqual(watch.review(api, CONFIG, 'convergia/jasmin', new)['categories'], [category])
            self.assertEqual(len(api.writes), 1)

    def test_unrelated_issues_are_ignored(self):
        api = FakeGitHub([{'number': 9, 'body': 'Operator note'}])
        self.assertTrue(watch.review(api, CONFIG, 'convergia/jasmin', {'head': 'a'})['changed'])

    def test_invalid_saved_state_fails_closed(self):
        with self.assertRaises(ValueError):
            watch.parse_state(watch.MARKER + 'invalid\n-->')

    def test_fingerprint_ignores_dictionary_key_order(self):
        self.assertEqual(watch.digest({'a': 1, 'b': 2}), watch.digest({'b': 2, 'a': 1}))

    def test_pagination_reads_every_page(self):
        class Paged(watch.GitHub):
            def request(self, path, data=None):
                page = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['page'][0]
                return list(range(100)) if page == '1' else [100]
        self.assertEqual(len(Paged().pages('repos/example/tags')), 101)


if __name__ == '__main__':
    unittest.main()

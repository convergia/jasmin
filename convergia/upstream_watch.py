"""Detect upstream changes and open deduplicated internal review issues.

Never merges, fetches code for execution, changes branches or deploys.
Uses existing GitHub CLI authentication without reading token values.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.parse

MARKER = '<!-- jasmin-upstream-snapshot\n'


class GitHub:
    def request(self, path, data=None):
        command = ['gh', 'api', '--method', 'GET' if data is None else 'POST', path]
        if data is not None:
            command += ['--input', '-']
        response = subprocess.run(command, input=json.dumps(data) if data is not None else None,
            text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        if response.returncode:
            raise RuntimeError('GitHub CLI API request failed at ' + path)
        return json.loads(response.stdout)

    def pages(self, path):
        result = []
        for page in range(1, 101):
            separator = '&' if '?' in path else '?'
            batch = self.request(path + separator + 'per_page=100&page=' + str(page))
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise RuntimeError('Pagination limit reached; refusing an incomplete snapshot')


def snapshot(api, config):
    repo = config['upstream']
    prefix = 'repos/' + repo
    metadata = api.request(prefix)
    head = api.request(prefix + '/commits/' + urllib.parse.quote(metadata['default_branch'], safe=''))
    state = {'repository': repo, 'default_branch': metadata['default_branch'], 'head': head['sha'],
        'tags': sorted(({'name': item['name'], 'sha': item['commit']['sha']}
            for item in api.pages(prefix + '/tags')), key=lambda item: item['name']),
        'releases': sorted(({'id': item['id'], 'tag': item['tag_name'],
            'updated_at': item['updated_at'], 'draft': item['draft'], 'prerelease': item['prerelease']}
            for item in api.pages(prefix + '/releases')), key=lambda item: item['id']),
        'issues': {}, 'pull_requests': {},
        'advisories': sorted(({'ghsa_id': item['ghsa_id'], 'updated_at': item['updated_at']}
            for item in api.pages(prefix + '/security-advisories')), key=lambda item: item['ghsa_id'])}
    for number in config['watched_issues']:
        item = api.request(prefix + '/issues/' + str(number))
        state['issues'][str(number)] = {key: item.get(key) for key in ('state', 'updated_at', 'title')}
    for number in config['watched_pull_requests']:
        item = api.request(prefix + '/pulls/' + str(number))
        state['pull_requests'][str(number)] = {key: item.get(key) for key in ('state', 'updated_at', 'merged_at', 'title')}
        state['pull_requests'][str(number)]['head'] = item['head']['sha']
    return state


def digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def parse_state(body):
    if MARKER not in (body or ''):
        return None
    encoded = body.split(MARKER, 1)[1].split('\n-->', 1)[0]
    return json.loads(encoded)


def review(api, config, destination, state, publish=False):
    # Closed issues count too. Closing a reviewed update must not recreate it.
    issues = api.pages('repos/' + destination + '/issues?state=all&labels=' + config['tracking_label'])
    records = [(item, parse_state(item.get('body'))) for item in issues if not item.get('pull_request')]
    records = [(item, old) for item, old in records if old is not None]
    previous = max(records, key=lambda pair: pair[0]['number'])[1] if records else None
    if previous is not None and digest(previous) == digest(state):
        return {'changed': False, 'reason': 'Snapshot already recorded', 'fingerprint': digest(state)}
    changed = [key for key in state if previous is None or previous.get(key) != state[key]]
    title = ('Initial upstream baseline' if previous is None else 'Upstream changes require review')
    title += ': Jasmin ' + state['head'][:12] + ' [' + digest(state)[:12] + ']'
    body = ('Upstream: https://github.com/' + config['upstream'] + '\n\n'
        'Observed categories: ' + ', '.join(changed) + '.\n\n'
        'Compare upstream code with the Convergia maintenance branch, assess security and compatibility, '
        'and run isolated qualification before accepting changes. This issue does not authorize a deployment.\n\n'
        + MARKER + json.dumps(state, sort_keys=True) + '\n-->')
    result = {'changed': True, 'categories': changed, 'fingerprint': digest(state), 'published': False}
    if publish:
        issue = api.request('repos/' + destination + '/issues',
            {'title': title, 'body': body, 'labels': [config['tracking_label']]})
        result.update(published=True, issue_url=issue['html_url'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('convergia/upstream-observation.json'))
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name('upstream.json').read_text())
    destination = os.environ.get('GITHUB_REPOSITORY', 'convergia/jasmin')
    if args.publish and destination != 'convergia/jasmin':
        parser.error('Publishing is restricted to convergia/jasmin')
    api = GitHub()
    state = snapshot(api, config)
    result = review(api, config, destination, state, args.publish)
    args.output.write_text(json.dumps({'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'snapshot': state, 'result': result}, indent=2) + '\n', encoding='ascii')
    print(json.dumps(result))


if __name__ == '__main__':
    main()

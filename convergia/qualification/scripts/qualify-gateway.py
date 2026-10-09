"""Destructive reliability tests, restricted to a new private Docker project.

No host/provider/account options are accepted. See the qualification runbook.
Exit 0: all pass; 1: gateway qualification failed; 2: infrastructure blocked.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'tools/gateway-qualification/compose.yaml'
SCENARIOS = (
    'single-dlr',
    'http-multipart-gsm', 'http-multipart-ucs2',
    'mt-udh-0', 'mt-udh-8', 'mt-sar-0', 'mt-sar-8',
    'mo-udh-ordered-0', 'mo-udh-ordered-8', 'mo-sar-ordered-0', 'mo-sar-ordered-8',
    'mo-udh-lastfirst-0', 'mo-udh-lastfirst-8', 'mo-sar-lastfirst-0',
    'mo-udh-duplicate-0', 'mo-udh-missing-0',
    'queued-gateway-crash', 'queued-broker-crash', 'redis-dlr-crash',
    'upstream-disconnect', 'broker-reconnect', 'upstream-throttle', 'multipart-response-timeout', 'bounded-burst',
)


def validate_stack(config):
    """Reject host access, sockets, external networks or unexpected services."""
    if set(config['services']) != {'rabbitmq', 'redis', 'lab'}:
        raise ValueError('Unexpected qualification service')
    networks = config.get('networks', {})
    if set(networks) != {'qualification'} or not networks['qualification'].get('internal'):
        raise ValueError('Private internal network required')
    if networks['qualification'].get('external'):
        raise ValueError('External network forbidden')
    for service in config['services'].values():
        for forbidden in ('ports', 'volumes', 'network_mode', 'privileged', 'devices',
                          'pid', 'ipc', 'extra_hosts', 'external_links', 'cap_add', 'env_file'):
            if service.get(forbidden):
                raise ValueError('Forbidden qualification setting: ' + forbidden)
        if set(service.get('networks', {})) != {'qualification'}:
            raise ValueError('Unexpected service network')


def validate_docker_endpoint(endpoint):
    if not isinstance(endpoint, str) or not endpoint.startswith(('unix://', 'npipe://')):
        raise ValueError('A local Docker socket is required; remote TCP/SSH engines are forbidden')


def validate_output(output):
    output = output.resolve()
    if not output.is_relative_to((ROOT / 'archive').resolve()) or output.suffix != '.json':
        raise ValueError('Reports must be JSON files inside the repository archive directory')
    return output


class DockerLab:
    def __init__(self):
        self.project = 'smpp-qual-' + uuid.uuid4().hex[:12]
        self.command = ['docker', 'compose', '--project-name', self.project, '-f', str(COMPOSE)]
        # Ignore provider and SMPP settings, compose overrides and Docker host overrides.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('SMPP_', 'CMI_', 'COMPOSE_', 'DOCKER_HOST', 'DOCKER_CONTEXT'))}

    def run(self, *args, timeout=120, capture=True):
        return subprocess.run(self.command + list(args), cwd=ROOT, env=self.env,
            text=True, stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None, timeout=timeout, check=True)

    def fault(self, service):
        if service not in ('rabbitmq', 'redis'):
            raise ValueError('Only private broker/cache faults are supported')
        # SIGKILL retains the container data volume, unlike a fresh empty deployment.
        self.run('kill', '--signal', 'SIGKILL', service)
        self.run('up', '-d', '--wait', '--wait-timeout', '90', service)

    def preflight(self):
        inspected = subprocess.run(['docker', 'context', 'inspect'], env=self.env,
            text=True, capture_output=True, timeout=20, check=True)
        contexts = json.loads(inspected.stdout)
        if len(contexts) != 1:
            raise ValueError('Exactly one local Docker context is required')
        validate_docker_endpoint(contexts[0]['Endpoints']['docker']['Host'])

    def case(self, name):
        self.run('up', '-d', '--no-deps', '--force-recreate', 'lab')
        proc = subprocess.Popen(self.command + ['exec', '-T', 'lab', 'python', '-u',
            '/suite/qualification/case.py', name], cwd=ROOT, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        lines = queue.Queue()
        def reader():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + 180
        faults = []
        try:
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=min(1, max(.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    raise RuntimeError('Scenario process exited without a result')
                message = json.loads(line)
                if 'fault_request' in message:
                    service = message['fault_request']
                    self.fault(service)
                    faults.append(service)
                    proc.stdin.write(json.dumps({'fault_complete': service}) + '\n')
                    proc.stdin.flush()
                elif 'result' in message:
                    result = message['result']
                    if result.get('scenario') != name or result.get('status') not in ('PASS', 'FAIL', 'BLOCKED'):
                        raise ValueError('Invalid scenario result')
                    proc.wait(timeout=15)
                    if proc.returncode:
                        raise RuntimeError('Scenario process failed after result')
                    result['faults_injected'] = faults
                    return result
                else:
                    raise ValueError('Unexpected scenario protocol message')
            raise TimeoutError('Scenario exceeded 180 second deadline')
        except Exception as error:
            return {'scenario': name, 'status': 'BLOCKED', 'reason': type(error).__name__ + ': ' + str(error),
                    'faults_injected': faults}
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait(timeout=10)
            # Kill any remaining exec children before the next independent scenario.
            self.run('stop', '--timeout', '2', 'lab')


def exit_status(results):
    if not results or any(r['status'] == 'BLOCKED' for r in results):
        return 2
    return 1 if any(r['status'] == 'FAIL' for r in results) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', action='append', choices=SCENARIOS,
                        help='Run selected cases, repeatable; partial runs never qualify the whole suite')
    parser.add_argument('--output', type=Path, default=ROOT / 'archive/gateway-qualification/latest.json')
    args = parser.parse_args()
    try:
        args.output = validate_output(args.output)
    except ValueError as error:
        parser.error(str(error))
    selected = args.scenario or list(SCENARIOS)
    lab = DockerLab()
    report = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'project': lab.project, 'complete_suite': not args.scenario, 'results': [], 'cleanup': 'pending'}
    started = False
    code = 2
    try:
        lab.preflight()
        validate_stack(json.loads(lab.run('config', '--format', 'json').stdout))
        started = True
        print('Building and starting private qualification stack ' + lab.project, flush=True)
        lab.run('up', '--build', '-d', '--wait', '--wait-timeout', '120', timeout=900, capture=False)
        report['build'] = json.loads(lab.run('exec', '-T', 'lab', 'cat', '/suite/build.json').stdout)
        report['images'] = json.loads(lab.run('images', '--format', 'json').stdout)
        for name in selected:
            result = lab.case(name)
            report['results'].append(result)
            print(result['status'] + ' ' + name + ': ' + result.get('reason', 'assertions satisfied'), flush=True)
        code = exit_status(report['results'])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # Docker error strings can contain environment input; retain type and bounded generic context.
        report['infrastructure_error'] = type(error).__name__
        print('BLOCKED: isolated stack setup or controller failed (' + type(error).__name__ + ')', flush=True)
    finally:
        if started:
            try:
                lab.run('down', '--volumes', '--remove-orphans')
                report['cleanup'] = 'PASS'
            except (OSError, subprocess.SubprocessError):
                report['cleanup'] = 'BLOCKED'
                code = 2
        else:
            report['cleanup'] = 'not started'
        report['exit_code'] = code
        report['qualified'] = code == 0 and report['complete_suite']
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='ascii')
        print('Report: ' + str(args.output), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())

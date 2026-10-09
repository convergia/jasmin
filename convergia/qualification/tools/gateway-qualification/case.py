"""One real gateway scenario inside the private Docker lab. No live targets."""
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import pika
import redis
from lockfile import FileLock

QUEUES = ('DLRLookup-main', 'RouterPB_deliver_sm_all',
          'RouterPB_bill_request_submit_sm_resp_all', 'dlr_thrower',
          'submit.sm.labfake', 'deliver_sm_thrower', 'qualification.control')
spec = importlib.util.spec_from_file_location('console', '/suite/scripts/setup-cmi-bind-only.py')
console_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(console_module)


def wait(check, description, seconds=18):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.15)
    raise AssertionError('Deadline exceeded: ' + description)


def amqp_connection():
    # rabbitmq-diagnostics ping can succeed before AMQP listeners accept connections.
    deadline = time.monotonic() + 30
    while True:
        try:
            return pika.BlockingConnection(pika.ConnectionParameters('rabbitmq', socket_timeout=3))
        except pika.exceptions.AMQPConnectionError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Private AMQP broker did not become protocol-ready')
            time.sleep(.25)


class Lab:
    def __init__(self, name):
        self.name, self.process, self.peer, self.console = name, None, None, None
        self.evidence = {}
        self.env = dict(os.environ, SMPP_SYSTEM_ID='qualification', SMPP_PASSWORD=secrets.token_hex(4))
        self.jcli_username = 'qualification-admin'
        self.jcli_password = secrets.token_hex(8)
        self.redis = redis.Redis(host='redis', socket_timeout=3)
        self.redis.flushall()
        connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
        channel = connection.channel()
        for queue in QUEUES:
            channel.queue_delete(queue)
        if name == 'queued-broker-crash':
            # Establish the disk-retention control before provisioning the gateway.
            channel.queue_declare(queue='qualification.control', durable=True)
            channel.confirm_delivery()
            channel.basic_publish(exchange='', routing_key='qualification.control',
                body=b'confirmed-control', properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
        connection.close()
        # Only non-secret config is written. Synthetic fixture credentials stay in memory.
        config = '''
[smpp-server]
id = qualification
bind = 127.0.0.1
port = 2775
[jcli]
bind = 127.0.0.1
port = 8990
[http-api]
bind = 127.0.0.1
port = 1401
log_privacy = yes
access_log = /dev/null
[amqp-broker]
host = rabbitmq
connection_loss_retry_delay = 1
connection_failure_retry_delay = 1
[redis-client]
host = redis
[sm-listener]
log_privacy = yes
submit_retrial_delay_smppc_not_ready = 1
submit_error_retrial = {'ESME_RTHROTTLED': {'count': 3, 'delay': 1}}
'''
        config = config.replace('port = 8990\n', 'port = 8990\nadmin_username = ' + self.jcli_username +
            '\nadmin_password = ' + hashlib.md5(self.jcli_password.encode()).hexdigest() + '\n')
        Path('/etc/jasmin/jasmin.cfg').write_text(config)
        self.peer = subprocess.Popen(['node', '/suite/qualification/peer.js'], env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.rpc('start')
        self.log = open('/tmp/qualification-gateway.log', 'w')
        self.start_gateway()
        self.configure()
        self.rpc('connect')

    def rpc(self, op, **kwargs):
        self.peer.stdin.write(json.dumps(dict(op=op, **kwargs)) + '\n')
        self.peer.stdin.flush()
        line = self.peer.stdout.readline()
        if not line:
            raise RuntimeError('Private fixture exited')
        result = json.loads(line)
        if not result['ok']:
            raise AssertionError('Fixture: ' + result['error'])
        return result['result']

    def events(self, kind):
        return [e for e in self.rpc('events')['events'] if e['type'] == kind]

    def start_gateway(self):
        self.process = subprocess.Popen(['jasmind.py', '--enable-dlr-lookup', '--enable-dlr-thrower',
            '--enable-deliver-thrower', '--username', self.jcli_username, '--password', self.jcli_password],
            stdout=self.log, stderr=self.log)
        self.console = console_module.Console(self.jcli_username, self.jcli_password)

    def configure(self):
        c = self.console
        c.edit('group -a', ['gid qualification'], 'Successfully')
        c.edit('user -a', ['username qualification', 'password ' + self.env['SMPP_PASSWORD'],
               'gid qualification', 'uid qualification'], 'Successfully')
        c.edit('smppccm -a', ['cid labfake', 'host 127.0.0.1', 'port 2776',
            'username qualification', 'password ' + self.env['SMPP_PASSWORD'],
            'submit_throughput 0', 'res_to 2', 'con_loss_delay 1', 'con_fail_delay 1'], 'Successfully')
        c.command('smppccm -1 labfake')
        c.edit('mtrouter -a', ['type DefaultRoute', 'connector smppc(labfake)', 'rate 0'], 'Successfully')
        if self.name.startswith('mo-'):
            c.edit('httpccm -a', ['cid webhook', 'url http://127.0.0.1:2777/', 'method POST'], 'Successfully')
            connector = 'http(webhook)'
        else:
            connector = 'smpps(qualification)'
        c.edit('morouter -a', ['type DefaultRoute', 'connector ' + connector], 'Successfully')
        wait(lambda: 'BOUND_TRX' in c.command('smppccm -l'), 'upstream bind')

    def stop_connector(self):
        self.console.command('smppccm -0 labfake')
        wait(lambda: self.rpc('events')['upstream_binds'] == 0, 'connector stopped')

    def start_connector(self):
        self.console.command('smppccm -1 labfake')
        wait(lambda: self.rpc('events')['upstream_binds'] == 1, 'connector rebound')

    def submit(self, body, **kwargs):
        reply = self.rpc('submit', hex=body.hex(), **kwargs)
        assert reply['status'] == 0, 'Gateway did not accept submit: ' + str(reply['status'])
        assert reply.get('id'), 'Accepted submit has no message ID'
        return reply['id']

    def http_submit(self, body, coding=0):
        fields = {'username': 'qualification', 'password': self.env['SMPP_PASSWORD'],
                  'from': '15559990001', 'to': '15551230100', 'coding': str(coding),
                  'hex-content': body.hex()}
        request = urllib.request.Request('http://127.0.0.1:1401/send',
            data=urllib.parse.urlencode(fields).encode('ascii'))
        with urllib.request.urlopen(request, timeout=8) as response:
            answer = response.read().decode('ascii')
        assert answer.startswith('Success'), 'HTTP gateway did not accept multipart message'
        return answer

    def fault(self, service):
        assert service in ('rabbitmq', 'redis')
        print(json.dumps({'fault_request': service}), flush=True)
        reply = json.loads(sys.stdin.readline())
        if reply != {'fault_complete': service}:
            raise RuntimeError('Isolated fault controller failed')
        if service == 'rabbitmq':
            connection = amqp_connection()
            connection.close()
            self.evidence['broker_protocol_ready_after_restart'] = True

    def expect_mt(self, bodies, seconds=18):
        wait(lambda: len([e for e in self.events('mt') if e['outcome'] == 'accepted']) >= len(bodies),
             'all accepted MT reach upstream', seconds)
        time.sleep(1)
        observed = [e['hex'] for e in self.events('mt') if e['outcome'] == 'accepted']
        self.evidence.update(expected=len(bodies), observed=len(observed))
        assert collections.Counter(observed) == collections.Counter(b.hex() for b in bodies), \
            'Missing, duplicate or corrupted accepted MT'

    def close(self):
        if self.console:
            self.console.close()
        for proc in (self.process, self.peer):
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        if hasattr(self, 'log'):
            self.log.close()


def parts(method, coding, ref=71):
    # Extension-table characters and non-ASCII UCS2 across segment boundaries.
    text = ('A' * 148 + '^' + '{}' + 'end') if coding == 0 else ('\u00e9\u6f22' * 39 + 'end')
    body = (text.replace('^', '\x1b\x14').replace('{', '\x1b\x28').replace('}', '\x1b\x29').encode('ascii')
            if coding == 0 else text.encode('utf-16-be'))
    width = 153 if coding == 0 else 134
    # Avoid splitting a GSM escape or a UCS2 code unit.
    cut = width - 1 if coding == 0 and body[width - 1] == 27 else width
    chunks = [body[:cut], body[cut:]]
    output = []
    for seq, chunk in enumerate(chunks, 1):
        if method == 'udh':
            output.append((bytes([5, 0, 3, ref, 2, seq]) + chunk, {'esm': 64, 'coding': coding}))
        else:
            output.append((chunk, {'coding': coding, 'sar_ref': ref, 'sar_total': 2, 'sar_seq': seq}))
    return body, output


def scenario(lab, name):
    if name == 'single-dlr':
        accepted = lab.submit(b'baseline-dlr-correlation', dlr=True)
        wait(lambda: lab.events('mt'), 'upstream MT acknowledgement')
        wait(lambda: lab.redis.exists('dlr:' + accepted), 'DLR correlation saved')
        time.sleep(.5)
        lab.rpc('receipts')
        wait(lambda: lab.events('dlr'), 'baseline correlated DLR')
        time.sleep(1)
        dlrs = lab.events('dlr')
        assert len(dlrs) == 1 and dlrs[0]['receipt_id'] == accepted, 'Baseline DLR correlation failed'
    elif name.startswith('http-multipart-'):
        coding = 0 if name.endswith('gsm') else 8
        # Jasmin generates linked outbound PDUs, exercising native split/transaction code.
        text = ('GSM^{}' * 40) if coding == 0 else ('\u00e9\u6f22' * 80)
        body = (text.replace('^', '\x1b\x14').replace('{', '\x1b\x28').replace('}', '\x1b\x29').encode('ascii')
                if coding == 0 else text.encode('utf-16-be'))
        lab.http_submit(body, coding)
        width = 153 if coding == 0 else 134
        total = (len(body) + width - 1) // width
        wait(lambda: len(lab.events('mt')) >= total, 'generated multipart reaches upstream')
        time.sleep(1)
        events = lab.events('mt')
        assert len(events) == total, 'Generated part count incorrect'
        content = []
        reference = None
        for seq, event in enumerate(events, 1):
            raw = bytes.fromhex(event['hex'])
            assert event['coding'] == coding and event['esm'] & 64, 'Generated UDH/coding incorrect'
            assert raw[:3] == b'\x05\x00\x03' and raw[4:6] == bytes([total, seq]), 'Generated UDH sequence incorrect'
            reference = raw[3] if reference is None else reference
            assert reference == raw[3], 'Generated multipart reference changed'
            assert len(raw) <= (159 if coding == 0 else 140), 'Generated part exceeds SMS capacity'
            content.append(raw[6:])
        assert b''.join(content) == body, 'Generated multipart content corrupted'
        lab.evidence.update(parts=total, coding=coding, exact_content=True)
    elif name.startswith('mt-'):
        _, method, coding = name.split('-')
        _, segments = parts(method, int(coding))
        for body, opts in segments:
            lab.submit(body, **opts)
        lab.expect_mt([body for body, _ in segments])
        for event, (_, opts) in zip(lab.events('mt'), segments):
            assert event['coding'] == int(coding), 'Data coding changed'
            if method == 'sar':
                assert (event['sar_ref'], event['sar_total'], event['sar_seq']) == \
                       (opts['sar_ref'], 2, opts['sar_seq']), 'SAR metadata changed'
            else:
                assert event['esm'] & 64, 'UDH indicator lost'
    elif name.startswith('mo-'):
        _, method, order, coding = name.split('-')
        expected, segments = parts(method, int(coding))
        sequence = {'ordered': [0, 1], 'lastfirst': [1, 0], 'duplicate': [0, 0, 1], 'missing': [0]}[order]
        for index in sequence:
            body, opts = segments[index]
            reply = lab.rpc('inject', hex=body.hex(), **opts)
            assert reply['status'] == 0, 'MO segment rejected'
        if order == 'missing':
            time.sleep(3)
            assert not lab.events('http_mo'), 'Incomplete multipart MO was delivered'
            lab.evidence['incomplete_delivery_count'] = 0
        else:
            wait(lambda: lab.events('http_mo'), 'assembled HTTP MO')
            time.sleep(1)
            received = lab.events('http_mo')
            lab.evidence.update(expected=1, observed=len(received))
            assert len(received) == 1, 'Multipart MO delivered more than once'
            assert received[0]['hex'] == expected.hex(), 'Multipart MO content/order changed'
            assert received[0]['coding'] == int(coding), 'MO coding changed'
    elif name in ('queued-gateway-crash', 'queued-broker-crash'):
        lab.stop_connector()
        bodies = [('durability-%02d' % i).encode() for i in range(10)]
        for body in bodies:
            lab.submit(body)
        # No upstream delivery may precede the crash.
        assert not lab.events('mt')
        def count():
            connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
            value = connection.channel().queue_declare(queue='submit.sm.labfake', passive=True).method.message_count
            connection.close()
            return value
        wait(lambda: count() == 10, 'ten accepted messages queued')
        lab.evidence['queued_before_crash'] = count()
        if name == 'queued-broker-crash':
            connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
            channel = connection.channel()
            control = channel.queue_declare(queue='qualification.control', passive=True)
            if control.method.message_count != 1:
                raise RuntimeError('Durable restart control was not established')
            connection.close()
            lab.fault('rabbitmq')
            connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
            channel = connection.channel()
            channel.queue_declare(queue='qualification.control', passive=True)
            controls = []
            while True:
                method, _, body = channel.basic_get(queue='qualification.control', auto_ack=True)
                if method is None:
                    break
                controls.append(body)
            connection.close()
            if b'confirmed-control' not in controls:
                raise RuntimeError('Durable restart control missing, invalid crash test')
            lab.evidence['durable_restart_control_survived'] = True
            connection = pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'))
            try:
                survived = connection.channel().queue_declare(queue='submit.sm.labfake', passive=True)
                lab.evidence['queued_after_crash'] = survived.method.message_count
                lab.evidence['queue_survived_crash'] = True
            except pika.exceptions.ChannelClosedByBroker as error:
                if error.reply_code != 404:
                    raise
                lab.evidence.update(queued_after_crash=0, queue_survived_crash=False)
            finally:
                connection.close()
            # Require automatic broker recovery, without restarting Jasmin.
            time.sleep(3)
            lab.start_connector()
        else:
            # Persist operator config, then kill the gateway with messages queued.
            lab.console.command('persist')
            lab.console.close()
            lab.process.kill(); lab.process.wait(timeout=10)
            # The packaged daemon uses a filesystem lock that survives SIGKILL.
            # This durability case explicitly relaunches after confirming its child is dead.
            # Autonomous systemd/host recovery is a separate deployment contract.
            FileLock('/tmp/jasmind').break_lock()
            lab.evidence['dead_process_lock_removed'] = True
            lab.start_gateway()
            # The stored lab connector was stopped. Start it explicitly after recovery.
            lab.start_connector()
            lab.rpc('connect')
        lab.expect_mt(bodies, 25)
    elif name == 'redis-dlr-crash':
        lab.redis.set('qualification:restart-control', b'snapshot-control')
        lab.redis.save()
        accepted = lab.submit(b'redis-dlr-correlation', dlr=True)
        wait(lambda: lab.events('mt'), 'upstream MT acknowledgement')
        wait(lambda: lab.redis.exists('dlr:' + accepted), 'DLR correlation saved')
        lab.evidence['mapping_before_crash'] = True
        lab.fault('redis')
        if lab.redis.get('qualification:restart-control') != b'snapshot-control':
            raise RuntimeError('Redis snapshot control missing, invalid crash test')
        lab.evidence['snapshot_control_survived'] = True
        lab.evidence['mapping_after_crash'] = bool(lab.redis.exists('dlr:' + accepted))
        lab.rpc('receipts')
        wait(lambda: lab.events('dlr'), 'final DLR after Redis crash')
        time.sleep(1)
        dlrs = lab.events('dlr')
        assert len(dlrs) == 1 and dlrs[0]['receipt_id'] == accepted, 'Final DLR missing/duplicate/miscorrelated'
    elif name == 'upstream-disconnect':
        lab.rpc('disconnect')
        bodies = [b'reconnect-one', b'reconnect-two']
        for body in bodies:
            lab.submit(body)
        wait(lambda: lab.rpc('events')['upstream_binds'] == 1, 'automatic upstream rebind')
        lab.expect_mt(bodies)
    elif name == 'broker-reconnect':
        lab.fault('rabbitmq')
        time.sleep(3)
        # The front remains bound; broker reconnection must recover all routing consumers.
        body = b'progress-after-broker-reconnect'
        lab.submit(body)
        lab.expect_mt([body], 25)
    elif name == 'upstream-throttle':
        lab.rpc('mode', mode='accept', reject=1)
        body = b'retry-after-throttle'
        lab.submit(body)
        lab.expect_mt([body])
        assert len(lab.events('mt')) == 2, 'Expected one rejection then one success'
        assert lab.events('mt')[0]['outcome'] == 'rejected', 'Fault was not exercised'
    elif name == 'multipart-response-timeout':
        lab.rpc('mode', mode='drop')
        lab.http_submit(b'native-multipart-timeout-' * 20)
        wait(lambda: len(lab.events('mt')) >= 2, 'linked unanswered multipart reached upstream')
        time.sleep(4)  # beyond the isolated connector's two-second response timer
        before = len(lab.events('mt'))
        lab.rpc('mode', mode='accept')
        marker = b'consumer-progress-after-timeout'
        lab.submit(marker)
        wait(lambda: any(e['hex'] == marker.hex() and e['outcome'] == 'accepted' for e in lab.events('mt')[before:]),
             'consumer makes progress after multipart response timeout')
        lab.evidence['consumer_recovered'] = True
    elif name == 'bounded-burst':
        bodies = [('burst-%03d' % i).encode() for i in range(100)]
        for body in bodies:
            lab.submit(body)
        lab.expect_mt(bodies)
    else:
        raise RuntimeError('Unknown qualification scenario')


def main():
    name = sys.argv[1]
    lab = Lab.__new__(Lab)
    lab.process = lab.peer = lab.console = None
    lab.evidence = {}
    result = {'scenario': name, 'status': 'BLOCKED', 'evidence': lab.evidence}
    started = time.monotonic()
    try:
        lab.__init__(name)
        result['evidence'] = lab.evidence
        try:
            scenario(lab, name)
            result['status'] = 'PASS'
        except AssertionError as error:
            result.update(status='FAIL', reason=str(error))
    except Exception as error:
        result.update(status='BLOCKED', reason=type(error).__name__ + ': ' + str(error))
    finally:
        if lab.peer and lab.peer.poll() is None:
            try:
                observations = lab.rpc('events')['events']
                result['evidence']['event_counts'] = dict(collections.Counter(e['type'] for e in observations))
            except Exception:
                pass
        lab.close()
    result['seconds'] = round(time.monotonic() - started, 2)
    print(json.dumps({'result': result}), flush=True)


if __name__ == '__main__':
    main()

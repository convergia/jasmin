"""Offline safety/result regressions for the reliability controller."""
import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('qualification', Path(__file__).with_name('qualify-gateway.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def stack():
    return {'services': {name: {'networks': {'qualification': {}}} for name in ('rabbitmq', 'redis', 'lab')},
            'networks': {'qualification': {'internal': True}}}


class QualificationGuards(unittest.TestCase):
    def test_report_cannot_overwrite_secrets_or_source(self):
        for output in (module.ROOT / 'secrets.md', module.ROOT / 'scripts/qualify-gateway.py',
                       module.ROOT / 'archive/../accounts.md', module.ROOT / 'archive/report.txt'):
            with self.assertRaises(ValueError):
                module.validate_output(output)
        self.assertEqual(module.validate_output(module.ROOT / 'archive/result.json').suffix, '.json')

    def test_remote_docker_engines_rejected(self):
        for endpoint in ('tcp://remote:2376', 'ssh://production', None):
            with self.assertRaises(ValueError):
                module.validate_docker_endpoint(endpoint)
        for endpoint in ('unix:///var/run/docker.sock', 'npipe:////./pipe/dockerDesktopLinuxEngine'):
            module.validate_docker_endpoint(endpoint)

    def test_isolated_stack_allowed(self):
        module.validate_stack(stack())

    def test_host_access_rejected(self):
        for option, value in (('ports', ['2775:2775']), ('volumes', ['/var/run/docker.sock:/socket']),
                              ('network_mode', 'host'), ('privileged', True), ('pid', 'host'),
                              ('extra_hosts', ['provider:20.169.220.245']), ('env_file', ['secrets.md'])):
            with self.subTest(option=option):
                config = copy.deepcopy(stack())
                config['services']['lab'][option] = value
                with self.assertRaises(ValueError):
                    module.validate_stack(config)

    def test_external_network_rejected(self):
        for change in ({'internal': False}, {'internal': True, 'external': True}):
            config = stack()
            config['networks']['qualification'] = change
            with self.assertRaises(ValueError):
                module.validate_stack(config)

    def test_extra_service_rejected(self):
        config = stack()
        config['services']['live-provider'] = {}
        with self.assertRaises(ValueError):
            module.validate_stack(config)

    def test_failures_are_not_expected_successes(self):
        self.assertEqual(module.exit_status([{'status': 'PASS'}, {'status': 'FAIL'}]), 1)
        self.assertEqual(module.exit_status([{'status': 'PASS'}, {'status': 'BLOCKED'}]), 2)
        self.assertEqual(module.exit_status([]), 2)
        self.assertEqual(module.exit_status([{'status': 'PASS'}]), 0)

    def test_faults_cannot_target_gateway_or_live_resources(self):
        lab = module.DockerLab()
        for target in ('lab', 'smpp-lab', '20.169.220.245', '--all'):
            with self.assertRaises(ValueError):
                lab.fault(target)


if __name__ == '__main__':
    unittest.main()

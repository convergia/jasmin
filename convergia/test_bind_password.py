"""Offline checks for the widened bind password field. No network.

Run from the repository root with the pinned dependencies installed:
    python convergia/test_bind_password.py
"""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smpp.pdu import pdu_types
from smpp.pdu.operations import BindReceiver, BindTransceiver, BindTransmitter
from smpp.pdu.pdu_encoding import COctetStringEncoder, PDUEncoder

import jasmin.protocols.smpp  # noqa: F401  applies the widened field on import
from jasmin.protocols.smpp import bind_password


def roundtrip(kind, length):
    value = 'p' * length
    raw = PDUEncoder().encode(kind(seqNum=1, system_id='selftest', password=value, system_type=''))
    back = PDUEncoder().decode(io.BytesIO(raw)).params['password']
    return (back.decode() if isinstance(back, bytes) else back) == value


class BindPassword(unittest.TestCase):
    def tearDown(self):
        bind_password.apply()

    def test_standard_and_long_passwords_round_trip_on_every_bind_type(self):
        for kind in (BindTransceiver, BindTransmitter, BindReceiver):
            for length in (1, 8, 9, 10, bind_password.MAX_BIND_PASSWORD_LENGTH):
                self.assertTrue(roundtrip(kind, length), (kind.__name__, length))

    def test_limit_is_enforced_and_error_hides_the_value(self):
        with self.assertRaises(ValueError) as caught:
            roundtrip(BindTransceiver, bind_password.MAX_BIND_PASSWORD_LENGTH + 1)
        self.assertNotIn('pppp', str(caught.exception))

    def test_oversize_password_from_a_peer_is_refused_with_invalid_password(self):
        raw = PDUEncoder().encode(BindTransceiver(seqNum=1, system_id='peer', password='p' * 20, system_type=''))
        bind_password.apply(8)
        with self.assertRaises(Exception) as caught:
            PDUEncoder().decode(io.BytesIO(raw))
        self.assertEqual(getattr(caught.exception, 'status', None), pdu_types.CommandStatus.ESME_RINVPASWD)

    def test_other_bind_fields_keep_the_library_sizes(self):
        encoders = PDUEncoder.DefaultRequiredParamEncoders
        self.assertEqual((encoders['system_id'].maxSize, encoders['system_type'].maxSize), (16, 13))
        self.assertIsInstance(encoders['password'], COctetStringEncoder)

    def test_invalid_limits_are_refused(self):
        for value in (7, 65, '64', None):
            with self.assertRaises(ValueError):
                bind_password.apply(value)


if __name__ == '__main__':
    unittest.main()

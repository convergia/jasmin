"""Bind password length accepted by this gateway.

SMPP 3.4 sizes the bind password field at 9 octets: 8 characters and a
terminator. The PDU library enforces that size for every bind PDU, encoded or
decoded. Some SMSCs issue longer passwords and accept them on the wire, so a
strict limit makes those accounts unusable. This module widens the field for
this process and keeps the oversize error free of the value.

It is an explicit source change, applied when jasmin.protocols.smpp is
imported. It does not modify the installed PDU library.
"""
from smpp.pdu import pdu_types
from smpp.pdu.pdu_encoding import COctetStringEncoder, PDUEncoder

MAX_BIND_PASSWORD_LENGTH = 64


class BindPasswordEncoder(COctetStringEncoder):
    """C-octet string encoder that never puts the value into an error."""

    def _encode(self, value):
        data = value.encode('ascii') if isinstance(value, str) else value
        if len(data) + 1 > self.maxSize:
            raise ValueError('Bind password is longer than the allowed maximum (%d characters)' % (self.maxSize - 1))
        return COctetStringEncoder._encode(self, value)


def apply(max_length=MAX_BIND_PASSWORD_LENGTH):
    """Install the widened bind password field. Safe to call more than once."""
    if not isinstance(max_length, int) or not 8 <= max_length <= 64:
        raise ValueError('Bind password limit must be between 8 and 64 characters')
    PDUEncoder.DefaultRequiredParamEncoders['password'] = BindPasswordEncoder(
        max_length + 1, decodeErrorStatus=pdu_types.CommandStatus.ESME_RINVPASWD)
    return max_length

# Maintained engine patches

## Existing lab DLR response access correction

Base: eb75667381ba7f9859456717130c348a8dfbb653, Jasmin 0.11.1.
File: jasmin/managers/listeners.py, submit_sm response handling.
Use `r.response` instead of `_pdu.response` for response ID/status/message ID.
This mirrors the already deployed lab correction. It is an explicit source
change, not an installed-package mutation during image construction.
Reference: https://github.com/jookies/jasmin/commit/a3635cc.
Regression: `single-dlr` and correlated DLR scenarios in the isolated suite.
Retirement: remove this patch when equivalent upstream source is adopted and
the full qualification suite passes. The seven reliability failures remain.

## Bind password longer than the SMPP 3.4 field size

Base: Jasmin 0.11.1 with smpp.pdu3 0.6.
Files: jasmin/protocols/smpp/bind_password.py, applied from
jasmin/protocols/smpp/__init__.py.
The PDU library sizes the bind password at 8 characters and refuses longer
values before any PDU is sent. Some SMSCs issue longer passwords. This patch
widens the field to 64 characters for this process and keeps the value out of
the oversize error. It is an explicit source change, not an installed-package
mutation. Bind PDUs received from peers use the same field, so a longer
password from a peer is now parsed and then judged by authentication.
Regression: `python convergia/test_bind_password.py`, plus the isolated suite.
Retirement: remove when the PDU library accepts a configurable size and the
full qualification suite passes without this module.

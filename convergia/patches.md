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

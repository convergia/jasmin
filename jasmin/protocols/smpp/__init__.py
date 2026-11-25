# Copyright (c) Jookies LTD <jasmin@jookies.net>
# See LICENSE for details.

"""Jasmin SMS Gateway by Jookies LTD <jasmin@jookies.net>"""

from jasmin.protocols.smpp import bind_password

# Accept bind passwords longer than the SMPP 3.4 field size. See bind_password.py.
bind_password.apply()

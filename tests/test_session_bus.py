"""JeepneyTransport against a FAKE connection (no bus is ever opened). Needs jeepney (venv)."""
import unittest
from collections import deque

try:
    from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_call, new_method_return, new_signal
    from jeepney.io.common import FilterHandle, MessageFilters
    from aion2_assist.session_bus import DBusError, JeepneyTransport
    HAVE = True
except ImportError:
    HAVE = False

from aion2_assist.portal import FOCUS_IFACE, FOCUS_OBJ, PortalDenied

PORTAL_OWNER, KWIN_OWNER = ":1.5", ":1.20"
PORTAL = "org.freedesktop.portal.Desktop"
REQ = "org.freedesktop.portal.Request"
SESSION = "/org/freedesktop/portal/desktop/session/1_77/aion2assist"


def sig(path, iface, member, signature, body, sender):
    m = new_signal(DBusAddress(path, interface=iface), member, signature, body)
    m.header.fields[HeaderFields.sender] = sender
    return m


if HAVE:
    class FakeConn:
        unique_name = ":1.77"

        def __init__(self):
            self._filters, self.sent, self.inbox, self.closed = MessageFilters(), [], deque(), False
            self.owners = {PORTAL: PORTAL_OWNER, "org.kde.KWin": KWIN_OWNER}
            self.response = (0, {"session_handle": ("s", SESSION)})
            self.response_sender = PORTAL_OWNER
            self.request_result = 1

        def send(self, msg, serial=None):
            self.sent.append(msg)

        def send_and_get_reply(self, msg, timeout=None):
            self.sent.append(msg)
            f = msg.header.fields
            member = f[HeaderFields.member]
            if member in ("AddMatch",):
                return new_method_return(msg)
            if member == "GetNameOwner":
                return new_method_return(msg, "s", (self.owners[msg.body[0]],))
            if member == "RequestName":
                return new_method_return(msg, "u", (self.request_result,))
            if member == "Boom":
                return new_error(msg, "org.example.Error.Boom", "s", ("nope",))
            if member == "OpenPipeWireRemote":
                return new_method_return(msg, "h", ("FD-OBJECT",))
            token = msg.body[-1]["handle_token"][1]
            handle = f"/org/freedesktop/portal/desktop/request/1_77/{token}"
            if self.response is not None:
                self.inbox.append(sig(handle, REQ, "Response", "ua{sv}", self.response, self.response_sender))
            return new_method_return(msg, "o", (handle,))

        def filter(self, rule, queue=None, bufsize=1):
            return FilterHandle(self._filters, rule, queue if queue is not None else deque(maxlen=bufsize))

        def recv_messages(self, timeout=None):
            if not self.inbox:
                raise TimeoutError
            msg = self.inbox.popleft()
            for f in self._filters.matches(msg):
                f.queue.append(msg)

        def close(self):
            self.closed = True

    def members(c):
        return [m.header.fields[HeaderFields.member] for m in c.sent]


@unittest.skipUnless(HAVE, "jeepney not installed (run inside .venv)")
class SessionBus(unittest.TestCase):
    def make(self, **kw):
        c = FakeConn()
        return c, JeepneyTransport(c, response_timeout=0.05, **kw)

    def test_init_subscribes_to_request_response_and_session_closed(self):
        c, t = self.make()
        adds = [m.body[0] for m in c.sent if m.header.fields[HeaderFields.member] == "AddMatch"]
        self.assertEqual(len(adds), 2)
        self.assertTrue(any("Response" in a and "portal.Request" in a for a in adds))
        self.assertTrue(any("Closed" in a and "portal.Session" in a for a in adds))

    def test_request_pattern(self):
        c, t = self.make()
        res = t.request(PORTAL, "/org/freedesktop/portal/desktop", "org.freedesktop.portal.RemoteDesktop",
                        "CreateSession", "a{sv}", [], {"session_handle_token": "x"})
        self.assertEqual(res, {"session_handle": SESSION})
        sent = next(m for m in c.sent if m.header.fields[HeaderFields.member] == "CreateSession")
        opts = sent.body[-1]
        self.assertEqual(opts["handle_token"], ("s", "aion2_1"))
        self.assertEqual(opts["session_handle_token"], ("s", "x"))
        self.assertEqual(sent.header.fields[HeaderFields.destination], PORTAL)

    def test_denied_response_raises_and_unknown_option_refused(self):
        c, t = self.make()
        c.response = (1, {})
        with self.assertRaises(PortalDenied):
            t.request(PORTAL, "/p", "i.f", "CreateSession", "a{sv}", [], {})
        with self.assertRaises(KeyError):  # only known option names/signatures are ever sent
            t.request(PORTAL, "/p", "i.f", "CreateSession", "a{sv}", [], {"restore_token": "x"})

    def test_response_from_non_portal_sender_ignored(self):
        c, t = self.make()
        c.response_sender = ":1.666"
        with self.assertRaises(TimeoutError):
            t.request(PORTAL, "/p", "i.f", "CreateSession", "a{sv}", [], {})

    def test_no_response_times_out(self):
        c, t = self.make()
        c.response = None
        with self.assertRaises(TimeoutError):
            t.request(PORTAL, "/p", "i.f", "Start", "osa{sv}", ["/s", ""], {})

    def test_session_closed_tracked_from_portal_only(self):
        c, t = self.make()
        c.inbox.append(sig(SESSION, "org.freedesktop.portal.Session", "Closed", "a{sv}", ({},), ":1.666"))
        self.assertFalse(t.is_closed(SESSION))
        c.inbox.append(sig(SESSION, "org.freedesktop.portal.Session", "Closed", "a{sv}", ({},), PORTAL_OWNER))
        self.assertTrue(t.is_closed(SESSION))

    def test_focus_report_is_received_and_answered(self):
        c, t = self.make()
        t.own_name("org.aion2assist.focus")
        call = new_method_call(DBusAddress(FOCUS_OBJ, bus_name="org.aion2assist.focus", interface=FOCUS_IFACE),
                               "Report", "s", ("steam_app_3393110|steam|AION 2",))
        call.header.fields[HeaderFields.sender] = KWIN_OWNER
        other = new_method_call(DBusAddress(FOCUS_OBJ, bus_name="x.y", interface=FOCUS_IFACE), "Evil", "", ())
        other.header.fields[HeaderFields.sender] = ":1.9"
        c.inbox.extend([call, other])
        got = t.pop_calls("Report")
        self.assertEqual([(g.sender, g.args) for g in got], [(KWIN_OWNER, ("steam_app_3393110|steam|AION 2",))])
        self.assertEqual([m.header.message_type for m in c.sent if m.header.message_type != MessageType.method_call], [MessageType.method_return, MessageType.error])
        self.assertEqual(t.pop_calls("Report"), [])

    def test_own_name_requires_primary_ownership(self):
        c, t = self.make()
        t.own_name("org.aion2assist.focus")
        self.assertEqual(c.sent[-1].body, ("org.aion2assist.focus", 4))  # do_not_queue
        c.request_result = 3
        with self.assertRaises(DBusError):
            t.own_name("org.aion2assist.focus")
        self.assertEqual(t.name_owner("org.kde.KWin"), KWIN_OWNER)

    def test_subscribed_signals_collected(self):
        c, t = self.make()
        iface = "org.freedesktop.portal.GlobalShortcuts"
        t.subscribe(iface, "Activated")
        t.subscribe(iface, "Activated")  # idempotent
        self.assertEqual(sum(1 for m in members(c) if m == "AddMatch"), 3)
        c.inbox.append(sig("/org/freedesktop/portal/desktop", iface, "Activated", "osta{sv}", (SESSION, "hold", 5, {}), PORTAL_OWNER))
        self.assertEqual(t.pop_signals(iface, "Activated"), [(SESSION, "hold", 5, {})])

    def test_call_errors_and_fd_passthrough(self):
        c, t = self.make()
        with self.assertRaises(DBusError):
            t.call("a.b", "/x", "a.B", "Boom", "", [])
        self.assertEqual(t.call(PORTAL, "/p", "i.f", "OpenPipeWireRemote", "oa{sv}", [SESSION, {}]), "FD-OBJECT")

    def test_close(self):
        c, t = self.make()
        t.close()
        self.assertTrue(c.closed)


if __name__ == "__main__":
    unittest.main()

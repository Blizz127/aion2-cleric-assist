"""Persistent session-bus transport on jeepney (venv dependency; see requirements.txt).

One connection that: owns a bus name, answers method calls addressed to us (focus reports), tracks portal
Request::Response and Session::Closed signals, and collects subscribed signals. Portal signals are only
accepted from the current owner of org.freedesktop.portal.Desktop (anyone may emit a signal with our
request path). Built against any object with the jeepney blocking-connection surface, so it is tested with a
fake connection; open_session_bus() (real bus) is never called by tests.
"""
import itertools
import time
from collections import defaultdict, deque

from jeepney import DBusAddress, HeaderFields, MatchRule, MessageType, new_error, new_method_call, new_method_return
from jeepney.bus_messages import DBus, DBusNameFlags

from .portal import (FOCUS_IFACE, FOCUS_OBJ, PORTAL, DBusTransport, Incoming, PortalDenied, vdict)

REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


class DBusError(RuntimeError):
    pass


class JeepneyTransport(DBusTransport):
    supports_receive = True

    def __init__(self, conn, timeout=5.0, response_timeout=120.0):
        self.conn, self.timeout, self.response_timeout = conn, timeout, response_timeout
        self.sender_token = conn.unique_name.lstrip(":").replace(".", "_")
        self._tok = itertools.count(1)
        self._bus = DBus()
        self._portal_owner = None
        self._responses, self._closed = {}, set()
        self._signals, self._incoming = defaultdict(list), defaultdict(list)
        self._q_sig, self._q_call = deque(), deque()
        self._subs = set()
        self._handles = [
            conn.filter(MatchRule(type="signal", interface=REQUEST_IFACE, member="Response"), queue=self._q_sig),
            conn.filter(MatchRule(type="signal", interface=SESSION_IFACE, member="Closed"), queue=self._q_sig),
            conn.filter(MatchRule(type="method_call", path=FOCUS_OBJ, interface=FOCUS_IFACE), queue=self._q_call),
        ]
        self._add_match(REQUEST_IFACE, "Response")
        self._add_match(SESSION_IFACE, "Closed")

    # -- low level ----------------------------------------------------------
    def _bus_call(self, msg):
        reply = self.conn.send_and_get_reply(msg, timeout=self.timeout)
        if reply.header.message_type == MessageType.error:
            raise DBusError(f"{reply.header.fields.get(HeaderFields.error_name)}: {reply.body}")
        return reply.body

    def _add_match(self, iface, member):
        self._bus_call(self._bus.AddMatch(MatchRule(type="signal", interface=iface, member=member)))

    def _send(self, dest, path, iface, method, signature, args):
        msg = new_method_call(DBusAddress(path, bus_name=dest, interface=iface), method, signature, tuple(args))
        body = self._bus_call(msg)
        return body[0] if len(body) == 1 else (tuple(body) if body else None)

    def _owner_of_portal(self):
        if self._portal_owner is None:
            self._portal_owner = self.name_owner(PORTAL)
        return self._portal_owner

    def _drain(self):
        while self._q_sig:
            msg = self._q_sig.popleft()
            f = msg.header.fields
            if f.get(HeaderFields.sender) != self._owner_of_portal():
                continue  # not from the portal: ignore
            member, path = f[HeaderFields.member], f[HeaderFields.path]
            if member == "Response":
                self._responses[path] = msg.body
            elif member == "Closed":
                self._closed.add(path)
            else:
                self._signals[(f[HeaderFields.interface], member)].append(msg.body)
        while self._q_call:
            msg = self._q_call.popleft()
            f = msg.header.fields
            if f.get(HeaderFields.member) == "Report":
                self._incoming["Report"].append(Incoming(f.get(HeaderFields.sender), tuple(msg.body)))
                self.conn.send(new_method_return(msg))
            else:
                self.conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))

    def _pump(self, timeout):
        try:
            self.conn.recv_messages(timeout=timeout)
        except TimeoutError:
            pass
        self._drain()

    def _pump_all(self):
        """Process everything already waiting (non-blocking)."""
        while True:
            try:
                self.conn.recv_messages(timeout=0)
            except TimeoutError:
                break
            self._drain()
        self._drain()

    # -- DBusTransport --------------------------------------------------------
    def call(self, dest, path, iface, method, signature, args):
        r = self._send(dest, path, iface, method, signature, args)
        self._drain()
        return r

    def request(self, dest, path, iface, method, signature, args, options):
        token = f"aion2_{next(self._tok)}"
        handle = self._send(dest, path, iface, method, signature, list(args) + [vdict({"handle_token": token, **options})])
        deadline = time.monotonic() + self.response_timeout
        while True:
            self._drain()
            if handle in self._responses:
                code, results = self._responses.pop(handle)
                break
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no Response for {method}")
            self._pump(min(left, 1.0))
        if code != 0:
            raise PortalDenied(f"{method}: response code {code} (user cancelled or denied)")
        return {k: v[1] for k, v in results.items()}  # unwrap variants

    def subscribe(self, iface, member):
        if (iface, member) in self._subs:
            return
        self._subs.add((iface, member))
        self._handles.append(self.conn.filter(MatchRule(type="signal", interface=iface, member=member), queue=self._q_sig))
        self._add_match(iface, member)

    def pop_signals(self, iface, member):
        self._pump_all()
        return self._signals.pop((iface, member), [])

    def own_name(self, name):
        if self._bus_call(self._bus.RequestName(name, DBusNameFlags.do_not_queue))[0] != 1:
            raise DBusError(f"could not become primary owner of {name}")

    def name_owner(self, name):
        return self._bus_call(self._bus.GetNameOwner(name))[0]

    def pop_calls(self, member):
        self._pump_all()
        return self._incoming.pop(member, [])

    def is_closed(self, session_path):
        self._pump_all()
        return session_path in self._closed

    def close(self):
        self.conn.close()


def open_session_bus() -> JeepneyTransport:
    """Real session bus with fd passing (needed for OpenPipeWireRemote). NEVER called by tests."""
    from jeepney.io.blocking import open_dbus_connection
    return JeepneyTransport(open_dbus_connection(bus="SESSION", enable_fds=True))

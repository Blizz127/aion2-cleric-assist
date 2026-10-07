"""Real key names the input sink can press (Linux input-event-codes.h values). No binds are invented here."""

def _table():
    k = {str(d): 2 + (d - 1) for d in range(1, 10)}
    k["0"] = 11
    for row, base in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
        k.update({c: base + i for i, c in enumerate(row)})
    k.update({f"F{i}": 58 + i for i in range(1, 11)})
    k.update({"F11": 87, "F12": 88, "MINUS": 12, "EQUAL": 13, "TAB": 15, "GRAVE": 41, "SPACE": 57})
    return k


KEYCODES = _table()


def normalize_key(name):
    """'Q' -> 'q', 'f5' -> 'F5'; returns None if not a real, pressable key name (UNSET*, blank, chords...)."""
    if not isinstance(name, str):
        return None
    n = name.strip()
    n = n.lower() if len(n) == 1 else n.upper()
    return n if n in KEYCODES else None

"""A tiny, dependency-free YAML reader/writer for agent config files.

Kamelle only ever touches a handful of keys in someone else's config file, and
those files are full of hand-written comments. A normal ``yaml.safe_load`` /
``yaml.dump`` round trip would silently throw every comment and every bit of
formatting away, so this module does two narrow things instead:

* :func:`parse` reads the block-style YAML subset that agent configs use
  (nested mappings, sequences of scalars, sequences of flat mappings).
* :func:`set_path` / :func:`delete_path` rewrite *only* the lines that belong
  to one key. Everything else in the file — comments, blank lines, key order,
  quoting style — is returned byte-for-byte unchanged.

Anything outside that subset (anchors, aliases, tags, flow collections with
content) raises :class:`YamlMiniError` rather than being guessed at. Callers
are expected to fail loudly instead of writing a mangled config.

If PyYAML happens to be installed, :func:`parse_preferred` uses it for reading;
writing always goes through this module so comments survive.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "YamlMiniError",
    "parse",
    "parse_preferred",
    "get_path",
    "set_path",
    "delete_path",
    "dump",
]


class YamlMiniError(ValueError):
    """Raised when a document uses YAML features this module refuses to guess at."""


_KEY_RE = re.compile(r"^(?P<key>[^\s#][^:]*?)\s*:(?:\s+(?P<value>.*))?$")
_UNSUPPORTED_RE = re.compile(r"^[&*!]|^<<\s*:")
_BOOL_TRUE = {"true", "yes", "on", "y"}
_BOOL_FALSE = {"false", "no", "off", "n"}
_NULL = {"", "null", "~"}
_RESERVED_PLAIN = _BOOL_TRUE | _BOOL_FALSE | _NULL
_PLAIN_SAFE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:+@-]*$")
_LITERAL_MARKERS = {"|", ">", "|-", ">-", "|+", ">+"}


# ---------------------------------------------------------------------------
# Scalars
# ---------------------------------------------------------------------------

def _strip_comment(raw: str) -> str:
    """Drop a trailing ``# comment`` that sits outside quotes."""
    out: list[str] = []
    quote: str | None = None
    for i, ch in enumerate(raw):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_scalar(raw: str) -> Any:
    text = _strip_comment(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        return inner.replace("''", "'") if text[0] == "'" else inner
    low = text.lower()
    if low in _NULL:
        return None
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    if text in ("{}", "[]"):
        return {} if text == "{}" else []
    if text.startswith(("{", "[")):
        raise YamlMiniError(f"flow collections with content are not supported: {text!r}")
    if text[:1] in ("&", "*", "!"):
        # An anchor, alias or tag in value position. Reading it as the literal
        # string would quietly lose whatever it points at.
        raise YamlMiniError(f"anchors, aliases and tags are not supported: {text!r}")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _render_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if _PLAIN_SAFE_RE.match(text) and text.lower() not in _RESERVED_PLAIN:
        return text
    return "'" + text.replace("'", "''") + "'"


def _render_block(key: str, value: Any, indent: int) -> list[str]:
    """Render ``key: value`` as a list of lines starting at ``indent`` spaces."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}"]
        lines = [f"{pad}{key}:"]
        for k, v in value.items():
            lines.extend(_render_block(str(k), v, indent + 2))
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return [f"{pad}{key}: []"]
        lines = [f"{pad}{key}:"]
        for item in value:
            lines.extend(_render_sequence_item(item, indent + 2))
        return lines
    return [f"{pad}{key}: {_render_scalar(value)}"]


def _render_sequence_item(item: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(item, (list, tuple)):
        raise YamlMiniError("nested sequences are not supported")
    if not isinstance(item, dict):
        return [f"{pad}- {_render_scalar(item)}"]
    if not item:
        return [f"{pad}- {{}}"]
    lines: list[str] = []
    for position, (k, v) in enumerate(item.items()):
        rendered = _render_block(str(k), v, indent + 2)
        if position == 0:
            rendered[0] = f"{pad}- " + rendered[0].lstrip()
        lines.extend(rendered)
    return lines


def dump(data: dict) -> str:
    """Render a plain mapping as block-style YAML. Used by the generic adapter."""
    if not isinstance(data, dict):
        raise YamlMiniError("dump() expects a mapping at the top level")
    lines: list[str] = []
    for key, value in data.items():
        lines.extend(_render_block(str(key), value, 0))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Line helpers
# ---------------------------------------------------------------------------

def _is_skippable(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_sequence_line(stripped: str) -> bool:
    return stripped == "-" or stripped.startswith("- ")


def _split_key(stripped: str) -> tuple[str, str] | None:
    """Return ``(key, inline_value)`` for a mapping line, else None."""
    if _is_sequence_line(stripped):
        return None
    match = _KEY_RE.match(stripped)
    if not match:
        return None
    key = match.group("key").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1]
    return key, (match.group("value") or "")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def parse(text: str) -> dict:
    """Parse the block-style YAML subset used by agent config files."""
    root: dict = {}
    lines = text.splitlines()
    stack: list[tuple[int, Any]] = [(-1, root)]  # (minimum indent, container)

    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_skippable(line) or line.strip() in ("---", "..."):
            index += 1
            continue
        stripped = line.strip()
        if _UNSUPPORTED_RE.match(stripped):
            raise YamlMiniError(f"unsupported YAML syntax on line {index + 1}: {stripped!r}")

        indent = _indent_of(line)
        is_sequence = _is_sequence_line(stripped)
        while len(stack) > 1 and indent < stack[-1][0]:
            stack.pop()
        # A sequence written at its key's own indent keeps the list frame open
        # until a mapping key appears at that same indent.
        while (
            len(stack) > 1
            and not is_sequence
            and indent == stack[-1][0]
            and isinstance(stack[-1][1], list)
        ):
            stack.pop()
        container = stack[-1][1]

        if is_sequence:
            if not isinstance(container, list):
                raise YamlMiniError(f"sequence entry outside a sequence on line {index + 1}")
            item_text = stripped[2:].strip() if stripped != "-" else ""
            item_split = _split_key(item_text) if item_text else None
            if item_split is None:
                container.append(_parse_scalar(item_text))
                index += 1
                continue
            item: dict = {}
            container.append(item)
            stack.append((indent + 2, item))
            key, inline = item_split
            index = _assign(item, key, inline, lines, index, stack, indent + 2)
            continue

        split = _split_key(stripped)
        if split is None:
            raise YamlMiniError(f"cannot parse line {index + 1}: {stripped!r}")
        if not isinstance(container, dict):
            raise YamlMiniError(f"mapping key inside a sequence on line {index + 1}")
        key, inline = split
        index = _assign(container, key, inline, lines, index, stack, indent)

    return root


def _assign(
    container: dict,
    key: str,
    inline: str,
    lines: list[str],
    index: int,
    stack: list[tuple[int, Any]],
    indent: int,
) -> int:
    """Store ``key`` in ``container``; open a child frame when the value is a block."""
    value_text = _strip_comment(inline).strip()
    if value_text in _LITERAL_MARKERS:
        container[key], next_index = _consume_literal(lines, index + 1, indent)
        return next_index
    if value_text:
        container[key] = _parse_scalar(value_text)
        return index + 1

    child = _peek_child(lines, index + 1, indent)
    container[key] = child
    if isinstance(child, dict):
        stack.append((indent + 1, child))
    elif isinstance(child, list):
        # Sequences may sit at the key's indent or deeper; the frame accepts both.
        stack.append((indent, child))
    return index + 1


def _peek_child(lines: list[str], start: int, indent: int) -> Any:
    """Decide whether an empty ``key:`` opens a mapping, a sequence, or is null."""
    for line in lines[start:]:
        if _is_skippable(line):
            continue
        line_indent = _indent_of(line)
        is_sequence = _is_sequence_line(line.strip())
        if line_indent > indent:
            return [] if is_sequence else {}
        if line_indent == indent and is_sequence:
            return []
        return None
    return None


def _consume_literal(lines: list[str], start: int, indent: int) -> tuple[str, int]:
    collected: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if line.strip() and _indent_of(line) <= indent:
            break
        collected.append(line[indent + 2:] if len(line) > indent + 2 else "")
        index += 1
    return "\n".join(collected).strip("\n"), index


def parse_preferred(text: str) -> dict:
    """Parse with PyYAML when it is installed, otherwise with :func:`parse`."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return parse(text)
    loaded = yaml.safe_load(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise YamlMiniError("top-level YAML value must be a mapping")
    return loaded


def get_path(data: dict, path: tuple[str, ...] | list[str], default: Any = None) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

class _KeyLine:
    """Where one mapping key lives in the source text, and how far its block runs."""

    __slots__ = ("path", "index", "indent", "inline", "end", "hidden")

    def __init__(self, path: tuple[str, ...], index: int, indent: int, inline: str, hidden: bool):
        self.path = path
        self.index = index
        self.indent = indent
        self.inline = inline
        self.end = index + 1  # exclusive, extended while scanning the block
        self.hidden = hidden

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_KeyLine({'.'.join(self.path)} @{self.index}:{self.end})"


_SEQUENCE_ITEM = ("\0seq",)  # sentinel path: keys below one are not addressable


def _scan(lines: list[str]) -> list[_KeyLine]:
    """Index every addressable mapping key with its path and block extent."""
    found: list[_KeyLine] = []
    stack: list[_KeyLine] = []

    for index, line in enumerate(lines):
        if _is_skippable(line):
            continue
        stripped = line.strip()
        if _UNSUPPORTED_RE.match(stripped):
            raise YamlMiniError(f"unsupported YAML syntax on line {index + 1}: {stripped!r}")
        indent = _indent_of(line)
        is_sequence = _is_sequence_line(stripped)

        while stack and indent <= stack[-1].indent:
            # A sequence at its key's own indent still belongs to that key.
            if is_sequence and indent == stack[-1].indent and not stack[-1].inline.strip():
                break
            stack.pop()
        for parent in stack:
            parent.end = index + 1

        if is_sequence:
            stack.append(_KeyLine(_SEQUENCE_ITEM, index, indent + 1, "", True))
            continue

        split = _split_key(stripped)
        if split is None:
            continue
        key, inline = split
        hidden = any(entry.hidden for entry in stack)
        path = tuple([entry.path[-1] for entry in stack if not entry.hidden] + [key])
        entry = _KeyLine(path, index, indent, inline, hidden)
        if not hidden:
            found.append(entry)
        stack.append(entry)

    return found


def _find(keylines: list[_KeyLine], path: tuple[str, ...]) -> _KeyLine | None:
    for entry in keylines:
        if entry.path == path:
            return entry
    return None


def _rejoin(lines: list[str], trailing_newline: bool) -> str:
    text = "\n".join(lines)
    return text + "\n" if trailing_newline else text


def set_path(text: str, path: tuple[str, ...] | list[str], value: Any) -> str:
    """Return ``text`` with ``path`` set to ``value``, leaving everything else alone.

    An existing key is rewritten in place, block and all. A missing key is
    inserted under the deepest ancestor that does exist, or appended to the
    document when even its top-level key is new.
    """
    path = tuple(path)
    if not path:
        raise YamlMiniError("set_path() needs at least one key")

    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    keylines = _scan(lines)

    existing = _find(keylines, path)
    if existing is not None:
        block = _render_block(path[-1], value, existing.indent)
        return _rejoin(lines[: existing.index] + block + lines[existing.end:], trailing_newline)

    for depth in range(len(path) - 1, 0, -1):
        ancestor = _find(keylines, path[:depth])
        if ancestor is None:
            continue
        inline = ancestor.inline.strip()
        if inline and inline not in ("{}", "[]"):
            raise YamlMiniError(
                f"cannot nest {'.'.join(path)} under scalar key {'.'.join(path[:depth])}"
            )
        block = _render_block(path[depth], _nest(path[depth + 1:], value), ancestor.indent + 2)
        if inline:
            # "key: {}" has no block yet — turn it into one.
            opener = " " * ancestor.indent + f"{path[depth - 1]}:"
            return _rejoin(
                lines[: ancestor.index] + [opener] + block + lines[ancestor.end:], trailing_newline
            )
        return _rejoin(lines[: ancestor.end] + block + lines[ancestor.end:], trailing_newline)

    block = _render_block(path[0], _nest(path[1:], value), 0)
    separator = [""] if lines and lines[-1].strip() else []
    return _rejoin(lines + separator + block, True)


def _nest(keys: tuple[str, ...], value: Any) -> Any:
    for key in reversed(keys):
        value = {key: value}
    return value


def delete_path(text: str, path: tuple[str, ...] | list[str]) -> str:
    """Return ``text`` with ``path`` and its block removed. A missing key is a no-op."""
    path = tuple(path)
    trailing_newline = text.endswith("\n")
    lines = text.splitlines()
    entry = _find(_scan(lines), path)
    if entry is None:
        return text
    return _rejoin(lines[: entry.index] + lines[entry.end:], trailing_newline)

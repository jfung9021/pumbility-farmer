"""Canonical artifact hashes without allocating a full serialized copy."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterator


BLOCK_BYTES = 64 * 1024
_STRING_CHARACTERS = BLOCK_BYTES // 6
_BLOCK_NODES = 4096


def _fits_block(value: Any) -> bool:
    """Conservatively bound a C-encoded subtree without serializing it first."""
    remaining, nodes = BLOCK_BYTES, _BLOCK_NODES
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        nodes -= 1
        kind = type(item)
        if kind is str:
            # A control character needs six ASCII bytes; every other Unicode
            # character needs at most four UTF-8 bytes, plus the two quotes.
            remaining -= 2 + 6 * len(item)
        elif kind is int:
            remaining -= (int.bit_length(item) * 30103) // 100000 + 3
        elif kind is float:
            remaining -= 32
        elif item is None or kind is bool:
            remaining -= 5
        elif kind is list or kind is tuple:
            remaining -= 2 + len(item)
            stack.append(iter(item))
        elif kind is dict:
            remaining -= 2 + 4 * len(item)
            # Visit keys too: integer/float JSON keys become quoted strings.
            stack.append(iter(item.values()))
            stack.append(iter(item))
        else:
            return False  # Keep JSONEncoder's existing custom-type behavior.
        if remaining < 0 or nodes < 0:
            return False
    return True


def _quoted_string(value: str) -> Iterator[bytes]:
    yield b'"'
    for offset in range(0, len(value), _STRING_CHARACTERS):
        # Encoding independent pieces preserves the standard JSON escapes.
        try:
            block = json.encoder.encode_basestring(value[offset:offset + _STRING_CHARACTERS])[1:-1].encode("utf-8")
        except UnicodeEncodeError:
            # Preserve the legacy exception's string and character offsets.
            # Only this already-failing input takes the unbounded error path.
            json.encoder.encode_basestring(value).encode("utf-8")
            raise
        yield block
    yield b'"'


def _canonical_blocks(value: Any, encoder: json.JSONEncoder,
                      ancestors: set[int]) -> Iterator[bytes]:
    if _fits_block(value):
        # Unlike iterencode's default Python token iterator, encode uses the C
        # encoder. The conservative bound above prevents a whole-artifact copy.
        try:
            block = encoder.encode(value).encode("utf-8")
        except UnicodeEncodeError:
            for chunk in encoder.iterencode(value):
                yield chunk.encode("utf-8")
            raise
        yield block
        return
    kind = type(value)
    if kind is str:
        yield from _quoted_string(value)
        return
    if kind not in (dict, list, tuple):
        for chunk in encoder.iterencode(value):
            yield chunk.encode("utf-8")
        return
    identity = id(value)
    if identity in ancestors:
        raise ValueError("Circular reference detected")
    ancestors.add(identity)
    try:
        if kind is dict:
            yield b"{"
            for index, (key, item) in enumerate(sorted(value.items())):
                if index:
                    yield b","
                if isinstance(key, str):
                    # Calling the base str method avoids a subclass's __str__.
                    text = str.__str__(key)
                elif key is None:
                    text = "null"
                elif isinstance(key, (bool, int, float)):
                    text = encoder.encode(key)
                else:
                    # Let the legacy encoder produce its exact key-type error.
                    for _ in encoder.iterencode({key: None}):
                        pass
                    raise AssertionError("An unsupported JSON key was accepted.")
                yield from _quoted_string(text)
                yield b":"
                yield from _canonical_blocks(item, encoder, ancestors)
            yield b"}"
        else:
            yield b"["
            for index, item in enumerate(value):
                if index:
                    yield b","
                yield from _canonical_blocks(item, encoder, ancestors)
            yield b"]"
    finally:
        ancestors.remove(identity)


def canonical_json_sha256(value: Any, *, trailing_newline: bool = False) -> str:
    hasher = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    pending = bytearray()
    for chunk in _canonical_blocks(value, encoder, set()):
        if len(pending) + len(chunk) > BLOCK_BYTES:
            hasher.update(pending)
            pending.clear()
        if len(chunk) >= BLOCK_BYTES:
            hasher.update(chunk)
        else:
            pending.extend(chunk)
    if pending:
        hasher.update(pending)
    if trailing_newline:
        hasher.update(b"\n")
    return hasher.hexdigest()

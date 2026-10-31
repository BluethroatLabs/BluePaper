from __future__ import annotations

from collections import deque
from collections.abc import Callable

# Bytes matched between calls to ``find``'s ``check``.
CHECK_EVERY = 1 << 16


class _Node:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[int, _Node] = {}
        self.fail: _Node | None = None
        self.outputs: list[tuple[str, int]] = []


class AhoCorasick:
    """Linear-time multi-pattern matcher over bytes (no Python `re`)."""

    def __init__(self, patterns: list[tuple[str, bytes]]) -> None:
        self._root = _Node()
        self._root.fail = self._root
        for pattern_id, literal in patterns:
            if not literal:
                continue
            node = self._root
            for byte in literal:
                node = node.children.setdefault(byte, _Node())
            node.outputs.append((pattern_id, len(literal)))
        queue: deque[_Node] = deque()
        for child in self._root.children.values():
            child.fail = self._root
            queue.append(child)
        while queue:
            node = queue.popleft()
            assert node.fail is not None
            for byte, child in node.children.items():
                fail = node.fail
                while fail is not self._root and byte not in fail.children:
                    assert fail.fail is not None
                    fail = fail.fail
                child.fail = fail.children.get(byte, self._root)
                child.outputs = child.outputs + child.fail.outputs
                queue.append(child)

    def find(
        self, data: bytes, check: Callable[[], None] | None = None
    ) -> dict[str, tuple[int, int]]:
        """Return pattern id -> (count, first_offset).

        ``check`` runs every ``CHECK_EVERY`` bytes; it can raise to stop.
        """
        hits: dict[str, tuple[int, int]] = {}
        node = self._root
        view = memoryview(data)
        for base in range(0, len(data), CHECK_EVERY):
            if check is not None:
                check()
            for index, byte in enumerate(view[base : base + CHECK_EVERY], base):
                while node is not self._root and byte not in node.children:
                    assert node.fail is not None
                    node = node.fail
                node = node.children.get(byte, self._root)
                for pattern_id, length in node.outputs:
                    start = index - length + 1
                    prev = hits.get(pattern_id)
                    if prev is None:
                        hits[pattern_id] = (1, start)
                    else:
                        count, first = prev
                        hits[pattern_id] = (count + 1, first)
        return hits

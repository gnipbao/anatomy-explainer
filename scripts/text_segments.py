"""Split narration into short, punctuation-preserving phrases using only stdlib."""

import re
import unicodedata


MIN_LENGTH = 12
TARGET_LENGTH = 26
MAX_LENGTH = 32
_SENTENCE_ENDS = frozenset("。！？!?；;…｡؟.")
_PHRASE_ENDS = frozenset("，,、：:")
_CLOSERS = frozenset("”’\"'」』）》〉】〕〗〙〛)]}")
_OPENERS = frozenset("“‘\"'「『（《〈【〔〖〘〚([{")
_ABBREVIATION = re.compile(
    r"(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc)\.|\b(?:[a-z]\.){2,})$",
    re.IGNORECASE,
)


def _is_cjk(character):
    codepoint = ord(character)
    return (0x2E80 <= codepoint <= 0x31FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x20000 <= codepoint <= 0x323AF)


def _period_is_internal(text, index):
    """Keep decimals, domain names, and common English abbreviations together."""
    before = text[index - 1] if index else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if before and after and before.isascii() and after.isascii() and before.isalnum() and after.isalnum():
        return True
    return bool(_ABBREVIATION.search(text[:index + 1]))


def _semantic_units(text, sentence=False):
    """Keep each delimiter and its closing quotation marks on the preceding unit."""
    marks = _SENTENCE_ENDS if sentence else _PHRASE_ENDS
    units = []
    start = index = 0
    while index < len(text):
        character = text[index]
        boundary = character in marks
        if character == "." and boundary:
            boundary = not _period_is_internal(text, index)
        if character in ",:" and boundary and index and index + 1 < len(text):
            boundary = not (text[index - 1].isdigit() and text[index + 1].isdigit())
        if not boundary:
            index += 1
            continue
        index += 1
        while index < len(text) and (text[index] in marks or text[index] in _CLOSERS):
            index += 1
        units.append(text[start:index])
        start = index
    if start < len(text):
        units.append(text[start:])
    return units


def _break_positions(text):
    """A Latin word is atomic; CJK text can also break between characters."""
    positions = [0]
    for index in range(1, len(text)):
        before, after = text[index - 1], text[index]
        if before.isspace():
            positions.append(index)
            continue
        if after.isspace():
            continue
        after_is_closer = (after in _CLOSERS
                           or (unicodedata.category(after).startswith("P") and after not in _OPENERS))
        if (before not in _OPENERS and not after_is_closer
                and (_is_cjk(before) or _is_cjk(after))):
            positions.append(index)
    positions.append(len(text))
    return positions


def _length_cost(length):
    # Prefer readable lengths without manufacturing tiny final fragments.
    outside = max(MIN_LENGTH - length, length - TARGET_LENGTH, 0)
    return 1 + 4 * outside * outside + (length - 20) ** 2 / 100


def _partition(text, positions):
    """Balance chunks at the supplied legal boundaries, retaining raw whitespace."""
    costs = [float("inf")] * len(positions)
    next_position = [None] * len(positions)
    costs[-1] = 0
    for start in range(len(positions) - 2, -1, -1):
        first_nonempty = True
        for end in range(start + 1, len(positions)):
            length = len(text[positions[start]:positions[end]].strip())
            if not length:
                continue
            if length > MAX_LENGTH and not first_nonempty:
                break
            cost = _length_cost(length) + costs[end]
            if cost < costs[start]:
                costs[start], next_position[start] = cost, end
            first_nonempty = False
            if length > MAX_LENGTH:
                # A single unbroken word may exceed the limit; never cut the word.
                break
    chunks = []
    start = 0
    while start < len(positions) - 1:
        end = next_position[start]
        chunks.append(text[positions[start]:positions[end]])
        start = end
    return chunks


def _short_units(text):
    if len(text.strip()) <= TARGET_LENGTH:
        return [text]
    return _partition(text, _break_positions(text))


def split_phrases(text: str, language: str = "zh-CN") -> list[str]:
    """Return short narration cues without losing words or punctuation.

    Sentence endings (including semicolons) stay as natural cue boundaries,
    even for short sentences. Comma, enumeration-comma, and colon fragments
    within each sentence are combined toward 12–26 characters per cue, with
    a 32-character limit. Long unbroken non-CJK words remain intact and may
    exceed that limit. Whitespace is normalized to single spaces and trimmed
    at cue edges. ``language`` accepts the caller's language tag; boundaries
    are determined from the actual text so mixed Chinese/English also works.
    """
    normalized = " ".join(text.split())
    if not normalized:
        return []
    phrases = []
    for sentence in _semantic_units(normalized, sentence=True):
        units = []
        for unit in _semantic_units(sentence):
            units.extend(_short_units(unit))
        joined = "".join(units)
        positions = [0]
        for unit in units:
            positions.append(positions[-1] + len(unit))
        phrases.extend(chunk.strip() for chunk in _partition(joined, positions) if chunk.strip())
    return phrases

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex


@dataclass(frozen=True)
class SearchRequest:
    query: str
    page: int = 1


_NAMESPACE_SHORTCUTS = {
    "a": "a",
    "artist": "a",
    "c": "c",
    "char": "c",
    "character": "c",
    "circle": "g",
    "cos": "cos",
    "cosplayer": "cos",
    "f": "f",
    "female": "f",
    "g": "g",
    "group": "g",
    "lang": "l",
    "language": "l",
    "l": "l",
    "loc": "loc",
    "location": "loc",
    "m": "m",
    "male": "m",
    "o": "o",
    "other": "o",
    "p": "p",
    "parody": "p",
    "r": "r",
    "reclass": "r",
    "series": "p",
    "x": "x",
    "mixed": "x",
}


def _split_option(token: str) -> tuple[str, str | None]:
    if not token.startswith("--"):
        return token, None

    content = token[2:]
    if "=" not in content:
        return content, None

    key, value = content.split("=", 1)
    return key, value


def _quote_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized

    if re.search(r"\s", normalized) or '"' in normalized:
        escaped = normalized.replace('"', '\\"')
        return f'"{escaped}"'
    return normalized


def _format_raw_fragment(fragment: str) -> str:
    normalized = fragment.strip()
    if not normalized:
        return ""

    prefix = ""
    if normalized[0] in {"-", "~"}:
        prefix = normalized[0]
        normalized = normalized[1:].lstrip()

    if not normalized:
        return prefix

    if ":" in normalized:
        head, tail = normalized.split(":", 1)
        if tail:
            return f"{prefix}{head}:{_quote_value(tail)}"
        return f"{prefix}{head}:"

    return f"{prefix}{_quote_value(normalized)}"


def _consume_value(tokens: list[str], index: int) -> tuple[str | None, int]:
    token = tokens[index]
    key, inline_value = _split_option(token)
    if inline_value is not None:
        return inline_value, 1

    if index + 1 >= len(tokens):
        return None, 1

    next_token = tokens[index + 1]
    if next_token.startswith("--") and len(next_token) > 2:
        return None, 1

    return next_token, 2


def _build_namespace_term(namespace: str, value: str) -> str:
    return f"{namespace}:{_quote_value(value)}"


def _build_tag_term(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""

    if ":" in normalized:
        return _format_raw_fragment(normalized)

    return f"tag:{_quote_value(normalized)}"


def _parse_page(value: str) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1

    return page if page > 0 else 1


def parse_search_request(raw_input: str) -> SearchRequest:
    """Parse a chat search message into a query string and page number.

    Supported user-friendly filters:
    - --page N / --page=N
    - --title / --uploader / --uploaduid / --gid / --comment / --favnote / --tag
    - --not / --exclude for exclusions
    - --or for OR terms
    - namespace shortcuts such as --f, --m, --a, --c, --g, --l, --p, --x, --o

    Any other bare terms are preserved as raw E-Hentai search terms.
    """
    if not raw_input.strip():
        return SearchRequest(query="", page=1)

    try:
        tokens = shlex.split(raw_input, posix=True)
    except ValueError:
        tokens = raw_input.split()

    search_terms: list[str] = []
    page = 1

    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token in {"--page", "-p"} or token.startswith("--page=") or token.startswith("-p="):
            if "=" in token:
                _, page_value = token.split("=", 1)
                page = _parse_page(page_value)
                index += 1
                continue

            value, consumed = _consume_value(tokens, index)
            if value is None:
                search_terms.append(token)
                index += 1
                continue

            page = _parse_page(value)
            index += consumed
            continue

        if token.startswith("--"):
            key, inline_value = _split_option(token)
            key = key.lower()

            if key in _NAMESPACE_SHORTCUTS:
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                search_terms.append(_build_namespace_term(_NAMESPACE_SHORTCUTS[key], value))
                index += consumed
                continue

            if key in {"title", "uploader", "uploaduid", "gid", "comment", "favnote", "lang", "language"}:
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                qualifier = "l" if key in {"lang", "language"} else key
                search_terms.append(_build_namespace_term(qualifier, value))
                index += consumed
                continue

            if key == "tag":
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                search_terms.append(_build_tag_term(value))
                index += consumed
                continue

            if key in {"not", "exclude"}:
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                term = _format_raw_fragment(value)
                search_terms.append(term if term.startswith("-") else f"-{term}")
                index += consumed
                continue

            if key == "or":
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                term = _format_raw_fragment(value)
                search_terms.append(term if term.startswith("~") else f"~{term}")
                index += consumed
                continue

            if key == "raw":
                if inline_value is None:
                    value, consumed = _consume_value(tokens, index)
                    if value is None:
                        search_terms.append(token)
                        index += 1
                        continue
                else:
                    value = inline_value
                    consumed = 1

                search_terms.append(_format_raw_fragment(value))
                index += consumed
                continue

        search_terms.append(_format_raw_fragment(token))
        index += 1

    query = " ".join(term for term in search_terms if term).strip()
    return SearchRequest(query=query, page=page)
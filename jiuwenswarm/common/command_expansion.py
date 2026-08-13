# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Expand a user-defined slash command body into the prompt that gets sent.

Two substitutions, in one pass each:

- ``$ARGUMENTS`` — everything the user typed after the command name.
- ``$1`` .. ``$9`` — positional arguments, shell-style quoting.
- ``@path`` — embed a workspace file's contents.

The file resolver is injected rather than reached for directly, so the
substitution rules can be tested without a filesystem and so the caller keeps
ownership of what "inside the workspace" means. This module never opens a file
itself.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Callable, Optional

# ``$1``..``$9``. Deliberately not ``$0`` -- the command name is not an argument.
#
# The two lookaheads earn their place. ``(?!\d)`` stops ``$10`` becoming ``$1``
# followed by a literal ``0``. ``(?!\.\d)`` keeps ``$5.00`` as money: a prompt
# body is prose far more often than it is shell, and silently turning a price
# into ``.00`` is a corruption the author would never think to look for.
# ``use $1.`` at the end of a sentence still substitutes, because the ``.`` is
# not followed by a digit.
_POSITIONAL = re.compile(r"\$([1-9])(?!\d)(?!\.\d)")
_ARGUMENTS = "$ARGUMENTS"

# ``@`` followed by a path, taken greedily to whitespace. The earlier version
# stopped at the first ``.``, which turned ``@src/a.py`` into ``src/a``.
# Trailing prose punctuation is stripped afterwards instead, where the
# distinction between ``a.py`` and ``a.py,`` can actually be made.
# ``(?<![\w@])`` keeps ``me@example.com`` from looking like a reference.
_FILE_REF = re.compile(r"(?<![\w@])@([^\s]+)")

# Punctuation that ends a sentence rather than a filename.
_TRAILING_PUNCTUATION = ",.;:)]}!?'\""

# One embedded file cannot dominate the prompt.
MAX_EMBED_CHARS = 20_000
# Cap how many @file references one expansion will read.
MAX_EMBED_COUNT = 20

#: Resolver contract: given the raw reference, return the file text, or raise.
FileResolver = Callable[[str], str]


@dataclass
class ExpansionResult:
    text: str
    #: Files successfully embedded, in the order encountered.
    embedded: list[str] = field(default_factory=list)
    #: Human-readable problems. Non-empty does **not** mean expansion failed:
    #: the text is still usable, with the failures marked inline.
    errors: list[str] = field(default_factory=list)


def split_arguments(raw: str) -> list[str]:
    """Split the argument string shell-style, falling back to whitespace.

    ``shlex`` gives the quoting users expect -- ``/review "src/a b.py"`` is one
    argument -- but it raises on an unbalanced quote, which is a typo, not a
    reason to refuse the command.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def substitute_arguments(body: str, raw_args: str) -> str:
    """Replace ``$ARGUMENTS`` and ``$1``..``$9``.

    A positional with no corresponding argument becomes the empty string, the
    same as an unset shell variable. Substituting the literal ``$3`` instead
    would put a stray token in the prompt the model then tries to interpret.
    """
    positionals = split_arguments(raw_args)

    def _positional(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return positionals[index] if index < len(positionals) else ""

    # $ARGUMENTS first: a body using both should see the whole string there and
    # the split pieces in the positionals, not the positionals substituted into
    # a string that then gets re-scanned.
    text = body.replace(_ARGUMENTS, (raw_args or "").strip())
    return _POSITIONAL.sub(_positional, text)


def expand_file_refs(
    body: str,
    resolver: Optional[FileResolver],
    *,
    max_chars: int = MAX_EMBED_CHARS,
    max_embeds: int = MAX_EMBED_COUNT,
) -> ExpansionResult:
    """Replace ``@path`` with the file's contents.

    A reference that cannot be read is replaced by a visible marker rather than
    by nothing. An empty section would make the model answer about a file it
    never saw, which is worse than an obvious error in the prompt.
    """
    result = ExpansionResult(text=body)
    if resolver is None:
        return result

    def _embed(match: re.Match[str]) -> str:
        raw = match.group(1)
        # Split the path from any prose punctuation that trailed it, and put
        # that punctuation back after the embedded block so the sentence still
        # reads correctly.
        ref = raw.rstrip(_TRAILING_PUNCTUATION)
        suffix = raw[len(ref):]
        if not ref:
            return match.group(0)

        if len(result.embedded) >= max_embeds:
            result.errors.append(f"{ref}: embed limit reached ({max_embeds})")
            return f"[could not read @{ref}: embed limit reached]{suffix}"

        try:
            content = resolver(ref)
        except Exception as exc:  # noqa: BLE001 - surfaced inline, see docstring
            result.errors.append(f"{ref}: {exc}")
            return f"[could not read @{ref}: {exc}]{suffix}"
        if content is None:
            result.errors.append(f"{ref}: not found")
            return f"[could not read @{ref}: not found]{suffix}"
        truncated = ""
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = f"\n[… truncated at {max_chars} characters]"
        result.embedded.append(ref)
        return f"\n--- {ref} ---\n{content}{truncated}\n--- end {ref} ---\n{suffix}"

    result.text = _FILE_REF.sub(_embed, body)
    return result


def expand_command(
    body: str,
    raw_args: str,
    resolver: Optional[FileResolver] = None,
    *,
    max_chars: int = MAX_EMBED_CHARS,
    max_embeds: int = MAX_EMBED_COUNT,
) -> ExpansionResult:
    """Full expansion: arguments first, then file references.

    Order matters and is load-bearing. Arguments are substituted **before**
    file references so that ``@$1`` works -- a command that takes a path and
    embeds it. Doing it the other way round would look for a file literally
    named ``$1``.

    It also means a user-supplied argument can name a file to embed, which is
    exactly the intended power and exactly why the resolver, not this module,
    must enforce the workspace boundary.
    """
    with_args = substitute_arguments(body, raw_args)
    return expand_file_refs(
        with_args, resolver, max_chars=max_chars, max_embeds=max_embeds,
    )

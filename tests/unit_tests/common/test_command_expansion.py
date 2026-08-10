# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Argument and @file substitution for user-defined slash commands."""

from __future__ import annotations

from jiuwenswarm.common.command_expansion import (
    expand_command,
    expand_file_refs,
    split_arguments,
    substitute_arguments,
)


# ---------------------------------------------------------------- arguments


def test_arguments_placeholder_gets_the_whole_string() -> None:
    assert substitute_arguments("Review: $ARGUMENTS", "src/a.py and be brief") == (
        "Review: src/a.py and be brief"
    )


def test_positionals_split_shell_style() -> None:
    body = "compare $1 with $2"
    assert substitute_arguments(body, "alpha beta") == "compare alpha with beta"


def test_a_quoted_argument_stays_one_positional() -> None:
    """/review "src/a b.py" is one path, not two."""
    assert split_arguments('"src/a b.py" other') == ["src/a b.py", "other"]
    assert substitute_arguments("read $1", '"src/a b.py"') == "read src/a b.py"


def test_an_unbalanced_quote_falls_back_instead_of_failing() -> None:
    """A typo must not refuse the command."""
    assert split_arguments('unclosed "quote here') == ["unclosed", '"quote', "here"]


def test_a_positional_embedded_via_at_is_quoted_when_it_contains_spaces() -> None:
    """``@$1`` must carry a space-containing path through to ``_FILE_REF`` whole.

    ``_FILE_REF`` stops at the first whitespace, so a bare substitution would
    turn ``@src/a b.py`` into a reference to ``src/a``. Quoting only kicks in
    right after ``@``, so a plain ``$1`` elsewhere in the body is untouched.
    """
    assert substitute_arguments("review @$1", '"src/a b.py"') == 'review @"src/a b.py"'


def test_a_missing_positional_becomes_empty_not_literal() -> None:
    """Leaving a literal $3 would put a stray token in the prompt."""
    assert substitute_arguments("a=$1 b=$2 c=$3", "one") == "a=one b= c="


def test_arguments_and_positionals_coexist() -> None:
    """$ARGUMENTS sees the whole string; positionals see the split pieces."""
    out = substitute_arguments("all=[$ARGUMENTS] first=$1", "x y")
    assert out == "all=[x y] first=x"


def test_a_dollar_that_is_not_a_placeholder_survives() -> None:
    assert substitute_arguments("cost is $5.00 and $0 and $x", "arg") == (
        "cost is $5.00 and $0 and $x"
    )


def test_no_arguments_leaves_placeholders_empty() -> None:
    assert substitute_arguments("[$ARGUMENTS] [$1]", "") == "[] []"


# ---------------------------------------------------------------- @file


def _resolver(files: dict[str, str]):
    def _resolve(ref: str) -> str:
        if ref not in files:
            raise ValueError("not found")
        return files[ref]

    return _resolve


def test_a_file_reference_is_embedded_with_delimiters() -> None:
    result = expand_file_refs("look at @src/a.py please", _resolver({"src/a.py": "CODE"}))
    assert "CODE" in result.text
    assert "--- src/a.py ---" in result.text
    assert result.embedded == ["src/a.py"]
    assert result.errors == []


def test_an_unreadable_file_is_marked_not_silently_dropped() -> None:
    """An empty section would make the model answer about a file it never saw."""
    result = expand_file_refs("check @missing.py", _resolver({}))
    assert "could not read @missing.py" in result.text
    assert result.errors
    assert result.embedded == []


def test_embedded_content_is_capped() -> None:
    # The payload character must not appear in the filename, or the assertion
    # counts the delimiters too -- "big.txt" contains an "x".
    result = expand_file_refs("@big.log", _resolver({"big.log": "x" * 500}), max_chars=100)
    assert "truncated at 100" in result.text
    assert result.text.count("x") == 100


def test_trailing_punctuation_is_not_part_of_the_path() -> None:
    result = expand_file_refs("see @a.py, then stop", _resolver({"a.py": "OK"}))
    assert result.embedded == ["a.py"]
    assert result.text.rstrip().endswith("then stop")


def test_an_email_like_token_is_not_a_file_reference() -> None:
    result = expand_file_refs("ping me@example.com about it", _resolver({}))
    assert result.embedded == []
    assert result.errors == []
    assert result.text == "ping me@example.com about it"


def test_without_a_resolver_nothing_is_embedded() -> None:
    result = expand_file_refs("see @a.py", None)
    assert result.text == "see @a.py"


# ---------------------------------------------------------------- ordering


def test_arguments_are_substituted_before_file_refs() -> None:
    """@$1 must embed the file the caller named — the load-bearing ordering.

    Expanding files first would look for a file literally called "$1".
    """
    result = expand_command("review @$1", "src/a.py", _resolver({"src/a.py": "CODE"}))
    assert "CODE" in result.text
    assert result.embedded == ["src/a.py"]


def test_at_dollar_one_embeds_a_quoted_path_with_spaces() -> None:
    """``expand_command("review @$1", '"src/a b.py"', …)`` embeds ``src/a b.py``."""
    result = expand_command("review @$1", '"src/a b.py"', _resolver({"src/a b.py": "CODE"}))
    assert "CODE" in result.text
    assert result.embedded == ["src/a b.py"]


def test_a_command_with_neither_is_returned_unchanged() -> None:
    result = expand_command("just a prompt", "", _resolver({}))
    assert result.text == "just a prompt"
    assert result.errors == []


def test_embed_count_is_capped() -> None:
    body = " ".join(f"@{i}.txt" for i in range(25))
    resolver = _resolver({f"{i}.txt": "x" for i in range(25)})
    result = expand_file_refs(body, resolver, max_embeds=20)
    assert len(result.embedded) == 20
    assert any("embed limit reached" in err for err in result.errors)


def test_expand_command_forwards_max_embeds() -> None:
    body = "@a.txt @b.txt"
    result = expand_command(body, "", _resolver({"a.txt": "A", "b.txt": "B"}), max_embeds=1)
    assert len(result.embedded) == 1
    assert any("embed limit reached" in err for err in result.errors)

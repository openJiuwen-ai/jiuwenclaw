from jiuwenswarm.common.think_tags import ThinkTagStreamParser, split_think_tags, strip_think_tags


def test_split_think_tags_separates_reasoning_from_visible_text():
    parts = split_think_tags("before <think>hidden</think> after")

    assert [(part.kind, part.content) for part in parts] == [
        ("text", "before "),
        ("reasoning", "hidden"),
        ("text", " after"),
    ]
    assert strip_think_tags("before <think>hidden</think> after") == "before  after"


def test_stream_parser_keeps_partial_tags_out_of_visible_text():
    parser = ThinkTagStreamParser()

    parts = []
    parts.extend(parser.feed("answer <thi"))
    parts.extend(parser.feed("nk>secret</thi"))
    parts.extend(parser.feed("nk> done"))
    parts.extend(parser.flush())

    assert [(part.kind, part.content) for part in parts] == [
        ("text", "answer "),
        ("reasoning", "secret"),
        ("text", " done"),
    ]


def test_stream_parser_treats_unclosed_think_as_reasoning_on_flush():
    parser = ThinkTagStreamParser()

    parts = [*parser.feed("<think>hidden"), *parser.flush()]

    assert [(part.kind, part.content) for part in parts] == [("reasoning", "hidden")]


def test_stream_parser_handles_orphan_closing_think_tag():
    parts = split_think_tags("hidden</think>answer")

    assert [(part.kind, part.content) for part in parts] == [
        ("reasoning", "hidden"),
        ("text", "answer"),
    ]


def test_stream_parser_does_not_treat_thinking_as_think_tag():
    parts = split_think_tags("plain <thinking>visible</thinking> text")

    assert [(part.kind, part.content) for part in parts] == [
        ("text", "plain <thinking>visible</thinking> text")
    ]

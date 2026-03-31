"""Tests for app.titling — session title generation helpers and heuristic path."""

import pytest

from app.titling import (
    _is_trivial,
    _clean_message,
    _score,
    _is_system_junk,
    _extract_user_texts,
    _to_title,
    _heuristic_title,
    _validate_llm_title,
    _cli_title,
    smart_title,
)


# ---------------------------------------------------------------------------
# _is_trivial
# ---------------------------------------------------------------------------

class TestIsTrivial:

    def test_empty_string_is_trivial(self):
        assert _is_trivial("") is True

    def test_single_trivial_word(self):
        assert _is_trivial("yes") is True
        assert _is_trivial("ok") is True
        assert _is_trivial("thanks") is True

    def test_two_trivial_words(self):
        assert _is_trivial("ok thanks") is True
        assert _is_trivial("sure, ok") is True

    def test_three_or_more_words_not_trivial(self):
        assert _is_trivial("yes ok thanks") is False

    def test_non_trivial_word(self):
        assert _is_trivial("refactor") is False
        assert _is_trivial("fibonacci") is False

    def test_case_insensitive(self):
        assert _is_trivial("YES") is True
        assert _is_trivial("Ok") is True

    def test_punctuation_stripped(self):
        assert _is_trivial("ok!") is True
        assert _is_trivial("thanks.") is True
        assert _is_trivial("sure?") is True


# ---------------------------------------------------------------------------
# _clean_message
# ---------------------------------------------------------------------------

class TestCleanMessage:

    def test_strips_xml_tags(self):
        result = _clean_message("<system>stuff</system> hello")
        assert "system" not in result
        assert "hello" in result

    def test_strips_self_closing_tags(self):
        result = _clean_message("<br/> hello")
        assert "br" not in result
        assert "hello" in result

    def test_strips_continuation_preamble(self):
        text = "This session is being continued from earlier.\nNow do X"
        result = _clean_message(text)
        assert "being continued" not in result

    def test_strips_what_we_were_working_on(self):
        text = "**What we were working on:** some context\n\nReal content here"
        result = _clean_message(text)
        assert "working on" not in result
        assert "Real content here" in result

    def test_strips_line_number_arrows(self):
        text = "  42\u2192some code\n  43\u2192more code"
        result = _clean_message(text)
        assert "\u2192" not in result

    def test_normalises_whitespace(self):
        result = _clean_message("  hello   world  ")
        assert result == "hello world"

    def test_empty_string(self):
        result = _clean_message("")
        assert result == ""


# ---------------------------------------------------------------------------
# _score
# ---------------------------------------------------------------------------

class TestScore:

    def test_short_text_scores_zero(self):
        assert _score("hi") == 0.0
        assert _score("ok no") == 0.0

    def test_longer_text_scores_higher(self):
        short = _score("write a function")
        long = _score("write a comprehensive fibonacci function with memoization")
        assert long > short

    def test_long_words_increase_score(self):
        # "refactoring" and "optimization" are >6 chars
        score = _score("refactoring optimization patterns")
        assert score > 0

    def test_empty_string(self):
        assert _score("") == 0.0


# ---------------------------------------------------------------------------
# _is_system_junk
# ---------------------------------------------------------------------------

class TestIsSystemJunk:

    def test_agent_catalog_detected(self):
        assert _is_system_junk("# Available Agents\nYou have 72 agents") is True

    def test_specialist_agents_detected(self):
        assert _is_system_junk("You have 50 specialist agents available in your workforce.") is True

    def test_continuation_preamble(self):
        assert _is_system_junk("This session is being continued from an earlier conversation.") is True
        assert _is_system_junk("This is a continuation of the previous work.") is True

    def test_read_tool_arrows(self):
        text = "  1\u2192code\n  2\u2192more\n  3\u2192stuff"
        assert _is_system_junk(text) is True

    def test_file_dump_detected(self):
        # Long text, no question marks, lots of code chars
        code = "function foo() { return bar[0].baz(); }" * 20
        assert _is_system_junk(code) is True

    def test_normal_user_message(self):
        assert _is_system_junk("Help me write a fibonacci function") is False

    def test_normal_question(self):
        assert _is_system_junk("How do I fix this bug?") is False

    def test_user_opened_prefix(self):
        assert _is_system_junk("The user opened this session to work on feature X") is True


# ---------------------------------------------------------------------------
# _extract_user_texts
# ---------------------------------------------------------------------------

class TestExtractUserTexts:

    def test_extracts_user_messages(self):
        messages = [
            {"role": "user", "content": "Write a fibonacci function"},
            {"role": "assistant", "content": "Sure, here it is"},
            {"role": "user", "content": "Now add memoization"},
        ]
        texts = _extract_user_texts(messages)
        assert len(texts) == 2
        assert "fibonacci" in texts[0].lower()

    def test_skips_tool_results(self):
        messages = [
            {"role": "user", "content": "hello", "type": "tool_result"},
            {"role": "user", "content": "Write a function"},
        ]
        texts = _extract_user_texts(messages)
        assert len(texts) == 1

    def test_skips_trivial_messages(self):
        messages = [
            {"role": "user", "content": "yes"},
            {"role": "user", "content": "ok"},
            {"role": "user", "content": "Write a sorting algorithm"},
        ]
        texts = _extract_user_texts(messages)
        assert len(texts) == 1
        assert "sorting" in texts[0].lower()

    def test_skips_system_junk(self):
        messages = [
            {"role": "user", "content": "# Available Agents\nYou have 50 specialist agents"},
            {"role": "user", "content": "Write me a web server"},
        ]
        texts = _extract_user_texts(messages)
        assert len(texts) == 1
        assert "web server" in texts[0].lower()

    def test_respects_max_msgs(self):
        messages = [{"role": "user", "content": f"Task number {i}"} for i in range(20)]
        texts = _extract_user_texts(messages, max_msgs=3)
        assert len(texts) == 3

    def test_truncates_long_messages(self):
        messages = [{"role": "user", "content": "x" * 500}]
        texts = _extract_user_texts(messages, max_chars=100)
        assert len(texts[0]) == 100

    def test_empty_messages_list(self):
        assert _extract_user_texts([]) == []

    def test_skips_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "I can help with that"},
        ]
        assert _extract_user_texts(messages) == []


# ---------------------------------------------------------------------------
# _to_title
# ---------------------------------------------------------------------------

class TestToTitle:

    def test_strips_common_prefixes(self):
        result = _to_title("can you write a fibonacci function")
        assert not result.lower().startswith("can you")
        assert "fibonacci" in result.lower()

    def test_capitalises_first_letter(self):
        result = _to_title("build a web app")
        assert result[0].isupper()

    def test_truncates_long_text(self):
        long_text = "word " * 50
        result = _to_title(long_text, max_chars=65)
        assert len(result) <= 66  # 65 + ellipsis char

    def test_strips_trailing_punctuation(self):
        result = _to_title("fix the bug.")
        assert not result.endswith(".")

    def test_takes_first_sentence(self):
        result = _to_title("Fix the login page. Also update the tests.")
        assert "Also" not in result

    def test_takes_up_to_newline(self):
        result = _to_title("Fix the login page\nUpdate the tests too")
        assert "Update" not in result

    def test_empty_string(self):
        result = _to_title("")
        assert result == ""

    def test_strips_comment_prefixes(self):
        result = _to_title("# Fix the bug")
        assert not result.startswith("#")

    def test_please_prefix_stripped(self):
        result = _to_title("please fix the login page")
        assert not result.lower().startswith("please")


# ---------------------------------------------------------------------------
# _heuristic_title
# ---------------------------------------------------------------------------

class TestHeuristicTitle:

    def test_generates_title_from_first_user_message(self):
        messages = [
            {"role": "user", "content": "Write a fibonacci function in Python"},
            {"role": "assistant", "content": "Sure!"},
        ]
        title = _heuristic_title(messages)
        assert "fibonacci" in title.lower() or "Fibonacci" in title

    def test_combines_short_title_with_second_message(self):
        messages = [
            {"role": "user", "content": "Fix bug"},
            {"role": "assistant", "content": "Which bug?"},
            {"role": "user", "content": "The login validation error on mobile"},
        ]
        title = _heuristic_title(messages)
        # Should be "Fix bug — ..." or similar combined form
        assert len(title) > len("Fix bug")

    def test_returns_untitled_for_empty(self):
        assert _heuristic_title([]) == "Untitled Session"

    def test_returns_untitled_for_trivial_only(self):
        messages = [
            {"role": "user", "content": "ok"},
            {"role": "user", "content": "yes"},
        ]
        assert _heuristic_title(messages) == "Untitled Session"


# ---------------------------------------------------------------------------
# _validate_llm_title
# ---------------------------------------------------------------------------

class TestValidateLlmTitle:

    def test_valid_title_passes(self):
        result = _validate_llm_title("Debug idle state after messaging",
                                     ["I'm hitting issues where it goes into idle state after messaging"])
        assert result == "Debug idle state after messaging"

    def test_strips_quotes_and_trailing_dot(self):
        result = _validate_llm_title('"Fix login page."',
                                     ["fix the login page"])
        assert result == "Fix login page"

    def test_rejects_empty_title(self):
        assert _validate_llm_title("", ["some text"]) is None

    def test_rejects_too_short(self):
        assert _validate_llm_title("AB", ["some text"]) is None

    def test_rejects_too_long(self):
        assert _validate_llm_title("x " * 50, ["some text"]) is None

    def test_rejects_single_word(self):
        assert _validate_llm_title("Debug", ["debug the thing"]) is None

    def test_rejects_all_caps(self):
        assert _validate_llm_title("ALL CAPS TITLE", ["all caps title"]) is None

    def test_allows_short_all_caps(self):
        # 4 chars or less all-caps is allowed (e.g. "API" — though single word blocked)
        assert _validate_llm_title("Fix API bug", ["fix api bug"]) is not None

    def test_rejects_no_word_overlap(self):
        assert _validate_llm_title("Quantum computing introduction",
                                   ["fix the login bug"]) is None

    def test_allows_short_word_only_title(self):
        # Title with only short words (<=3 chars) — can't validate overlap, so allow
        result = _validate_llm_title("Fix the bug", ["something completely different"])
        # "Fix" and "the" and "bug" are all <=3 chars, so overlap check is skipped
        assert result == "Fix the bug"


# ---------------------------------------------------------------------------
# _cli_title
# ---------------------------------------------------------------------------

class TestCliTitle:

    def test_returns_none_for_empty_messages(self):
        assert _cli_title([]) is None

    def test_returns_none_for_trivial_only(self):
        messages = [{"role": "user", "content": "ok"}]
        assert _cli_title(messages) is None

    def test_returns_none_when_cli_not_found(self):
        import unittest.mock
        messages = [{"role": "user", "content": "Write a fibonacci function in Python"}]
        with unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError):
            assert _cli_title(messages) is None

    def test_returns_none_on_timeout(self):
        import subprocess
        import unittest.mock
        messages = [{"role": "user", "content": "Write a fibonacci function in Python"}]
        with unittest.mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 20)):
            assert _cli_title(messages) is None

    def test_returns_none_on_nonzero_exit(self):
        import unittest.mock
        messages = [{"role": "user", "content": "Write a fibonacci function in Python"}]
        mock_result = unittest.mock.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"
        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            assert _cli_title(messages) is None

    def test_parses_valid_cli_output(self):
        import unittest.mock
        messages = [{"role": "user", "content": "Write a fibonacci function in Python"}]
        mock_result = unittest.mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Fibonacci function in Python\n"
        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            title = _cli_title(messages)
        assert title == "Fibonacci function in Python"


# ---------------------------------------------------------------------------
# smart_title (heuristic fallback path)
# ---------------------------------------------------------------------------

class TestSmartTitle:

    def test_falls_back_to_heuristic_when_no_anthropic(self):
        """smart_title should work even without anthropic SDK."""
        messages = [
            {"role": "user", "content": "Write a REST API with Flask"},
            {"role": "assistant", "content": "Sure, let me help."},
        ]
        # Patch anthropic import to fail
        import unittest.mock
        with unittest.mock.patch.dict("sys.modules", {"anthropic": None}):
            title = smart_title(messages)
        assert isinstance(title, str)
        assert len(title) > 0
        # Should contain something related to the message
        assert "REST" in title or "Flask" in title or "API" in title

    def test_returns_string_for_trivial_input(self):
        messages = [{"role": "user", "content": "hi"}]
        import unittest.mock
        with unittest.mock.patch.dict("sys.modules", {"anthropic": None}):
            title = smart_title(messages)
        assert isinstance(title, str)
        assert title == "Untitled Session"

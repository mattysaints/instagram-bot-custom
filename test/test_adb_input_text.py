from GramAddict.core.views import (
    adb_input_text_command_arg,
    adb_input_text_stderr_is_failure,
)


def test_adb_input_text_command_arg_quotes_hashtags():
    assert adb_input_text_command_arg("#posing") == "'#posing'"


def test_adb_input_text_command_arg_converts_spaces():
    assert adb_input_text_command_arg("fit pose") == "fit%spose"


def test_adb_input_text_stderr_is_failure_for_invalid_arguments_with_zero_rc():
    stderr = """Error: Invalid arguments for command: text
Usage: input [<source>] <command> [<arg>...]"""

    assert adb_input_text_stderr_is_failure(stderr)


def test_adb_input_text_stderr_allows_empty_stderr():
    assert not adb_input_text_stderr_is_failure("")

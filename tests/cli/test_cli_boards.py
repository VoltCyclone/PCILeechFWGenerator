import pytest

from pcileechfwgenerator.cli import cli


def test_get_supported_boards_returns_list():
    boards = cli.get_supported_boards()
    assert isinstance(boards, list)
    assert all(isinstance(b, str) for b in boards)
    assert len(boards) > 0


def test_board_choices_match_discovery():
    boards_from_func = set(cli.get_supported_boards())
    # Import direct from board_config for ground truth
    from pcileechfwgenerator.device_clone.board_config import list_supported_boards

    boards_from_config = set(list_supported_boards())
    assert boards_from_func == boards_from_config


# Optionally, test CLI argparser choices
import argparse


def test_build_sub_board_choices():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    cli.build_sub(subparsers)
    build_parser = subparsers.choices["build"]
    board_arg = next(a for a in build_parser._actions if a.dest == "board")
    assert set(board_arg.choices) == set(cli.get_supported_boards())

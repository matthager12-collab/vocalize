"""Swap with an in-memory pasteboard, never the system clipboard."""
import pytest
from click.testing import CliRunner

from vocalize import cli, dictate


@pytest.fixture
def board(monkeypatch):
    state = {'ok': True, 'plain': 'Clean words', 'said': 'um clean words', 'change': 7}
    notices, writes = [], []
    race = [False]

    def clipboard(request):
        if request['op'] == 'read':
            return state.copy()
        if race[0]:
            state.update(plain='foreign copy', said=None, change=8)
        if request['expect'] != state['change']:
            return {'ok': False, 'reason': 'changed'}
        writes.append(request)
        state.update(plain=request['plain'], said=request['said'], change=state['change'] + 1)
        return {'ok': True, 'change': state['change']}

    monkeypatch.setattr(dictate, '_clipboard', clipboard)
    monkeypatch.setattr(dictate, '_notify', notices.append)
    return state, notices, writes, race


def test_swap_and_swap_back(board):
    state, notices, writes, _ = board
    assert dictate.swap({}) == 0
    assert (state['plain'], state['said']) == ('um clean words', 'Clean words')
    assert dictate.swap({}) == 0
    assert (state['plain'], state['said']) == ('Clean words', 'um clean words')
    assert [w['expect'] for w in writes] == [7, 8]
    assert notices == [dictate._NOTIFY_SWAPPED] * 2


@pytest.mark.parametrize('said', [None, '', 123])
def test_without_private_text_changes_nothing(board, said):
    state, notices, writes, _ = board
    state['said'] = said
    before = state.copy()
    assert dictate.swap({}) == 0
    assert state == before
    assert writes == []
    assert notices == [dictate._NOTIFY_NO_SWAP]


def test_change_between_read_and_write_refuses(board):
    state, notices, writes, race = board
    race[0] = True
    assert dictate.swap({}) == 0
    assert state['plain'] == 'foreign copy'
    assert writes == []
    assert notices == [dictate._NOTIFY_SWAP_CHANGED]


def test_sanitizes_both_types(board):
    state, _, _, _ = board
    state.update(plain='Clean\nwords\x00', said='um\nclean\twords\x1b')
    dictate.swap({})
    assert (state['plain'], state['said']) == ('um clean words', 'Clean words')


@pytest.mark.parametrize('field,value', [('plain', None), ('plain', 2), ('change', None),
                                        ('change', True), ('change', '7')])
def test_malformed_board_does_not_write(board, field, value):
    state, notices, writes, _ = board
    state[field] = value
    dictate.swap({})
    assert writes == []
    assert notices == [dictate._NOTIFY_CLIPBOARD_FAILED]


def test_cli_reaches_swap(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, '_stt_options', lambda *a: {'cleanup': 'off'})
    monkeypatch.setattr(dictate, 'swap', lambda stt: calls.append(stt) or 0)
    result = CliRunner().invoke(cli.main, ['dictate', '--swap'])
    assert result.exit_code == 0, result.output
    assert calls == [{'cleanup': 'off'}]


@pytest.mark.parametrize('mode', ['--start', '--stop', '--pause', '--resume'])
def test_cli_modes_are_exclusive(mode):
    result = CliRunner().invoke(cli.main, ['dictate', '--swap', mode])
    assert result.exit_code == 2
    assert 'Use only one' in result.output


@pytest.mark.parametrize('reply', [None, {'ok': False, 'reason': 'error'}])
def test_read_failure_notifies(monkeypatch, reply):
    notices = []
    monkeypatch.setattr(dictate, '_clipboard', lambda request: reply)
    monkeypatch.setattr(dictate, '_notify', notices.append)
    assert dictate.swap({}) == 0
    assert notices == [dictate._NOTIFY_CLIPBOARD_FAILED]


def test_swap_notifications_are_fixed():
    assert {dictate._NOTIFY_SWAPPED, dictate._NOTIFY_NO_SWAP,
            dictate._NOTIFY_SWAP_CHANGED} <= dictate._FIXED_NOTIFICATIONS


def test_read_race_notifies_without_writing(monkeypatch):
    requests, notices = [], []

    def clipboard(request):
        requests.append(request)
        return {'ok': False, 'reason': 'changed'}

    monkeypatch.setattr(dictate, '_clipboard', clipboard)
    monkeypatch.setattr(dictate, '_notify', notices.append)
    assert dictate.swap({}) == 0
    assert requests == [{'op': 'read'}]
    assert notices == [dictate._NOTIFY_SWAP_CHANGED]

import json

import pytest

from prometejs_miner.stratum import messages


def test_subscribe_response_wire_format():
    sub = messages.Subscribe.parse(
        json.loads('{"id": 1, "method": "mining.subscribe", "params": ["bitaxe v2.2"]}')
    )
    assert sub.user_agent == "bitaxe"
    assert (
        sub.response_line("57a6f098")
        == '{"id":1,"error":null,"result":[[["mining.notify","57a6f098"]],"57a6f098",8]}\n'
    )


def test_user_agent_refinement():
    assert messages.refine_user_agent("cpuminer/2.5.1") == "cpuminer"
    assert messages.refine_user_agent("bosminer 1.0") == "Braiins OS"
    assert messages.refine_user_agent("bitaxe v2.2") == "bitaxe"


def test_configure_response():
    conf = messages.Configure.parse(
        json.loads(
            '{"id": 2, "method": "mining.configure",'
            ' "params": [["version-rolling"], {"version-rolling.mask": "ffffffff"}]}'
        )
    )
    result = json.loads(conf.response_line())
    assert result["result"] == {"version-rolling": True, "version-rolling.mask": "1fffe000"}


def test_authorize_parses_address_and_worker():
    auth = messages.Authorize.parse(
        json.loads(
            '{"id": 3, "method": "mining.authorize",'
            ' "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4.bitaxe3", "x"]}'
        ),
        network="testnet",
    )
    assert auth.address == "tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4"
    assert auth.worker == "bitaxe3"
    assert auth.starting_diff is None
    assert auth.response_line() == '{"id":3,"error":null,"result":true}\n'


def test_authorize_default_worker_and_starting_diff():
    auth = messages.Authorize.parse(
        {
            "id": 3,
            "method": "mining.authorize",
            "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4", "d=65536"],
        },
        network="testnet",
    )
    assert auth.worker == "worker"
    assert auth.starting_diff == 65536


def test_authorize_rejects_wrong_network():
    with pytest.raises(messages.MessageError):
        messages.Authorize.parse(
            {"id": 3, "method": "mining.authorize", "params": ["tb1qumezefzdeqqwn5zfvgdrhxjzc5ylr39uhuxcz4", "x"]},
            network="mainnet",
        )


def test_authorize_rejects_garbage_address():
    with pytest.raises(messages.MessageError):
        messages.Authorize.parse(
            {"id": 3, "method": "mining.authorize", "params": ["not-an-address", "x"]},
            network="testnet",
        )


def test_submit_parse_and_defaults():
    sub = messages.Submit.parse(
        json.loads(
            '{"id": 5, "method": "mining.submit", "params":'
            ' ["addr.w", "1", "c708000000000000", "64b3f3ec", "ed460d91", "00002000"]}'
        )
    )
    assert sub.job_id == "1"
    assert sub.version_mask == "00002000"
    assert sub.response_line() == '{"id":5,"error":null,"result":true}\n'

    no_mask = messages.Submit.parse(
        {"id": 5, "method": "mining.submit", "params": ["a", "1", "c708000000000000", "64b3f3ec", "ed460d91"]}
    )
    assert no_mask.version_mask == "0"


def test_submit_rejects_short_extranonce2():
    # exactly EXTRANONCE2_SIZE_BYTES*2 hex chars required
    with pytest.raises(messages.MessageError):
        messages.Submit.parse(
            {"id": 5, "method": "mining.submit", "params": ["a", "1", "c7080000", "64b3f3ec", "ed460d91", "00002000"]}
        )


def test_error_line_wire_format():
    assert (
        messages.error_line(5, messages.LOW_DIFFICULTY_SHARE, "Difficulty too low")
        == '{"id":5,"result":null,"error":[23,"Difficulty too low",""]}\n'
    )


def test_set_difficulty_line():
    assert (
        messages.set_difficulty_line(512)
        == '{"id":null,"method":"mining.set_difficulty","params":[512]}\n'
    )
    assert (
        messages.set_difficulty_line(0.1)
        == '{"id":null,"method":"mining.set_difficulty","params":[0.1]}\n'
    )

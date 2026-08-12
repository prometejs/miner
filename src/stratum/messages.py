"""Stratum V1 request parsing and response construction.

Responses use compact JSON separators — the wire format miners expect and the
exact byte layout locked in by the golden-vector tests.
"""

import json
import re
from dataclasses import dataclass

from ..bitcoin.coinbase import EXTRANONCE2_SIZE_BYTES
from ..bitcoin.encoding import address_to_script

# error codes (eStratumErrorCode)
OTHER_UNKNOWN = 20
JOB_NOT_FOUND = 21
DUPLICATE_SHARE = 22
LOW_DIFFICULTY_SHARE = 23
UNAUTHORIZED_WORKER = 24
NOT_SUBSCRIBED = 25

VERSION_ROLLING_MASK = "1fffe000"


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def error_line(msg_id, code: int, message: str) -> str:
    return dumps({"id": msg_id, "result": None, "error": [code, message, ""]}) + "\n"


class MessageError(ValueError):
    def __init__(self, msg_id, message: str):
        super().__init__(message)
        self.msg_id = msg_id


def refine_user_agent(user_agent: str) -> str:
    refined = user_agent.split(" ")[0].split("/")[0].split("V")[0].split("-")[0]
    if "bosminer" in refined or "bOS" in refined:
        return "Braiins OS"
    if "cpuminer" in refined:
        return "cpuminer"
    return refined


@dataclass
class Subscribe:
    id: int | None
    user_agent: str

    @classmethod
    def parse(cls, msg: dict) -> "Subscribe":
        params = msg.get("params")
        if not isinstance(params, list):
            raise MessageError(msg.get("id"), "Subscription validation error")
        raw_agent = params[0] if params and params[0] is not None else None
        user_agent = "unknown" if raw_agent is None else refine_user_agent(str(raw_agent))
        if len(user_agent) > 128:
            raise MessageError(msg.get("id"), "Subscription validation error")
        return cls(id=msg.get("id"), user_agent=user_agent)

    def response_line(self, extranonce1: str) -> str:
        return (
            dumps(
                {
                    "id": self.id,
                    "error": None,
                    "result": [
                        [["mining.notify", extranonce1]],
                        extranonce1,
                        EXTRANONCE2_SIZE_BYTES,
                    ],
                }
            )
            + "\n"
        )


@dataclass
class Configure:
    id: int | None

    @classmethod
    def parse(cls, msg: dict) -> "Configure":
        if not isinstance(msg.get("params"), list):
            raise MessageError(msg.get("id"), "Configuration validation error")
        return cls(id=msg.get("id"))

    def response_line(self) -> str:
        return (
            dumps(
                {
                    "id": self.id,
                    "error": None,
                    "result": {
                        "version-rolling": True,
                        "version-rolling.mask": VERSION_ROLLING_MASK,
                    },
                }
            )
            + "\n"
        )


@dataclass
class Authorize:
    id: int | None
    address: str
    worker: str
    password: str | None
    starting_diff: int | None

    @classmethod
    def parse(cls, msg: dict, network: str) -> "Authorize":
        params = msg.get("params")
        if not isinstance(params, list) or not (1 <= len(params) <= 2) or not isinstance(params[0], str):
            raise MessageError(msg.get("id"), "Authorization validation error")
        parts = params[0].split(".")
        address = parts[0]
        worker = parts[1] if len(parts) > 1 and parts[1] else "worker"
        if len(worker) > 64:
            raise MessageError(msg.get("id"), "Authorization validation error")
        try:
            address_to_script(address, network)
        except (ValueError, KeyError):
            raise MessageError(msg.get("id"), "Authorization validation error")
        password = params[1] if len(params) > 1 and isinstance(params[1], str) else None
        starting_diff = None
        if password:
            match = re.search(r"(?:^|,)d=(\d+)(?:,|$)", password)
            if match:
                starting_diff = int(match.group(1))
        return cls(
            id=msg.get("id"),
            address=address,
            worker=worker,
            password=password,
            starting_diff=starting_diff,
        )

    def response_line(self) -> str:
        return dumps({"id": self.id, "error": None, "result": True}) + "\n"


@dataclass
class SuggestDifficulty:
    id: int | None
    suggested_difficulty: float

    @classmethod
    def parse(cls, msg: dict) -> "SuggestDifficulty":
        params = msg.get("params")
        if not isinstance(params, list) or len(params) != 1:
            raise MessageError(msg.get("id"), "Suggest difficulty validation error")
        try:
            difficulty = float(params[0])
        except (TypeError, ValueError):
            raise MessageError(msg.get("id"), "Suggest difficulty validation error")
        return cls(id=msg.get("id"), suggested_difficulty=difficulty)


def set_difficulty_line(difficulty: float) -> str:
    value = int(difficulty) if float(difficulty).is_integer() else difficulty
    return dumps({"id": None, "method": "mining.set_difficulty", "params": [value]}) + "\n"


@dataclass
class Submit:
    id: int | None
    user_id: str
    job_id: str
    extranonce2: str
    ntime: str
    nonce: str
    version_mask: str

    @classmethod
    def parse(cls, msg: dict) -> "Submit":
        params = msg.get("params")
        if not isinstance(params, list) or not (5 <= len(params) <= 6):
            raise MessageError(msg.get("id"), "Mining Submit validation error")
        if not all(isinstance(p, str) for p in params[:5]):
            raise MessageError(msg.get("id"), "Mining Submit validation error")
        extranonce2 = params[2]
        if len(extranonce2) != EXTRANONCE2_SIZE_BYTES * 2:
            raise MessageError(msg.get("id"), "Mining Submit validation error")
        version_mask = params[5] if len(params) > 5 and params[5] is not None else "0"
        for hex_field in (extranonce2, params[3], params[4], version_mask):
            try:
                int(hex_field, 16)
            except ValueError:
                raise MessageError(msg.get("id"), "Mining Submit validation error")
        return cls(
            id=msg.get("id"),
            user_id=params[0],
            job_id=params[1],
            extranonce2=extranonce2,
            ntime=params[3],
            nonce=params[4],
            version_mask=version_mask,
        )

    def response_line(self) -> str:
        return dumps({"id": self.id, "error": None, "result": True}) + "\n"

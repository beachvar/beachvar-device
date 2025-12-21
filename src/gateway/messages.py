"""
Message types for gateway communication.
"""

from typing import Optional, Any, Literal
from pydantic import BaseModel


# Device -> Gateway messages

class AuthMessage(BaseModel):
    type: Literal['auth'] = 'auth'
    payload: dict  # Contains device_id and token


class HeartbeatMessage(BaseModel):
    type: Literal['heartbeat'] = 'heartbeat'
    payload: dict  # Contains uptime, cpuUsage, memoryUsage, temperature


class CamerasMessage(BaseModel):
    type: Literal['cameras'] = 'cameras'
    payload: dict  # Contains cameras list


class CommandResponseMessage(BaseModel):
    type: Literal['command_response'] = 'command_response'
    request_id: str
    payload: Any


# Gateway -> Device messages

class AuthResultMessage(BaseModel):
    type: Literal['auth_result'] = 'auth_result'
    payload: dict  # Contains success, error, newToken


class CommandMessage(BaseModel):
    type: Literal['command'] = 'command'
    request_id: str
    payload: dict  # Contains action, params


class HeartbeatAckMessage(BaseModel):
    type: Literal['heartbeat_ack'] = 'heartbeat_ack'

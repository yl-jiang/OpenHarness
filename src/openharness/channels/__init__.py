"""OpenHarness channels subsystem.

Provides a message-bus architecture for integrating chat platforms
(Telegram, Discord, Slack, etc.) with the OpenHarness query engine.

Usage::

    from openharness.channels import BaseChannel, ChannelManager, MessageBus
"""

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.base import BaseChannel
from openharness.channels.impl.manager import ChannelManager
from openharness.channels.impl import SUPPORTED_CHANNELS

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "InboundMessage",
    "MessageBus",
    "OutboundMessage",
    "SUPPORTED_CHANNELS",
]

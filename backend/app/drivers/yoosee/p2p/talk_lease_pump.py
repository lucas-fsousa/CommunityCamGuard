"""Account-thread handoff to the exact existing media lease, never a new route."""

from collections.abc import Callable

from .account_media_channel import AccountMediaChannel
from .cooperative_talk import CooperativeTalk
from .kcp_receive import ReceiveError
from .talk_inbox import TalkInbox, TalkOutcome, TalkTicket


class TalkLeasePump:
    def __init__(self, inbox: TalkInbox):
        self._inbox = inbox
        self._active: dict[TalkTicket, tuple[int, CooperativeTalk]] = {}

    def poll(self, channel_for: Callable[[int], AccountMediaChannel | None]) -> None:
        for ticket, (lease, talk) in tuple(self._active.items()):
            if self._inbox.should_cancel(ticket) or channel_for(lease) is None:
                self._inbox.cancel(ticket)
                talk.cancel()
            if talk.done or talk.closed:
                cancelled = self._inbox.should_cancel(ticket)
                self._inbox.finish(ticket, TalkOutcome(
                    "cancelled" if cancelled else "transport_received" if talk.done else "unavailable",
                    talk.acknowledged_frames, talk.microphone_stopped))
                del self._active[ticket]
        claimed = self._inbox.claim()
        if claimed is None:
            return
        ticket, lease, frames = claimed
        if self._inbox.should_cancel(ticket):
            self._inbox.finish(ticket, TalkOutcome("cancelled"))
            return
        channel = channel_for(lease)
        if channel is None:
            self._inbox.finish(ticket, TalkOutcome("unavailable"))
            return
        try:
            talk = channel.begin_talk(frames)
        except (ReceiveError, ValueError):
            self._inbox.finish(ticket, TalkOutcome("unavailable"))
            return
        self._active[ticket] = lease, talk

    def finish(self) -> None:
        self._inbox.close()
        for ticket, (_, talk) in self._active.items():
            self._inbox.finish(ticket, TalkOutcome("unavailable",
                talk.acknowledged_frames, talk.microphone_stopped))
        self._active.clear()

from services.notifier import (
    TelegramNotifier,
    format_signal_message,
    format_trade_closed_message,
    format_trade_filled_message,
    format_trade_cancelled_message,
)


class SignalRouter:
    """
    Fans a validated signal (or a closed-trade event) out to every
    configured notification channel. Adding a new destination (Discord,
    WhatsApp, a dashboard websocket, ...) means writing a class with a
    `.send(text)` method and appending it to `self.channels` -- nothing
    upstream (scanner, ai.py) needs to change.
    """

    def __init__(self, channels=None):
        self.channels = channels if channels is not None else [TelegramNotifier()]

    def fire_signal(self, signal, entry, risk, timeframe="M15"):
        message = format_signal_message(signal, entry, risk, timeframe=timeframe)
        for channel in self.channels:
            channel.send(message)

    def fire_trade_closed(self, trade):
        message = format_trade_closed_message(trade)
        for channel in self.channels:
            channel.send(message)

    def fire_trade_filled(self, trade, timeframe="M15"):
        message = format_trade_filled_message(trade, timeframe=timeframe)
        for channel in self.channels:
            channel.send(message)

    def fire_trade_cancelled(self, trade, timeframe="M15"):
        message = format_trade_cancelled_message(trade, timeframe=timeframe)
        for channel in self.channels:
            channel.send(message)

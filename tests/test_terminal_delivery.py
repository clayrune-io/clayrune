from mc.terminal_delivery import TerminalDeliveryGate


def test_terminal_delivery_continues_if_the_error_callback_raises(caplog):
    def failed_event():
        raise OSError('delivery failed')

    def failed_callback(_error):
        raise RuntimeError('callback failed')

    events = []
    gate = TerminalDeliveryGate(failed_callback)
    gate.submit(failed_event)
    gate.submit(lambda: events.append('queued'))

    gate.accept()
    gate.submit(lambda: events.append('later'))

    assert events == ['queued', 'later']
    assert 'terminal delivery error callback failed' in caplog.text

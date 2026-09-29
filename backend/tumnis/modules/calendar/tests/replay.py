"""Google's HTTP answers replayed from the recordings (P1-09): the real Google client and
connector run against them in the contract layer, with no socket opened. Interface stub
until the implementation."""

from tumnis.modules.integrations.api import Connector


def recorded_connector() -> Connector:
    """The registered real connector on the real client over the recorded responses."""
    raise NotImplementedError

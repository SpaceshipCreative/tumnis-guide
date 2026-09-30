"""github event payload models and subscribers. The payload models live in `payloads.py`
(re-exported here) so `api.py` can emit them; github subscribes to nothing (`tasks` reacts
to `artifact.updated`)."""

from tumnis.modules.github.payloads import GitHubFetchedV1

__all__ = ["GitHubFetchedV1"]

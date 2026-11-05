"""EC2 instance identity from IMDSv2, fetched once at startup."""

import logging
import urllib.request
from dataclasses import asdict, dataclass

IMDS = "http://169.254.169.254/latest"
TIMEOUT_SECONDS = 1

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstanceInfo:
    id: str = "local"
    az: str = "local"
    type: str = "local"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _imds_get(path: str, token: str) -> str:
    request = urllib.request.Request(  # noqa: S310 - fixed http:// link-local URL
        f"{IMDS}/meta-data/{path}", headers={"X-aws-ec2-metadata-token": token}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return response.read().decode()


def fetch_instance_info() -> InstanceInfo:
    """Return this instance's identity, or "local" when IMDS can't be reached.

    Containers are one extra network hop from the host, so this only works in Docker
    when the launch template sets http_put_response_hop_limit = 2.
    """
    try:
        token_request = urllib.request.Request(  # noqa: S310 - fixed http:// link-local URL
            f"{IMDS}/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        with urllib.request.urlopen(token_request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            token = response.read().decode()
        return InstanceInfo(
            id=_imds_get("instance-id", token),
            az=_imds_get("placement/availability-zone", token),
            type=_imds_get("instance-type", token),
        )
    except OSError as exc:
        log.info("Instance metadata unavailable, reporting 'local': %s", exc)
        return InstanceInfo()

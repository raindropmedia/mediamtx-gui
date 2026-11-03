"""Optional control of the MediaMTX container via the Docker Engine API.

Locked by default: only active when DOCKER_CONTROL_ENABLED=true AND
/var/run/docker.sock is mounted into this container AND MTX_CONTAINER_NAME
is configured. All functions fail soft (return a
descriptive error string) rather than raising, since Docker access is an
optional convenience feature, not a hard requirement of the GUI.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import get_settings

try:
    import docker
    from docker.errors import DockerException, NotFound
except ImportError:  # pragma: no cover - docker package always in requirements.txt
    docker = None


@dataclass
class ContainerStatus:
    available: bool
    name: str = ""
    status: str = ""
    image: str = ""
    started_at: str = ""
    error: str = ""


_LOCKED_MSG = "Docker-Steuerung ist gesperrt (DOCKER_CONTROL_ENABLED ist nicht aktiviert)."


def is_enabled() -> bool:
    return get_settings().docker_control_enabled


def _get_client():
    if not is_enabled():
        return None, _LOCKED_MSG
    if docker is None:
        return None, "Das docker-Paket ist nicht installiert."
    try:
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        client.ping()
        return client, ""
    except Exception as exc:  # noqa: BLE001 - want to surface any docker error as text
        return None, f"Docker-Socket nicht erreichbar: {exc}"


def is_configured() -> bool:
    return is_enabled() and bool(get_settings().mtx_container_name)


def get_status() -> ContainerStatus:
    if not is_enabled():
        return ContainerStatus(available=False, error=_LOCKED_MSG)
    name = get_settings().mtx_container_name
    if not name:
        return ContainerStatus(available=False, error="MTX_CONTAINER_NAME ist nicht gesetzt.")

    client, err = _get_client()
    if client is None:
        return ContainerStatus(available=False, error=err)

    try:
        container = client.containers.get(name)
        attrs = container.attrs
        return ContainerStatus(
            available=True,
            name=container.name,
            status=container.status,
            image=", ".join(container.image.tags) if container.image.tags else container.image.short_id,
            started_at=attrs.get("State", {}).get("StartedAt", ""),
        )
    except NotFound:
        return ContainerStatus(available=False, error=f"Container '{name}' nicht gefunden.")
    except Exception as exc:  # noqa: BLE001
        return ContainerStatus(available=False, error=str(exc))
    finally:
        client.close()


def _do(action: str) -> tuple[bool, str]:
    if not is_enabled():
        return False, _LOCKED_MSG
    name = get_settings().mtx_container_name
    if not name:
        return False, "MTX_CONTAINER_NAME ist nicht gesetzt."
    client, err = _get_client()
    if client is None:
        return False, err
    try:
        container = client.containers.get(name)
        getattr(container, action)()
        return True, ""
    except NotFound:
        return False, f"Container '{name}' nicht gefunden."
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        client.close()


def start() -> tuple[bool, str]:
    return _do("start")


def stop() -> tuple[bool, str]:
    return _do("stop")


def restart() -> tuple[bool, str]:
    return _do("restart")

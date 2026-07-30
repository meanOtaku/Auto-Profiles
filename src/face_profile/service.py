"""Headless service lifecycle for the project foundation."""

from enum import StrEnum

from face_profile.camera import FrameSourceLifecycle
from face_profile.config import AppConfig
from face_profile.settings import SettingsAdapter


class ServiceState(StrEnum):
    """Observable service lifecycle states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class ServiceStateError(RuntimeError):
    """Raised when a lifecycle operation cannot be completed safely."""


class Service:
    """Minimal headless lifecycle coordinator for M0 adapters."""

    def __init__(
        self,
        *,
        config: AppConfig,
        settings: SettingsAdapter,
        frame_source: FrameSourceLifecycle | None = None,
    ) -> None:
        self._config = config
        self._settings = settings
        self._frame_source = frame_source
        self._frame_source_active = False
        self._state = ServiceState.STOPPED

    @property
    def state(self) -> ServiceState:
        """Return the current lifecycle state."""

        return self._state

    def start(self) -> None:
        """Start configured resources and enter the running state."""

        if self._state is not ServiceState.STOPPED:
            raise ServiceStateError(f"cannot start service from state {self._state}")
        self._state = ServiceState.STARTING
        try:
            if self._config.camera.enabled:
                if self._frame_source is None:
                    raise ServiceStateError("camera is enabled but no frame source is configured")
                self._frame_source_active = True
                self._frame_source.open()
        except Exception as error:
            if self._frame_source_active and self._frame_source is not None:
                try:
                    self._frame_source.close()
                except Exception as cleanup_error:
                    error.add_note(f"camera cleanup also failed: {type(cleanup_error).__name__}")
                else:
                    self._frame_source_active = False
            self._state = ServiceState.FAILED
            raise
        self._state = ServiceState.RUNNING

    def stop(self) -> None:
        """Stop configured resources and enter the stopped state."""

        if self._state is ServiceState.STOPPED:
            return
        self._state = ServiceState.STOPPING
        try:
            if self._frame_source_active and self._frame_source is not None:
                self._frame_source.close()
                self._frame_source_active = False
        except Exception:
            self._state = ServiceState.FAILED
            raise
        self._state = ServiceState.STOPPED

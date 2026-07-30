from contextlib import suppress

import pytest


def test_service_starts_without_camera_and_stops_cleanly() -> None:
    from face_profile.config import AppConfig
    from face_profile.service import Service
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    service = Service(
        config=AppConfig(),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
    )

    service.start()
    assert service.state.value == "running"

    service.stop()
    assert service.state.value == "stopped"


def test_service_marks_failed_when_camera_open_fails() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class FailingSource:
        def open(self) -> None:
            raise OSError("camera unavailable")

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            pass

    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=FailingSource(),
    )

    with suppress(OSError):
        service.start()

    assert service.state is ServiceState.FAILED


def test_service_marks_failed_when_camera_close_fails() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class FailingCloseSource:
        def open(self) -> None:
            pass

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            raise OSError("close failed")

    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=FailingCloseSource(),
    )
    service.start()

    with suppress(OSError):
        service.stop()

    assert service.state is ServiceState.FAILED


def test_service_stop_is_idempotent_before_start() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class CountingSource:
        close_count = 0

        def open(self) -> None:
            pass

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            self.close_count += 1

    source = CountingSource()
    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=source,
    )

    service.stop()

    assert service.state is ServiceState.STOPPED
    assert source.close_count == 0


def test_service_rejects_double_start_without_reopening_resource() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState, ServiceStateError
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class CountingSource:
        open_count = 0

        def open(self) -> None:
            self.open_count += 1

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            pass

    source = CountingSource()
    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=source,
    )
    service.start()

    with pytest.raises(ServiceStateError, match="cannot start"):
        service.start()

    assert service.state is ServiceState.RUNNING
    assert source.open_count == 1


def test_service_cleans_up_after_partial_open_failure() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class PartialSource:
        close_count = 0

        def open(self) -> None:
            raise OSError("open failed after allocation")

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            self.close_count += 1

    source = PartialSource()
    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=source,
    )

    with pytest.raises(OSError, match="open failed"):
        service.start()

    assert service.state is ServiceState.FAILED
    assert source.close_count == 1


def test_service_can_retry_cleanup_after_cleanup_failure() -> None:
    from face_profile.camera import Frame
    from face_profile.config import AppConfig, CameraConfig
    from face_profile.service import Service, ServiceState
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    class RetriableSource:
        close_count = 0

        def open(self) -> None:
            raise OSError("open failed")

        def read(self) -> Frame:
            raise AssertionError("read must not be called")

        def close(self) -> None:
            self.close_count += 1
            if self.close_count == 1:
                raise OSError("first cleanup failed")

    source = RetriableSource()
    service = Service(
        config=AppConfig(camera=CameraConfig(enabled=True)),
        settings=MockSettingsAdapter(DeviceSettings(volume=50, brightness=50)),
        frame_source=source,
    )

    with pytest.raises(OSError, match="open failed"):
        service.start()
    service.stop()

    assert service.state is ServiceState.STOPPED
    assert source.close_count == 2

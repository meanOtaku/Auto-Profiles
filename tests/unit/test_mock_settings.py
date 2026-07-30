import pytest


def test_mock_settings_adapter_applies_and_reads_values() -> None:
    from face_profile.settings import DeviceSettings, MockSettingsAdapter

    adapter = MockSettingsAdapter(DeviceSettings(volume=25, brightness=40))
    requested = DeviceSettings(volume=70, brightness=65)

    result = adapter.apply(requested)

    assert result.applied is True
    assert adapter.read_current() == requested


def test_device_settings_reject_invalid_values() -> None:
    from face_profile.settings import DeviceSettings, SettingsValidationError

    with pytest.raises(SettingsValidationError, match="volume"):
        DeviceSettings(volume=101, brightness=50)


def test_device_settings_reject_boolean_percentages() -> None:
    from face_profile.settings import DeviceSettings, SettingsValidationError

    with pytest.raises(SettingsValidationError, match="volume"):
        DeviceSettings(volume=True, brightness=50)

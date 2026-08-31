import pytest
from unittest.mock import patch, MagicMock
from src.audio_control import WindowsAudioController, mute, unmute, is_muted, set_mute, restore

@pytest.fixture
def mock_volume():
    vol = MagicMock()
    vol.GetMute.return_value = 0
    return vol

def test_audio_controller_mute(mock_volume):
    ctrl = WindowsAudioController()
    with patch.object(ctrl, "_get_endpoint_volume", return_value=mock_volume):
        assert ctrl.mute() is True
        mock_volume.SetMute.assert_called_with(1, None)

def test_audio_controller_unmute(mock_volume):
    ctrl = WindowsAudioController()
    with patch.object(ctrl, "_get_endpoint_volume", return_value=mock_volume):
        assert ctrl.unmute() is True
        mock_volume.SetMute.assert_called_with(0, None)

def test_audio_controller_is_muted(mock_volume):
    ctrl = WindowsAudioController()
    with patch.object(ctrl, "_get_endpoint_volume", return_value=mock_volume):
        mock_volume.GetMute.return_value = 1
        assert ctrl.is_muted() is True
        mock_volume.GetMute.return_value = 0
        assert ctrl.is_muted() is False

def test_audio_controller_restore(mock_volume):
    ctrl = WindowsAudioController()
    with patch.object(ctrl, "_get_endpoint_volume", return_value=mock_volume):
        mock_volume.GetMute.return_value = 0
        ctrl.mute() # saves previous state = False
        ctrl.restore() # restores False -> unmute
        mock_volume.SetMute.assert_called_with(0, None)

def test_module_level_helpers(mock_volume):
    with patch("src.audio_control._controller._get_endpoint_volume", return_value=mock_volume):
        assert mute() is True
        assert unmute() is True
        assert is_muted() is False
        assert set_mute(True) is True

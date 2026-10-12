from app.services.telegram_dialogs import normalize_channel_reference


def test_normalize_channel_reference_accepts_supported_formats() -> None:
    assert normalize_channel_reference("@izhevsk_smi") == "izhevsk_smi"
    assert normalize_channel_reference("t.me/izhevsk_smi") == "izhevsk_smi"
    assert (
        normalize_channel_reference("https://www.t.me/izhevsk_smi/?single")
        == "izhevsk_smi"
    )


def test_normalize_channel_reference_rejects_invites_and_invalid_paths() -> None:
    assert normalize_channel_reference("https://t.me/+privateInvite") is None
    assert normalize_channel_reference("https://t.me/channel/123") is None
    assert normalize_channel_reference("channel-name") is None

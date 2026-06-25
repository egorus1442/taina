def normalize_telegram_id(entity) -> str:
    from telethon import utils

    return str(utils.get_peer_id(entity))


#!/usr/bin/env python3
"""Generate a Telethon StringSession for the userbot.

Run it on a trusted machine (not inside a container):

    .venv/bin/python scripts/gen_userbot_session.py

It asks for api_id / api_hash / phone, then the login code Telegram sends.
The printed string goes into USERBOT_SESSION in .env. Treat it like a password:
it is full access to the account.
"""

from __future__ import annotations

import asyncio
import getpass

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(input("TG_API_ID: ").strip())
    api_hash = input("TG_API_HASH: ").strip()
    phone = input("phone (+91...): ").strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        code = getpass.getpass("login code: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except Exception:
            password = getpass.getpass("2FA password: ")
            await client.sign_in(password=password)
    me = await client.get_me()
    print("\nlogged in as", me.first_name, f"(@{me.username})")
    print("\nUSERBOT_SESSION=" + StringSession.save(client.session))
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

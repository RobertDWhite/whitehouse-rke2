from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from matrix_gateway import OPERATOR, pin_intake


class MatrixIntakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_operator_in_the_pinned_room_can_trigger_text_intake(self):
        on_message = AsyncMock()
        on_invite = AsyncMock()
        on_reaction = AsyncMock()
        adapter = pin_intake(SimpleNamespace(
            _on_room_message=on_message, _on_invite=on_invite, _on_reaction=on_reaction,
        ), "!private")

        def event(room="!private", sender=OPERATOR, msgtype="m.text", encrypted=True, body="Check FileBot"):
            return SimpleNamespace(room_id=room, sender=sender, content={"msgtype": msgtype, "body": body},
                                   get=lambda *_: {"was_encrypted": encrypted})

        for rejected in (
            event(room="!other"), event(sender="@other:white.fm"),
            event(msgtype="m.file"), event(encrypted=False), event(body="/tools"),
        ):
            await adapter._on_room_message(rejected)
        await adapter._on_invite(event())
        await adapter._on_reaction(event())
        on_message.assert_not_awaited()
        on_invite.assert_not_awaited()
        on_reaction.assert_not_awaited()

        accepted = event()
        await adapter._on_room_message(accepted)
        on_message.assert_awaited_once_with(accepted)


if __name__ == "__main__":
    unittest.main()

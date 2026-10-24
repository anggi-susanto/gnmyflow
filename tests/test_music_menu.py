from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from studiowright.flow.page import FlowError
from studiowright.music.page import MusicPage


def page_with_menu(missing=False):
    item = Mock()
    item.wait_for = AsyncMock(side_effect=PlaywrightTimeoutError('timeout') if missing else None)
    item.focus = AsyncMock()
    entries = Mock(
        all_inner_texts=AsyncMock(return_value=["Details", "Download", "Add to...", "Split stems"])
    )
    trigger = Mock()
    trigger.first = SimpleNamespace(click=AsyncMock())
    page = Mock()
    page.locator.return_value = trigger
    page.get_by_role.side_effect = lambda role, **kwargs: item if 'name' in kwargs else entries
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    music = object.__new__(MusicPage)
    music.page = page
    return music, page, item


async def test_missing_remix_explains_available_actions_without_submitting():
    music, page, item = page_with_menu(missing=True)
    with pytest.raises(FlowError, match="action 'Remix' is not available") as caught:
        await music.song_menu('Remix', 'Cover')
    assert 'Details, Download, Add to..., Split stems' in str(caught.value)
    assert 'upload may have succeeded' in str(caught.value)
    item.focus.assert_not_called()
    page.keyboard.press.assert_not_called()


async def test_existing_menu_uses_keyboard_submenu_navigation():
    music, page, _item = page_with_menu()
    await music.song_menu('Remix', 'Cover')
    assert [call.args[0] for call in page.keyboard.press.call_args_list] == ['ArrowRight', 'Enter']

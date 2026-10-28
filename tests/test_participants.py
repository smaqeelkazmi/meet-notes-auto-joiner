from __future__ import annotations

from meet_notes.meet_flow import media_is_off
from meet_notes.participants import (
    count_real_others,
    is_self_name,
    others_from_people_label,
)


def test_self_names():
    names = ("You", "Aqeel")
    assert is_self_name("You", names)
    assert is_self_name("Aqeel (You)", names)
    assert is_self_name("Aqeel", names)
    assert not is_self_name("Sam", names)
    assert not is_self_name("Aqeel Syed", names)
    assert count_real_others(["You", "Sam", "Aqeel"], names) == 1


def test_people_button_count_includes_self():
    assert others_from_people_label("People - 1 joined") == 0
    assert others_from_people_label("People - 3 joined") == 2
    assert others_from_people_label("People") is None


def test_media_label_means_the_action():
    assert media_is_off("Turn off microphone") is False
    assert media_is_off("Turn on camera") is True
    assert media_is_off("Mute microphone") is False
    assert media_is_off("Unmute microphone") is True
    assert media_is_off("Unmute camera (ctrl + e)") is True
    assert media_is_off("Microphone") is None


class _Node:
    def __init__(self, text="", label=None, visible=True, attrs=None):
        self.text = text
        self.label = label
        self.visible = visible
        self.attrs = attrs or {}

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        if name == "aria-label":
            return self.label
        return self.attrs.get(name)


class _Page:
    def __init__(self, found):
        self.found = found

    def locator(self, selector):
        nodes = self.found.get(selector, [])

        class _Locator:
            def count(_self):
                return len(nodes)

            def nth(_self, index):
                return nodes[index]

        return _Locator()


def test_people_button_overrides_a_roster_that_only_shows_self():
    from meet_notes.participants import count_others_on_page
    from meet_notes.selectors import PEOPLE_BUTTON_SELECTORS, ROSTER_ITEM_SELECTORS

    page = _Page(
        {
            ROSTER_ITEM_SELECTORS[-1]: [_Node("You")],
            PEOPLE_BUTTON_SELECTORS[0]: [_Node("People", label="People - 4 joined")],
        }
    )
    others, signal = count_others_on_page(page, ("You", "Aqeel"))
    assert others == 3
    assert signal == "people_button"


def test_show_everyone_label_counts_other_people():
    from meet_notes.participants import count_others_on_page
    from meet_notes.selectors import PEOPLE_BUTTON_SELECTORS

    page = _Page({PEOPLE_BUTTON_SELECTORS[1]: [_Node("4", label="Show everyone")]})
    others, signal = count_others_on_page(page, ("You", "Aqeel"))
    assert others == 3
    assert signal == "people_button"


def test_launch_points_fake_devices_at_silence(tmp_path):
    from meet_notes.blank_media import wav_is_silent
    from meet_notes.browser import _launch_kwargs
    from tests.conftest import make_cfg

    cfg = make_cfg(tmp_path)
    kwargs = _launch_kwargs(cfg)
    args = kwargs["args"]
    assert "--use-fake-device-for-media-stream" in args
    assert "--use-fake-ui-for-media-stream" in args
    audio = next(item for item in args if item.startswith("--use-file-for-fake-audio-capture="))
    video = next(item for item in args if item.startswith("--use-file-for-fake-video-capture="))
    audio_path = audio.split("=", 1)[1]
    video_path = video.split("=", 1)[1]
    assert wav_is_silent(__import__("pathlib").Path(audio_path))
    assert video_path.endswith("black.y4m")

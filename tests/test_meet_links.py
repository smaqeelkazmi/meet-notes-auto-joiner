from __future__ import annotations

from meet_notes.meet_links import canonical_meet_url, extract_meet_url


def test_canonical_meet_codes():
    assert canonical_meet_url("https://meet.google.com/abc-defg-hij") == "https://meet.google.com/abc-defg-hij"
    assert (
        canonical_meet_url("https://meet.google.com/lookup/abcd?authuser=0")
        == "https://meet.google.com/lookup/abcd"
    )
    assert canonical_meet_url("https://meet.google.com/new") is None
    assert canonical_meet_url("https://zoom.us/j/123") is None
    assert canonical_meet_url("http://meet.google.com/abc-defg-hij") is None


def test_extract_prefers_hangout_link_and_rejects_zoom():
    item = {
        "hangoutLink": "https://meet.google.com/abc-defg-hij?hs=122",
        "description": "backup https://meet.google.com/zzz-yyyy-xxx",
    }
    assert extract_meet_url(item) == "https://meet.google.com/abc-defg-hij"


def test_extract_from_conference_entry_and_description():
    video = {
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+123"},
                {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
            ]
        }
    }
    assert extract_meet_url(video) == "https://meet.google.com/abc-defg-hij"
    zoom = {
        "conferenceData": {
            "entryPoints": [{"entryPointType": "video", "uri": "https://zoom.us/j/1"}]
        },
        "description": "Notes only",
    }
    assert extract_meet_url(zoom) is None
    described = {"description": "Join at https://meet.google.com/abc-defg-hij."}
    assert extract_meet_url(described) == "https://meet.google.com/abc-defg-hij"

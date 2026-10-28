"""Meet UI selectors.

Every visible string the bot looks for lives in this module. Meet renames
controls often. When a live run cannot find Join, the people list, Take notes,
or Leave, update the lists here (first match wins) and the HTML fixture under
tests/fixtures/meet_page.html, then rerun pytest.

Labels below follow Google's computer help article for "Take notes for me"
as published at https://support.google.com/meet/answer/14754931 (read 2026-10-28):

- Join the call, then click "Take notes for me" at the top right.
- Click "Start taking notes".
- An admin consent dialog may offer Start, Join, Continue, or Leave.
  This bot accepts Start, Join, or Continue. It never clicks the consent
  dialog's Leave. Hanging up uses the separate "Leave call" control.
- While notes are on, Meet tells everyone and shows a pencil icon.
  The strongest machine-readable signal we can rely on without a live DOM
  is a "Stop taking notes" control, a "Taking notes" label (including
  "Taking notes with Gemini"), or a "Gemini is taking notes" banner.
  Do not treat the "Gemini is available" popover as notes. That card is a
  tip. Dismiss it with "Don't show again" or its close button before
  looking for the notes control.

The in-call hang-up lives in the bottom toolbar. A lobby "Leave call"
button uses the same name and is not proof of being in the meeting.
A 2026 headless run (Uzma / Rev9) was already in the call: red hang-up,
mic and camera with fake-media "!" badges, present / reactions / captions /
raise hand / more, a top-right count of 2, and the pencil chip. The old
check required the hang-up plus a People button or an exact notes label,
and this UI has a count badge and a "Taking notes with Gemini" chip
instead, so the bot logged join_failed.

The account's Meet language should be English. These strings are English.
A non-English UI needs matching entries added at the top of each list.

Mic and camera use Meet's pre-join convention: the button describes the
action, so "Turn off microphone" means the mic is currently on.
"""

from __future__ import annotations

# Click these to switch the device OFF. Visible only while it is on.
MIC_TURN_OFF_SELECTORS = [
    "button[aria-label='Turn off microphone']",
    "button[aria-label^='Turn off microphone']",
    "button[aria-label^='Mute microphone']",
    "button[aria-label^='Mute mic']",
    "[role='button'][aria-label='Turn off microphone']",
    "[role='button'][aria-label^='Mute microphone']",
    "[data-device='microphone'][data-is-muted='false']",
]

# Visible when the mic is already off.
MIC_IS_OFF_SELECTORS = [
    "button[aria-label='Turn on microphone']",
    "button[aria-label^='Turn on microphone']",
    "button[aria-label^='Unmute microphone']",
    "button[aria-label^='Unmute mic']",
    "[role='button'][aria-label='Turn on microphone']",
    "[role='button'][aria-label^='Unmute microphone']",
    "[data-device='microphone'][data-is-muted='true']",
]

CAMERA_TURN_OFF_SELECTORS = [
    "button[aria-label='Turn off camera']",
    "button[aria-label^='Turn off camera']",
    "button[aria-label^='Mute camera']",
    "[role='button'][aria-label='Turn off camera']",
    "[role='button'][aria-label^='Mute camera']",
    "[data-device='camera'][data-is-muted='false']",
]

CAMERA_IS_OFF_SELECTORS = [
    "button[aria-label='Turn on camera']",
    "button[aria-label^='Turn on camera']",
    "button[aria-label^='Unmute camera']",
    "[role='button'][aria-label='Turn on camera']",
    "[role='button'][aria-label^='Unmute camera']",
    "[data-device='camera'][data-is-muted='true']",
]

# Any visible mic or camera control, used only to log what Meet showed.
DEVICE_LABEL_SELECTORS = [
    "button[aria-label*='icrophone']",
    "[role='button'][aria-label*='icrophone']",
    "button[aria-label*='amera']",
    "[role='button'][aria-label*='amera']",
]

# Dismiss nags on the greenroom. "Join" here is only inside a dialog
# (consent to enter a meeting that already has notes, or a warning).
PREJOIN_DISMISS_SELECTORS = [
    "button:has-text('Got it')",
    "button:has-text('Dismiss')",
    "button:has-text('Not now')",
    "[role='dialog'] button:has-text('Join anyway')",
    "[role='dialog'] button:has-text('Continue without')",
    "[role='dialog'] button:has-text('Continue')",
    "[role='dialog'] button:has-text('Join')",
]

JOIN_SELECTORS = [
    "button[aria-label='Join now']",
    "button:has-text('Join now')",
    "button[aria-label='Ask to join']",
    "button:has-text('Ask to join')",
    "[role='button'][aria-label='Join now']",
    "[role='button'][aria-label='Ask to join']",
]

LEAVE_SELECTORS = [
    "button[aria-label='Leave call']",
    "button[aria-label^='Leave call']",
    "button[aria-label='Leave the call']",
    "button[aria-label^='Leave the call']",
    "button[aria-label^='Leave meeting']",
    "[role='button'][aria-label='Leave call']",
    "[role='button'][aria-label^='Leave call']",
    "[role='button'][aria-label^='Leave the call']",
    "[role='toolbar'] [data-tooltip^='Leave call']",
    "[data-tooltip='Leave call']",
    "[data-tooltip^='Leave call']",
    "button:has-text('Leave call')",
]

# Hang-up inside the bottom call toolbar. Enough on its own to know we are
# in the meeting. The lobby button is not inside this toolbar, and an
# exact aria-label misses "Leave call" plus a shortcut, or a tooltip on an
# icon-only control. `button:has-text` misses that icon-only control too.
IN_CALL_LEAVE_SELECTORS = [
    "[role='toolbar'] button[aria-label='Leave call']",
    "[role='toolbar'] button[aria-label^='Leave call']",
    "[role='toolbar'] button[aria-label^='Leave the call']",
    "[role='toolbar'] button[aria-label^='Leave meeting']",
    "[role='toolbar'] [role='button'][aria-label^='Leave call']",
    "[role='toolbar'] [role='button'][aria-label^='Leave the call']",
    "[aria-label='Call controls'] button[aria-label^='Leave call']",
    "[aria-label='Call controls'] button[aria-label^='Leave the call']",
    "[aria-label='Call controls'] [role='button'][aria-label^='Leave call']",
    "[data-panel='call-controls'] button[aria-label^='Leave call']",
    "[data-panel='call-controls'] [role='button'][aria-label^='Leave call']",
    "[role='toolbar'] button:has([aria-label^='Leave call'])",
    "[role='toolbar'] [data-tooltip^='Leave call']",
    "[data-panel='call-controls'] [data-tooltip^='Leave call']",
    "[aria-label^='Leave call']:near([aria-label^='Raise hand'], 400)",
    "[aria-label^='Leave call']:near([aria-label*='aption'], 400)",
    "[data-tooltip^='Leave call']:near([aria-label^='Raise hand'], 400)",
]

# Bottom-bar controls that the greenroom and the admit lobby do not show.
# Mic and camera are excluded: both screens have those.
IN_CALL_TOOLBAR_SELECTORS = [
    "button[aria-label='Present now']",
    "button[aria-label^='Present now']",
    "button[aria-label^='Share screen']",
    "button[aria-label^='Present']",
    "[role='button'][aria-label^='Present now']",
    "[role='button'][aria-label^='Share screen']",
    "button[aria-label='Send a reaction']",
    "button[aria-label^='Send a reaction']",
    "button[aria-label^='Reactions']",
    "[role='button'][aria-label^='Send a reaction']",
    "button[aria-label='Raise hand']",
    "button[aria-label^='Raise hand']",
    "button[aria-label^='Lower hand']",
    "[role='button'][aria-label^='Raise hand']",
    "[role='button'][aria-label^='Lower hand']",
    "button[aria-label='Turn on captions']",
    "button[aria-label^='Turn on captions']",
    "button[aria-label^='Turn off captions']",
    "button[aria-label^='Show captions']",
    "[role='button'][aria-label*='aption']",
    "button[aria-label*='aption']",
]

# Top-right count. The Uzma / Rev9 badge was "2", not a People button.
PARTICIPANT_BADGE_SELECTORS = [
    "[data-participant-count]",
    "[data-people-badge]",
    "[role='button'][aria-label*='Show everyone']",
    "[role='button'][aria-label*='people in']",
    "[role='button'][aria-label*='in the call']",
    "button[aria-label*='in the call']",
    "button[aria-label*='Show everyone']",
]

# Clock and meeting title across the top of the call, not the greenroom.
IN_CALL_TITLE_SELECTORS = [
    "[data-call-titlebar]",
    "[data-meeting-title]",
    "[aria-label='Meeting details']",
    "button[aria-label^='Meeting details']",
    "[role='button'][aria-label^='Meeting details']",
]

# Saved next to a failure screenshot so the next selector drift is visible.
TOOLBAR_HTML_SELECTORS = [
    "[role='toolbar'][aria-label='Call controls']",
    "[data-panel='call-controls']",
    "[aria-label='Call controls']",
    "[role='toolbar']",
]

# Tips that sit on top of the call. "Learn more" is not here: it navigates away.
# Straight and curly apostrophes both appear on the Gemini card.
CALLOUT_DISMISS_SELECTORS = [
    "[data-callout='gemini'] button:has-text(\"Don't show again\")",
    "[data-callout='gemini'] button:has-text(\"Don’t show again\")",
    "[aria-label='Gemini is available'] button:has-text(\"Don't show again\")",
    "[aria-label='Gemini is available'] button:has-text(\"Don’t show again\")",
    "button:has-text(\"Don't show again\")",
    "button:has-text(\"Don’t show again\")",
    "[data-callout='gemini'] button[aria-label='Close']",
    "[data-callout='gemini'] button[aria-label='Dismiss']",
    "[aria-label='Gemini is available'] button[aria-label='Close']",
    "[aria-label='Gemini is available'] button[aria-label='Dismiss']",
    "button[aria-label='Close']:near(:text('Gemini is available'), 300)",
    "button[aria-label='Dismiss']:near(:text('Gemini is available'), 300)",
    "button:has-text('Got it')",
    "button:has-text('Dismiss')",
    "button:has-text('Not now')",
]

# Shown only after the hang-up button is clicked. Not used before that,
# so the consent dialog's plain "Leave" is not in this list.
LEAVE_CONFIRM_SELECTORS = [
    "[role='dialog'] button:has-text('Leave meeting')",
    "[role='dialog'] button:has-text('Just leave the call')",
    "[role='dialog'] button:has-text('Just leave')",
]

LEFT_CONFIRM_SELECTORS = [
    "text=You left the meeting",
    "text=You've left the meeting",
    "button:has-text('Rejoin')",
    "button:has-text('Return to home screen')",
]

PEOPLE_BUTTON_SELECTORS = [
    "button[aria-label^='People']",
    "button[aria-label*='Show everyone']",
    "button[aria-label^='Participants']",
    "button[aria-label*='Show participants']",
    "[role='button'][aria-label^='People']",
    "[role='button'][aria-label^='Participants']",
    "[role='button'][aria-label*='Show everyone']",
    "[role='button'][aria-label*='people in']",
    "[role='button'][aria-label*='in the call']",
    "button[aria-label*='in the call']",
    "[data-participant-count]",
    "[data-people-badge]",
]

TILE_SELECTORS = [
    "[data-participant-id][data-self-name]",
    "[data-participant-id][aria-label]",
]

ROSTER_ITEM_SELECTORS = [
    "[aria-label='Participants'] [role='listitem'][data-participant-id]",
    "[role='list'][aria-label='Participants'] [role='listitem']",
    "[data-panel='people'] [role='listitem']",
    "[role='listitem'][data-participant-id]",
]

NOTES_ENTRY_SELECTORS = [
    "button[aria-label='Take notes for me']",
    "[role='button'][aria-label='Take notes for me']",
    "button[aria-label='Take notes with Gemini']",
    "[role='button'][aria-label='Take notes with Gemini']",
    "button:has-text('Take notes for me')",
    "button:has-text('Take notes with Gemini')",
]

MORE_OPTIONS_SELECTORS = [
    "button[aria-label='More options']",
    "button[aria-label='More actions']",
    "[role='button'][aria-label='More options']",
]

NOTES_MENU_ITEM_SELECTORS = [
    "[role='menuitem']:has-text('Take notes for me')",
    "[role='menuitem']:has-text('Take notes with Gemini')",
    "[role='menuitem']:has-text('Start taking notes')",
]

START_NOTES_SELECTORS = [
    "[role='dialog'] button:has-text('Start taking notes')",
    "button:has-text('Start taking notes')",
    "[role='dialog'] button:has-text('Start')",
]

# Accept consent. The dialog's Leave button is intentionally absent.
CONSENT_ACCEPT_SELECTORS = [
    "[role='dialog'] button:has-text('Start taking notes')",
    "[role='dialog'] button:has-text('Start')",
    "[role='dialog'] button:has-text('Continue')",
    "[role='dialog'] button:has-text('Join')",
    "[role='dialog'] button:has-text('Got it')",
]

# Do not use a lowercase substring such as "taking notes": that also matches
# the Start taking notes button. "Taking notes" (capital T) and
# "Gemini is taking notes" do not.
NOTES_ACTIVE_SELECTORS = [
    "button[aria-label='Stop taking notes']",
    "[aria-label='Stop taking notes']",
    "[aria-label='Taking notes']",
    "[aria-label^='Taking notes']",
    "[aria-label='Notes are being taken']",
    "[aria-label='Gemini is taking notes']",
    "[aria-label*='Gemini is taking notes']",
    "[aria-label='Meeting notes are on']",
    "[aria-label^='Meeting notes are on']",
    "[data-notes-chip][aria-label*='Taking notes']",
    "[data-notes-chip][aria-label*='Gemini']",
    "[role='status']:text-is('Gemini is taking notes')",
    ":text-is('Gemini is taking notes')",
    "[data-meet-notes='active']",
]

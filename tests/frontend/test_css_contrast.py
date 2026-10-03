"""WCAG contrast on App.css, checked here because nothing else ever would.

Why this file exists
====================
The register screen told users what a password had to look like in
``#90A3B4``. Measured against the white card behind it, that is a **2.60:1**
contrast ratio, where WCAG 2.1 AA asks 4.5:1 for text under 18.66px. The hint
was set at 0.8rem — 12.8px — the size that misses worst. It shipped because
nothing measures contrast and nothing failed: ``npm run build`` only checks that
the stylesheet parses, and a designer's eye adapts to the colour it is reading.

Three separate greys were failing the same way, in the same file:

    --darkgray  #90A3B4   2.60:1   .auth-hint .upload-hint .settings-hint ...
    --bluegrey  #7C8F9E   3.34:1   .source-excerpt
    --bluegray  #7D82A1   3.76:1   .app-header p .setup-subtitle .setup-desc

``.source-excerpt`` was the worst of the three to actually hit, because it is
not a hint at all — it is the quoted passage from the user's own document, the
evidence the assistant is expected to be trusted on.

They now all resolve to ``--ink-muted``, which measures 5.27:1 on white and
4.97:1 on the app background. The greys remain defined because they are still
the disabled-button fill, which WCAG exempts from 1.4.3 as an inactive control.

THE ALLOWLIST IS THE POINT
==========================
Two entries are deliberate, and each is a place where a naive "every text
colour must reach 4.5:1" rule would be wrong:

``--white`` on brand fills
    White on ``--blue`` is 4.13:1. Below AA for body text, yes — but the token
    cannot be changed, because it is not text colour, it is what is painted
    *underneath*. Darkening it would repaint every button and active tab. This
    is a real finding about the brand blue and it is reported in the README of
    this file rather than fixed here; see the ``--blue`` note below.

``--babyblue`` on ``--navyblue``
    Measured the naive way this looks like 1.14:1. Inverted — it is
    ``--babyblue`` text on a ``--navyblue`` background, i.e. **10.29:1**. Any
    check that compares a token to white without looking at the block it sits in
    will call this unreadable, and it is the most legible text in the file.

WHAT THIS DOES NOT CATCH
========================
Contrast depends on the background actually painted behind a colour, which CSS
can compute from gradients, inheritance and stacking contexts that a regex
cannot resolve. This guards the light-on-light mistake — the one that was made
here — by requiring every *known palette token* used in a ``color:`` position to
clear AA against the two light surfaces the app renders on. A raw hex literal is
caught too. Anything requiring real cascade analysis is still a judgement call.

One blind spot is worth naming rather than hiding. The allowlist is keyed on the
TOKEN, not on the RULE, so ``--blue`` is excused everywhere because its four
surviving uses are SVG icons judged by 1.4.11. If someone later colours a
paragraph with ``--blue``, this file will not notice — the token is already
excused. Splitting the icon colour into its own token would close that, at the
cost of a second blue; on a file this size the honest note is cheaper than the
duplication.
"""

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'App.css'

# WCAG 2.1 SC 1.4.3 — normal-size text.
AA_NORMAL_TEXT = 4.5

# Both light surfaces the app paints: the white card (--white) and the page wash
# (--page). Text has to clear AA on the darker of the two, so --page sets the bar.
SURFACES = {'white card (--white)': '#FFFFFF', 'page (--page)': '#F4F9FE'}

# Token -> why a 4.5:1 assertion would be the wrong rule for it. See the module
# docstring. Each value in ALLOWED_TEXT_TOKENS is measured by
# test_the_allowlist_entries_are_justified, so a new entry cannot be waved in.
ALLOWED_TEXT_TOKENS = {
    '--white': 'painted on brand fills; the colour underneath is the design, not the text',
    '--babyblue': 'code on --navyblue, not on a light surface: 10.29:1 inverted',
    '--blue': 'remaining uses are all SVG icon colours, which WCAG 1.4.11 holds '
              'to 3:1, not 1.4.3\'s 4.5:1; it measures 3.97:1. The two real text '
              'uses (.source-expand-btn, .setup-link) were moved to --blue-600.',
    '--red': 'the one use, .btn-delete:hover, colours an SVG icon: 4.11:1 against '
            'the 3:1 that 1.4.11 asks of non-text content.',
}


def _channel(value: int) -> float:
    v = value / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture(scope='module')
def css() -> str:
    assert CSS_PATH.exists(), f'App.css is missing at {CSS_PATH}'
    return CSS_PATH.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def tokens(css: str) -> dict[str, str]:
    root = re.search(r':root\s*\{(.*?)\}', css, re.S)
    assert root, 'no :root block found in App.css'
    return dict(re.findall(r'(--[\w-]+):\s*(#[0-9A-Fa-f]{3,6})', root.group(1)))


def text_colour_usages(css: str) -> dict[str, list[int]]:
    """Every token referenced in a ``color:`` position, mapped to line numbers.

    The negative lookbehind is load-bearing and was a bug the first time round.
    A plain ``color:`` pattern also matches inside ``border-color:``, ``outline-
    color:`` and ``caret-color:`` — 16 of the 22 ``--blue`` hits in this file
    were borders, not text. Treating a border as a text colour reported the brand
    blue at 3.97:1 and would have sent someone off to repaint the design system
    over a property that was never legible in the first place.
    """
    found: dict[str, list[int]] = {}
    for match in re.finditer(r'(?<![-\w])color\s*:\s*var\((--[\w-]+)\)', css):
        line = css[:match.start()].count('\n') + 1
        found.setdefault(match.group(1), []).append(line)
    return found


class TestTextColoursClearAA:
    def test_every_text_token_clears_aa_on_both_light_surfaces(self, tokens, css):
        """The whole point: a grey that fails AA on a white card must not exist.

        Asserted against ``--page`` as well as white because a hint can land on
        either, and the page wash is the darker of the two — so clearing it
        clears both.
        """
        offenders: list[str] = []
        for token, lines in sorted(text_colour_usages(css).items()):
            if token in ALLOWED_TEXT_TOKENS:
                continue
            assert token in tokens, (
                f'{token} is used as a text colour (lines {lines}) but is not '
                'defined as a hex token in :root, so it cannot be checked'
            )
            worst = min(contrast_ratio(tokens[token], bg) for bg in SURFACES.values())
            if worst < AA_NORMAL_TEXT:
                offenders.append(
                    f'{token} {tokens[token]} is {worst:.2f}:1 on its worst '
                    f'surface (AA needs {AA_NORMAL_TEXT}) at lines {lines}'
                )
        assert not offenders, (
            'text colours below WCAG AA:\n  ' + '\n  '.join(offenders)
        )

    def test_a_raw_hex_in_a_text_position_clears_aa(self, css):
        """Catches the bypass: hardcoding a grey instead of using a token.

        Worth its own test because a literal hex is the easiest way to skip
        check one — the token list above cannot see it.
        """
        offenders = []
        for match in re.finditer(r'(?<![-\w])color\s*:\s*(#[0-9A-Fa-f]{3,6})\b', css):
            line = css[:match.start()].count('\n') + 1
            worst = min(contrast_ratio(match.group(1), bg) for bg in SURFACES.values())
            if worst < AA_NORMAL_TEXT:
                offenders.append(
                    f'{match.group(1)} at line {line} is {worst:.2f}:1'
                )
        assert not offenders, (
            'literal hex text colours below WCAG AA: ' + '; '.join(offenders)
        )

    def test_muted_text_is_still_visibly_secondary_to_body_text(self, tokens):
        """Guards the opposite failure: a "readable" grey that reads as body copy.

        Accessibility fixes drift into making everything the same weight, and
        the hierarchy disappears. Secondary text has to stay quiet — it must not
        creep above the primary ink.
        """
        muted = contrast_ratio(tokens['--ink-muted'], '#FFFFFF')
        body = contrast_ratio(tokens['--navyblue'], '#FFFFFF')
        assert muted < body, (
            f'--ink-muted ({muted:.2f}:1) is not quieter than --navyblue '
            f'({body:.2f}:1); the visual hierarchy has flattened'
        )

    def test_the_muted_token_clears_aa_on_its_own(self, tokens):
        """Spelled out, so the number the rest of the app depends on is pinned.

        5.27:1 on white and 4.97:1 on --page. Both are above AA with room to
        spare, which is what lets it be reused on any surface without each call
        site re-checking.
        """
        assert '--ink-muted' in tokens, (
            '--ink-muted is the token the hint/label colours now resolve to; '
            'without it every one of those declarations is undefined'
        )
        for label, surface in SURFACES.items():
            ratio = contrast_ratio(tokens['--ink-muted'], surface)
            assert ratio >= AA_NORMAL_TEXT, (
                f'--ink-muted is {ratio:.2f}:1 on {label}, below AA '
                f'{AA_NORMAL_TEXT}:1'
            )

    def test_the_greys_are_no_longer_used_for_text(self, tokens, css):
        """The three tokens that caused this must stay out of ``color:``.

        They are still legitimate as the disabled-button fill, so this asserts
        on usage rather than on their values — deleting the token outright would
        break ``.btn-primary:disabled``.
        """
        usages = text_colour_usages(css)
        for token in ('--darkgray', '--bluegray', '--bluegrey'):
            assert token not in usages, (
                f'{token} {tokens[token]} is {contrast_ratio(tokens[token], "#FFFFFF"):.2f}:1 '
                f'on white and is being used as text again (lines {usages[token]}). '
                'Text belongs on --ink-muted; this grey is for disabled fills.'
            )


class TestTheAllowlistIsJustified:
    def test_the_allowlist_entries_are_justified(self, tokens, css):
        """An allowlist with no measured reason behind it is just a hole.

        Each entry exists because the naive check is wrong for it. This asserts
        that reasoning is still attached, so a future edit cannot quietly widen
        the list by dropping a sentence.
        """
        for token, reason in ALLOWED_TEXT_TOKENS.items():
            assert reason.strip(), f'{token} is allowlisted with no stated reason'
            assert token in text_colour_usages(css), (
                f'{token} is allowlisted but no longer used as a text colour; '
                'remove the entry rather than leaving dead permission'
            )
            assert token in tokens, f'{token} is allowlisted but undefined in :root'

    def test_the_allowlist_covers_nothing_that_actually_passes(self, tokens, css):
        """An allowlist entry that would have passed anyway is dead permission.

        Keeps the list down to the two genuinely-unmeasurable cases. If a future
        change makes ``--babyblue`` legible on a light surface by accident, this
        fails and the entry gets deleted instead of lingering as a free pass.
        """
        removable = []
        for token in ALLOWED_TEXT_TOKENS:
            if token not in tokens:
                continue
            worst = min(contrast_ratio(tokens[token], bg) for bg in SURFACES.values())
            if worst >= AA_NORMAL_TEXT:
                removable.append(f'{token} {tokens[token]} measures {worst:.2f}:1')
        assert not removable, (
            'these allowlist entries now measure above AA on a light surface and '
            'are no longer needed: ' + '; '.join(removable)
        )
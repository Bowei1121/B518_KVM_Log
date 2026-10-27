"""Text of the red alarm banner under the top bar (spec v5.3 §13, §14.4, §14.10 (c)(e), §14.12; issue #127).

Tk-free. The caller passes ``fits_main(text)`` and ``fits_line(text)``, which measure the text in the 40 px and the
20 px banner font; the longest form that fits is used, and the shortest form is used when nothing fits, so
characters are never cut off and lines never wrap. On the hint line the category hint is kept whole and the scroll
prompt shrinks first (full list, first few ids + a count, a count, a short count).
"""
from typing import Callable, List, Sequence, Tuple

from ui_i18n import t

# Abnormal categories in click / list order (§13.4): kind -> (icon, status words key, hint key).
# Heartbeat stopped, Heartbeat not found (issue #130), Frame frozen; connection failure (■✕) comes with its own ticket.
KINDS = {"heartbeat": ("♥", "station_heartbeat", "alarm_hint_heartbeat"),
         "heartbeat_not_found": ("♥", "station_heartbeat_not_found", "alarm_hint_heartbeat_not_found"),
         "frozen": ("▲", "station_frozen", "alarm_hint_frozen")}
GAP = "　"  # full-width space between categories and between the scroll prompt and the hints

Abnormal = Sequence[Tuple[str, str]]  # (device, kind) in device-list order


def sort_abnormal(abnormal: Abnormal) -> List[Tuple[str, str]]:
    """Category order first, device-list order inside a category (the sort is stable)."""
    order = list(KINDS)
    return sorted(abnormal, key=lambda item: order.index(item[1]))


def shapes(kinds: Sequence[str]) -> List[str]:
    """Distinct icon shapes of ``kinds`` in category order (♥ is shared by both heartbeat categories)."""
    found: List[str] = []
    for kind in KINDS:
        if kind in kinds and KINDS[kind][0] not in found:
            found.append(KINDS[kind][0])
    return found


def _groups(abnormal: Abnormal) -> List[Tuple[str, List[str]]]:
    groups: List[Tuple[str, List[str]]] = []
    for kind in KINDS:
        ids = [device for device, k in abnormal if k == kind]
        if ids:
            groups.append((kind, ids))
    return groups


def _first_fit(candidates: List[str], fits: Callable[[str], bool]) -> str:
    return next((text for text in candidates if fits(text)), candidates[-1])


def _list_line(abnormal: Abnormal, fits: Callable[[str], bool]) -> str:
    sep = t("sep_list")
    full = GAP.join(t("alarm_list_cat", icon=KINDS[kind][0], kind=t(KINDS[kind][1]), ids=sep.join(ids))
                    for kind, ids in _groups(abnormal))
    candidates = [full]
    for keep in range(len(abnormal), -1, -1):  # all ids with icons only, then fewer ids and a count
        parts = [f"{KINDS[kind][0]} {sep.join(ids)}" for kind, ids in _groups(abnormal[:keep])]
        if keep < len(abnormal):
            parts.append(t("alarm_more", n=len(abnormal) - keep))
        candidates.append(GAP.join(parts))
    return _first_fit(candidates, fits)


def _scroll_prompts(above: Sequence[str], below: Sequence[str]) -> List[str]:
    """Scroll prompts, longest first; the caller uses the first that fits (together with the whole hints)."""
    sep = t("sep_list")

    def one(key: str, ids: Sequence[str]) -> str:
        return t(f"alarm_{key}_{'one' if len(ids) == 1 else 'many'}", ids=sep.join(ids))

    def one_side(key: str, ids: Sequence[str], nearest_first: bool) -> List[str]:
        # Full list; then the ids nearest the visible area + a count of the rest (k = n-1 .. 1); then count; then short.
        n = len(ids)
        prompts = [one(key, ids)]
        for keep in range(n - 1, 0, -1):
            kept = ids[:keep] if nearest_first else ids[n - keep:]
            prompts.append(t(f"alarm_{key}_many", ids=sep.join(kept) + t("alarm_more", n=n - keep)))
        return prompts + [t(f"alarm_{key}_count", n=n), t(f"alarm_{key}_short", n=n)]

    if above and below:
        every = list(above) + list(below)
        return [one("up", above) + GAP + one("down", below), t("alarm_scroll_both", ids=sep.join(every)),
                t("alarm_scroll_count", n=len(every)), t("alarm_scroll_short", a=len(above), b=len(below))]
    if above:
        return one_side("up", above, nearest_first=False)  # the last ids are nearest the view
    if below:
        return one_side("down", below, nearest_first=True)
    return []


def _hint_line(above: Sequence[str], below: Sequence[str], hints: List[str], fits: Callable[[str], bool]) -> str:
    """Scroll prompt first (it has priority), then the category hints, shortened to a count of the rest."""
    # All hints whole first; then fewer hints + "…另 N 項", but the first hint is always kept whole (never cut).
    variants = [GAP.join(hints)]
    for keep in range(len(hints) - 1, 0, -1):
        variants.append(GAP.join(hints[:keep] + [t("alarm_hint_more", n=len(hints) - keep)]))
    prompts = _scroll_prompts(above, below) or [""]
    for text in variants:
        for prompt in prompts:  # longest prompt first
            line = prompt + GAP + text if prompt else text
            if fits(line):
                return line
    return prompts[-1] + GAP + variants[-1] if prompts[-1] else variants[-1]


def banner_lines(
    abnormal: Abnormal,
    above: Sequence[str],
    below: Sequence[str],
    fits_main: Callable[[str], bool],
    fits_line: Callable[[str], bool],
) -> Tuple[str, str, str]:
    """(main line, second line, third line) of the banner; ``above`` / ``below`` are off-screen abnormal ids."""
    abnormal = sort_abnormal(abnormal)
    if not abnormal:
        return "", "", ""
    if len(abnormal) == 1:  # icon + id + kind, then the hint line (with the scroll prompt)
        device, kind = abnormal[0]
        icon, word, hint = KINDS[kind]
        main = _first_fit([f"{icon} {device} {t(word)}", f"{icon} {device}"], fits_main)
        return main, _hint_line(above, below, [t(hint)], fits_line), ""
    hints = [f"{KINDS[kind][0]} {t(KINDS[kind][2])}" for kind, _ids in _groups(abnormal)]
    return (t("alarm_devices", n=len(abnormal)), _list_line(abnormal, fits_line),
            _hint_line(above, below, hints, fits_line))

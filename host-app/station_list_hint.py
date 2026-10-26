"""Bottom hint line of the station list: which rows are off-screen (spec v5.3 §14.10, §14.12; issue #126).

Tk-free. The caller passes ``fits(text)``, which measures the text in the hint font; the longest form that fits
is used, and the shortest form is used when nothing fits, so characters are never cut off.
When abnormal devices are hidden on both sides, show each side's ids, then first-of-many ids,
then the combined abnormal count. If only one side has abnormal devices, show that side alone.
"""
from typing import Callable, Sequence

from ui_i18n import current_language, t

# Several abnormal ids shortened to the first one (§14.12): "BT-01 +1" / "BT-01 等". Chinese has no count, so this
# phrase cannot be a ui_i18n key (every key must carry the same placeholders in both languages, test_monitor_hmi_106).
_FIRST_OF_MANY = {"en": "{first} +{rest}", "zh": "{first} 等"}


def first_of_many(first: str, rest: int) -> str:
    """The first abnormal id standing for ``rest`` more, in the current language."""
    return _FIRST_OF_MANY.get(current_language(), _FIRST_OF_MANY["en"]).format(first=first, rest=rest)


def list_hint_text(
    above: Sequence[str],
    below: Sequence[str],
    abnormal_above: Sequence[str],
    abnormal_below: Sequence[str],
    fits: Callable[[str], bool],
) -> str:
    """Hint for rows above and below the view; abnormal ids are listed in device-list order."""
    if not above and not below:
        return ""
    if abnormal_above and abnormal_below:
        sides = (abnormal_above, abnormal_below)
        candidates = [t("list_abnormal_both", above=t("sep_list").join(abnormal_above),
                        below=t("sep_list").join(abnormal_below))]
        if any(len(side) > 1 for side in sides):
            firsts = [first_of_many(side[0], len(side) - 1) if len(side) > 1 else side[0]
                      for side in sides]
            candidates.append(t("list_abnormal_both", above=firsts[0], below=firsts[1]))
        candidates.append(t("list_abnormal_both_count", k=sum(len(side) for side in sides)))
        return next((text for text in candidates if fits(text)), candidates[-1])
    if abnormal_above:
        below = ()
    elif abnormal_below:
        above = ()
    if above and below:
        full = short = t("list_more_both", a=len(above), b=len(below))
    elif below:
        full, short = t("list_more_below", n=len(below)), t("list_more_down", n=len(below))
    else:
        full, short = t("list_more_above", n=len(above)), t("list_more_up", n=len(above))
    ids = list(abnormal_above) + list(abnormal_below)
    if not ids:
        candidates = [full, short]
    else:
        k = len(ids)
        every = t("list_abnormal", k=k, ids=t("sep_list").join(ids))
        first = ids[0] if k == 1 else first_of_many(ids[0], k - 1)
        candidates = [full + every, short + every]
        if k > 1:
            candidates.append(short + t("list_abnormal", k=k, ids=first))
        arrow = "▲" if abnormal_above else "▼"
        candidates.append(t("list_abnormal_only", arrow=arrow, k=k, ids=first))
    return next((text for text in candidates if fits(text)), candidates[-1])

"""Pictures that options are chosen from (Common Test 1B / Part 2): what an option differs by, and the composition of a
set, so that generation can rotate what the answer depends on and a set like one already in stock is caught before
review and audio are paid for. Pure functions over UnitContent figures; no I/O."""

from __future__ import annotations

from lq.domain.content import Figure, PicItem

PICTURE_FORMS = ("picture_set", "labeled_scene")

# What a picture set can make the answer depend on, with the Japanese name shown to the owner. Checked against the
# 2024 / 2025 Common Test (watch shape x shade, scarves and gloves, a dog pulling a boy, fish in a cooler, phone icons,
# box or bottle x design, a tree through the seasons, a TV on or off, striped or plain cats, envelopes on / under a desk).
AXES: dict[str, str] = {
    "position": "位置",
    "action": "動作",
    "presence": "有無・種類",
    "count": "数",
    "time": "時刻・文字",
    "shade": "色の濃淡",
    "pattern": "柄",
    "shape": "形",
    "state": "状態（季節・オンオフ・開閉）",
    "wear": "身に着けている物",
    "hair": "髪型",
}


def _attr(it: PicItem, axis: str):
    return {
        "position": (round(it.x / 10), round(it.y / 10)),
        "action": it.pose,
        "count": it.count,
        "time": it.label,
        "shade": it.shade,
        "pattern": it.pattern,
        "shape": it.shape,
        "state": it.state,
        "wear": tuple(sorted((w.kind, w.pattern, w.shade) for w in it.wear)),
        "hair": it.hair,
    }[axis]


def item_key(it: PicItem) -> tuple:
    """Everything that changes how an item is drawn, except its scale."""
    return (it.kind, it.x, it.y, it.pose, it.label, it.flip, it.shade, it.pattern, it.shape, it.count, it.state,
            it.hair, tuple((w.kind, w.pattern, w.shade) for w in it.wear))  # fmt: skip


def options(fig: Figure) -> list[list[PicItem]]:
    """The four options as lists of items: one panel each (picture_set), or the marked items of the one scene."""
    if fig.kind == "labeled_scene":
        scene = fig.pictures[0].items if fig.pictures else []
        return [[it for it in scene if it.mark == n] for n in sorted({it.mark for it in scene if it.mark})]
    return [p.items for p in fig.pictures]


def contrast_axes(fig: Figure) -> list[str]:
    """The axes along which the options differ. An item kind present in some options and not others is 'presence'."""
    opts = options(fig)
    if len(opts) < 2:
        return []
    axes: set[str] = set()
    kinds = [sorted(it.kind for it in o) for o in opts]
    if fig.kind == "labeled_scene":
        if len({tuple(k) for k in kinds}) > 1:
            axes.add("presence")
        axes.add("position")  # the marked things always sit in different places of one picture
    elif len({tuple(k) for k in kinds}) > 1:
        axes.add("presence")
    for axis in AXES:
        if axis in ("presence",) or (fig.kind == "labeled_scene" and axis == "position"):
            continue
        per_kind: dict[str, set] = {}
        for o in opts:
            for k in {it.kind for it in o}:
                per_kind.setdefault(k, set()).add(frozenset(str(_attr(it, axis)) for it in o if it.kind == k))
        if any(len(v) > 1 for v in per_kind.values()):
            axes.add(axis)
    return sorted(axes)


def composition_key(part: str, fig: Figure) -> tuple:
    """Two sets with the same key show the same kinds of things and differ in the same way - a repeat for the learner
    even when the wording of the script is new."""
    kinds = sorted({it.kind for o in options(fig) for it in o})
    return (part, fig.kind, tuple(kinds), tuple(contrast_axes(fig)))


def picture_figure(figures: list[Figure]) -> Figure | None:
    return next((f for f in figures if f.kind in PICTURE_FORMS), None)


def least_used_axis(used: dict[str, int], seed: str) -> str:
    """The axis generated least so far for this part; ties broken by the seed so parallel requests spread out."""
    order = sorted(AXES, key=lambda a: (used.get(a, 0), (hash_seed(seed) + list(AXES).index(a)) % len(AXES)))
    return order[0]


def hash_seed(seed: str) -> int:
    import hashlib

    return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % 1000

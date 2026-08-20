import re


GM = {
    0: "osu",
    1: "taiko",
    2: "fruits",
    3: "mania",
}
NGM = {
    "0": "osu",
    "1": "taiko",
    "2": "fruits",
    "3": "mania",
}
GMN = {
    "osu": "Std",
    "taiko": "Taiko",
    "fruits": "Ctb",
    "mania": "Mania",
}
FGM = {
    "osu": 0,
    "taiko": 1,
    "fruits": 2,
    "mania": 3,
}

MODE_ALIASES = {
    "0": "0",
    "osu": "0",
    "osu!": "0",
    "o": "0",
    "std": "0",
    "standard": "0",
    "1": "1",
    "taiko": "1",
    "t": "1",
    "tk": "1",
    "2": "2",
    "catch": "2",
    "c": "2",
    "ctb": "2",
    "fruits": "2",
    "3": "3",
    "mania": "3",
    "m": "3",
}


def parse_mode(value: int | str) -> str | None:
    mode = MODE_ALIASES.get(str(value).strip().lower())
    if mode is None or mode not in {"0", "1", "2", "3"}:
        return None
    return mode


BEATMAPSET_URL_PATTERN = re.compile(r"(?:https?://)?osu\.ppy\.sh/beatmapsets/(\d+)(?:#[^/\s]+/(\d+))?")
BEATMAP_URL_PATTERN = re.compile(r"(?:https?://)?osu\.ppy\.sh/(?:b|beatmaps)/(\d+)")
USER_URL_PATTERN = re.compile(r"(?:https?://)?osu\.ppy\.sh/(?:u|users)/(\d+)")


def extract_beatmap_id(value: str) -> str | None:
    if match := BEATMAPSET_URL_PATTERN.search(value):
        return match.group(2)
    if match := BEATMAP_URL_PATTERN.search(value):
        return match.group(1)
    return None


def extract_beatmapset_id(value: str) -> str | None:
    if match := BEATMAPSET_URL_PATTERN.search(value):
        return match.group(1)
    return None


def extract_user_id(value: str) -> str | None:
    if match := USER_URL_PATTERN.search(value):
        return match.group(1)
    return None


def normalize_map_mode(requested_mode: int | str, native_mode: int, source: str = "osu") -> str:
    """Return a score mode compatible with the beatmap's native ruleset."""
    requested = int(requested_mode)
    if native_mode == 0:
        # Standard beatmaps may be converted to other rulesets.
        return str(requested)
    return str(native_mode)


def mods2list(args: str) -> list:
    args = args.replace(" ", "").replace(",", "").replace("，", "")
    args = args.upper()
    return [args[i : i + 2] for i in range(0, len(args), 2)]

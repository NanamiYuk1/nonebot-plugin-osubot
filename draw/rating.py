import re
from io import BytesIO
from pathlib import Path
from datetime import datetime
from collections import Counter
from statistics import mode, median

import jinja2
from nonebot.log import logger
from PIL import Image, ImageDraw

from ..api import api_info
from ..schema.user import UserCompact
from ..schema.match import Game, Match
from .utils import crop_bg, draw_fillet, open_user_icon, draw_rounded_rectangle
from .browser import persistent_page
from .static import (
    Torus_SemiBold_20,
    Torus_SemiBold_25,
    Torus_SemiBold_30,
    Torus_SemiBold_40,
    Torus_SemiBold_45,
)


# ===== 从 match_history 导入 rooms API 适配函数 =====
from .match_history import (
    _is_rooms_response,
    _convert_rooms_to_match_format,
    _fetch_room_team_map,
    _ROOM_TYPE_MAP,
)


async def _fetch_match_data(match_id: str) -> dict:
    """
    统一获取比赛/多人房数据，自动回退 matches → rooms。
    返回已转换为 Match schema 兼容格式的原始字典。
    """
    raw = None
    source = None

    # 先尝试 /matches/ API（传统 mp lobby）
    try:
        raw = await api_info("matches", f"https://osu.ppy.sh/api/v2/matches/{match_id}")
        source = "matches"
    except Exception:
        raw = None

    # 失败则尝试 /rooms/ API（osu!lazer multiplayer room）
    if raw is None:
        try:
            raw = await api_info("matches", f"https://osu.ppy.sh/api/v2/rooms/{match_id}")
            source = "rooms"
        except Exception:
            raw = None

    if raw is None:
        raise ValueError(f"未找到 ID 为 {match_id} 的比赛/多人房，请检查 ID 是否正确。")

    # 如果是 rooms 格式，转换为 matches 格式
    if source == "rooms" or _is_rooms_response(raw):
        raw = await _convert_rooms_to_match_format(raw, match_id)

    return raw


def _inject_team_from_events(games: list[Game], pid_teams: dict[int, dict[str, str]], fallback_teams: dict[str, str] | None) -> None:
    """
    将 events 接口获取的红蓝分队信息注入到每个 game 的 score.match.team 中。

    由于 Game schema 没有 id/game_id 字段，无法按 playlist_item_id 精确匹配，
    因此合并所有数据源（fallback_teams + pid_teams 并集）统一注入。
    对于大多数多人房场景，同一用户在所有对局中的队伍是固定的，全局快照足够准确。
    """
    # 合并所有数据源：pid_teams 先放，fallback_teams 覆盖（优先级更高）
    merged_teams: dict[str, str] = {}
    for uid_map in pid_teams.values():
        merged_teams.update(uid_map)
    if fallback_teams:
        merged_teams.update(fallback_teams)

    if not merged_teams:
        return

    injected_count = 0
    for game in games:
        for score in game.scores:
            uid_str = str(score.user_id)
            team_colour = merged_teams.get(uid_str)
            if team_colour in ("red", "blue"):
                if score.match is None:
                    score.match = {"team": team_colour}
                else:
                    score.match["team"] = team_colour
                injected_count += 1



async def draw_rating_card(data: dict) -> bytes:
    """Render a rating card from prepared match statistics."""
    template_path = Path(__file__).parent / "rating_templates"
    template = jinja2.Environment(  # noqa: S701
        loader=jinja2.FileSystemLoader(str(template_path)), enable_async=True
    ).get_template("index.html")
    async with persistent_page(
        "rating", (template_path / "index.html").as_uri(), {"width": 1280, "height": 900}
    ) as page:
        await page.set_content(await template.render_async(**data), wait_until="domcontentloaded")
        await page.evaluate(
            "Promise.race([Promise.all([document.fonts.ready,"
            "...Array.from(document.images,x=>x.decode().catch(()=>{}))]),"
            "new Promise(resolve=>setTimeout(resolve,8000))])"
        )
        element = await page.query_selector(".card")
        assert element
        return await element.screenshot(type="png")


async def draw_rating(match_id: str, algorithm: str = "osuplus") -> list[bytes]:
    """Render multiplayer rating with a layout matched to the room type.

    房间可能在运行中切换模式（如热身 head-to-head → 正赛 team-vs），
    每种模式分别渲染一张 rating 卡，返回多张图片。
    """
    raw = await _fetch_match_data(match_id)
    match_info = Match(**raw)

    all_games = [
        event.game
        for event in match_info.events
        if event.detail.type == "other" and event.game is not None and event.game.scores
    ]
    if not all_games:
        raise ValueError("该多人房没有可用于评分的对局")

    # ── 按模式分组（team 类在前，其余在后）──
    mode_set: set[str] = {game.team_type for game in all_games}
    team_modes = [m for m in ("team-vs", "tag-team-vs") if m in mode_set]
    other_modes = [m for m in mode_set if m not in ("team-vs", "tag-team-vs")]
    ordered_modes = team_modes + other_modes

    images: list[bytes] = []
    for mode_type in ordered_modes:
        mode_games = [game for game in all_games if game.team_type == mode_type]
        # 构造只含该模式 events 的 Match，保证评分/胜负统计只针对该模式
        mode_events = [
            event
            for event in match_info.events
            if event.game is not None and event.game.team_type == mode_type
        ]
        mode_match = Match(
            match=match_info.match,
            events=mode_events,
            users=match_info.users,
        )
        data = _build_rating_data(mode_match, mode_games, match_id, algorithm, mode_type)
        images.append(await draw_rating_card(data))
    return images


def _build_rating_data(
    match_info: Match,
    games: list,
    match_id: str,
    algorithm: str,
    team_type: str,
) -> dict:
    """为指定模式的 games 构建 rating 渲染数据。"""
    is_team = team_type in ("team-vs", "tag-team-vs")
    if is_team:
        _has_team = any(
            (score.match or {}).get("team") in ("red", "blue")
            for game in games
            for score in game.scores
        )
        if not _has_team:
            team_type = "head-to-head"
            is_team = False

    # ── 核心改动：记录存在即算参与（不再要求 score > 0） ──
    appeared_user_ids = {score.user_id for game in games for score in game.scores}
    users = [user for user in match_info.users if user.id in appeared_user_ids]

    team_size = None
    red_wins = 0
    blue_wins = 0
    if team_type == "team-vs":
        # ── team_size 仅用于展示，绝不用于过滤对局 ──
        team_sizes = []
        for game in games:
            # team-vs：记录存在且有队伍归属即计入人数
            red_size = sum(
                1 for score in game.scores
                if (score.match or {}).get("team") == "red"
            )
            blue_size = sum(
                1 for score in game.scores
                if (score.match or {}).get("team") == "blue"
            )
            if red_size == blue_size and red_size > 0:
                team_sizes.append(red_size)
        if team_sizes:
            team_size = mode(team_sizes)

        # ── 全部对局逐局比红蓝总分计胜场，跳过 abort 局 ──
        for index, game in enumerate(games, start=1):
            # ── 兜底：该局所有 score 都未通过(passed=false) → 整局被强制关闭 ──
            # 主防线已在 _convert_rooms_to_match_format 用 events 的
            # game_aborted 跳过；这里是 events 解析失败时的二级保险。
            all_failed = all(
                not (sc.match or {}).get("passed", True) for sc in game.scores
            )
            if game.scores and all_failed:
                logger.info(f"[逐局] 第{index}局: → 跳过（整局被强制关闭/abort：全员未通过）")
                continue

            red_score = 0
            blue_score = 0
            red_alive = 0
            blue_alive = 0
            for score in game.scores:
                # team-vs：与 osu! 官方判定一致，只统计通过(passed)玩家的分数。
                # 参考 osu-web resources/js/legacy-match/content.tsx：
                #   if (!score.passed) continue; scores[team] += score.total_score;
                # fail(HP归零) 的玩家记录仍在、队伍仍在，但分数不计入队伍总分。
                if not (score.match or {}).get("passed", True):
                    continue
                team_colour = (score.match or {}).get("team")
                if team_colour == "red":
                    red_alive += 1
                    red_score += score.score
                elif team_colour == "blue":
                    blue_alive += 1
                    blue_score += score.score

            # 只有某队【完全无人出手】(没有任何带队伍的 score 记录) 才不计；
            # 有人但全 fail 仍计——蓝队全 fail 时蓝队总分为 0，按官方规则判红队胜。
            # （官方判定：队伍总分只统计 passed 玩家，winningTeam = blue > red ? blue : red）
            if red_alive == 0 and blue_alive == 0:
                logger.info(
                    f"[逐局] 第{index}局: 红={red_score}({red_alive}人) "
                    f"蓝={blue_score}({blue_alive}人) → 不计（两队均无通过玩家）"
                )
                continue

            if red_score > blue_score:
                red_wins += 1
                result_text = "红胜"
            elif blue_score > red_score:
                blue_wins += 1
                result_text = "蓝胜"
            else:
                result_text = "平局不计"
            logger.info(
                f"[逐局] 第{index}局: 红={red_score}({red_alive}人) "
                f"蓝={blue_score}({blue_alive}人) → {result_text}"
            )
        logger.info(f"[比分] 红={red_wins} 蓝={blue_wins}")

    players = []
    calculator = PlayerRatingCalculation(match_info)
    for user in users:
        stats = PlayerMatchStats(user, games)
        _team = getattr(user, "team", None)
        tag = (getattr(_team, "short_name", None) or getattr(_team, "name", None)) if _team else None
        # 记录存在即算参与：不再因总分为 0（如全部 fail）而剔除玩家，保证全员上卡
        rating = calculator.get_rating(user.id, algorithm)
        player = {
            "user_id": user.id,
            "name": f"[{tag}] {user.username}" if tag else user.username,
            "avatar": f"https://a.ppy.sh/{user.id}",
            "team": stats.player_team,
            "rating": rating,
            "total_score": stats.total_score,
            "average_score": stats.average_score,
            "wins": stats.win_and_lose[0],
            "losses": stats.win_and_lose[1],
            "played": stats.win_and_lose[2],
            "win_rate": stats.win_rate,
            "record_text": f"{stats.win_and_lose[0]}W—{stats.win_and_lose[1]}L · {stats.win_rate:.1%}",
            "top1_count": 0,
            "top1_rate": 0.0,
        }
        if team_type == "head-to-head":
            h2h = analyze_head_to_head_history(games, user.id)
            player.update(
                {
                    "top1_count": h2h["number_of_games_top1"],
                    "played": h2h["number_of_games"],
                    "top1_rate": h2h["top1_rate"],
                }
            )
        players.append(player)

    if not players:
        raise ValueError("该多人房没有有效的玩家成绩")
    players.sort(key=lambda player: player["rating"], reverse=True)
    for rank, player in enumerate(players, start=1):
        player["rank"] = rank

    match_pattern = r"([^:]+): [\(（](.+?)[\)）] vs [\(（](.+?)[\)）]"
    match_name = match_info.match["name"]
    name_match = re.search(match_pattern, match_name, re.IGNORECASE)
    title = name_match.group(1) if name_match else match_name
    red_name = name_match.group(2) if name_match else "红队"
    blue_name = name_match.group(3) if name_match else "蓝队"
    start_time = datetime.fromisoformat(match_info.match["start_time"])
    end_value = match_info.match.get("end_time")
    end_time = datetime.fromisoformat(end_value).strftime("%H:%M") if end_value else "进行中"

    data = {
        "match_id": match_id,
        "title": title,
        "time_range": f"{start_time.strftime('%Y/%m/%d %H:%M')}—{end_time}",
        "team_type": team_type,
        "algorithm": algorithm.upper(),
        "game_count": len(games),
        "player_count": len(players),
        "players": players,
        "mvp": players[0],
        "max_top1_count": max((player.get("top1_count", 0) for player in players), default=0),
        "max_total_score": max(player["total_score"] for player in players),
        "average_rating": sum(player["rating"] for player in players) / len(players),
        "red_name": red_name,
        "blue_name": blue_name,
        "red_wins": red_wins,
        "blue_wins": blue_wins,
        "red_players": [player for player in players if player["team"] == "red"],
        "blue_players": [player for player in players if player["team"] == "blue"],
        "team_size": team_size,
    }
    return data


def rating_to_wn8_hex(rating: float, win_rate: float) -> tuple[float, str]:
    rating_to_wn8_factor = 2900 / 2
    wn8_rating = rating * rating_to_wn8_factor * 0.6
    wn8_rating += (win_rate / 100) * 2900 * 0.4

    wn8_ranges_hex_colors = [
        (0, 300, "#871F17"),
        (300, 449, "#BD413A"),
        (450, 649, "#C17E2B"),
        (650, 899, "#C9B93C"),
        (900, 1199, "#899B3B"),
        (1200, 1599, "#557232"),
        (1600, 1999, "#5998BC"),
        (2000, 2449, "#4871C1"),
        (2450, 2899, "#7141AF"),
        (2900, float("inf"), "#3A136B"),
    ]

    for lower_bound, upper_bound, hex_color in wn8_ranges_hex_colors:
        if lower_bound <= int(wn8_rating) <= upper_bound:
            return wn8_rating, hex_color
    return wn8_rating, "#FFFFFF"


def score_to_3digit(score: float) -> str:
    if score > 1000000:
        short_score = score / 1000000
        return f"{short_score:.2f}M"
    elif score > 1000:
        short_score = score / 1000
        return f"{short_score:.2f}K"
    return str(score)


def analyze_team_vs_game_history(game_history: list[Game]) -> dict:
    red_score = 0
    blue_score = 0
    team_size_list = []
    for game in game_history:
        win_side = get_win_side(game)
        if win_side == "red":
            red_score += 1
        elif win_side == "blue":
            blue_score += 1
        team_red_size = 0
        team_blue_size = 0
        for entry in game.scores:
            # team-vs：记录存在且有队伍归属即计入人数（不再跳过 score==0）
            if (entry.match or {}).get("team") == "red":
                team_red_size += 1
            elif (entry.match or {}).get("team") == "blue":
                team_blue_size += 1
        if team_red_size == team_blue_size and team_red_size != 0:
            team_size_list.append(team_red_size)

    analyze_result = {
        "red_score": red_score,
        "blue_score": blue_score,
        "team_size": mode(team_size_list) if team_size_list else 0,
    }
    return analyze_result


def analyze_head_to_head_history(game_history: list[Game], user_id: int) -> dict:
    """
    分析个人混战（head-to-head）历史数据。
    TOP 1 判定：每局 Score 最高者为该局 TOP 1（支持并列）。
    被强制关闭(abort)的局（全员 passed=false）不计入。
    """
    number_of_games = 0
    number_of_games_top1 = 0

    for game in game_history:
        # 兜底：全员未通过 → 整局被强制关闭，跳过
        all_failed = all(
            not (entry.match or {}).get("passed", True) for entry in game.scores
        )
        if game.scores and all_failed:
            continue

        # 通用口径：score 记录存在即算参与（不再要求 score > 0）
        valid_scores = list(game.scores)
        if not valid_scores:
            continue

        user_entries = [entry for entry in valid_scores if int(entry.user_id) == int(user_id)]
        if not user_entries:
            continue

        number_of_games += 1
        max_score = max(entry.score for entry in valid_scores)
        if any(entry.score == max_score for entry in user_entries):
            number_of_games_top1 += 1

    return {
        "number_of_games": number_of_games,
        "number_of_games_top1": number_of_games_top1,
        "top1_rate": (number_of_games_top1 / number_of_games if number_of_games != 0 else 0),
    }


def get_win_side(game: Game) -> str:
    """判定 team-vs 单局获胜方。

    与 osu! 官方判定一致：只统计通过(passed)玩家的 total_score。
    参考 osu-web resources/js/legacy-match/content.tsx：
        if (!score.passed) continue; scores[team] += score.total_score;
    """
    scores = game.scores
    total_score_red = 0
    total_score_blue = 0

    for entry in scores:
        if not (entry.match or {}).get("passed", True):
            continue
        if (entry.match or {}).get("team") == "red":
            total_score_red += entry.score
        elif (entry.match or {}).get("team") == "blue":
            total_score_blue += entry.score

    if total_score_red > total_score_blue:
        return "red"
    else:
        return "blue"


class PlayerRatingCalculation:
    def __init__(self, match_info: Match):
        self._match_info = match_info

    def get_rating(self, user_id: int, algorithm: str = "osuplus"):
        if algorithm == "osuplus":
            return self._osuplus_rating(user_id)
        if algorithm == "bathbot":
            return self._bathbot_rating(user_id)
        if algorithm == "flashlight":
            return self._flashlight_rating(user_id)
        return None

    def _osuplus_rating(self, user_id: int) -> float:
        game_history = []
        for sequence in self._match_info.events:
            if sequence.detail.type == "other" and sequence.game is not None:
                game_history.append(sequence.game)

        number_of_games = 0
        number_of_games_by_user = 0
        user_scores = []
        average_scores = []

        for i, game in enumerate(game_history):
            if len(game.scores) == 0:
                continue
            number_of_games += 1
            for entry in game.scores:
                user_info = next(
                    (user for user in self._match_info.users if user.id == user_id),
                    None,
                )
                if user_info is None:
                    continue
                if entry.user_id == user_id:
                    average_scores.append(sum(e.score for e in game.scores) / len(game.scores))
                    user_scores.append(entry.score)
                    number_of_games_by_user += 1

        n_prime = len(user_scores)
        if n_prime == 0:
            return 0.0
        sum_of_ratios = sum(s_i / m_i for s_i, m_i in zip(user_scores, average_scores) if m_i != 0)
        cost = (2 / (n_prime + 2)) * sum_of_ratios
        return cost

    def _bathbot_rating(self, user_id: int) -> float:
        game_history = []
        for sequence in self._match_info.events:
            if sequence.detail.type == "other" and sequence.game is not None:
                game_history.append(sequence.game)

        number_of_games = 0
        number_of_games_by_user = 0
        user_tiebreaker_score = 0
        average_tiebreaker_score = 0
        user_scores = []
        average_scores = []
        red_score = 0
        blue_score = 0
        tiebreaker = False
        all_played_mods = set()

        for i, game in enumerate(game_history):
            if len(game.scores) == 0:
                continue
            number_of_games += 1
            win_side = get_win_side(game)
            if win_side == "red":
                red_score += 1
            elif win_side == "blue":
                blue_score += 1
            for entry in game.scores:
                user_info = next(
                    (user for user in self._match_info.users if user.id == user_id),
                    None,
                )
                if user_info is None:
                    continue
                if entry.user_id == user_id:
                    for mod in entry.mods:
                        all_played_mods.add(mod)
                    average_scores.append(sum(e.score for e in game.scores) / len(game.scores))
                    user_scores.append(entry.score)
                    number_of_games_by_user += 1
            if i == len(game_history) - 2 and red_score == blue_score:
                tiebreaker = True
                average_tiebreaker_score = sum(entry.score for entry in game.scores) / len(game.scores)
                for entry in game.scores:
                    if entry.user_id == user_id:
                        user_tiebreaker_score = entry.score
                        break

        if number_of_games_by_user == 0:
            return 0.0

        score_sum = sum(ps / avs for ps, avs in zip(user_scores, average_scores) if avs != 0)
        participation_bonus = number_of_games_by_user * 0.5
        tiebreaker_bonus = user_tiebreaker_score / average_tiebreaker_score if tiebreaker and average_tiebreaker_score else 0
        average_factor = 1 / number_of_games_by_user
        participation_bonus_factor = 1.4 ** ((number_of_games_by_user - 1) / max(number_of_games - 1, 1)) ** 0.6
        mod_combination_bonus_factor = 1 + 0.02 * max(0, len(all_played_mods) - 2)
        rating = (
            (score_sum + participation_bonus + tiebreaker_bonus)
            * average_factor
            * participation_bonus_factor
            * mod_combination_bonus_factor
        )
        return rating

    def _flashlight_rating(self, user_id: int) -> float:
        game_history = []
        for sequence in self._match_info.events:
            if sequence.detail.type == "other" and sequence.game is not None:
                game_history.append(sequence.game)

        number_of_games_by_user = 0
        user_scores = []
        median_scores = []
        counts = Counter()

        for i, game in enumerate(game_history):
            if len(game.scores) == 0:
                continue
            for entry in game.scores:
                counts[entry.user_id] += 1
                user_info = next(
                    (user for user in self._match_info.users if user.id == user_id),
                    None,
                )
                if user_info is None:
                    continue
                if entry.user_id == user_id:
                    median_scores.append(median(e.score for e in game.scores))
                    user_scores.append(entry.score)
                    number_of_games_by_user += 1

        if number_of_games_by_user == 0:
            return 0.0

        occurrences = sorted(counts.values(), reverse=True)
        median_of_games_of_all_users = median(occurrences)

        sum_of_ratios = sum(N_i / M_i for N_i, M_i in zip(user_scores, median_scores) if M_i != 0)
        average_ratio = sum_of_ratios / number_of_games_by_user
        adjustment_factor = (number_of_games_by_user / max(median_of_games_of_all_users, 1)) ** (1 / 3)
        match_costs = average_ratio * adjustment_factor
        return match_costs


class PlayerMatchStats:
    def __init__(self, user: UserCompact, game_history: list[Game]):
        self.user = user
        self.game_history = game_history
        self.player_team = self._get_player_team()
        self.win_and_lose = self._get_win_and_lose()
        self.win_rate = self._get_win_rate()
        self.total_score = self._get_total_score()
        self.average_score = self._get_average_score()

    def _get_player_team(self) -> str:
        for game in self.game_history:
            for entry in game.scores:
                if entry.user_id == self.user.id:
                    return (entry.match or {}).get("team", "none")
        return "none"

    def _get_win_and_lose(self) -> tuple:
        number_of_wins_by_user = 0
        number_of_games_by_user = 0

        for i, game in enumerate(self.game_history):
            if len(game.scores) == 0:
                continue
            # 跳过被强制关闭(abort)的局
            if all(not (e.match or {}).get("passed", True) for e in game.scores):
                continue
            for entry in game.scores:
                if entry.user_id == self.user.id:
                    number_of_games_by_user += 1
                    player_team = (entry.match or {}).get("team", "none")
                    win_side = get_win_side(game)
                    if player_team == win_side:
                        number_of_wins_by_user += 1

        number_of_loses_by_user = number_of_games_by_user - number_of_wins_by_user
        return number_of_wins_by_user, number_of_loses_by_user, number_of_games_by_user

    def _get_win_rate(self) -> float:
        number_of_wins_by_user = 0
        number_of_games_by_user = 0

        for i, game in enumerate(self.game_history):
            if len(game.scores) == 0:
                continue
            # 跳过被强制关闭(abort)的局
            if all(not (e.match or {}).get("passed", True) for e in game.scores):
                continue
            for entry in game.scores:
                if entry.user_id == self.user.id:
                    number_of_games_by_user += 1
                    player_team = (entry.match or {}).get("team", "none")
                    win_side = get_win_side(game)
                    if player_team == win_side:
                        number_of_wins_by_user += 1

        if number_of_games_by_user == 0:
            return 0
        return number_of_wins_by_user / number_of_games_by_user

    def _get_total_score(self) -> int:
        total_score = 0
        for game in self.game_history:
            for entry in game.scores:
                if entry.user_id == self.user.id:
                    total_score += entry.score
        return total_score

    def _get_average_score(self) -> float:
        total_score = 0
        number_of_games = 0
        for game in self.game_history:
            for entry in game.scores:
                if entry.user_id == self.user.id:
                    total_score += entry.score
                    number_of_games += 1
        if number_of_games == 0:
            return 0
        return total_score / number_of_games
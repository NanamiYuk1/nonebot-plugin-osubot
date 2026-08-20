import asyncio
import json
from io import BytesIO
from urllib.parse import quote, urlencode
from datetime import datetime, timedelta
from typing import Union, Literal, Optional

from nonebot.log import logger
from expiringdict import ExpiringDict
from nonebot import get_plugin_config
from httpx import HTTPError, Response

from .network.manager import network_manager
from .schema.beatmapsets import BeatmapSets
from .utils import FGM, extract_user_id
from .config import Config
from .mods import get_mods
from .network import auto_retry
from .exceptions import NetworkError
from .network.first_response import get_first_response
from .schema import User, NewScore, RecommendData
from .schema.score import UnifiedScore, NewStatistics, UnifiedBeatmap, get_score_version
from .schema.ppysb import InfoResponse, ScoresResponse, V2ScoresResponse
from .schema.user import Level, GradeCounts, UnifiedUser, UserStatistics

api = "https://osu.ppy.sh/api/v2"
cache = ExpiringDict(max_len=1, max_age_seconds=86400)
# 谱面元信息变化极少，缓存 1 小时避免每次出图都重新请求
map_cache = ExpiringDict(max_len=500, max_age_seconds=3600)
plugin_config = get_plugin_config(Config)


@auto_retry
async def safe_async_get(url, headers: Optional[dict] = None, params: Optional[dict] = None) -> Response:
    client = await network_manager.get_client()
    req = await client.get(url, headers=headers, params=params)
    return req


@auto_retry
async def safe_async_post(url, headers=None, data=None, json=None) -> Response:
    client = await network_manager.get_client()
    req = await client.post(url, headers=headers, data=data, json=json)
    return req


async def renew_token():
    """获取/刷新 osu! API 客户端令牌。

    凭据优先级：osu_client/osu_key（主 API 凭据）→ osu_oauth_client_id/osu_oauth_client_secret
    （OAuth 应用凭据，同样可用于 client_credentials 授权）。
    失败时抛 NetworkError 而不是静默返回，避免后续请求带着空令牌全部 401。
    """
    url = "https://osu.ppy.sh/oauth/token"
    cid = get_oauth_client_id()
    csec = get_oauth_client_secret()
    if not cid or not csec:
        raise NetworkError(
            "osu! API 未配置凭据：请设置 osu_client/osu_key，或 osu_oauth_client_id/osu_oauth_client_secret"
        )
    req = await safe_async_post(
        url,
        json={
            "client_id": cid,
            "client_secret": csec,
            "grant_type": "client_credentials",
            "scope": "public",
        },
    )
    if req and req.status_code == 200:
        osu_token = req.json()
        cache.update({"token": osu_token["access_token"]})
    else:
        logger.error(f"更新OSU token出错 错误{req.status_code if req else 'None'}")
        raise NetworkError(
            f"osu! API token 刷新失败：HTTP {req.status_code if req else 'None'}"
        )


async def get_headers() -> dict[str, str]:
    token = cache.get("token")
    if not token:
        await renew_token()
        token = cache.get("token")
    return {"Authorization": f"Bearer {token}", "x-api-version": "20220705"}


async def fetch_score_batch(
    uid: Union[int, str],
    mode: str,
    scope: str,
    batch_size: int,
    offset: int,
    legacy_only: bool,
    include_failed: bool,
) -> list[UnifiedScore]:
    """并发获取单次批次数据"""
    url = (
        f"{api}/users/{uid}/scores/{scope}?mode={mode}&limit={batch_size}"
        f"&offset={offset}&legacy_only={int(legacy_only)}"
        f"&include_fails={int(include_failed)}"
    )
    data = await make_request(url, await get_headers(), "未找到该玩家BP")
    if not data:
        return []
    scores = [NewScore(**i) for i in data]
    return [
        UnifiedScore(
            mods=i.mods,
            ruleset_id=i.ruleset_id,
            rank=i.rank,
            accuracy=i.accuracy * 100,
            total_score=i.total_score,
            ended_at=datetime.strptime(i.ended_at.replace("Z", ""), "%Y-%m-%dT%H:%M:%S") + timedelta(hours=8),
            max_combo=i.max_combo,
            statistics=i.statistics,
            legacy_total_score=i.legacy_total_score,
            passed=i.passed,
            pp=i.pp,
            score_version=get_score_version(i.legacy_score_id),
            beatmap=UnifiedBeatmap(
                id=i.beatmap_id,
                user_id=i.beatmap.user_id,
                set_id=i.beatmapset.id,
                artist=i.beatmapset.artist,
                title=i.beatmapset.title,
                version=i.beatmap.version,
                creator=i.beatmapset.creator,
                total_length=i.beatmap.total_length,
                mode=i.beatmap.mode_int,
                bpm=i.beatmap.bpm,
                cs=i.beatmap.cs,
                ar=i.beatmap.ar,
                hp=i.beatmap.drain,
                od=i.beatmap.accuracy,
                stars=i.beatmap.difficulty_rating,
                convert=i.beatmap.convert,
            ),
            beatmapset=i.beatmapset,
        )
        for i in scores
    ]


async def get_user_scores(
    uid: Union[int, str],
    mode: str,
    scope: str = Literal["recent", "best"],
    source: str = "osu",
    legacy_only: bool = 0,
    include_failed: bool = True,
    offset: int = 0,
    limit: int = 200,
) -> list[UnifiedScore]:
    if source == "osu":
        if limit <= 0:
            return []

        # 计算需要多少次请求
        # 计算需要多少批次
        batch_size = 100
        total_batches = (limit + batch_size - 1) // batch_size  # ceiling(limit/batch_size)
        all_scores = []
        # 分批并发请求
        for batch_idx in range(0, total_batches, 2):
            current_batches = range(batch_idx, min(batch_idx + 2, total_batches))

            # 生成 tasks（并发执行）
            tasks = []
            for batch_n in current_batches:
                batch_offset = offset + batch_n * batch_size
                actual_batch_size = min(batch_size, limit - batch_n * batch_size)

                if actual_batch_size <= 0:
                    continue  # 已获取足够数据

                task = fetch_score_batch(uid, mode, scope, actual_batch_size, batch_offset, legacy_only, include_failed)
                tasks.append(task)
            # 并发请求当前批次
            batch_results = await asyncio.gather(*tasks)

            for batch_scores in batch_results:
                all_scores.extend(batch_scores)
                if len(all_scores) >= limit:
                    return all_scores[:limit]  # 提前终止
        return all_scores[:limit]

    elif source == "ppysb":
        limit = min(limit, 100)
        url = f"https://api.ppy.sb/v1/get_player_scores?scope={scope}&id={uid}&mode={FGM[mode]}&limit={limit}&include_failed={int(include_failed)}"
        data = await make_request(url, {}, "未找到该玩家BP")
        data = ScoresResponse(**data)
        # 手动 offset；过滤掉缺少 beatmap 信息的成绩（无法渲染，仅个别异常条目）
        filtered_scores = [i for i in data.scores[offset:] if i.beatmap is not None]
        return [
            UnifiedScore(
                mods=get_mods(i.mods),
                ruleset_id=i.mode,
                rank=i.grade,
                accuracy=i.acc,
                total_score=i.score,
                ended_at=datetime.strptime(i.play_time, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=8),
                max_combo=i.max_combo,
                passed=True,
                pp=i.pp,
                statistics=NewStatistics(
                    miss=i.nmiss,
                    perfect=i.ngeki,
                    good=i.nkatu,
                    meh=i.n50,
                    ok=i.n100,
                    great=i.n300,
                    large_tick_hit=i.n100,
                    small_tick_miss=i.nkatu,
                ),
                beatmap=UnifiedBeatmap(
                    id=i.beatmap.id,
                    set_id=i.beatmap.set_id,
                    artist=i.beatmap.artist,
                    title=i.beatmap.title,
                    version=i.beatmap.version,
                    creator=i.beatmap.creator,
                    total_length=i.beatmap.total_length,
                    mode=i.beatmap.mode,
                    bpm=i.beatmap.bpm,
                    cs=i.beatmap.cs,
                    ar=i.beatmap.ar,
                    hp=i.beatmap.hp,
                    od=i.beatmap.od,
                    stars=i.beatmap.diff,
                ),
            )
            for i in filtered_scores
        ]


async def get_user_info_data(uid: Union[int, str], mode: str, source: str = "osu") -> UnifiedUser:
    if source == "osu":
        url = f"{api}/users/{uid}/{mode}"
        data = await make_request(url, await get_headers(), "未找到该玩家，请确认玩家ID")
        return UnifiedUser(**data)

    elif source == "ppysb":
        url = f"https://api.ppy.sb/v1/get_player_info?scope=all&id={uid}"
        data = await make_request(url, {}, "未找到该玩家，请确认玩家ID")
        data = InfoResponse(**data)
        info_data = UnifiedUser(
            avatar_url=f"https://a.ppy.sb/{data.player.info.id}",
            country_code=data.player.info.country.upper(),
            id=data.player.info.id,
            username=data.player.info.name,
            is_supporter=False,
        )
        if mode == "osu":
            info_data.statistics = parse_statistics(data, "0")
        if mode == "taiko":
            info_data.statistics = parse_statistics(data, "1")
        if mode == "fruits":
            info_data.statistics = parse_statistics(data, "2")
        if mode == "mania":
            info_data.statistics = parse_statistics(data, "3")
        if mode == "rxosu":
            info_data.statistics = parse_statistics(data, "4")
        if mode == "rxtaiko":
            info_data.statistics = parse_statistics(data, "5")
        if mode == "rxfruits":
            info_data.statistics = parse_statistics(data, "6")
        if mode == "aposu":
            info_data.statistics = parse_statistics(data, "8")
        return info_data


def parse_statistics(data: InfoResponse, mode):
    # 某模式可能没有统计记录，用 .get 兜底，避免 KeyError
    stats = data.player.stats.get(mode, {})
    return UserStatistics(
        grade_counts=GradeCounts(
            ssh=stats.get("xh_count", 0),
            ss=stats.get("x_count", 0),
            sh=stats.get("sh_count", 0),
            s=stats.get("s_count", 0),
            a=stats.get("a_count", 0),
        ),
        hit_accuracy=stats.get("acc", 0),
        is_ranked=True,
        level=Level(current=100, progress=99),
        maximum_combo=stats.get("max_combo", 0),
        play_count=stats.get("plays", 0),
        play_time=stats.get("playtime", 0),
        pp=stats.get("pp", 0),
        ranked_score=stats.get("rscore", 0),
        replays_watched_by_others=0,
        total_hits=stats.get("total_hits", 0),
        total_score=stats.get("tscore", 0),
        global_rank=stats.get("rank"),
        country_rank=stats.get("country_rank"),
    )


async def get_ppysb_map_scores(map_md5: str, uid: Union[int, str], mode: str):
    url = f"https://api.ppy.sb/v2/scores?user_id={uid}&mode={FGM[mode]}&map_md5={map_md5}"
    data = await make_request(url, {}, "未找到该玩家成绩")
    data = V2ScoresResponse(**data)
    return [
        UnifiedScore(
            mods=get_mods(i.mods),
            ruleset_id=i.mode,
            rank=i.grade,
            accuracy=i.acc,
            total_score=i.score,
            ended_at=datetime.strptime(i.play_time, "%Y-%m-%dT%H:%M:%S") + timedelta(hours=8),
            max_combo=i.max_combo,
            passed=True,
            pp=i.pp,
            statistics=NewStatistics(
                miss=i.nmiss,
                perfect=i.ngeki,
                good=i.nkatu,
                meh=i.n50,
                ok=i.n100,
                great=i.n300,
                large_tick_hit=i.n100,
                small_tick_miss=i.nkatu,
            ),
            beatmap=None,
        )
        for i in data.data
    ]


async def osu_api(
    project: str,
    uid: int = 0,
    mode: str = None,
    map_id: int = 0,
    offset: int = 0,
    limit: int = 5,
    legacy_only: int = 0,
) -> dict:
    # 获取用户 ID
    base_url = f"{api}/users/{uid}"
    query_params = {"limit": limit, "offset": offset, "legacy_only": legacy_only}

    if project == "recent":
        endpoint = f"{base_url}/scores/recent"
        query_params["include_fails"] = 1
    elif project == "pr":
        endpoint = f"{base_url}/scores/recent"
    elif project == "score":
        endpoint = f"{api}/beatmaps/{map_id}/scores/users/{uid}/all"
    elif project == "best_score":
        endpoint = f"{api}/beatmaps/{map_id}/scores/users/{uid}"
    elif project == "bp":
        endpoint = f"{base_url}/scores/best"
        query_params["limit"] = 100
    elif project == "map":
        endpoint = f"{api}/beatmaps/{map_id}"
        query_params = {}
    else:
        endpoint = f"{base_url}/{mode}" if mode else base_url
        query_params = {}

    if mode:
        query_params["mode"] = mode

    url = f"{endpoint}?{urlencode(query_params)}" if query_params else endpoint
    if project == "map":
        cache_key = f"{map_id}:{mode}"
        if cached := map_cache.get(cache_key):
            return cached
        data = await api_info(project, url)
        map_cache[cache_key] = data
        return data
    return await api_info(project, url)


async def api_info(project: str, url: str) -> dict:
    headers = (
        {"user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) Chrome/78.0.3904.108"}
        if project in ["mapinfo", "PPCalc"]
        else await get_headers()
    )
    error_messages = {
        "info": "未找到该玩家，请确认玩家ID",
        "bind": "未找到该玩家，请确认玩家ID",
        "recent": "未找到该玩家，请确认玩家ID",
        "score": "未找到该地图成绩，请检查是否搞混了mapID与setID或模式",
        "best_score": "未找到该地图成绩，请检查是否搞混了mapID与setID或模式",
        "bp": "未找到该玩家BP",
        "map": "未找到该地图，请检查是否搞混了mapID与setID",
    }
    return await make_request(url, headers, error_messages.get(project, "API请求失败，请联系管理员或稍后再尝试"))


async def make_request(url: str, headers: dict, error_message: str) -> dict:
    req = await safe_async_get(url, headers=headers)
    if not req:
        raise NetworkError("多次api请求失败，请稍后再试")
    if req.status_code == 401:
        # osu! token 可能已过期：强制刷新令牌后重试一次（自愈）
        try:
            await renew_token()
            req = await safe_async_get(url, headers=await get_headers())
        except Exception:
            req = None
        if not req:
            raise NetworkError(
                "osu! API 授权失败（401）且令牌刷新失败，请检查 osu_client/osu_key 或 OAuth 凭据配置"
            )
    if req.status_code == 404:
        raise NetworkError(error_message)
    elif req.status_code == 200:
        return req.json()
    elif req.status_code == 422:
        # ppysb 等接口对用户名/参数有格式校验（如 2-15 位），超长或含特殊字符会返回 422
        raise NetworkError("请求参数不合法（用户名可能过长或包含不允许的字符）")
    raise NetworkError(f"出现了未意料的响应码 {req.status_code}")


async def get_uid_by_name(name: str, source: str) -> int:
    if source == "osu":
        info = await get_osu_user(name)
        return info["id"]
    else:
        url = f"https://api.ppy.sb/v1/get_player_info?scope=all&name={name}"
        data = await make_request(url, {}, "未找到该玩家，请确认玩家ID是否正确")
        return data["player"]["info"]["id"]


async def get_osu_user(identifier: str) -> dict:
    """Resolve a username, UID, or osu! profile URL without losing numeric usernames."""
    identifier = identifier.strip()
    profile_id = extract_user_id(identifier)
    if profile_id:
        return await get_user_info(f"{api}/users/{profile_id}?key=id")

    key: str | None = None
    value = identifier
    if ":" in identifier:
        prefix, explicit_value = identifier.split(":", 1)
        if prefix.lower() in {"id", "uid"}:
            key, value = "id", explicit_value.strip()
        elif prefix.lower() in {"name", "user"}:
            key, value = "username", explicit_value.strip()

    if not value:
        raise NetworkError("用户名或 UID 不能为空")
    if key:
        return await get_user_info(f"{api}/users/{quote(value)}?key={key}")
    if not value.isdigit():
        return await get_user_info(f"{api}/users/{quote(value)}?key=username")

    # Pure numbers are ambiguous. Prefer an exact numeric username, then fall back to UID.
    try:
        return await get_user_info(f"{api}/users/{quote(value)}?key=username")
    except NetworkError:
        return await get_user_info(f"{api}/users/{value}?key=id")


async def get_ppysb_uid(name: str) -> int:
    url = f"https://api.ppy.sb/v1/get_player_info?scope=all&name={name}"
    data = await make_request(url, {}, "未找到该玩家，请确认玩家ID是否正确")
    return data["player"]["info"]["id"]


async def get_user_info(url: str) -> dict:
    return await make_request(url, await get_headers(), "未找到该玩家，请确认玩家ID是否正确")


# ===========================================================================
# 成就（Achievements）
# ===========================================================================
# osu! API v2 没有公开的成就列表接口。成就目录来源（按优先级）：
#   1. inex.osekai.net/api/medals/get_all —— osekai 全量成就列表（首选，实时）
#   2. 用户主页 HTML 的 data-initial-data 内嵌 JSON（兜底）
#   3. 磁盘缓存 osufile/medals/achievements_catalog.json（离线兜底）
# 目录字段统一规范化为：id/name/slug/icon_url/grouping/mode/instructions/description。
_achievements_cache: dict = {}  # {"fetched_at": ts, "achievements": [...]}
_OSEKAI_MEDALS_URL = "https://inex.osekai.net/api/medals/get_all"
_ACH_CACHE_FILE = "achievements_catalog.json"


def _normalize_achievement(raw: dict) -> dict:
    """将不同来源的成就字段规范化为统一结构。"""
    if "Medal_ID" in raw:  # osekai get_all 格式
        name = raw.get("Name", "")
        link = raw.get("Link", "") or ""
        if link and not link.startswith("http"):
            link = f"https://assets.ppy.sh/medals/web/{link}"
        return {
            "id": int(raw["Medal_ID"]),
            "name": name,
            "slug": raw.get("Link", "") or "",
            "icon_url": link,
            "grouping": raw.get("Grouping") or "",
            "mode": raw.get("Gamemode") or None,
            "instructions": raw.get("Instructions") or raw.get("Solution") or "",
            "description": raw.get("Description") or "",
            "solution": raw.get("Solution") or "",
        }
    # osu 用户主页 data-initial-data 格式
    return {
        "id": raw.get("id"),
        "name": raw.get("name", ""),
        "slug": raw.get("slug", ""),
        "icon_url": raw.get("icon_url", ""),
        "grouping": raw.get("grouping", ""),
        "mode": raw.get("mode"),
        "instructions": raw.get("instructions", ""),
        "description": raw.get("description", ""),
        "solution": "",
    }


def _save_achievements_disk(achievements: list[dict]) -> None:
    try:
        from pathlib import Path as _Path

        cache_file = _Path(__file__).parent / "osufile" / "medals" / _ACH_CACHE_FILE
        cache_file.write_text(json.dumps(achievements, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_achievements_catalog_disk() -> list[dict]:
    """从磁盘读取缓存的成就目录（无网络请求）。失败返回空列表。"""
    if _achievements_cache.get("achievements"):
        return _achievements_cache["achievements"]
    try:
        from pathlib import Path as _Path

        cache_file = _Path(__file__).parent / "osufile" / "medals" / _ACH_CACHE_FILE
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                normalized = [_normalize_achievement(a) for a in data if isinstance(a, dict)]
                _achievements_cache["achievements"] = normalized
                return normalized
    except Exception:
        pass
    return []


async def fetch_achievements_catalog(force: bool = False) -> list[dict]:
    """获取全量成就目录（缓存 24 小时）。

    首选 osekai inex 接口，失败时回退 osu 用户主页 HTML。
    返回 list[dict]，每项含 id/name/slug/icon_url/grouping/mode/instructions/description。
    """
    import time as _time

    now = _time.time()
    cached = _achievements_cache.get("achievements")
    if cached and not force and now - _achievements_cache.get("fetched_at", 0) < 24 * 3600:
        return cached

    achievements: list[dict] = []

    # ── 首选：osekai inex 全量列表接口 ──
    try:
        req = await safe_async_get(_OSEKAI_MEDALS_URL, headers={"User-Agent": "Mozilla/5.0"})
        if req and req.status_code == 200:
            try:
                payload = req.json()
            except Exception:
                payload = json.loads(req.content.decode("utf-8", "ignore"))
            content = (payload or {}).get("content") or []
            if content and isinstance(content, list):
                for m in content:
                    if not isinstance(m, dict) or not m.get("Medal_ID"):
                        continue
                    try:
                        achievements.append(_normalize_achievement(m))
                    except Exception:
                        continue
    except Exception as e:
        logger.debug(f"osekai 成就目录获取失败: {e}")

    # ── 兜底：osu 用户主页 HTML ──
    if not achievements:
        from html import unescape as _unescape
        import re as _re

        profile_url = "https://osu.ppy.sh/users/78024"  # Cookiezi
        try:
            req = await safe_async_get(
                profile_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64)"}
            )
            if req and req.status_code == 200:
                html = req.text if hasattr(req, "text") else req.content.decode("utf-8", "ignore")
                m = _re.search(r'data-initial-data="([^"]*)"', html)
                if m:
                    payload = json.loads(_unescape(m.group(1)))
                    achievements = [_normalize_achievement(a) for a in (payload.get("achievements") or []) if isinstance(a, dict)]
        except Exception:
            pass

    if achievements:
        _achievements_cache["fetched_at"] = now
        _achievements_cache["achievements"] = achievements
        _save_achievements_disk(achievements)
    return achievements


async def get_user_achievements(uid: int, mode: str = "osu") -> list[dict]:
    """获取用户已获得的成就。

    返回 list[dict]：{achievement_id, achieved_at, ...目录字段(若有)}。
    目录字段尽量从全量目录补齐（name/icon_url/grouping 等）。
    """
    data = await get_user_info_data(uid, mode)
    user_ach = data.user_achievements or []

    catalog = load_achievements_catalog_disk()
    by_id = {a.get("id"): a for a in catalog} if catalog else {}

    result = []
    for item in user_ach:
        if not isinstance(item, dict):
            continue
        ach_id = item.get("achievement_id")
        entry = {"achievement_id": ach_id, "achieved_at": item.get("achieved_at", "")}
        detail = by_id.get(ach_id) if ach_id is not None else None
        if detail:
            entry.update(detail)
        result.append(entry)
    return result


async def get_users(users: list[int]):
    headers = await get_headers()
    req = await safe_async_get(f"{api}/users", headers=headers, params={"ids[]": users})
    return [User(**i) for i in req.json()["users"]] if req else []


async def get_beatmapsets_info(sid) -> BeatmapSets:
    url = f"https://osu.ppy.sh/api/v2/beatmapsets/{sid}"
    res = await make_request(url, await get_headers(), "未查询到该谱面集(Setid)信息")
    return BeatmapSets(**res)


async def get_map_bg(mapid, sid, bg_name) -> BytesIO | None:
    res = await get_first_response(
        [
            f"https://catboy.best/preview/background/{mapid}",
            f"https://osu.direct/api/media/background/{mapid}",
            f"https://dl.sayobot.cn/beatmaps/files/{sid}/{bg_name}",
        ],
        timeout=10.0,
    )
    if res:
        return BytesIO(res.content)
    return None


async def get_seasonal_bg() -> Optional[dict]:
    url = f"{api}/seasonal-backgrounds"
    headers = await get_headers()
    req = await safe_async_get(url, headers=headers)
    return req.json() if req.status_code == 200 else None


# ===========================================================================
# osu! OAuth（用户级令牌，/friend 好友功能依赖，scope 需含 friends.read）
# ===========================================================================

OAUTH_AUTHORIZE_URL = "https://osu.ppy.sh/oauth/authorize"
OAUTH_TOKEN_URL = "https://osu.ppy.sh/oauth/token"
OAUTH_SCOPES = "friends.read identify public"


def get_oauth_client_id() -> int:
    return plugin_config.osu_oauth_client_id or plugin_config.osu_client


def get_oauth_client_secret() -> str:
    return plugin_config.osu_oauth_client_secret or plugin_config.osu_key


def get_oauth_redirect_uri() -> str:
    uri = plugin_config.osu_oauth_redirect_uri
    if not uri:
        raise NetworkError("未配置 osu_oauth_redirect_uri（osu! OAuth 回调地址），请先在 NoneBot 配置中填写")
    # 兼容误填占位符：剔除所有尖括号与首尾空白（如 https://<1.2.3.4>/...）
    uri = uri.replace("<", "").replace(">", "").strip()
    if not uri.startswith(("http://", "https://")):
        raise NetworkError(
            f"osu_oauth_redirect_uri 格式不正确: {uri!r}\n应为 https://<域名或IP>/osubot/oauth/callback 的形式"
        )
    return uri.rstrip("/")


def build_oauth_authorize_url(state: str) -> str:
    """构造 osu! OAuth 授权链接（授权码流程）。"""
    from urllib.parse import urlencode

    params = {
        "client_id": get_oauth_client_id(),
        "redirect_uri": get_oauth_redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _oauth_error_detail(req) -> str:
    """从 osu! OAuth 错误响应中提取 error 字段，便于区分 401(invalid_client) 与 400(invalid_grant)。"""
    if req is None:
        return ""
    try:
        body = req.json()
    except Exception:
        return ""
    detail = body.get("error") if isinstance(body, dict) else None
    return f"（{detail}）" if detail else ""


async def exchange_oauth_code(code: str) -> dict:
    """用授权码换取用户令牌。返回 {access_token, refresh_token, expires_in, ...}"""
    req = await safe_async_post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": get_oauth_client_id(),
            "client_secret": get_oauth_client_secret(),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": get_oauth_redirect_uri(),
        },
    )
    if not req or req.status_code != 200:
        raise NetworkError(
            f"OAuth 授权码兑换失败：HTTP {req.status_code if req else 'None'}{_oauth_error_detail(req)}"
        )
    return req.json()


async def refresh_oauth_token(refresh_token: str) -> dict:
    """用 refresh_token 刷新用户令牌。返回 {access_token, refresh_token, expires_in, ...}"""
    req = await safe_async_post(
        OAUTH_TOKEN_URL,
        data={
            "client_id": get_oauth_client_id(),
            "client_secret": get_oauth_client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    if not req or req.status_code != 200:
        raise NetworkError(
            f"OAuth 令牌刷新失败：HTTP {req.status_code if req else 'None'}{_oauth_error_detail(req)}"
        )
    return req.json()


def warn_oauth_config() -> None:
    """启动自检：已配置回调地址但缺 client_id/secret 时提前警告，避免用户对着 401 排查。"""
    try:
        get_oauth_redirect_uri()
    except NetworkError:
        return  # 未配置回调地址，/friend 本身会提示，不重复警告
    if not get_oauth_client_id():
        logger.warning(
            "osu! OAuth 未配置 client_id：请设置 osu_oauth_client_id 或 osu_client"
            "（env: OSU_OAUTH_CLIENT_ID / OSU_CLIENT）"
        )
    elif not get_oauth_client_secret():
        logger.warning(
            "osu! OAuth 未配置 client_secret：请设置 osu_oauth_client_secret 或 osu_key"
            "（env: OSU_OAUTH_CLIENT_SECRET / OSU_KEY；注意 OSU_CLIENT_SECRET 这个变量名不会被读取）"
        )


def _oauth_headers(access_token: str, version: str = "20220705") -> dict:
    return {"Authorization": f"Bearer {access_token}", "x-api-version": version}


async def get_me_with_token(access_token: str) -> dict:
    """GET /me：获取令牌所属用户的 id / username。"""
    req = await safe_async_get(f"{api}/me", headers=_oauth_headers(access_token))
    if not req or req.status_code != 200:
        raise NetworkError(f"OAuth 令牌无效：HTTP {req.status_code if req else 'None'}")
    return req.json()


async def get_user_friends(access_token: str) -> list:
    """GET /friends：获取令牌所属用户的好友列表（含 mutual 标记）。

    osu! 从 20241022 起 /friends 返回 UserRelation 结构
    {target_id, relation_type, mutual, target}；旧版本（x-api-version < 20241022）
    返回扁平 UserCompact 列表（无 mutual）。这里显式请求新版本以获得 mutual 信息，
    同时兼容旧格式解析（防御性）。
    """
    from .schema.friend import Friend
    from .schema.user import UserCompact

    req = await safe_async_get(
        f"{api}/friends",
        headers=_oauth_headers(access_token, "20241022"),
    )
    if not req or req.status_code != 200:
        raise NetworkError(f"获取好友列表失败：HTTP {req.status_code if req else 'None'}")
    data = req.json()
    if not isinstance(data, list):
        return []
    friends = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            if "target_id" in item:
                # 新格式：UserRelation（x-api-version >= 20241022）
                friend = Friend(**item)
            else:
                # 旧格式：扁平 UserCompact 列表（无 mutual，标记为 False）
                friend = Friend(
                    target_id=int(item["id"]),
                    relation_type="friend",
                    mutual=False,
                    target=UserCompact(**item),
                )
        except Exception:
            continue  # 个别条目字段异常时跳过，不拖垮整个列表
        if friend.target is not None:
            friends.append(friend)
    return friends


def _recommend_target(target: str | None) -> str:
    value = (target or "mixed").strip().lower()
    aliases = {
        "farm": "farm",
        "pp": "farm",
        "mixed": "mixed",
        "mix": "mixed",
        "all": "mixed",
        "overall": "mixed",
        "综合": "mixed",
        "总和": "mixed",
        "全部": "mixed",
        "吃分": "farm",
        "涨pp": "farm",
        "balanced": "balanced",
        "balance": "balanced",
        "normal": "balanced",
        "推荐": "mixed",
        "普通": "balanced",
        "peak": "peak",
        "hard": "peak",
        "harder": "peak",
        "difficult": "peak",
        "challenge": "peak",
        "难一点": "peak",
        "更难": "peak",
        "高难": "peak",
        "冲分": "peak",
        "style": "style",
        "practice": "style",
        "train": "style",
        "training": "style",
        "风格": "style",
        "练习": "style",
        "练图": "style",
        "练习推荐": "style",
        "难": "peak",
    }
    return aliases.get(value, "mixed")


async def _get_recommend_beatmapset_ids(items: list[dict]) -> dict[int, int]:
    missing_map_ids = list(
        dict.fromkeys(
            int(item["beatmap_id"]) for item in items if item.get("beatmap_id") and not item.get("beatmapset_id")
        )
    )
    if not missing_map_ids:
        return {}

    async def fetch_beatmapset_id(map_id: int) -> tuple[int, int | None]:
        client = await network_manager.get_client()
        try:
            res = await client.get(f"https://osu.ppy.sh/b/{map_id}", timeout=15)
            url = str(res.url)
            if "/beatmapsets/" in url:
                beatmapset_id = url.split("/beatmapsets/", 1)[1].split("#", 1)[0].split("/", 1)[0]
                return map_id, int(beatmapset_id)
        except Exception as e:
            logger.debug(f"failed to fetch beatmapset id by redirect for recommended map {map_id}: {e}")

        try:
            data = await osu_api("map", map_id=map_id)
            beatmapset_id = data.get("beatmapset_id") or (data.get("beatmapset") or {}).get("id")
            return map_id, int(beatmapset_id) if beatmapset_id else None
        except Exception as e:
            logger.debug(f"failed to fetch beatmapset id for recommended map {map_id}: {e}")
            return map_id, None

    results = await asyncio.gather(*(fetch_beatmapset_id(map_id) for map_id in missing_map_ids))
    return {map_id: beatmapset_id for map_id, beatmapset_id in results if beatmapset_id}


async def _request_recommend(url: str, params: dict) -> Response:
    client = await network_manager.get_client()
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(
                url,
                params=params,
                timeout=plugin_config.osu_recommend_timeout,
            )
        except HTTPError as e:
            if attempt == max_attempts:
                detail = str(e) or e.__class__.__name__
                raise NetworkError(f"推荐服务请求失败: {detail}") from e
            logger.warning(f"recommend request failed ({attempt}/{max_attempts}): {e}")
        else:
            if response.status_code < 500:
                return response
            if attempt == max_attempts:
                raise NetworkError("推荐服务繁忙，请稍后再试")
            logger.warning(
                f"recommend service returned HTTP {response.status_code} ({attempt}/{max_attempts}), retrying"
            )

        await asyncio.sleep(float(attempt))

    raise NetworkError("推荐服务繁忙，请稍后再试")


async def get_recommend(uid, mode, target: str | None = "mixed"):
    mode_map = {"0": "osu", "1": "taiko", "2": "fruits", "3": "mania"}
    mode_str = mode_map.get(str(mode), "osu")
    target_str = _recommend_target(target)
    base_url = plugin_config.osu_recommend_api.rstrip("/")
    res = await _request_recommend(
        f"{base_url}/recommend/{mode_str}/{uid}",
        params={
            "target": target_str,
            "candidate_limit": plugin_config.osu_recommend_candidate_limit,
            "result_limit": plugin_config.osu_recommend_result_limit,
        },
    )
    if res.status_code >= 400:
        raise NetworkError(f"推荐服务返回 {res.status_code}: {res.text[:120]}")

    data = res.json()
    items = data.get("items", [])
    section_items = [item for section in data.get("sections", []) or [] for item in section.get("items", []) or []]
    beatmapset_ids = await _get_recommend_beatmapset_ids(items + section_items)

    def convert_item(item: dict) -> dict:
        map_id = item.get("beatmap_id")
        artist = item.get("artist") or ""
        title = item.get("title") or f"Map {map_id}"
        version = item.get("version") or "Unknown"
        display_title = f"{artist} - {title} [{version}]" if artist else f"{title} [{version}]"
        return {
            "map_id": map_id,
            "mod": item.get("mod_int", 0),
            "mod_str": item.get("mods") or "NM",
            "stars": item.get("stars", 0.0),
            "pred_pp": item.get("pred_pp", 0.0),
            "pred_acc": item.get("pred_acc", 0.0),
            "final_score": item.get("ranking_score", 0.0),
            "title": display_title,
            "beatmapset_id": item.get("beatmapset_id") or beatmapset_ids.get(map_id) or 0,
            "url": item.get("url"),
            "evidence_count": item.get("evidence_count"),
            "target": item.get("target"),
        }

    recommendations = [convert_item(item) for item in items]
    sections = [
        {
            "key": section.get("key", ""),
            "title": section.get("title", ""),
            "items": [convert_item(item) for item in section.get("items", []) or []],
        }
        for section in data.get("sections", []) or []
    ]
    return RecommendData(
        player_id=data.get("player_id", uid),
        mode=data.get("mode", mode_str),
        target=data.get("target", target_str),
        recommendations=recommendations,
        sections=sections,
    )

async def get_preview_audio(sid: int) -> bytes | None:
    """
    获取谱面集(sid)的官方 30s 试听音频，返回 mp3 字节流。
    接口文档(SayoBot 静态资源 - 试听音频):
        https://cdn.sayobot.cn:25225/preview/{sid}.mp3
        https://a.sayobot.cn/preview/{sid}.mp3
    获取失败或内容不合法时返回 None。
    """
    res = await get_first_response(
        [
            f"https://cdn.sayobot.cn:25225/preview/{sid}.mp3",
            f"https://a.sayobot.cn/preview/{sid}.mp3",
        ],
        timeout=15.0,
    )
    # 校验：必须 200 且内容足够大（排除错误页/空文件）
    if res and res.status_code == 200 and len(res.content) > 1024:
        return res.content
    return None
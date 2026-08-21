import re

from nonebot import on_command
from nonebot.params import CommandArg, T_State
from nonebot.internal.adapter import Event, Message
from nonebot_plugin_alconna import UniMessage

from ..utils import NGM
from .utils import split_msg
from ..draw import draw_score
from ..draw.bp import draw_pfm
from ..draw.utils import filter_scores_with_regex
from ..api import get_user_scores
from ..exceptions import NetworkError
from ..draw.score import cal_score_info
from ..mods import get_mods_list
from .map_context import remember_map

recent = on_command("recent", priority=11, block=True, aliases={"re", "RE", "Re", "rE"})
pr = on_command("pr", priority=11, block=True, aliases={"PR", "Pr", "pR"})
recent_list = on_command("rl", priority=11, block=True, aliases={"relist", "recentlist"})
pass_list = on_command("pl", priority=11, block=True, aliases={"prlist", "passlist"})


def _extract_day(arg: Message) -> int:
    """[修复] 从原始命令参数中提取独立序号。

    - 可匹配: "2"、"2:o"、"2:osu"、"2:m" 等（数字后可跟 :模式）
    - 不匹配: 范围 "1-30"、Mod "+HDHR"、玩家名、"&sb" 等
    未找到有效序号时返回 0。
    """
    for token in arg.extract_plain_text().split():
        matched = re.fullmatch(r"(\d+)(?::[a-zA-Z0-9]+)?", token)
        if matched:
            return int(matched.group(1))
    return 0


async def _draw_recent_list(state: T_State, include_fails: bool, project: str):
    mode = NGM[state["mode"]]
    low, high = map(int, state["range"].split("-"))
    if not 0 < low <= high <= 200:
        await UniMessage.text("仅支持查询最近1-200条成绩").finish(reply_to=True)
    needs_filter = bool(state["mods"] or state["query"])
    try:
        if needs_filter:
            # 带 mods/筛选条件时放宽拉取窗口，本地过滤后再按范围切片
            scores = await get_user_scores(
                state["user"],
                mode,
                "recent",
                state["source"],
                not state["is_lazer"],
                include_fails,
                0,
                200,
            )
        else:
            scores = await get_user_scores(
                state["user"],
                mode,
                "recent",
                state["source"],
                not state["is_lazer"],
                include_fails,
                low - 1,
                high if state["source"] == "ppysb" else high - low + 1,
            )
    except NetworkError as e:
        mods = f" mod:{state['mods']}" if state["mods"] else ""
        await UniMessage.text(
            f"在查找用户：{state['username']} {mode}模式{mods} 最近{state['range']}成绩时 {str(e)}"
        ).finish(reply_to=True)
    try:
        if state["mods"]:
            mods_ls = get_mods_list(scores, state["mods"])
            if len(mods_ls) < low:
                raise NetworkError(f"未找到开启 {'|'.join(state['mods'])} Mods的成绩")
            scores = [scores[i] for i in mods_ls]
        if state["query"]:
            scores = filter_scores_with_regex(scores, state["query"])
        # 无过滤时窗口已是对应区间，直接使用；有过滤时按范围切片
        selected = scores[low - 1 : high] if needs_filter else scores
        if not selected:
            raise NetworkError("未查询到游玩记录")
    except NetworkError as e:
        mods = f" mod:{state['mods']}" if state["mods"] else ""
        await UniMessage.text(
            f"在查找用户：{state['username']} {mode}模式{mods} 最近{state['range']}成绩时 {str(e)}"
        ).finish(reply_to=True)
    for score in selected:
        cal_score_info(state["is_lazer"], score, state["source"])
    return await draw_pfm(project, state["user"], selected, selected, mode, source=state["source"])


@recent_list.handle(parameterless=[split_msg()])
async def _recent_list(state: T_State):
    if "error" in state:
        await UniMessage.text(state["error"]).finish(reply_to=True)
    if not state["range"]:
        state["range"] = "1-30"
    pic = await _draw_recent_list(state, include_fails=True, project="relist")
    await UniMessage.image(raw=pic).finish(reply_to=True)


@pass_list.handle(parameterless=[split_msg()])
async def _pass_list(state: T_State):
    if "error" in state:
        await UniMessage.text(state["error"]).finish(reply_to=True)
    if not state["range"]:
        state["range"] = "1-30"
    pic = await _draw_recent_list(state, include_fails=False, project="prlist")
    await UniMessage.image(raw=pic).finish(reply_to=True)


@recent.handle(parameterless=[split_msg()])
async def _recent(event: Event, state: T_State, arg: Message = CommandArg()):
    if "error" in state:
        await UniMessage.text(state["error"]).finish(reply_to=True)
    mode = NGM[state["mode"]]
    if state["range"]:
        pic = await _draw_recent_list(state, include_fails=True, project="relist")
        await UniMessage.image(raw=pic).finish(reply_to=True)
    # ===== [修复] 手动从原始参数提取序号，修复 /re 2 不生效的问题 =====
    day = _extract_day(arg)
    if day:
        state["day"] = day
    # ================================================================
    if state["day"] == 0:
        state["day"] = 1
    try:
        data, map_id, set_id = await draw_score(
            "recent",
            state["user"],
            state["is_lazer"],
            mode,
            state["mods"],
            state["query"],
            state["source"],
            state["day"],
            return_context=True,
        )
    except NetworkError as e:
        mods = f" mod:{state['mods']}" if state["mods"] else ""
        await UniMessage.text(
            f"在查找用户：{state['username']} {NGM[state['mode']]}模式{mods} 最近第{state['day']}个成绩时 {str(e)}"
        ).finish(reply_to=True)
    remember_map(event, map_id, set_id)
    await UniMessage.image(raw=data).finish(reply_to=True)


@pr.handle(parameterless=[split_msg()])
async def _pr(event: Event, state: T_State, arg: Message = CommandArg()):
    if "error" in state:
        await UniMessage.text(state["error"]).finish(reply_to=True)
    mode = NGM[state["mode"]]
    if state["range"]:
        pic = await _draw_recent_list(state, include_fails=False, project="prlist")
        await UniMessage.image(raw=pic).finish(reply_to=True)
    # ===== [修复] 手动从原始参数提取序号，修复 /pr 2 不生效的问题 =====
    day = _extract_day(arg)
    if day:
        state["day"] = day
    # ================================================================
    if state["day"] == 0:
        state["day"] = 1
    try:
        data, map_id, set_id = await draw_score(
            "pr",
            state["user"],
            state["is_lazer"],
            mode,
            state["mods"],
            state["query"],
            state["source"],
            state["day"],
            return_context=True,
        )
    except NetworkError as e:
        mods = f" mod:{state['mods']}" if state["mods"] else ""
        await UniMessage.text(
            f"在查找用户：{state['username']} {NGM[state['mode']]}模式{mods} 最近第{state['day']}个成绩时 {str(e)}"
        ).finish(reply_to=True)
    remember_map(event, map_id, set_id)
    await UniMessage.image(raw=data).finish(reply_to=True)
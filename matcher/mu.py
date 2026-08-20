from io import BytesIO

from nonebot import on_command
from nonebot.typing import T_State
from nonebot_plugin_alconna import UniMessage

from .utils import split_msg
from ..utils import NGM
from ..api import get_user_info_data
from ..exceptions import NetworkError
from ..draw.utils import open_user_icon

mu = on_command("mu", priority=11, block=True)


@mu.handle(parameterless=[split_msg()])
async def _mu(state: T_State):
    if "error" in state:
        await UniMessage.text(state["error"]).finish(reply_to=True)
    try:
        info = await get_user_info_data(state["user"], NGM[state["mode"]], state["source"])
        icon = await open_user_icon(info, state["source"])
    except NetworkError as e:
        await UniMessage.text(f"查询玩家信息失败：{str(e)}").finish(reply_to=True)
    except Exception as e:
        await UniMessage.text(f"获取头像失败：{str(e)}").finish(reply_to=True)
        return
    byt = BytesIO()
    icon.convert("RGBA").save(byt, "png")
    msg = (
        UniMessage.text(
            f"{info.username}（uid: {info.id}）\nhttps://osu.ppy.sh/u/{info.id}"
        )
        + UniMessage.image(raw=byt)
    )
    await msg.finish(reply_to=True)

import asyncio

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot.internal.adapter import Event, Message
from nonebot_plugin_orm import get_session
from sqlalchemy import select, delete

from ..info import bind_user_info
from ..exceptions import NetworkError
from ..database import UserData

bind = on_command("bind", priority=11, block=True)
unbind = on_command("unbind", priority=11, block=True)
lock = asyncio.Lock()


@bind.handle()
async def _bind(event: Event, name: Message = CommandArg()):
    name = name.extract_plain_text().strip()
    if not name:
        await UniMessage.text("请在指令后输入 osu! 用户名、UID 或个人主页链接").finish(reply_to=True)
    async with lock:
        async with get_session() as session:
            user = await session.scalar(select(UserData).where(UserData.user_id == event.get_user_id()))
        if user:
            await UniMessage.text(f"您已绑定{user.osu_name}，如需要解绑请输入/unbind").finish(reply_to=True)
        try:
            msg = await bind_user_info("bind", name, event.get_user_id())
        except NetworkError:
            await UniMessage.text(f"绑定失败，找不到叫 {name} 的人哦").finish(reply_to=True)
        # 绑定成功后同步附带 OAuth 授权链接（用于 /f 好友查询）；
        # 无法生成链接（未配置回调地址）时退化为 /frbind 提示。
        from .friend import build_friend_authorize_link

        link = build_friend_authorize_link(event.get_user_id())
        if link:
            msg += f"\n如需查询好友/互关，请点击以下链接完成 osu! OAuth 授权：\n{link}"
        else:
            msg += "\n如需查询好友/互关，请发送 /frbind 完成 osu! OAuth 授权（每个账号各自授权）"
    await UniMessage.text(msg).finish(reply_to=True)


@unbind.handle()
async def _unbind(event: Event):
    async with get_session() as session:
        user = await session.scalar(select(UserData).where(UserData.user_id == event.get_user_id()))
        if user:
            await session.execute(delete(UserData).where(UserData.user_id == event.get_user_id()))
            await session.commit()
            await UniMessage.text("解绑成功！").send(reply_to=True)
        else:
            await UniMessage.text("尚未绑定，无需解绑").send(reply_to=True)

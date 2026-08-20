from pathlib import Path

from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.internal.adapter import Message
from nonebot_plugin_alconna import UniMessage

from ..help_data import HELP_TOPICS, TOPIC_ALIASES, TOPIC_LABELS, get_command_help

osu_help = on_command("osuhelp", aliases={"oh", "OSUBot", "osubot", "osu帮助"}, priority=11, block=True)

# 概览页已移除：/osuhelp 与 /osuhelp detail 均直接展示详情页
with open(Path(__file__).parent.parent / "osufile" / "detail.png", "rb") as f:
    detail_img = f.read()


@osu_help.handle()
async def _help(arg: Message = CommandArg()):
    arg = arg.extract_plain_text().strip().lower().lstrip("/")
    if arg in {"", "overview", "概览", "detail", "详细", "详情"}:
        await UniMessage.image(raw=detail_img).finish(reply_to=True)
    topic = TOPIC_ALIASES.get(arg, arg)
    if topic in HELP_TOPICS:
        await UniMessage.text(get_command_help(topic)).finish(reply_to=True)
    await UniMessage.text(
        f"没有找到该帮助主题。可用主题：{TOPIC_LABELS}\n发送 /oh 查看快速指南，或发送 /osuhelp 查看完整命令"
    ).finish(reply_to=True)

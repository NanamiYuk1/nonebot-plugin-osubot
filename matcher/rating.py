from nonebot import on_command
from nonebot.params import CommandArg
from nonebot.internal.adapter import Message
from nonebot.log import logger
from nonebot_plugin_alconna import UniMessage

from ..draw.rating import draw_rating

rating = on_command("rating", aliases={"rt"}, priority=11, block=True)


@rating.handle()
async def _(arg: Message = CommandArg()):
    match_id = arg.extract_plain_text().strip()

    # ===== 参数校验 =====
    if not match_id:
        await rating.finish("请输入比赛/多人房 ID，例如：/rt 3985712")

    # ===== 异常捕获，防止 Matcher 崩溃 =====
    try:
        images = await draw_rating(match_id)
    except ValueError as e:
        # 数据层面的问题（无效ID、无对局、无有效成绩等）→ 友好提示用户
        await rating.finish(str(e))
    except Exception as e:
        # 网络/API/未知错误 → 记录完整日志 + 友好提示
        logger.opt(exception=e).error(f"绘制 rating 失败: match_id={match_id}")
        await rating.finish(f"查询失败，请稍后再试或联系管理员\n错误信息: {e}")

    if not images:
        await rating.finish("该多人房没有可用于评分的对局")

    # 单图：直接回复发送；多图（如房间中途切换模式）：首图回复，其余顺序发送
    for index, img in enumerate(images):
        if index == len(images) - 1:
            await UniMessage.image(raw=img).finish(reply_to=(index == 0))
        else:
            await UniMessage.image(raw=img).send(reply_to=(index == 0))

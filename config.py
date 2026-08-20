from pathlib import Path
from typing import Union, Optional

from pydantic import BaseModel


class Config(BaseModel):
    osu_client: Optional[int] = None
    osu_key: Optional[str] = None
    osu_proxy: Optional[Union[str, dict]] = None
    osutrack_enabled: bool = True
    osutrack_default_days: int = 365
    osu_recommend_api: str = "https://mayumi.xyz"
    osu_recommend_timeout: float = 240.0
    osu_recommend_candidate_limit: int = 1000
    osu_recommend_result_limit: int = 10
    osu_preview_taiko_skin_path: Optional[Path] = None
    osu_preview_ffmpeg_path: Optional[Path] = None
    osu_preview_full_scale: float = 0.75
    osu_preview_full_frame_interval: int = 30
    osu_preview_taiko_full_scale: float = 0.5
    osu_preview_taiko_full_frame_interval: int = 30
    osu_preview_std_catch_full_scale: float = 0.5
    osu_preview_std_catch_full_frame_interval: int = 30
    
    # ---- osu-beatmap-preview (Rust 二进制) ----
    osu_preview_bin_path: Optional[Path] = None   # 渲染二进制绝对路径；未配置时自动回退旧浏览器链路
    osu_preview_use_core: bool = True       # 是否启用二进制渲染
    osu_preview_fallback: bool = True       # core 失败时是否回退旧链路
    osu_preview_timeout: float = 120.0      # gif/png 单次超时(秒)
    osu_preview_video_timeout: float = 300.0  # 完整 mp4 超时(秒)，全曲可能较慢

    # ---- osu! OAuth（/friend 好友功能需要用户级令牌，scope 需含 friends.read）----
    # 回调地址：需在 osu! 的 OAuth 应用设置中登记，且必须为公网可访问的 HTTPS
    # 地址（osu! 仅允许 https 或 http://localhost）。回调路径固定为 /osubot/oauth/callback。
    osu_oauth_redirect_uri: Optional[str] = None
    # 默认使用 osu_client / osu_key；如需单独的 OAuth 应用可单独指定
    osu_oauth_client_id: Optional[int] = None
    osu_oauth_client_secret: Optional[str] = None
    # 好友列表默认展示条数 / 单页上限
    osu_friend_page_size: int = 20
    osu_friend_max_page: int = 100
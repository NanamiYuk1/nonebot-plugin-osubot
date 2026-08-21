import datetime
from typing import Literal, Optional, Union, Any, List

from pydantic.fields import Field
from pydantic import field_validator, model_validator

from .basemodel import Base
from .user import UserCompact
from .beatmap import Beatmap, Beatmapset


class Statistics(Base):
    count_50: Optional[int] = None
    count_100: Optional[int] = None
    count_300: Optional[int] = None
    count_geki: Optional[int] = None
    count_katu: Optional[int] = None
    count_miss: Optional[int] = None


# 定义 Mod 类 (原本在下方，为了 Score 类引用方便，逻辑上保持原位或提前均可，这里保持原结构，但在 Score 中使用 Union)
# 注意：原代码中 Mod 类定义在 NewScore 之前，Score 类在 Mod 类之前。
# 为了让 Score 类能引用 Mod 类进行类型提示，我们需要调整顺序或者使用字符串前向引用 "Mod"。
# 这里采用字符串前向引用 "Mod" 以避免大幅调整代码结构，或者直接将 Mod 类定义移到 Score 之前。
# 为了代码清晰，我将 Mod 类的定义逻辑保留在原位，但在 Score 中使用 Union[str, dict, "Mod"]

class Score(Base):
    id: Optional[int] = None
    best_id: Optional[int] = None
    user_id: Optional[int] = None  # 【本次修改】改为可选，多人房残缺数据兜底
    accuracy: Optional[float] = 0  # 【本次修改】改为可选 + 默认 0

    # 【修改点 1】mods 兼容 str, dict, 和 Mod 对象
    # 使用 field_validator 在下面统一处理
    mods: List[Union[str, dict, "Mod"]] = Field(default_factory=list)

    # 【本次修改】数值字段默认值改为 0，避免 None 参与比较/求和时报 TypeError
    score: Optional[int] = 0
    max_combo: Optional[int] = 0
    perfect: Optional[int] = 0

    statistics: Optional[Statistics] = None  # 建议也改为可选，以防万一
    passed: Optional[bool] = False  # 给个默认值，改为 Optional 防 None

    pp: Optional[float] = None
    rank: Optional[str] = None  # 建议改为可选

    # 原为 str，改为 Optional[str]
    created_at: Optional[str] = None

    # 原为 Literal[...]，改为 Optional[Literal[...]]
    mode: Optional[Literal["fruits", "mania", "osu", "taiko"]] = None

    # 原为 int，改为 Optional[int]
    mode_int: Optional[int] = 0  # 【本次修改】默认 0

    beatmap: Optional[Beatmap] = None
    beatmapset: Optional[Beatmapset] = None
    match: Optional[dict] = None

    # ============================================================
    # 【本次新增·核心修复】兼容 osu! v2 多人房接口的 total_score 字段
    # ------------------------------------------------------------
    # 问题背景：
    #   osu! v2 的 /matches/{id} 接口返回的 score 对象里，分数字段叫
    #   `total_score`（以及 `legacy_total_score`），而不是 `score`。
    #   导致 Pydantic 解析后 self.score 永远是默认值 0，
    #   下游 `(score.score or 0) > 0` 过滤把所有成绩全部筛掉，
    #   最终抛 "该多人房没有有效成绩"。
    # 解决：
    #   在模型解析前（mode='before'），如果原始数据里没有 `score`
    #   但有 `total_score` / `legacy_total_score`，就把它映射过来。
    #   优先级：score > total_score > legacy_total_score
    # ============================================================
    @model_validator(mode="before")
    @classmethod
    def _compat_total_score(cls, data):
        if isinstance(data, dict):
            if "score" not in data or data.get("score") is None:
                if "total_score" in data and data.get("total_score") is not None:
                    data["score"] = data["total_score"]
                elif "legacy_total_score" in data and data.get("legacy_total_score") is not None:
                    data["score"] = data["legacy_total_score"]
        return data

    # 【新增验证器】处理 mods 字段，统一转换为字符串列表或保持对象列表
    # 根据报错，API 返回的是 {'acronym': 'HR'}，而原代码期望 list[str]
    # 如果下游代码依赖 mods 是字符串列表，这里转为字符串；如果依赖 Mod 对象，则转为 Mod。
    # 观察 NewScore 用的是 list[Mod]，而 Score 用的是 list[str]。
    # 为了兼容性，我们将 dict 转为 str (acronym)，或者如果已经是 str 则保留。
    @field_validator('mods', mode='before')
    @classmethod
    def parse_mods(cls, v):
        if not v:
            return []

        result = []
        for m in v:
            if isinstance(m, dict):
                # 如果是字典，提取 acronym
                result.append(m.get('acronym', ''))
            elif isinstance(m, str):
                result.append(m)
            else:
                # 如果是 Mod 对象或其他，尝试获取 acronym
                result.append(getattr(m, 'acronym', str(m)))
        return result


class BeatmapUserScore(Base):
    position: int
    score: Score


class NewStatistics(Base):
    great: Optional[int] = Field(default=0)
    slider_tail_hit: Optional[int] = Field(default=0)
    large_tick_hit: Optional[int] = Field(default=0)
    small_tick_hit: Optional[int] = Field(default=0)
    small_tick_miss: Optional[int] = Field(default=0)
    miss: Optional[int] = Field(default=0)
    ok: Optional[int] = Field(default=0)
    meh: Optional[int] = Field(default=0)
    good: Optional[int] = Field(default=0)
    perfect: Optional[int] = Field(default=0)


class Mod(Base):
    acronym: str
    settings: Optional[dict] = None


class NewScore(Base):
    accuracy: float
    beatmap_id: int
    best_id: Optional[int] = None
    build_id: Optional[int] = None
    ended_at: str
    has_replay: bool
    id: int
    is_perfect_combo: bool
    legacy_perfect: bool
    legacy_score_id: Optional[int] = None
    legacy_total_score: int
    max_combo: int
    maximum_statistics: Optional[Statistics] = None
    mods: list[Mod]
    passed: bool
    playlist_item_id: Optional[int] = None
    pp: Optional[float] = None
    preserve: bool
    rank: str
    ranked: bool
    room_id: Optional[int] = None
    ruleset_id: int
    started_at: Optional[str] = None
    statistics: Optional[NewStatistics] = None
    total_score: int
    type: str
    user_id: int
    beatmap: Optional[Beatmap] = None
    beatmapset: Optional[Beatmapset] = None
    # current_user_attributes: Optional[int]
    position: Optional[int] = None
    rank_country: Optional[int] = None
    rank_global: Optional[int] = None
    user: Optional[UserCompact] = None


class UnifiedBeatmap(Base):
    id: int
    set_id: int
    artist: str
    title: str
    version: str
    creator: str
    total_length: int
    mode: int
    bpm: float
    cs: float
    od: float
    ar: float
    hp: float
    stars: float
    checksum: Optional[str] = None
    user_id: Optional[int] = None
    convert: Optional[bool] = False


class UnifiedScore(Base):
    mods: list[Mod]
    ruleset_id: int
    rank: str
    accuracy: float
    total_score: int
    legacy_total_score: Optional[int] = None
    ended_at: datetime.datetime
    max_combo: int
    statistics: NewStatistics
    beatmap: Optional[UnifiedBeatmap] = None
    passed: bool
    pp: Optional[float] = None
    beatmapset: Optional[Beatmapset] = None
    score_version: Optional[Literal["stable", "lazer"]] = None


def get_score_version(legacy_score_id: Optional[int]) -> Literal["stable", "lazer"]:
    """Return the official client generation that submitted a score."""
    return "stable" if legacy_score_id is not None else "lazer"
"""
上下线段完整判断与交替衔接——Python 风格伪代码
================================================

用途
----
给定：
    1. 已经正确划分、按时间排列的全部笔；
    2. 当前线段的已确认起点和方向。

返回：
    1. 当前线段的方向和结构端点；
    2. 最早确认时点；
    3. 使用了哪一种规则；
    4. 可供人工逐步复核的证据与日志；
    5. 从一个起点开始交替得到的 UP/DOWN 连续线段链。

对外接口
--------
1. calculate_tdx_segment_events：完整执行“解码笔—寻找初始段—交替划段—编码事件”；
2. decode_pens_from_tdx_series：只在适配器测试中单独核查通达信笔输入；
3. find_earliest_segment_start：只在结构测试中单独核查最早初始段；
4. judge_one_segment_offline：判断一条已知起点和方向的线段；
5. judge_alternating_segments_offline：从同一输入继续生成共享端点、方向交替的线段链。

调用者不需要直接调用规范向上引擎、候选构造器、镜像函数或特殊分支函数；
这些都是完整入口和结构测试入口内部的可核查实现步骤。

迁移边界
--------
生产 C++ 必须移植状态、端点、确认时点、分支、接续候选和线段证书。
proof、trace、label 与 basis_ids 只用于参考实现审计，不进入 DLL ABI，也不
要求在发布构建中保存。通达信外部始终只看到三条输入数组和一条位掩码输出。

重要限定
--------
1. 这是“允许未来数据”的离线判定伪代码，不是实时交易信号。
2. 线段规则输入从“笔”开始；通达信三序列适配器只负责恢复已确认的笔端点。
3. 向上线段是唯一规范实现；向下线段通过“价格取负、方向互换”调用同一引擎。
4. 代码保持 Python 语法有效，是 C++17 内核的行为参考，不直接承担生产运行。
5. endpoint（结构端点）和 confirmed_at（确认时点）必须分别保存。
6. 每个已确认线段的 endpoint 必须原样成为下一反向线段的 start。
7. 走势 A 确认本段后，下一反向线段仍须重新执行完整流程；其候选 T
   不得早于最后触发本段确认的原始笔终点。
8. 普通第二种情况以原始创新高笔 U 为分支锚点；若该分支退出，记录
   固定的 U.end 为新候选 T'，即使退出事件来自更晚笔也不改用更晚终点。
   时间上位于 T/T' 之间的候选仍须逐个检查；实际向下线段严格镜像。
9. 每条已确认向上线段的结构端点必须严格高于起点；向下线段必须严格
   低于起点。价格方向不合格的假设点不具备候选资格，不能进入任何终局分支。

知识依据编号见同目录 README.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum, IntFlag
from math import isfinite
from typing import Any, Optional, Sequence


# 对外面只保留完整通达信入口、必要的领域对象，以及三个可独立测试的
# 结构入口。候选构造、包含处理、走势 A 和价格镜像都留在模块内部。
__all__ = [
    "Direction",
    "SegmentStatus",
    "EndCase",
    "PenEndpointKind",
    "PenInputErrorCode",
    "PenInputError",
    "PenDecodeResult",
    "SegmentEventBit",
    "Point",
    "PriceBar",
    "Pen",
    "InitialSegmentStart",
    "SegmentResult",
    "AlternatingSegmentsResult",
    "TdxSegmentCalculationResult",
    "decode_pens_from_tdx_series",
    "find_earliest_segment_start",
    "judge_one_segment_offline",
    "judge_alternating_segments_offline",
    "calculate_tdx_segment_events",
]


# ============================================================================
# 0. 常量与基础数据
# ============================================================================


EPS = 1e-9


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class CandidateStatus(str, Enum):
    """仅供单个候选内部流转；不得作为整条线段或 DLL 的状态。"""

    CONFIRMED = "CONFIRMED"   # 已找到端点，而且已有充分未来结构确认
    REJECTED = "REJECTED"     # 当前候选极值被否定，原方向线段继续延伸
    PENDING = "PENDING"       # 数据走完仍不足以确认或否定
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SegmentStatus(str, Enum):
    """单条线段的三种合法出口，与候选的 REJECTED 明确分层。"""

    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"
    INVALID_INPUT = "INVALID_INPUT"


class EndCase(str, Enum):
    CASE_1_NO_GAP = "第一种情况：A/B 无特征序列缺口"
    CASE_2_WITH_GAP = "第二种情况：A/B 有缺口，第二特征序列分型确认"
    TREND_A = "特殊情况：第一反向笔 + 走势A + 反应走势"
    LONG_STALL_RECOVERY = "工程托底：超长停滞恢复"


class PenEndpointKind(IntEnum):
    """通达信笔端点标记；它描述顶/底，不直接描述一根笔的方向。"""

    BOTTOM = -1
    NONE = 0
    TOP = 1


class PenInputErrorCode(IntEnum):
    """与 C++ 适配器保持稳定编号；最后一项只用于 Python 等长校验。"""

    NONE = 0
    NON_POSITIVE_LENGTH = 1
    NULL_INPUT_ARRAY = 2
    NON_FINITE_PRICE = 3
    HIGH_BELOW_LOW = 4
    INVALID_ENDPOINT_MARKER = 5
    INVALID_PEN_PRICE_DIRECTION = 7
    LENGTH_MISMATCH = 8


class SegmentEventBit(IntFlag):
    """函数编号 1 的逐 K 线输出位；取值必须与 C++ 头文件一致。"""

    UP_ENDPOINT = 1 << 0
    DOWN_ENDPOINT = 1 << 1
    UP_CONFIRMED = 1 << 2
    DOWN_CONFIRMED = 1 << 3
    UP_CHAIN_START = 1 << 4
    DOWN_CHAIN_START = 1 << 5
    INPUT_ERROR = 1 << 6


@dataclass(frozen=True)
class Point:
    """一个有先后顺序的价格点。order 可以是 K 线序号、时间戳序号等。"""

    order: float
    price: float
    label: str = ""


def has_strict_segment_price_direction(
    direction: Direction,
    start: Point,
    endpoint: Point,
) -> bool:
    """线段语义方向是否与起止价格的严格净方向一致。

    EPS 内相等不构成上涨或下跌。这个判据既用于候选入口，也用于已确认
    结果和交替链的防御性核查；它不是 Pen 流输入错误判据。
    """

    if direction == Direction.UP:
        return endpoint.price > start.price + EPS
    assert direction == Direction.DOWN
    return endpoint.price < start.price - EPS


@dataclass(frozen=True)
class PriceBar:
    """一根 K 线的价格区间；用于定位笔内首次严格穿越阈值的真实时点。"""

    order: float
    high: float
    low: float


@dataclass(frozen=True)
class Pen:
    """
    已完成的笔。

    position 是它在输入列表里的位置，必须从 0 连续递增。
    start/end 保存真实路径方向；low/high 只表示闭区间边界。price_bars
    保存从起点 K 线至终点 K 线的逐 K 线 H/L；抽象或合成输入可以留空，
    此时任何笔内事件只能保守定位到笔终点。
    """

    position: int
    start: Point
    end: Point
    direction: Direction
    price_bars: tuple[PriceBar, ...] = ()

    @property
    def low(self) -> float:
        return min(self.start.price, self.end.price)

    @property
    def high(self) -> float:
        return max(self.start.price, self.end.price)


@dataclass(frozen=True)
class PenInputError:
    """通达信输入错误；bar_index=-1 表示错误不属于某一根 K 线。"""

    code: PenInputErrorCode
    bar_index: int


@dataclass(frozen=True)
class PenDecodeResult:
    """适配器的强类型结果；合法但端点不足时 pens 可以为空。"""

    pens: tuple[Pen, ...] = ()
    errors: tuple[PenInputError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class InitialSegmentStart:
    """最早满足三笔基础定义的初始线段起点和方向。"""

    start_pos: int
    direction: Direction
    start: Point


@dataclass(frozen=True)
class FeatureElement:
    """
    特征序列元素。

    一个元素起初对应一笔；发生包含合并后，它可以对应多笔。
    source_pen_positions 用于把任何结论追溯到原始笔。
    """

    low: float
    high: float
    source_pen_positions: tuple[int, ...]
    first_order: float
    last_order: float


@dataclass(frozen=True)
class CandidateTop:
    """向上线段的候选结构顶 T，以及候选点两侧的 A/B/C。

    A/B/C 已满足第一特征序列专用三比较判据；普通 A 是候选边界左侧
    最近的合格标准元素，完整“3 没新高”形态才采用最高合格元素。
    B.low 不要求高于 A.low。
    B 是右侧包含标准化后冻结的第一个标准元素，不应与
    first_reverse_pen_position 指向的原始第一反向笔 B0 混为一谈。
    available_at 是 B/C 全部来源笔完成后的来源审计时点；第一种情况的
    confirmed_at 可以早于它。
    """

    endpoint: Point
    boundary_b_raw_index: int
    A: FeatureElement
    B: FeatureElement
    C: FeatureElement
    first_reverse_pen_position: int
    segment_start_position: int
    available_at: Point
    a_selection_reason: str = "nearest"


@dataclass(frozen=True)
class HypotheticalTurn:
    """
    尚未证明为线段端点的假设转折点。

    每一根可能成为旧向上线段末笔的向上笔终点，都先进入该对象；随后才判断：
    能否形成标准 A-B-C 顶分型，或是否只能由走势 A 特殊规则解决。即使
    右侧尚无独立 C，也按 SPEC-034 从 B 左侧标准化结果选 A：普通取最近
    合格元素，完整“3 没新高”才取原最高合格元素，供走势 A 检查 B.low<A.high。
    若暂时没有合格 A，
    T 仍保留在候选时序中，但不能形成 A-B-C 或进入走势 A。
    """

    endpoint: Point
    boundary_b_raw_index: int
    A: Optional[FeatureElement]
    first_reverse_pen_position: int
    segment_start_position: int
    a_selection_reason: str = "nearest"


@dataclass(frozen=True)
class TurnAttempt:
    turn: HypotheticalTurn
    primary_top: Optional[CandidateTop]
    pre_top_new_high_pen: Optional[Pen] = None


@dataclass(frozen=True)
class TrendAPattern:
    """
    “第一反向笔 q1 + 符合线段基础标准的走势 A”骨架。

    对旧向上线段而言，q1 向下，走势 A 从向上笔开始；A 至少先满足
    紧邻 q1 的上/下/上三笔及其公共重叠条件，绝不能跳过中间笔拼接
    种子，也不能把一根向上笔直接叫作走势 A。
    """

    q1: Pen
    left_primary_A: FeatureElement
    pre_t_seed: tuple[Pen, Pen, Pen]  # 尾部三笔，只在恰好三笔时是必需门槛
    pre_t_pen_count: int
    pre_t_first_pen: Pen
    pre_t_breakthrough_pen: Optional[Pen]
    pre_t_progress_seed: Optional[tuple[Pen, Pen, Pen]]
    pre_t_qualification_mode: str
    a_pens: tuple[Pen, ...]
    reactions: tuple[Pen, ...]
    q1_end_price: float

    @property
    def entered_at(self) -> Point:
        """走势 A 特殊状态的进入时点。

        只有当最小上/下/上三笔种子全部完成时，才算真正进入
        走势 A 特殊情况。进入后，外层候选只按特殊分支处理；该分支
        会把 q1 后包含三笔种子的完整后缀隔离为反应走势，不再让外层
        普通第一/第二种情况或“顶分型前创新高”插入。
        """

        return self.a_pens[-1].end


@dataclass
class CandidateDecision:
    status: CandidateStatus
    endpoint: Optional[Point] = None
    confirmed_at: Optional[Point] = None
    case_name: Optional[EndCase] = None
    # 用于候选被规则允许的创新高分支否定后的接续：普通第二种情况
    # 固定取原始创新高笔 U.end；走势 A 外层守卫取触发守卫笔终点。
    # 同事件底分型确认时不使用。它不改变主循环候选枚举顺序，也
    # 不得用来跳过 T 与 T' 之间的候选。
    successor_candidate_t: Optional[Point] = None
    # 以下三项是非规范的调试审计面；C++ 生产内核不依赖动态键取值。
    proof: dict[str, Any] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    basis_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateSuccessorEvent:
    """记录旧 T 由创新高分支否定并产生 T' 的事件，不改变候选顺序。"""

    rejected_t: Point
    successor_t: Point
    source_case: Optional[EndCase]


@dataclass
class SegmentResult:
    """
    单条实际线段的结果。

    direction 明确保存实际方向。confirmed_at 是最早充分证据事件；
    单一极值第一种情况可位于冻结 C 最后来源笔内部、早于 available_at，
    重复同价候选等待确认时也可晚于 available_at。
    next_segment_t_not_before 只在本段由
    走势 A 特殊分支确认时非空；它限制下一反向线段候选 T 的最早时点，
    但不直接确认下一线段。

    proof/trace/basis_ids 只帮助人工核查。生产 C++ 对外只需把已确认结果
    压缩成 SegmentCertificate；任何动态 proof 键都不得成为控制流输入。
    """

    direction: Direction
    status: SegmentStatus
    start: Point
    endpoint: Optional[Point]
    confirmed_at: Optional[Point]
    case_name: Optional[EndCase]
    included_pen_positions: tuple[int, ...]
    # 只有 CONFIRMED 才存在“本段结构区间”。待定或输入错误时不能把
    # 当前已观察到的全后缀高低值冒充结构结果。
    structural_low: Optional[float]
    structural_high: Optional[float]
    proof: dict[str, Any]
    trace: list[str]
    basis_ids: list[str]
    next_segment_t_not_before: Optional[Point] = None
    explicitly_rejected_ts: tuple[Point, ...] = ()
    recovery_evidence: Optional["LongStallEvidence"] = None
    wait_reason: str = "no_candidate_certificate"
    recovery_diagnostic: str = "disabled"


@dataclass(frozen=True)
class LongStallPolicy:
    enabled: bool = False
    length: int = 43
    stagnation: int = 13
    residence: int = 9
    last_pen_unfinished: bool = False

    def __post_init__(self):
        if min(self.length, self.stagnation, self.residence) < 1:
            raise ValueError("托底阈值必须为正整数")


def default_long_stall_policy(last_pen_unfinished: bool = False) -> LongStallPolicy:
    """2026-10-04校准选定的默认策略；显式LongStallPolicy()仍关闭托底。"""
    return LongStallPolicy(enabled=True, length=43, stagnation=13,
                           residence=13, last_pen_unfinished=last_pen_unfinished)


@dataclass(frozen=True)
class LongStallEvidence:
    wait_reason: str
    S: Point
    T: Point
    R: Point
    observed_pens: int
    stagnant_pens: int
    residence_pens: int
    reverse_case: EndCase
    wait_case: Optional[EndCase]
    reverse_intrinsic_at: Point
    reverse_visible_at: Point
    effective_at: Point


@dataclass
class AlternatingSegmentsResult:
    """从一个已知起点开始，按方向交替得到的连续线段链。"""

    confirmed_segments: tuple[SegmentResult, ...]
    unresolved_segment: Optional[SegmentResult]
    next_start: Point
    next_direction: Direction
    next_segment_t_not_before: Optional[Point]
    trace: list[str]


@dataclass(frozen=True)
class SegmentCertificate:
    """进入通达信编码器的最小强类型证书；不携带 proof 或 trace。"""

    direction: Direction
    start_bar: int
    endpoint_bar: int
    confirmed_bar: int


@dataclass
class TdxSegmentCalculationResult:
    """函数编号 1 的完整参考结果；event_mask 可直接与 C++ 输出对拍。"""

    event_mask: tuple[float, ...]
    input_errors: tuple[PenInputError, ...]
    initial_start: Optional[InitialSegmentStart]
    chain: Optional[AlternatingSegmentsResult]

    @property
    def ok(self) -> bool:
        return not self.input_errors


@dataclass(frozen=True)
class SecondaryPrefixSnapshot:
    """
    第二特征序列在一个真实时间前缀上的完整分析快照。

    状态机只读取这个对象，不再分别重复查找当前标准元素、合并后终点
    和新底分型，从而保证三者永远来自同一次标准化。原始笔是否创新高
    是独立的真实事件属性，不由合并后端点代替。
    """

    raw_prefix: tuple[FeatureElement, ...]
    latest_pen: Pen
    standardized: tuple[FeatureElement, ...]
    current_element: FeatureElement
    current_endpoint_price: float
    completed_bottom: Optional[
        tuple[FeatureElement, FeatureElement, FeatureElement]
    ]

    def endpoint_strictly_above(self, price: float) -> bool:
        return self.current_endpoint_price > price + EPS


@dataclass(frozen=True)
class SecondaryInnovationWait:
    """第二特征序列原始创新高 U 已取得 B2 资格后的局部等待状态。"""

    triggering_pen: Pen
    trigger_snapshot: SecondaryPrefixSnapshot
    left_anchor_A2: FeatureElement
    qualified_B2: FeatureElement

    @property
    def fixed_start_price(self) -> float:
        return self.triggering_pen.start.price


# ============================================================================
# 1. 数学基础：闭区间、包含、方向、分型
# ============================================================================


def almost_equal(x: float, y: float) -> bool:
    return abs(x - y) <= EPS


def points_equal(left: Point, right: Point) -> bool:
    """两个结构点的时间顺序和价格都在 EPS 内一致。"""

    return (
        almost_equal(left.order, right.order)
        and almost_equal(left.price, right.price)
    )


def event_not_before(event: Point, floor: Point, label: str) -> Point:
    """需要两份证据时，确认时点不得早于其中较晚才可获得的那一份。"""

    if event.order >= floor.order - EPS:
        return event
    return Point(order=floor.order, price=floor.price, label=label)


def first_bar_low_strictly_below(
    pen: Pen,
    threshold: float,
    *,
    label: str,
) -> Optional[Point]:
    """返回向下笔内第一根 `low < threshold-EPS` 的 K 线事件。

    通达信适配器会保留逐 K 线 H/L，因此可以把事件定位到真实 K 线。
    抽象或合成 Pen 没有 price_bars 时，只能用已确认笔终点作保守回退；
    绝不在两个端点之间插值或猜测穿越时刻。
    """

    assert pen.direction == Direction.DOWN, "向下跌破事件只能在向下笔内查找"
    assert isfinite(threshold), "跌破阈值必须是有限数"
    for bar in pen.price_bars:
        if bar.low < threshold - EPS:
            return Point(order=bar.order, price=bar.low, label=label)
    if pen.end.price < threshold - EPS:
        return Point(order=pen.end.order, price=pen.end.price, label=label)
    return None


def interval_overlap(x: FeatureElement | Pen, y: FeatureElement | Pen) -> bool:
    """
    闭区间重叠：max(低点) <= min(高点)。

    因为是闭区间，所以端点刚好相等仍算重叠，不算缺口。
    """

    return max(x.low, y.low) <= min(x.high, y.high) + EPS


def interval_gap(x: FeatureElement | Pen, y: FeatureElement | Pen) -> bool:
    """特征序列缺口：两个价格闭区间严格分离。"""

    return max(x.low, y.low) > min(x.high, y.high) + EPS


def interval_contains(x: FeatureElement | Pen, y: FeatureElement | Pen) -> bool:
    """x 的闭区间是否包含 y；边界相等也属于包含。"""

    return x.low <= y.low + EPS and x.high + EPS >= y.high


def has_containment(x: FeatureElement | Pen, y: FeatureElement | Pen) -> bool:
    return interval_contains(x, y) or interval_contains(y, x)


def common_overlap(items: Sequence[FeatureElement | Pen]) -> bool:
    """多个闭区间是否有非空公共交集。"""

    if not items:
        return False
    return max(item.low for item in items) <= min(item.high for item in items) + EPS


def merge_contained_elements(
    left: FeatureElement,
    right: FeatureElement,
    feature_pen_direction: Direction,
) -> FeatureElement:
    """
    向上线段中，包含合并规则由特征元素对应的笔方向固定决定：

    - 第一特征序列由向下笔组成：低点取较高者，高点也取较高者；
    - 第二特征序列由向上笔组成：低点取较低者，高点也取较低者。

    这里不推断所谓“序列运行方向”。只允许在 left/right 确有包含关系时调用。
    """

    assert has_containment(left, right), "只有包含元素才能合并"

    if feature_pen_direction == Direction.DOWN:
        merged_low = max(left.low, right.low)
        merged_high = max(left.high, right.high)
    else:
        assert feature_pen_direction == Direction.UP
        merged_low = min(left.low, right.low)
        merged_high = min(left.high, right.high)

    return FeatureElement(
        low=merged_low,
        high=merged_high,
        source_pen_positions=left.source_pen_positions + right.source_pen_positions,
        first_order=min(left.first_order, right.first_order),
        last_order=max(left.last_order, right.last_order),
    )


def is_symmetric_top_fractal(
    a: FeatureElement,
    b: FeatureElement,
    c: FeatureElement,
) -> bool:
    """通用对称严格顶分型：b 的高点和低点都严格高于左右元素。

    注意：第一特征序列的 A-B-C 候选不得调用本函数，因为其
    B.low 不要求高于 A.low；该场景必须调用 is_primary_top_fractal()。
    """

    return (
        b.high > a.high + EPS
        and b.high > c.high + EPS
        and b.low > a.low + EPS
        and b.low > c.low + EPS
    )


def is_primary_top_fractal(
    a: FeatureElement,
    b: FeatureElement,
    c: FeatureElement,
) -> bool:
    """第一特征序列顶分型。

    规范向上线段只要求：
        - B.high 严格高于 A.high 和 C.high；
        - B.low 严格高于 C.low。

    不比较 B.low 与 A.low。B.low < A.low 不是失败，而表示第一反向笔
    对原向上线段的破坏更强；B.low 与 A.low 相等同样不取消资格。
    """

    return (
        b.high > a.high + EPS
        and b.high > c.high + EPS
        and b.low > c.low + EPS
    )


def is_secondary_bottom_fractal(
    a: FeatureElement,
    b: FeatureElement,
    c: FeatureElement,
) -> bool:
    """第二特征序列严格底分型：b 的高点和低点都严格低于左右。"""

    return (
        b.high < a.high - EPS
        and b.high < c.high - EPS
        and b.low < a.low - EPS
        and b.low < c.low - EPS
    )


def pen_to_feature(pen: Pen) -> FeatureElement:
    return FeatureElement(
        low=pen.low,
        high=pen.high,
        source_pen_positions=(pen.position,),
        first_order=pen.start.order,
        last_order=pen.end.order,
    )


# ============================================================================
# 2. 特征序列标准化：按笔方向固定处理包含
# ============================================================================


def standardize_feature_sequence(
    raw: Sequence[FeatureElement],
    feature_pen_direction: Direction,
) -> tuple[FeatureElement, ...]:
    """
    从左到右标准化由同一方向笔组成的特征序列。

    - feature_pen_direction=DOWN：第一特征序列，任何包含都按“高高”合并；
    - feature_pen_direction=UP：第二特征序列，任何包含都按“低低”合并；
    - 无包含：直接保留新元素；
    - 合并后若与更左元素再次包含，继续用同一固定规则向左合并。

    候选 A/B 不跨边界合并，由调用者把左右两侧分开传入保证；
    本函数内部没有其他例外，也不需要向未来推断合并方向。
    """

    if not raw:
        return ()

    result: list[FeatureElement] = [raw[0]]

    for current in raw[1:]:
        previous = result[-1]

        if has_containment(previous, current):
            result[-1] = merge_contained_elements(
                previous,
                current,
                feature_pen_direction,
            )

            # 一次合并后，新元素还可能与它前面的标准元素再次形成包含。
            # 必须继续向左回溯，直到相邻标准元素不再包含；否则会留下
            # “半处理”的序列，后面的分型可能被误判。
            while len(result) >= 2 and has_containment(result[-2], result[-1]):
                merged_again = merge_contained_elements(
                    result[-2],
                    result[-1],
                    feature_pen_direction,
                )
                result[-2:] = [merged_again]
            continue

        result.append(current)

    return tuple(result)


# ============================================================================
# 3. 输入校验与通达信笔适配
# ============================================================================


def validate_pen_stream(pens: Sequence[Pen]) -> list[str]:
    """发现输入错误就返回文字说明；空列表表示通过。"""

    errors: list[str] = []
    for expected_position, pen in enumerate(pens):
        if pen.position != expected_position:
            errors.append(f"笔位置应为 {expected_position}，实际为 {pen.position}")
        if pen.start.order >= pen.end.order:
            errors.append(f"第 {pen.position} 笔的起止顺序错误")
        if pen.direction == Direction.UP and pen.end.price <= pen.start.price + EPS:
            errors.append(f"第 {pen.position} 笔标为向上，但终点没有严格更高")
        if pen.direction == Direction.DOWN and pen.end.price >= pen.start.price - EPS:
            errors.append(f"第 {pen.position} 笔标为向下，但终点没有严格更低")

        if pen.price_bars:
            if not almost_equal(pen.price_bars[0].order, pen.start.order):
                errors.append(f"第 {pen.position} 笔的逐 K 线路径没有从笔起点开始")
            if not almost_equal(pen.price_bars[-1].order, pen.end.order):
                errors.append(f"第 {pen.position} 笔的逐 K 线路径没有在笔终点结束")
            previous_bar_order: Optional[float] = None
            for bar in pen.price_bars:
                if not isfinite(bar.high) or not isfinite(bar.low):
                    errors.append(f"第 {pen.position} 笔含有非有限 K 线价格")
                if bar.high < bar.low:
                    errors.append(f"第 {pen.position} 笔含有 H<L 的 K 线")
                if (
                    bar.order < pen.start.order - EPS
                    or bar.order > pen.end.order + EPS
                ):
                    errors.append(f"第 {pen.position} 笔含有越出起终点范围的 K 线")
                if (
                    previous_bar_order is not None
                    and bar.order <= previous_bar_order + EPS
                ):
                    errors.append(f"第 {pen.position} 笔的逐 K 线路径顺序不严格递增")
                previous_bar_order = bar.order

        if expected_position > 0:
            previous = pens[expected_position - 1]
            if previous.direction == pen.direction:
                errors.append(f"第 {expected_position - 1}/{expected_position} 笔没有方向交替")
            if not almost_equal(previous.end.order, pen.start.order):
                errors.append(f"第 {expected_position - 1}/{expected_position} 笔时间端点不连续")
            if not almost_equal(previous.end.price, pen.start.price):
                errors.append(f"第 {expected_position - 1}/{expected_position} 笔价格端点不连续")
    return errors


def decode_tdx_endpoint_kind(value: float) -> Optional[PenEndpointKind]:
    """严格解释 -1、0、1；任何近似值或非有限数都返回无效。"""

    if not isfinite(value) or value not in (-1.0, 0.0, 1.0):
        return None
    return PenEndpointKind(int(value))


def _pen_decode_error(
    code: PenInputErrorCode,
    bar_index: int,
) -> PenDecodeResult:
    return PenDecodeResult(errors=(PenInputError(code, bar_index),))


def decode_pens_from_tdx_series(
    endpoint_markers: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
) -> PenDecodeResult:
    """
    把通达信逐 K 线的端点标记、最高价和最低价恢复为标准 Pen 序列。

    外部接口固定为三个等长序列：
        - endpoint_markers：1=顶端点，-1=底端点，0=非端点；
        - highs：原始 H 序列；
        - lows：原始 L 序列。

    相邻“底→顶”恢复为向上笔，相邻“顶→底”恢复为向下笔。同类
    标记连续出现时，它表示 CLG3/CLD3 对尚未闭合端点的候选更新：
    连续顶取整段更新中的最高顶，连续底取整段更新中的最低底；同价
    极值采用更晚一次的位置，较不极端的冗余更新忽略。完成这一步后，
    第一个端点只建立起点，以后每个异类端点闭合一根新笔。order
    使用 DLL 输入数组的零基下标，position 对生成的笔从 0 连续编号。
    同时把每根笔起点至终点的逐 K 线 H/L 保存到 price_bars；这些数据
    不参与重新识别笔，只用于定位第一种情况的笔内首次严格越界事件。
    """

    data_len = len(endpoint_markers)
    if data_len <= 0:
        return _pen_decode_error(PenInputErrorCode.NON_POSITIVE_LENGTH, -1)
    if data_len != len(highs) or data_len != len(lows):
        return _pen_decode_error(PenInputErrorCode.LENGTH_MISMATCH, -1)

    endpoints: list[tuple[PenEndpointKind, Point]] = []
    all_price_bars: list[PriceBar] = []
    for bar_index, (marker_value, high, low) in enumerate(
        zip(endpoint_markers, highs, lows)
    ):
        if not isfinite(high) or not isfinite(low):
            return _pen_decode_error(
                PenInputErrorCode.NON_FINITE_PRICE,
                bar_index,
            )
        if high < low:
            return _pen_decode_error(PenInputErrorCode.HIGH_BELOW_LOW, bar_index)
        all_price_bars.append(
            PriceBar(
                order=float(bar_index),
                high=float(high),
                low=float(low),
            )
        )

        kind = decode_tdx_endpoint_kind(marker_value)
        if kind is None:
            return _pen_decode_error(
                PenInputErrorCode.INVALID_ENDPOINT_MARKER,
                bar_index,
            )
        if kind == PenEndpointKind.NONE:
            continue

        price = high if kind == PenEndpointKind.TOP else low
        label = (
            "tdx_top_endpoint"
            if kind == PenEndpointKind.TOP
            else "tdx_bottom_endpoint"
        )
        endpoint = (
            kind,
            Point(
                order=float(bar_index),
                price=float(price),
                label=label,
            ),
        )
        if endpoints and endpoints[-1][0] == kind:
            previous_price = endpoints[-1][1].price
            is_more_extreme = (
                price > previous_price + EPS
                if kind == PenEndpointKind.TOP
                else price < previous_price - EPS
            )
            if is_more_extreme or almost_equal(price, previous_price):
                endpoints[-1] = endpoint
            continue
        endpoints.append(endpoint)

    pens: list[Pen] = []
    for previous, current in zip(endpoints, endpoints[1:]):
        previous_kind, start = previous
        current_kind, end = current

        direction = (
            Direction.UP
            if previous_kind == PenEndpointKind.BOTTOM
            else Direction.DOWN
        )
        if direction == Direction.UP:
            if (
                current_kind != PenEndpointKind.TOP
                or end.price <= start.price + EPS
            ):
                return _pen_decode_error(
                    PenInputErrorCode.INVALID_PEN_PRICE_DIRECTION,
                    int(end.order),
                )
        else:
            if (
                current_kind != PenEndpointKind.BOTTOM
                or end.price >= start.price - EPS
            ):
                return _pen_decode_error(
                    PenInputErrorCode.INVALID_PEN_PRICE_DIRECTION,
                    int(end.order),
                )

        pens.append(
            Pen(
                position=len(pens),
                start=start,
                end=end,
                direction=direction,
                price_bars=tuple(
                    all_price_bars[int(start.order) : int(end.order) + 1]
                ),
            )
        )

    stream_errors = validate_pen_stream(pens)
    assert not stream_errors, "适配器内部构造违反 Pen 不变量：" + "；".join(
        stream_errors
    )
    return PenDecodeResult(pens=tuple(pens))


def _require_pens_from_tdx_series(
    endpoint_markers: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
) -> tuple[Pen, ...]:
    """仅供合成回归夹具使用；生产路径必须检查 PenDecodeResult。"""

    decoded = decode_pens_from_tdx_series(endpoint_markers, highs, lows)
    assert decoded.ok, f"测试夹具输入错误：{decoded.errors}"
    return decoded.pens


def _three_pens_define_segment(
    pens: Sequence[Pen],
    start_pos: int,
    direction: Direction,
) -> bool:
    """三根连续笔是否满足指定方向线段的基础定义。

    已知起点的三笔基础结构只要求方向交替且存在公共重叠，不要求
    第三笔创新高/创新低。自动搜索首段起点另有严格方向条件，见
    find_earliest_segment_start；两者不能共用同一资格门槛。
    """

    if start_pos < 0 or start_pos + 2 >= len(pens):
        return False

    first, second, third = pens[start_pos : start_pos + 3]
    reverse = Direction.DOWN if direction == Direction.UP else Direction.UP
    if [first.direction, second.direction, third.direction] != [
        direction,
        reverse,
        direction,
    ]:
        return False
    if not common_overlap((first, second, third)):
        return False
    return True


def _three_pens_meet_strict_initial_progress(
    pens: Sequence[Pen],
    start_pos: int,
    direction: Direction,
) -> bool:
    """三根连续笔是否达到自动初始化使用的严格推进标准。

    它在基础的方向交替和公共重叠之外，还要求第二笔终点严格留在
    第一笔起点的段内一侧，第三笔终点严格越过第一笔终点。走势 A
    的候选 T 也复用本判据，避免初始化与特殊分支各维护一份公式。
    """

    if not _three_pens_define_segment(pens, start_pos, direction):
        return False
    first, second, third = pens[start_pos : start_pos + 3]
    sign = 1 if direction == Direction.UP else -1
    return (
        sign * (second.end.price - first.start.price) > EPS
        and sign * (third.end.price - first.end.price) > EPS
    )


def find_earliest_segment_start(
    pens: Sequence[Pen],
) -> Optional[InitialSegmentStart]:
    """
    从最早位置寻找初始线段。

    三笔方向交替、具有公共重叠；第二笔终点必须严格留在起点内侧，
    第三笔终点必须沿首笔方向严格越过第一笔终点。等价地，向上种子
    要求底抬高、顶创新高；向下种子要求顶降低、底创新低，相等不接受。
    这是 CZSC 一分钟自动起点的校准规则，不改变已知起点处的三笔定义。
    """

    stream_errors = validate_pen_stream(pens)
    assert not stream_errors, "；".join(stream_errors)
    for start_pos in range(max(0, len(pens) - 2)):
        direction = pens[start_pos].direction
        if _three_pens_meet_strict_initial_progress(
            pens,
            start_pos,
            direction,
        ):
            return InitialSegmentStart(
                start_pos=start_pos,
                direction=direction,
                start=pens[start_pos].start,
            )
    return None


def validate_up_segment_start(pens: Sequence[Pen], start_pos: int) -> list[str]:
    """
    向上线段的最低成立条件：
        U1, D1, U2 三笔；方向为上/下/上；三个笔区间有公共交集。
        U2 的终点可以不高于 U1 的终点。
    """

    errors: list[str] = []
    if start_pos < 0 or start_pos + 2 >= len(pens):
        return ["从给定起点开始不足三笔"]

    first_three = pens[start_pos : start_pos + 3]
    expected = [Direction.UP, Direction.DOWN, Direction.UP]
    actual = [pen.direction for pen in first_three]
    if actual != expected:
        errors.append(f"前三笔方向应为 {expected}，实际为 {actual}")
    if not common_overlap(first_three):
        errors.append("前三笔价格区间没有公共交集，不能构成该向上线段的基础")
    return errors


# ============================================================================
# 4. 第一特征序列与候选顶 T
# ============================================================================


def collect_primary_down_features(
    pens: Sequence[Pen],
    start_pos: int,
) -> tuple[list[FeatureElement], list[Pen]]:
    """向上线段的第一特征序列只取向下笔。"""

    down_pens = [pen for pen in pens[start_pos:] if pen.direction == Direction.DOWN]
    return [pen_to_feature(pen) for pen in down_pens], down_pens


def locate_candidate_endpoint(first_reverse_pen: Pen) -> Point:
    """
    B 是从候选顶 T 开始的第一根向下笔，因此 T 就是 B 的起点。

    注意：以后出现的更高价格不自动改写这个结构点；它只可能否定该候选。
    """

    return first_reverse_pen.start


def _highest_eligible_left_A(
    left_elements: Sequence[FeatureElement],
    candidate_price: float,
) -> Optional[FeatureElement]:
    """保留原 BASIS-032 选法；只供 SPEC-034 特例查询 H。"""
    eligible = [
        element
        for element in left_elements
        if element.high <= candidate_price + EPS
    ]
    if not eligible:
        return None

    highest_start = max(element.high for element in eligible)
    selected = eligible[0]
    for element in eligible:
        if almost_equal(element.high, highest_start):
            selected = element
    return selected


def _left_A_selection_at_boundary(
    raw_primary: Sequence[FeatureElement],
    b_raw_index: int,
    candidate_price: float,
) -> tuple[Optional[FeatureElement], str]:
    """SPEC-034：普通最近 A；完整未创新高形态才保留原最高合格 A。

    只读取 T 左侧标准序列和原始 B₀；不以未来标准 B/C 或最终分支决定 A。
    连续多个低顶和跨分支共用属于 BASIS-036 用户要求。
    """
    if b_raw_index <= 0 or b_raw_index >= len(raw_primary):
        return None, "invalid_boundary"
    left_elements = standardize_feature_sequence(
        raw_primary[:b_raw_index],
        feature_pen_direction=Direction.DOWN,
    )
    if not left_elements:
        return None, "empty_left"

    nearest = left_elements[-1]
    highest = _highest_eligible_left_A(left_elements, candidate_price)
    if highest is not None and highest is not nearest:
        highest_index = next(
            index for index, element in enumerate(left_elements)
            if element is highest
        )
        if (
            all(element.high < highest.high - EPS
                for element in left_elements[highest_index + 1:])
            and candidate_price > highest.high + EPS
            and interval_overlap(raw_primary[b_raw_index], highest)
        ):
            return highest, "no_new_high_special"

    if nearest.high > candidate_price + EPS:
        return None, "nearest_above_T"
    return nearest, "nearest"


def standardized_left_A_at_boundary(
    raw_primary: Sequence[FeatureElement],
    b_raw_index: int,
    candidate_price: float,
) -> Optional[FeatureElement]:
    """返回统一 A；假设点与普通候选共享同一 SPEC-034 选择结果。"""
    return _left_A_selection_at_boundary(
        raw_primary, b_raw_index, candidate_price,
    )[0]


def build_candidate_at_boundary(
    raw_primary: Sequence[FeatureElement],
    down_pens: Sequence[Pen],
    b_raw_index: int,
) -> Optional[CandidateTop]:
    """
    在第 b_raw_index 个向下特征元素处尝试建立候选顶。

    关键边界规则：
        - 左侧标准化到 A；
        - B 与其右侧元素可以按包含规则处理；
        - A/B 跨候选转折边界绝不合并；
        - 第一特征序列标准候选成立前，任何后续向下特征元素的起点若高于 T，
          T 的普通第一特征序列候选资格立即失效；
        - 若在该创新高事件之前或同时已经进入走势 A，则这个普通候选
          失效不直接决定线段，后续必须交给走势 A 特殊分支；
        - 最后用 A、B、C 判断第一特征序列顶分型；该判据不要求
          B.low 高于 A.low，但仍严格要求 C.low 低于 B.low。
    """

    if b_raw_index <= 0 or b_raw_index >= len(raw_primary) - 1:
        return None

    first_reverse_pen = down_pens[b_raw_index]
    candidate_endpoint = locate_candidate_endpoint(first_reverse_pen)
    A, a_selection_reason = _left_A_selection_at_boundary(
        raw_primary,
        b_raw_index,
        candidate_endpoint.price,
    )
    if A is None:
        return None

    # right_end 第一次取 b_raw_index+2，所以最短前缀恰好是
    # “B + B 后第一个原始特征元素”。这个首个右侧元素和以后每个
    # 新增元素完全同权：先检查其原始向下笔起点，再与 B 一起参加
    # 当前右侧前缀的高高包含标准化。
    #
    # 只读到“最早足以形成第一特征序列顶分型”的右侧前缀，随后立即冻结 A/B/C。
    # 不能拿候选确立后的遥远元素回头改写已经成立的 C。
    for right_end in range(b_raw_index + 2, len(raw_primary) + 1):
        newest_down_pen = down_pens[right_end - 1]

        # 新向下特征元素包括第一轮中 B 后的首个原始元素。它的起点
        # 就是前一根向上笔刚形成的新高。
        # 该事件早于本向下笔完成；因此必须先检查它，再用本笔完成 C。
        # 若第一特征序列顶分型尚未成立而这里已高于 T，旧 T 失去普通
        # A-B-C 普通候选资格。本函数只负责普通候选；如果此前已进入
        # 走势 A，主状态机会忽略这条普通取消规则，改由走势 A 处理。
        if newest_down_pen.start.price > candidate_endpoint.price + EPS:
            return None

        right_elements = standardize_feature_sequence(
            raw_primary[b_raw_index:right_end],
            feature_pen_direction=Direction.DOWN,
        )
        if len(right_elements) < 2:
            continue

        B = right_elements[0]
        C = right_elements[1]
        if not is_primary_top_fractal(A, B, C):
            # 右侧标准化已保证独立的 B/C 不再包含；结合所有右侧高点
            # 未实质高于候选 T，可知 B.high>C.high 且 B.low>C.low。
            # 第一特征序列专用判据此时若仍失败，只可能是
            # B.high 没有严格高于固定的 A.high。A 与候选 T 都已固定，
            # 更远元素不能修复这个候选，因此直接返回无标准候选。
            # 特别注意：绝不能因为 B.low<=A.low 在这里失败。
            return None

        evidence_last_pen_position = max(
            B.source_pen_positions + C.source_pen_positions
        )
        evidence_pen = next(
            pen
            for pen in down_pens
            if pen.position == evidence_last_pen_position
        )
        return CandidateTop(
            endpoint=candidate_endpoint,
            boundary_b_raw_index=b_raw_index,
            A=A,
            B=B,
            C=C,
            first_reverse_pen_position=first_reverse_pen.position,
            segment_start_position=raw_primary[0].source_pen_positions[0] - 1,
            available_at=evidence_pen.end,
            a_selection_reason=a_selection_reason,
        )

    return None


def find_first_later_primary_start_above_t(
    down_pens: Sequence[Pen],
    b_raw_index: int,
) -> Optional[Pen]:
    """
    返回 T 之后第一根起点严格高于 T 的向下特征笔。

    只有 build_candidate_at_boundary() 没能在该事件之前建立第一特征序列顶分型，
    且此前也没有进入走势 A 时，调用者才把它解释为
    “第一特征序列标准候选成立前创新高，旧 T 自动取消普通候选资格”。
    """

    if b_raw_index < 0 or b_raw_index >= len(down_pens):
        return None

    candidate_price = down_pens[b_raw_index].start.price
    for later_down_pen in down_pens[b_raw_index + 1 :]:
        if later_down_pen.start.price > candidate_price + EPS:
            return later_down_pen
    return None


def find_hypothetical_turn_attempts(
    pens: Sequence[Pen],
    start_pos: int,
) -> list[TurnAttempt]:
    """
    从早到晚列出所有值得检查的假设转折点。

    当前向上线段从 U1 开始。第一处可能端点是第三笔 U2 的终点，随后每根
    向上笔终点都可能成为新的假设转折点。它后面的第一根向下笔就是边界 B。

    标准 A-B-C 第一特征序列顶分型需要 B 右侧再出现 C；走势 A 则可能在没有标准候选
    时成立，所以两者必须分别保存。每个原始假设点先在 B 左侧完成标准化，
    再按 SPEC-034 选择 A：普通只取最近合格标准元素，完整“3 没新高”
    才用原最高合格选法；无合格 A 仍保留 T，不回退选取更早元素。
    所得 A 供特殊分支严格检查 B.low<A.high。
    若标准候选成立前已有
    后续向下特征笔从高于 T 的位置开始，也保存最早该笔作为普通取消证据。
    该证据只是事件记录；若走势 A 已在它之前或同时进入，后续解决函数
    会交给走势 A 分支，而不会直接套用普通自动取消。
    """

    raw_primary, down_pens = collect_primary_down_features(pens, start_pos)
    attempts: list[TurnAttempt] = []

    # b_raw_index=0 对应第一根向下笔 D1，它前面只有 U1，不足三笔；从 1 开始。
    for b_raw_index in range(1, len(raw_primary)):
        first_reverse_pen = down_pens[b_raw_index]
        candidate_endpoint = locate_candidate_endpoint(first_reverse_pen)
        A, a_selection_reason = _left_A_selection_at_boundary(
            raw_primary,
            b_raw_index,
            candidate_endpoint.price,
        )
        turn = HypotheticalTurn(
            endpoint=candidate_endpoint,
            boundary_b_raw_index=b_raw_index,
            A=A,
            first_reverse_pen_position=first_reverse_pen.position,
            segment_start_position=start_pos,
            a_selection_reason=a_selection_reason,
        )
        primary_top = (
            build_candidate_at_boundary(
                raw_primary,
                down_pens,
                b_raw_index,
            )
            if A is not None
            else None
        )
        pre_top_new_high_pen = None
        if primary_top is None:
            pre_top_new_high_pen = find_first_later_primary_start_above_t(
                down_pens,
                b_raw_index,
            )
        attempts.append(
            TurnAttempt(
                turn=turn,
                primary_top=primary_top,
                pre_top_new_high_pen=pre_top_new_high_pen,
            )
        )

    attempts.sort(key=lambda attempt: attempt.turn.endpoint.order)
    return attempts


# ============================================================================
# 5. 第一种情况：A/B 没有特征序列缺口
# ============================================================================


def classify_first_pen_break_strength(
    segment_start_price: float,
    A: FeatureElement,
    B: FeatureElement,
) -> str:
    """
    对附件中的四幅“第一笔跌破”图做强弱标记。

    它们全部走第一种情况，不是四套独立的线段结束算法。
    L3/L4 正是 B.low 不高于 A.low 的更强破坏；第一特征序列候选
    不得在进入本函数之前用通用四比较顶分型把它们过滤掉。
    仅在标准几何 s < A.low < A.high < B.high 下给出 L1-L4。
    """

    if not (segment_start_price < A.low - EPS and A.low < A.high - EPS):
        return "非四幅标准示意图几何；不影响第一种情况主判断"

    if almost_equal(B.low, A.high):
        return "L1：B.low = A.high（端点相接，仍属于重叠）"
    if A.low + EPS < B.low < A.high - EPS:
        return "L2：A.low < B.low < A.high"
    if segment_start_price + EPS < B.low <= A.low + EPS:
        return "L3：segment_start < B.low <= A.low"
    if B.low <= segment_start_price + EPS:
        return "L4：B.low <= segment_start"
    return "不落入四幅标准强弱面板；仍按区间重叠公式判断"


def judge_case_1(
    candidate: CandidateTop,
    segment_start: Point,
    pens: Sequence[Pen],
) -> CandidateDecision:
    """A/B 相交时确认 T；重复同价 B 可等待后续反向笔越过原始边界。

    B 只有一个候选极值来源时仍使用标准 B.low。冻结 B 内多次出现同价
    候选极值时，结构端点取最后一处，并以最后同价来源笔的原始 low 与
    标准 B.low 中更低者为阈值。冻结 C 未越界时保持待定；后续同方向笔
    可以确认旧 T，先出现更高起点则取消旧 T 并接续该新点。
    """

    if not interval_overlap(candidate.A, candidate.B):
        return CandidateDecision(status=CandidateStatus.NOT_APPLICABLE)

    c_last_source_pen_position = max(candidate.C.source_pen_positions)
    c_last_source_pen = next(
        pen
        for pen in pens
        if pen.position == c_last_source_pen_position
    )
    first_reverse_raw_pen = next(
        pen
        for pen in pens
        if pen.position == candidate.first_reverse_pen_position
    )
    strength = classify_first_pen_break_strength(
        segment_start.price,
        candidate.A,
        candidate.B,
    )
    equal_sources = [
        pen
        for pen in pens
        if pen.position in candidate.B.source_pen_positions
        and abs(pen.start.price - candidate.endpoint.price) <= EPS
    ]
    assert equal_sources, "冻结 B 必须包含候选端点的来源笔"
    endpoint = max((pen.start for pen in equal_sources), key=lambda point: point.order)
    threshold = candidate.B.low
    if len(equal_sources) > 1:
        threshold = min(threshold, equal_sources[-1].low)

    confirmation_source = c_last_source_pen
    confirmed_at = first_bar_low_strictly_below(
        confirmation_source,
        threshold,
        label="case_1_effective_threshold_break",
    )
    if confirmed_at is None:
        for later_pen in pens:
            if (
                later_pen.position <= c_last_source_pen_position
                or (later_pen.position - c_last_source_pen_position) % 2 != 0
            ):
                continue
            assert later_pen.direction == Direction.DOWN
            if later_pen.start.price > candidate.endpoint.price + EPS:
                return CandidateDecision(
                    status=CandidateStatus.REJECTED,
                    successor_candidate_t=later_pen.start,
                    proof={
                        "case_1_effective_threshold": threshold,
                        "case_1_equal_extreme_source_count": len(equal_sources),
                        "new_extreme_cancels_waiting_T": later_pen.start,
                    },
                    trace=[
                        "重复同价候选等待期间先出现更高起点；取消旧 T，"
                        "按时间顺序接续新极值"
                    ],
                    basis_ids=["BASIS-031"],
                )
            confirmation_source = later_pen
            confirmed_at = first_bar_low_strictly_below(
                later_pen,
                threshold,
                label="case_1_later_reverse_pen_break",
            )
            if confirmed_at is not None:
                break
    if confirmed_at is None:
        return CandidateDecision(
            status=CandidateStatus.PENDING,
            proof={
                "case_1_effective_threshold": threshold,
                "case_1_equal_extreme_source_count": len(equal_sources),
                "last_examined_confirmation_source": confirmation_source,
            },
            trace=["确认阈值尚未越过且未出现更新极值；候选保持待定"],
            basis_ids=["BASIS-031"],
        )

    return CandidateDecision(
        status=CandidateStatus.CONFIRMED,
        endpoint=endpoint,
        confirmed_at=confirmed_at,
        case_name=EndCase.CASE_1_NO_GAP,
        proof={
            "A": candidate.A,
            "B": candidate.B,
            "C": candidate.C,
            "primary_top_required_comparisons": {
                "B.high>A.high": candidate.B.high > candidate.A.high + EPS,
                "B.high>C.high": candidate.B.high > candidate.C.high + EPS,
                "B.low>C.low": candidate.B.low > candidate.C.low + EPS,
            },
            "B.low_vs_A.low_is_not_a_candidate_requirement": True,
            "stronger_break_B.low<A.low": (
                candidate.B.low < candidate.A.low - EPS
            ),
            "A_B_overlap": True,
            "case_1_break_threshold_standard_B.low": candidate.B.low,
            "case_1_effective_threshold": threshold,
            "case_1_equal_extreme_source_count": len(equal_sources),
            "first_reverse_raw_pen_B0.low": first_reverse_raw_pen.low,
            "standard_B_is_not_raw_first_reverse_pen": True,
            "case_1_C_last_source_pen": c_last_source_pen,
            "case_1_actual_confirmation_source_pen": confirmation_source,
            "case_1_break_confirmed_at": confirmed_at,
            "case_1_confirmed_before_C_source_pen_end": (
                confirmation_source.position == c_last_source_pen_position
                and confirmed_at.order < candidate.available_at.order - EPS
            ),
            "case_1_waited_beyond_frozen_C": (
                confirmation_source.position > c_last_source_pen_position
            ),
            "case_1_confirmed_within_frozen_C": (
                confirmation_source.position == c_last_source_pen_position
            ),
            "primary_top_available_at": candidate.available_at,
            "first_pen_strength": strength,
            "candidate_T_before_equal_extreme_resolution": candidate.endpoint,
            "latest_equal_extreme_in_frozen_B": endpoint,
        },
        trace=[
            "第一特征序列已形成候选顶分型 A-B-C；B.low 不要求高于 A.low",
            "A/B 使用闭区间判断为重叠；端点相等也算重叠",
            "走第一种情况：结构端点取冻结 B 内最后出现的同价极值；"
            "若 B 内同价极值重复，则使用最后同价来源笔的原始低边界；"
            "冻结 C 不足时继续等待后续向下笔越界，先创新高则取消旧 T",
            f"第一种情况确认时点为 order={confirmed_at.order}；"
            f"C 完整来源审计时点 available_at={candidate.available_at.order}",
        ],
        basis_ids=[
            "BASIS-003",
            "BASIS-004",
            "BASIS-005",
            "BASIS-006",
            "BASIS-022",
            "BASIS-030",
            "BASIS-031",
            "BASIS-032",
        ],
    )


# ============================================================================
# 6. 第二特征序列：第二种情况的核心
# ============================================================================


def collect_secondary_up_features(
    pens: Sequence[Pen],
    candidate: CandidateTop,
) -> tuple[list[FeatureElement], list[Pen]]:
    """
    候选顶 T 之后，反向下走势的特征序列取向上笔。
    这就是第二特征序列。
    """

    up_pens = [
        pen
        for pen in pens
        if pen.direction == Direction.UP and pen.start.order > candidate.endpoint.order + EPS
    ]
    return [pen_to_feature(pen) for pen in up_pens], up_pens


def find_new_bottom_created_by_latest_element(
    standardized: Sequence[FeatureElement],
    latest_raw_pen_position: int,
) -> Optional[tuple[FeatureElement, FeatureElement, FeatureElement]]:
    """
    找到由“当前新增原始元素”首次完成的第二特征序列底分型。

    C2 固定为含当前来源笔的最后标准元素，B2 固定为 C2 的左邻元素；
    A2 则从 B2 之前的全部标准元素中选择 high 最高者，EPS 内并列取
    时间较晚者。A2 不是 B2 的左邻元素。

    当前原始元素若触发包含，它只能降低或保持被合并右侧元素的 high/low，
    不可能在一个尚无底分型的活动前缀中首次制造右侧同时抬高的 C2；
    因此这种前缀明确返回 None。
    """

    if len(standardized) < 3:
        return None

    c2 = standardized[-1]
    assert latest_raw_pen_position in c2.source_pen_positions, (
        "当前前缀的最新原始笔必须归属于最后一个标准元素"
    )
    if c2.source_pen_positions != (latest_raw_pen_position,):
        return None

    b2_index = len(standardized) - 2
    b2 = standardized[b2_index]
    a2 = select_secondary_A_before_B(
        standardized,
        b2_index=b2_index,
    )
    if a2 is not None and is_secondary_bottom_fractal(a2, b2, c2):
        return a2, b2, c2
    return None


def select_secondary_A_before_B(
    standardized: Sequence[FeatureElement],
    b2_index: int,
) -> Optional[FeatureElement]:
    """
    从 B2 之前全部第二特征序列标准元素中选择 high 最高的 A2。

    先取得数值上的全局最高 high，再在与该最高值相差不超过 EPS 的
    元素中选择时间最晚者，避免逐项“近似相等”造成链式漂移。
    """

    assert 0 <= b2_index <= len(standardized)
    if b2_index == 0:
        return None
    left = standardized[:b2_index]
    global_high = max(element.high for element in left)
    eligible = [
        element
        for element in left
        if global_high - element.high <= EPS
    ]
    assert eligible
    return eligible[-1]


def find_standardized_element_containing_source_pen(
    standardized: Sequence[FeatureElement],
    source_pen_position: int,
) -> FeatureElement:
    """
    返回标准化后唯一包含指定原始来源笔的标准元素。

    当前前缀的最新原始元素在包含合并、向左回溯后仍应归属于最后一个
    标准元素。显式核对来源编号，可以避免把“当前原始笔”误当成
    “包含处理后的当前标准元素”。
    """

    matching_indices = [
        index
        for index, element in enumerate(standardized)
        if source_pen_position in element.source_pen_positions
    ]
    assert len(matching_indices) == 1, "每根原始来源笔必须且只能归属一个标准元素"
    matching_index = matching_indices[0]
    assert matching_index == len(standardized) - 1, (
        "当前前缀的最新原始笔在标准化后必须归属于最后一个标准元素"
    )
    return standardized[matching_index]


def standardized_feature_endpoint_price(
    element: FeatureElement,
    feature_pen_direction: Direction,
) -> float:
    """取得包含合并后的特征元素在其笔方向上的终点价格。"""

    if feature_pen_direction == Direction.UP:
        return element.high
    assert feature_pen_direction == Direction.DOWN
    return element.low


def analyze_secondary_prefix(
    raw_prefix: Sequence[FeatureElement],
    latest_pen: Pen,
) -> SecondaryPrefixSnapshot:
    """
    对一个第二特征序列原始前缀执行一次完整、不可拆分的分析。

    返回值保证标准化结果、承载最新笔的标准元素、合并后终点和底分型
    来自同一个前缀。调用者另外读取原始笔终点时，只能用于识别本规则
    明确定义的“原始创新高 U”事件，不能把它冒充标准元素端点。
    """

    assert raw_prefix, "第二特征序列前缀不能为空"
    standardized = standardize_feature_sequence(
        raw_prefix,
        feature_pen_direction=Direction.UP,
    )
    current_element = find_standardized_element_containing_source_pen(
        standardized,
        latest_pen.position,
    )
    return SecondaryPrefixSnapshot(
        raw_prefix=tuple(raw_prefix),
        latest_pen=latest_pen,
        standardized=standardized,
        current_element=current_element,
        current_endpoint_price=standardized_feature_endpoint_price(
            current_element,
            feature_pen_direction=Direction.UP,
        ),
        completed_bottom=find_new_bottom_created_by_latest_element(
            standardized,
            latest_pen.position,
        ),
    )


def _confirm_from_secondary_bottom(
    candidate: CandidateTop,
    snapshot: SecondaryPrefixSnapshot,
    trace: Sequence[str],
    innovation_wait: Optional[SecondaryInnovationWait] = None,
) -> CandidateDecision:
    """用当前快照中的底分型构造第二种情况确认结果。"""

    assert snapshot.completed_bottom is not None
    a2, b2, c2 = snapshot.completed_bottom
    assert c2.source_pen_positions == (snapshot.latest_pen.position,), (
        "由当前笔首次完成底分型时，当前笔必须作为独立 C2；"
        "触发包含的当前笔不可能首次完成底分型"
    )
    endpoint_above_t = snapshot.endpoint_strictly_above(candidate.endpoint.price)
    raw_endpoint_above_t = (
        snapshot.latest_pen.end.price > candidate.endpoint.price + EPS
    )
    return CandidateDecision(
        status=CandidateStatus.CONFIRMED,
        endpoint=candidate.endpoint,
        confirmed_at=event_not_before(
            snapshot.latest_pen.end,
            candidate.available_at,
            label="case_2_bottom_and_primary_top_ready",
        ),
        case_name=EndCase.CASE_2_WITH_GAP,
        proof={
            "A": candidate.A,
            "B": candidate.B,
            "C": candidate.C,
            "A_B_gap": True,
            "primary_top_available_at": candidate.available_at,
            "secondary_bottom": (a2, b2, c2),
            "secondary_standardized_prefix": snapshot.standardized,
            "latest_raw_up_pen": snapshot.latest_pen,
            "latest_standardized_up_element": snapshot.current_element,
            "latest_standardized_up_endpoint_price": (
                snapshot.current_endpoint_price
            ),
            "latest_standardized_up_endpoint_above_t": endpoint_above_t,
            "latest_raw_up_endpoint_above_t": raw_endpoint_above_t,
            "latest_raw_up_pen_triggered_containment": False,
            "secondary_A2_selected_by_global_high_before_B2": True,
            "bottom_precedes_same_event_raw_new_high": True,
            "confirmed_after_raw_innovation_wait": innovation_wait is not None,
            "raw_innovation_wait": innovation_wait,
            "gap_need_not_be_closed": True,
        },
        trace=list(trace),
        basis_ids=[
            "BASIS-003",
            "BASIS-004",
            "BASIS-005",
            "BASIS-006",
            "BASIS-009",
            "BASIS-010",
            "BASIS-016",
            "BASIS-026",
            "BASIS-027",
            "BASIS-032",
        ],
    )


def _reject_from_secondary_raw_innovation(
    candidate: CandidateTop,
    triggering_pen: Pen,
    trigger_snapshot: SecondaryPrefixSnapshot,
    decided_at_pen: Pen,
    reason: str,
    trace: Sequence[str],
    left_anchor_A2: Optional[FeatureElement] = None,
    qualified_B2: Optional[FeatureElement] = None,
) -> CandidateDecision:
    """结束原始创新高分支，并把最初创新高笔 U.end 设为 T'。"""

    assert trigger_snapshot.completed_bottom is None
    assert triggering_pen.direction == Direction.UP
    assert triggering_pen.end.price > candidate.endpoint.price + EPS
    assert decided_at_pen.end.order + EPS >= triggering_pen.end.order
    assert reason in {
        "NO_A2_BEFORE_B2",
        "B2_NOT_QUALIFIED",
        "LATER_RAW_START_BELOW_U_START",
    }
    successor_candidate_t = Point(
        order=triggering_pen.end.order,
        price=triggering_pen.end.price,
        label="ordinary_case_2_new_high_successor_T",
    )
    return CandidateDecision(
        status=CandidateStatus.REJECTED,
        endpoint=candidate.endpoint,
        confirmed_at=decided_at_pen.end,
        case_name=EndCase.CASE_2_WITH_GAP,
        successor_candidate_t=successor_candidate_t,
        proof={
            "raw_innovation_triggering_pen": triggering_pen,
            "raw_innovation_trigger_snapshot": trigger_snapshot,
            "raw_innovation_U_start": triggering_pen.start.price,
            "raw_innovation_U_end": triggering_pen.end.price,
            "innovation_left_anchor_A2": left_anchor_A2,
            "innovation_qualified_B2": qualified_B2,
            "innovation_exit_reason": reason,
            "innovation_decided_at_pen": decided_at_pen,
            "candidate_price": candidate.endpoint.price,
            "successor_candidate_T_prime": successor_candidate_t,
            "no_bottom_created_by_triggering_pen": True,
            "fixed_U_start_guard_only_in_raw_innovation_branch": True,
        },
        trace=list(trace) + [
            "旧 T 被原始创新高分支否定；接续候选固定取最初创新高笔 "
            "U 的真实终点，不取后来触发退出的笔终点",
        ],
        basis_ids=[
            "BASIS-005",
            "BASIS-006",
            "BASIS-009",
            "BASIS-016",
            "BASIS-023",
            "BASIS-026",
            "BASIS-027",
            "BASIS-032",
        ],
    )


def bottom_rule_5_vs_7(price_5: float, price_7: float) -> bool:
    """
    只用于附件/课程订正图的固定几何：
        5 > 7  -> 底分型保留；
        5 = 7  -> 发生包含，底分型不成立；
        5 < 7  -> 底分型不成立。

    一般数据仍应调用完整的包含标准化与 is_secondary_bottom_fractal()。
    """

    return price_5 > price_7 + EPS


def judge_secondary_sequence(
    candidate: CandidateTop,
    pens: Sequence[Pen],
) -> CandidateDecision:
    """
    第二种情况状态机。

    每新增一根第二特征序列的原始向上笔，严格按以下顺序：
        1. 若已经处于原始创新高 U 的等待状态，先比较当前原始笔起点与
           固定的 U.start；严格更低时直接结束等待并延续到 U.end；
        2. 记录当前原始笔终点是否严格高于候选 T；
        3. 对截至当前笔的完整前缀执行低低包含标准化；
        4. B2 固定为 C2 左邻，A2 从 B2 之前全部标准元素中取 high
           最高者；若当前笔作为独立 C2 完成严格底分型，立即确认 T；
        5. 同一根原始笔既创新高又完成底分型时，仍以底分型确认优先；
        6. 当前原始笔未创新高时，只继续普通包含和底分型流程，不使用
           U.start 固定下界；
        7. 当前原始笔 U 创新高但没有完成底分型时，把承载 U 的尾元素
           视为待审 B*：若其左侧没有 A2，或最高 A2.high 不高于 B*.high，
           旧段立即延续到 U.end；
        8. 若 A2.high>B*.high，则活动前缀不变量同时保证
           A2.low>B*.low；固定 U.start 并等待独立 C2；
        9. 等待中，后来原始笔若被 B* 包含就继续；起点严格低于
           U.start 时延续到原始 U.end；起点严格高于 U.start 且形成
           独立 C2 时，C2.high>B*.high 自动成立，确认旧 T。

    固定 U.start 的约束只存在于第 7 至第 9 步的原始创新高分支。
    普通未创新高前缀即使起点持续降低，也不得据此否定 T。

    第二特征序列底分型不必回补 A/B 的原缺口。
    """

    raw_secondary, up_pens = collect_secondary_up_features(pens, candidate)
    trace: list[str] = [
        "A/B 有特征序列缺口，进入第二种情况",
        "第二特征序列取候选顶之后的向上笔",
        "第二特征序列 A2 取 B2 前全部标准元素中 high 最高者，"
        "不固定为 B2 左邻",
        "每根原始向上笔记录真实终点是否创新高，再完成当前前缀的"
        "低低包含和底分型检查；同事件底分型优先",
    ]
    innovation_wait: Optional[SecondaryInnovationWait] = None

    # prefix_length 表示按真实时间逐个接收未来元素，即使我们离线拥有全部数据，
    # 也保持这个顺序，才能得到“最早确认时点”。
    for prefix_length in range(1, len(raw_secondary) + 1):
        raw_prefix = raw_secondary[:prefix_length]
        latest_pen = up_pens[prefix_length - 1]

        # 原始创新高 U 已取得 B* 资格后，所有后来原始向上笔先接受
        # 固定 U.start 下界检查。该检查不属于普通未创新高流程。
        if innovation_wait is not None:
            assert latest_pen.position != innovation_wait.triggering_pen.position
            if (
                latest_pen.start.price
                < innovation_wait.fixed_start_price - EPS
            ):
                trace.append(
                    f"到第 {latest_pen.position} 笔：处于原始创新高等待状态；"
                    f"当前起点 {latest_pen.start.price} 严格低于固定 "
                    f"U.start={innovation_wait.fixed_start_price}"
                )
                return _reject_from_secondary_raw_innovation(
                    candidate,
                    triggering_pen=innovation_wait.triggering_pen,
                    trigger_snapshot=innovation_wait.trigger_snapshot,
                    decided_at_pen=latest_pen,
                    reason="LATER_RAW_START_BELOW_U_START",
                    trace=trace,
                    left_anchor_A2=innovation_wait.left_anchor_A2,
                    qualified_B2=innovation_wait.qualified_B2,
                )

        raw_endpoint_above_t = (
            latest_pen.end.price > candidate.endpoint.price + EPS
        )

        # 顺序一：用一个快照完成当前全前缀的包含、来源定位、合并后
        # 终点和底分型分析。后续分支只读取这一份快照。
        snapshot = analyze_secondary_prefix(
            raw_prefix,
            latest_pen,
        )
        # 包含标准化后先检查底分型。当前原始笔若既以真实终点创新高、
        # 又作为独立 C2 完成底分型，仍以底分型结论优先确认旧 T。
        bottom = snapshot.completed_bottom
        if bottom is not None:
            trace.append(
                f"到第 {latest_pen.position} 笔：包含处理后形成严格底分型；"
                "A2 已从 B2 前全部标准元素中按最高 high 选定"
            )
            if raw_endpoint_above_t:
                trace.append(
                    "当前原始向上笔终点虽严格高于 T，但它同时作为独立 "
                    "C2 完成底分型；"
                    "按同事件优先级先采用底分型，确认旧候选顶 T"
                )
            elif innovation_wait is not None:
                trace.append(
                    "原始创新高 U 的等待状态取得独立 C2，确认旧候选顶 T"
                )
            else:
                trace.append("当前笔完成底分型，确认旧候选顶 T")
            return _confirm_from_secondary_bottom(
                candidate,
                snapshot,
                trace,
                innovation_wait=innovation_wait,
            )

        if innovation_wait is not None:
            if (
                innovation_wait.triggering_pen.position
                in snapshot.current_element.source_pen_positions
            ):
                relation = (
                    "与固定 U.start 近似相等"
                    if almost_equal(
                        latest_pen.start.price,
                        innovation_wait.fixed_start_price,
                    )
                    else "严格高于固定 U.start、但仍被 B* 包含"
                )
                trace.append(
                    f"到第 {latest_pen.position} 笔：{relation}；"
                    "尚无独立 C2，继续保持原始创新高等待状态"
                )
                continue

            # 当前来源不再与 U 同属尾元素，说明它已经成为独立 C2。
            # 由于起点下破已在标准化前拦截，而 A2/B* 资格已经固定，
            # 此时四项底分型比较必须自动成立，并已在上方返回确认。
            raise AssertionError(
                "原始创新高等待状态出现独立 C2 却未形成严格底分型；"
                "这违反 A2/B* 资格和固定 U.start 不变量"
            )

        # 未处于等待状态时，只有原始终点严格高于 T 才进入创新高分支。
        # 未创新高的笔无论起点怎样变化，都只继续普通底分型流程。
        if not raw_endpoint_above_t:
            trace.append(
                f"到第 {latest_pen.position} 笔：当前原始终点未严格高于 T；"
                "包含后也未形成底分型，按普通流程继续"
            )
            continue

        # 当前原始向上笔 U 已创新高，但同事件没有完成底分型。包含
        # 标准化后的尾元素作为待审 B*；A2 从 B* 左侧全体标准元素中选。
        b2_index = len(snapshot.standardized) - 1
        b2 = snapshot.current_element
        a2 = select_secondary_A_before_B(
            snapshot.standardized,
            b2_index=b2_index,
        )
        if a2 is None:
            trace.append(
                f"到第 {latest_pen.position} 笔：原始终点 "
                f"{latest_pen.end.price} 严格高于 T={candidate.endpoint.price}；"
                "包含处理后 B* 左侧没有任何标准元素可作为 A2"
            )
            return _reject_from_secondary_raw_innovation(
                candidate,
                triggering_pen=latest_pen,
                trigger_snapshot=snapshot,
                decided_at_pen=latest_pen,
                reason="NO_A2_BEFORE_B2",
                trace=trace,
            )

        if not (a2.high > b2.high + EPS):
            trace.append(
                f"到第 {latest_pen.position} 笔：原始创新高 U 包含处理后"
                f"得到 B*=[{b2.low}, {b2.high}]；最高 A2.high={a2.high} "
                "未严格高于 B*.high，B* 无底分型中间元素资格"
            )
            return _reject_from_secondary_raw_innovation(
                candidate,
                triggering_pen=latest_pen,
                trigger_snapshot=snapshot,
                decided_at_pen=latest_pen,
                reason="B2_NOT_QUALIFIED",
                trace=trace,
                left_anchor_A2=a2,
                qualified_B2=None,
            )

        assert a2.low > b2.low + EPS, (
            "活动第二特征序列在首次原始创新高前没有更早底分型；"
            "因此最高 A2.high 严格高于 B*.high 时，A2.low 也必须"
            "严格高于 B*.low"
        )
        innovation_wait = SecondaryInnovationWait(
            triggering_pen=latest_pen,
            trigger_snapshot=snapshot,
            left_anchor_A2=a2,
            qualified_B2=b2,
        )
        trace.append(
            f"到第 {latest_pen.position} 笔：原始终点 "
            f"{latest_pen.end.price} 严格高于 T；包含后的 B*="
            f"[{b2.low}, {b2.high}] 已取得 A2/B* 左侧资格；"
            f"固定 U.start={latest_pen.start.price}，等待独立 C2"
        )

    if len(raw_secondary) < 3:
        trace.append(
            "数据结束时第二特征序列不足三根；已逐根检查原始创新高、"
            "低低包含、A2 选择和底分型资格"
        )

    if innovation_wait is not None:
        trace.append(
            "数据结束时仍处于原始创新高等待状态：没有后来原始起点"
            "严格低于固定 U.start，也没有形成独立 C2"
        )

    return CandidateDecision(
        status=CandidateStatus.PENDING,
        endpoint=candidate.endpoint,
        trace=trace + ["完整输入数据结束，当前候选仍未确认也未否定"],
        proof={
            "secondary_innovation_wait": innovation_wait,
            "fixed_U_start_guard_only_in_raw_innovation_branch": True,
        },
        basis_ids=[
            "BASIS-005",
            "BASIS-006",
            "BASIS-009",
            "BASIS-016",
            "BASIS-026",
            "BASIS-027",
            "BASIS-032",
        ],
    )


# ============================================================================
# 7. 特殊情况：第一反向笔 + 走势 A + 隔离的反应走势
# ============================================================================


def first_reverse_pen_breaks_up_segment(
    candidate: CandidateTop | HypotheticalTurn,
    pens: Sequence[Pen],
) -> bool:
    """
    按原文公式检查从 T 下来的 q1 是否构成向上线段的“笔破坏”。

    向上线段点序列为 d1,g1,d2,g2,...。若存在 i、j，满足：
        j >= i + 2 且 d_j <= g_i
    则向上线段被笔破坏。

    在当前候选处，q1.end 就是新的 d_j；可比较的 g_i 是本段内、候选末笔
    之前至少一根向上笔的顶。等号也算破坏。

    标准第一/第二种情况仍以特征序列分型为最终依据；本函数只防止特殊规则
    被套到普通的小回撤上。等号在本函数中算笔破坏，但这不等于可以进入
    走势 A；特殊分支还会单独严格检查 B.low<A.high。
    """

    q1_position = candidate.first_reverse_pen_position
    if q1_position <= 0 or q1_position >= len(pens):
        return False
    q1 = pens[q1_position]
    candidate_last_up_pen = pens[q1_position - 1]
    if q1.direction != Direction.DOWN or candidate_last_up_pen.direction != Direction.UP:
        return False

    earlier_up_pens = [
        pen
        for pen in pens[candidate.segment_start_position : q1_position - 1]
        if pen.direction == Direction.UP
    ]
    return any(q1.low <= earlier_top.end.price + EPS for earlier_top in earlier_up_pens)


def find_trend_a_pattern(
    candidate: CandidateTop | HypotheticalTurn,
    pens: Sequence[Pen],
) -> Optional[TrendAPattern]:
    """
    寻找最基本的“q1 + 走势 A + 反应走势”骨架。

    q1/B：从候选顶开始的第一根向下笔；
    走势 A 最小种子：只能由紧随 q1 的三根原始笔组成，编号必须逐一连续，
        中间不得跳过任何笔；方向为上/下/上，三笔有公共重叠，第三笔
        必须相对第一笔创新高，但三笔整体不得越过外层候选 T0；
    reactions：q1 之后的全部未来笔，包含走势 A 的最小三笔种子。

    除通用“笔破坏”外，还必须先从左侧标准元素中取得
    A.high<=T.price+EPS 的候选作用域 A，再严格满足 q1.low < A.high。
    没有合格 A 时不能进入走势 A；等号只够满足 d_j<=g_i 的通用笔破坏，
    不足以进入走势 A 特殊分支。

    这里只提取 A 的最小三笔种子。第三根种子笔的终点是走势 A
    特殊状态的进入时点。进入后，把 q1 之后的完整后缀隔离成一条新的
    向上线段输入，从第一根种子笔开始重新运行普通完整流程。

    三笔种子之后不再用专用规则扩展 A；更长结构统一由隔离反应走势内
    的完整候选、包含和分支规则决定。
    """

    if candidate.A is None:
        return None
    left_A = candidate.A

    # BASIS-033 USER_REQUIREMENT：只检查当前共享起点至 T 的原始笔。
    # 恰好三笔仍用严格初始化；更长前缀接受首笔突破或任意局部三笔推进。
    # 后者允许第二笔不新低，第三笔只需突破局部第一笔，而非全段首笔。
    q1_position = candidate.first_reverse_pen_position
    if q1_position < 0 or q1_position + 3 >= len(pens):
        return None
    segment_start_position = candidate.segment_start_position
    pre_t_pen_count = q1_position - segment_start_position
    if segment_start_position < 0 or pre_t_pen_count < 3 or pre_t_pen_count % 2 != 1:
        return None
    pre_t_seed = tuple(pens[q1_position - 3:q1_position])
    if not points_equal(pens[q1_position - 1].end, candidate.endpoint):
        return None
    first_pen = pens[segment_start_position]
    breakthrough_pen: Optional[Pen] = None
    progress_seed: Optional[tuple[Pen, Pen, Pen]] = None
    if pre_t_pen_count == 3:
        if not _three_pens_meet_strict_initial_progress(
            pens, segment_start_position, Direction.UP,
        ):
            return None
        qualification_mode = "strict_three_pens"
        breakthrough_pen = pre_t_seed[-1]
        progress_seed = pre_t_seed
    else:
        breakthrough_pen = next((
            pen for pen in pens[segment_start_position + 2:q1_position]
            if pen.direction == Direction.UP and pen.high > first_pen.high + EPS
        ), None)
        progress_start = next((
            start for start in range(segment_start_position, q1_position - 2, 2)
            if _three_pens_define_segment(pens, start, Direction.UP)
            and pens[start + 1].end.price >= pens[start].start.price - EPS
            and pens[start + 2].end.price > pens[start].end.price + EPS
        ), None)
        if progress_start is not None:
            progress_seed = tuple(pens[progress_start:progress_start + 3])
        if breakthrough_pen is None and progress_seed is None:
            return None
        qualification_mode = (
            "first_pen_breakthrough_and_local_three_pen_progress"
            if breakthrough_pen is not None and progress_seed is not None else
            "first_pen_breakthrough" if breakthrough_pen is not None else
            "local_three_pen_progress"
        )

    if not first_reverse_pen_breaks_up_segment(candidate, pens):
        return None

    # 直接按完整笔流下标截取 B 及其后三笔，绝不通过方向筛选或来源编号
    # 搜索来“凑”种子。随后再核对 position 连续性，使本内部入口即使被
    # 绕过顶层 validate_pen_stream() 调用，也不会接受中间缺笔的结构。
    q1_and_seed = list(pens[q1_position : q1_position + 4])
    q1 = q1_and_seed[0]
    a_seed = q1_and_seed[1:]
    expected_positions = tuple(range(q1_position, q1_position + 4))
    actual_positions = tuple(pen.position for pen in q1_and_seed)
    if actual_positions != expected_positions:
        return None
    if q1.direction != Direction.DOWN:
        return None
    if q1.low >= left_A.high - EPS:
        return None
    if [pen.direction for pen in a_seed] != [
        Direction.UP,
        Direction.DOWN,
        Direction.UP,
    ]:
        return None
    if not common_overlap(a_seed):
        return None
    if a_seed[2].high <= a_seed[0].high + EPS:
        return None
    if max(pen.high for pen in a_seed) > candidate.endpoint.price + EPS:
        return None
    if min(pen.low for pen in a_seed) < q1.low - EPS:
        return None

    return TrendAPattern(
        q1=q1,
        left_primary_A=left_A,
        pre_t_seed=pre_t_seed,
        pre_t_pen_count=pre_t_pen_count,
        pre_t_first_pen=first_pen,
        pre_t_breakthrough_pen=breakthrough_pen,
        pre_t_progress_seed=progress_seed,
        pre_t_qualification_mode=qualification_mode,
        a_pens=tuple(a_seed),
        reactions=tuple(pens[q1_position + 1 :]),
        q1_end_price=q1.end.price,
    )


def trend_a_entry_proof(pattern: TrendAPattern) -> dict[str, Any]:
    """生成所有走势 A 终局路径共用的进入资格证书。"""

    seed_positions = tuple(pen.position for pen in pattern.a_pens)
    expected_seed_positions = tuple(
        range(pattern.q1.position + 1, pattern.q1.position + 4)
    )
    return {
        "q1": pattern.q1,
        "trend_A_left_primary_A": pattern.left_primary_A,
        "trend_A_pre_T_pen_count": pattern.pre_t_pen_count,
        "trend_A_pre_T_first_pen": pattern.pre_t_first_pen,
        "trend_A_pre_T_qualification_mode": pattern.pre_t_qualification_mode,
        "trend_A_pre_T_breakthrough_pen": pattern.pre_t_breakthrough_pen,
        "trend_A_pre_T_progress_seed": pattern.pre_t_progress_seed,
        "trend_A_pre_T_qualified": True,
        "trend_A_pre_T_tail_strict_required": pattern.pre_t_pen_count == 3,
        "trend_A_pre_T_seed": pattern.pre_t_seed,
        "trend_A_pre_T_seed_positions": tuple(
            pen.position for pen in pattern.pre_t_seed
        ),
        "trend_A_pre_T_seed_ends_at_T": points_equal(
            pattern.pre_t_seed[-1].end,
            pattern.q1.start,
        ),
        "trend_A_pre_T_seed_bottom_advances": (
            pattern.pre_t_seed[1].end.price
            > pattern.pre_t_seed[0].start.price + EPS
        ),
        "trend_A_pre_T_seed_top_advances": (
            pattern.pre_t_seed[2].end.price
            > pattern.pre_t_seed[0].end.price + EPS
        ),
        "trend_A_B.low<A.high": (
            pattern.q1.low < pattern.left_primary_A.high - EPS
        ),
        "trend_A_seed_pen_positions": seed_positions,
        "trend_A_seed_immediately_after_B": (
            seed_positions == expected_seed_positions
        ),
        "trend_A_pens": pattern.a_pens,
        "trend_A_entered_at": pattern.entered_at,
        "reaction_includes_trend_A_seed": True,
    }


def confirming_case_of_isolated_reaction_prefix(
    reaction_prefix: Sequence[Pen],
) -> Optional[EndCase]:
    """
    隔离反应走势当前前缀，并用完整向上线段引擎判断其内层线段是否已结束。

    反应走势从走势 A 的第一根向上种子笔开始，因此最初三笔应为上/下/上。
    每次只把截至当前原始笔的前缀送入引擎，不能偷看当前笔之后的数据。

    内层引擎可以使用第一种、第二种及走势 A 特殊情况；特殊情况还可继续
    递归嵌套。内层候选 T1 以及更深层候选只存在于本函数这次调用的局部
    作用域。本函数只把“内层整条向上线段是否已确认结束”及其规则名称
    带回外层，不返回 T1、内层证据、内层候选列表或内层后续线段状态。
    """

    if len(reaction_prefix) < 3:
        return None

    # 复制并从 0 重新编号，使内层流程在数据和编号上都与外层隔离。
    # Point.order 保留原时间轴，以便内外层仍能核查真实事件先后。
    isolated_pens = [
        replace(pen, position=local_position)
        for local_position, pen in enumerate(reaction_prefix)
    ]
    inner_result = _judge_canonical_up_segment(isolated_pens, start_pos=0)
    if inner_result.status == SegmentStatus.CONFIRMED:
        assert inner_result.case_name is not None
        return inner_result.case_name
    return None


def _confirm_outer_t0_from_inner_segment(
    candidate: CandidateTop | HypotheticalTurn,
    pattern: TrendAPattern,
    pen: Pen,
    observed_reactions: Sequence[Pen],
    inner_confirming_case: EndCase,
    trace: Sequence[str],
    *,
    same_event_case_2_precedes_outer_up_guard: bool = False,
) -> CandidateDecision:
    """以内层整段确认事件构造外层 T0 的特殊分支确认结果。"""

    priority_trace: list[str]
    basis_ids = ["BASIS-008", "BASIS-014", "BASIS-021", "BASIS-033"]
    if same_event_case_2_precedes_outer_up_guard:
        assert pen.direction == Direction.UP
        assert pen.end.price > candidate.endpoint.price + EPS
        assert inner_confirming_case == EndCase.CASE_2_WITH_GAP
        priority_trace = [
            f"第 {pen.position} 笔向上，终点 {pen.end.price} 严格高于 "
            f"外层 T0={candidate.endpoint.price}；同一笔也完成了内层第二种情况的底分型",
            "按同事件优先级，先采用内层底分型证书确认外层 T0；"
            "不再执行本笔的外层创新高否定",
        ]
        basis_ids.append("BASIS-019")
    else:
        priority_trace = [
            f"截至第 {pen.position} 笔，隔离反应走势内的完整向上线段"
            f"已由“{inner_confirming_case.value}”确认结束",
        ]

    return CandidateDecision(
        status=CandidateStatus.CONFIRMED,
        endpoint=candidate.endpoint,
        confirmed_at=pen.end,
        case_name=EndCase.TREND_A,
        proof={
            **trend_a_entry_proof(pattern),
            "reaction_prefix_pen_positions": tuple(
                reaction_pen.position
                for reaction_pen in observed_reactions
            ),
            "inner_segment_confirming_case": inner_confirming_case,
            "same_event_case_2_precedes_outer_up_guard": (
                same_event_case_2_precedes_outer_up_guard
            ),
            "outer_up_guard_would_trigger": (
                same_event_case_2_precedes_outer_up_guard
            ),
            "trigger_pen": pen,
            "outer_T0_price": candidate.endpoint.price,
            "q1_end_price": pattern.q1_end_price,
            "temporary_inner_candidates_released": True,
        },
        trace=list(trace)
        + priority_trace
        + [
            "内层线段结束即确认外层 T0；外层确认时点取当前原始笔终点",
            "内层 T1 未写入外层结果；返回时释放反应分支及全部临时内层候选",
        ],
        basis_ids=basis_ids,
    )


def judge_trend_a_special(
    candidate: CandidateTop | HypotheticalTurn,
    pens: Sequence[Pen],
) -> CandidateDecision:
    """
    走势 A 特殊分支的逐笔决策顺序。

    反应走势是 q1 之后的全部笔，走势 A 的三笔种子也包含在内。对每根
    反应笔执行以下顺序：

    1. 若向上笔终点严格高于 T0，先用截至本笔的隔离前缀检查一个唯一例外：
       本笔是否同时让内层第二种情况完成底分型；
    2. 若该底分型完成，先确认 T0，不再执行本笔的外层创新高否定；
     3. 若该底分型未完成，立即按向上笔外层守卫否定 T0，并把触发守卫的
        真实向上笔终点记录为新的 T'；T0 与 T' 之间的候选仍按时间顺序检查；
    4. 向下笔终点严格低于 q1 终点时，立即按向下笔外层守卫确认 T0；
    5. 未产生上述终局结果时，把含当前笔的反应前缀交给隔离的完整
       向上线段引擎；
    6. 只有内层整条向上线段已确认结束，才确认 T0；内层某个临时候选
       被否定并不等于内层线段结束，也不直接决定 T0；
    7. 外层确认或否定时点一律取触发结论的当前原始笔终点。

    内层使用与外层相同的第一种、第二种和走势 A 规则，所以允许嵌套。
    T1 仅是特殊分支内部的局部候选。外层一旦确认或否定便立即 return，
    相当于释放反应分支及 T1；任何更晚笔都不得再改变 T0。
    """

    pattern = find_trend_a_pattern(candidate, pens)
    if pattern is None:
        return CandidateDecision(status=CandidateStatus.NOT_APPLICABLE)

    reactions = list(pattern.reactions)
    trace = [
        f"走势 A 最小三笔种子在 order={pattern.entered_at.order} 完成",
        f"第一反向笔 B.low={pattern.q1.low} 严格低于左侧标准元素 "
        f"A.high={pattern.left_primary_A.high}；三笔种子编号紧邻 B",
        "本候选已进入走势 A 特殊状态；反应走势从 q1 后第一笔开始，包含走势 A 三笔种子",
        "反应向上笔越过 T0 时，先核查同一笔是否完成内层第二种情况底分型；"
        "底分型成立则确认 T0，否则才执行外层创新高否定",
    ]

    for reaction_count, pen in enumerate(reactions, start=1):
        observed_reactions = reactions[:reaction_count]

        # 外层向上守卫有一个唯一的同事件例外：截至当前笔的内层完整
        # 前缀若正好由第二种情况底分型确认结束，则先用该证书确认 T0。
        # 只有没有形成这一底分型证书时，才按原始向上笔终点越过 T0
        # 的外层守卫否定 T0。其他内层规则不具有这项优先权。
        if pen.direction == Direction.UP and pen.end.price > candidate.endpoint.price + EPS:
            same_event_inner_case = confirming_case_of_isolated_reaction_prefix(
                observed_reactions
            )
            if same_event_inner_case == EndCase.CASE_2_WITH_GAP:
                return _confirm_outer_t0_from_inner_segment(
                    candidate,
                    pattern,
                    pen,
                    observed_reactions,
                    same_event_inner_case,
                    trace,
                    same_event_case_2_precedes_outer_up_guard=True,
                )

            return CandidateDecision(
                status=CandidateStatus.REJECTED,
                endpoint=candidate.endpoint,
                confirmed_at=pen.end,
                case_name=EndCase.TREND_A,
                successor_candidate_t=pen.end,
                proof={
                    **trend_a_entry_proof(pattern),
                    "outer_guard": "UP_END_ABOVE_T0",
                    "trigger_pen": pen,
                    "outer_T0_price": candidate.endpoint.price,
                    "successor_candidate_T_prime": pen.end,
                    "q1_end_price": pattern.q1_end_price,
                    "same_event_case_2_priority_checked": True,
                    "same_event_inner_confirming_case": same_event_inner_case,
                    "temporary_inner_candidates_released": True,
                },
                trace=trace + [
                    f"第 {pen.position} 笔向上，终点 {pen.end.price} 严格高于 "
                    f"外层 T0={candidate.endpoint.price}",
                     "截至当前笔的隔离前缀没有完成内层第二种情况底分型；"
                     "按外层创新高守卫否定 T0；该向上笔终点记录为新的 T'",
                     "T0 与 T' 之间的候选不跳过，继续按时间顺序执行完整固定流程",
                    "释放反应分支及全部临时内层候选",
                ],
                basis_ids=[
                    "BASIS-008",
                    "BASIS-014",
                    "BASIS-019",
                    "BASIS-021",
                    "BASIS-025",
                    "BASIS-033",
                ],
            )

        # 外层守卫二：向下笔只比较它的终点与 q1 终点，使用严格小于。
        # 确认时点就是当前原始笔终点，不估算笔内穿越时刻。
        if pen.direction == Direction.DOWN and pen.end.price < pattern.q1_end_price - EPS:
            return CandidateDecision(
                status=CandidateStatus.CONFIRMED,
                endpoint=candidate.endpoint,
                confirmed_at=pen.end,
                case_name=EndCase.TREND_A,
                proof={
                    **trend_a_entry_proof(pattern),
                    "outer_guard": "DOWN_END_BELOW_Q1_END",
                    "trigger_pen": pen,
                    "outer_T0_price": candidate.endpoint.price,
                    "q1_end_price": pattern.q1_end_price,
                    "temporary_inner_candidates_released": True,
                },
                trace=trace + [
                    f"第 {pen.position} 笔向下，终点 {pen.end.price} 严格低于 "
                    f"q1 终点 {pattern.q1_end_price}",
                    "在当前笔进入内层流程前立即确认 T0；释放反应分支及全部临时内层候选",
                ],
                basis_ids=[
                    "BASIS-008",
                    "BASIS-014",
                    "BASIS-021",
                    "BASIS-033",
                ],
            )

        # 未产生本层终局结果时，才按通常路径判断隔离内层前缀。
        # helper 只返回内层整条向上线段的确认规则，不把临时 T1 带回外层。
        inner_confirming_case = confirming_case_of_isolated_reaction_prefix(
            observed_reactions
        )
        if inner_confirming_case is not None:
            return _confirm_outer_t0_from_inner_segment(
                candidate,
                pattern,
                pen,
                observed_reactions,
                inner_confirming_case,
                trace,
            )

        trace.append(
            f"检查到第 {pen.position} 笔：外层两条守卫均未触发，"
            "内层完整向上线段也尚未确认结束，继续"
        )

    return CandidateDecision(
        status=CandidateStatus.PENDING,
        endpoint=candidate.endpoint,
        case_name=EndCase.TREND_A,
        proof={
            **trend_a_entry_proof(pattern),
            "reaction_pen_positions": tuple(pen.position for pen in reactions),
            "outer_T0_price": candidate.endpoint.price,
            "q1_end_price": pattern.q1_end_price,
            "temporary_inner_candidates_not_exported": True,
        },
        trace=trace + [
            "未来数据结束；外层守卫均未触发，内层向上线段也尚未确认结束，T0 保持待定",
            "临时内层候选只属于当前待定的特殊分支，不进入外层候选序列或最终端点",
        ],
        basis_ids=["BASIS-008", "BASIS-014", "BASIS-021", "BASIS-033"],
    )


# ============================================================================
# 8. 单个候选点的统一判断顺序
# ============================================================================


def resolve_without_primary_top(
    turn: HypotheticalTurn,
    pens: Sequence[Pen],
    pre_top_new_high_pen: Optional[Pen],
) -> CandidateDecision:
    """
    统一解决“尚无标准 A-B-C 顶分型”的假设点。

    普通规则：第一特征序列标准候选成立前，后续向下特征笔的起点严格高于 T，
    取消旧 T 的普通候选资格。

    走势 A 例外：如果走势 A 已在该高起点之前或同时满足进入条件，
    候选已进入走势 A 特殊分支。此时不能再用 BASIS-013 的普通自动
    取消覆盖它。
    """

    trend_a_pattern = find_trend_a_pattern(turn, pens)
    if trend_a_pattern is not None and (
        pre_top_new_high_pen is None
        or trend_a_pattern.entered_at.order
        <= pre_top_new_high_pen.start.order + EPS
    ):
        decision = judge_trend_a_special(turn, pens)
        if "BASIS-015" not in decision.basis_ids:
            decision.basis_ids.append("BASIS-015")
        decision.trace.insert(
            0,
            "走势 A 在普通高起点取消之前或同时进入；"
            "后续只按走势 A 特殊规则处理",
        )
        return decision

    if pre_top_new_high_pen is None:
        return CandidateDecision(
            status=CandidateStatus.NOT_APPLICABLE,
            endpoint=turn.endpoint,
            proof={
                "trend_A_requires_immediate_seed_and_B.low<A.high": True,
                "trend_A_requires_pre_T_qualification": True,
                "trend_A_requires_strict_pre_T_seed": (
                    turn.first_reverse_pen_position - turn.segment_start_position == 3
                ),
            },
            trace=[
                "既无第一特征序列标准候选，也没有满足 BASIS-033 的 T 前分范围资格、"
                "T 后紧邻种子及 B.low<A.high 的合法走势 A 进入事件，"
                "也没有普通高起点取消事件"
            ],
            basis_ids=[
                "BASIS-008",
                "BASIS-013",
                "BASIS-015",
                "BASIS-021",
                "BASIS-032",
                "BASIS-033",
            ],
        )

    assert pre_top_new_high_pen.position > 0
    triggering_up_pen = pens[pre_top_new_high_pen.position - 1]
    assert triggering_up_pen.direction == Direction.UP
    assert points_equal(triggering_up_pen.end, pre_top_new_high_pen.start), (
        "第一特征序列候选成立前的高起点，必须是前一根真实向上笔的终点"
    )
    successor_candidate_t = Point(
        order=triggering_up_pen.end.order,
        price=triggering_up_pen.end.price,
        label="pre_primary_top_new_high_successor_T",
    )

    pre_top_rejection = CandidateDecision(
        status=CandidateStatus.REJECTED,
        endpoint=turn.endpoint,
        confirmed_at=pre_top_new_high_pen.start,
        successor_candidate_t=successor_candidate_t,
        proof={
            "pre_top_new_high_pen": pre_top_new_high_pen,
            "triggering_up_pen": triggering_up_pen,
            "trend_A_entry": (
                trend_a_pattern.entered_at if trend_a_pattern is not None else None
            ),
            "candidate_price": turn.endpoint.price,
            "new_high_price": pre_top_new_high_pen.start.price,
            "successor_candidate_T_prime": successor_candidate_t,
            "trend_A_requires_immediate_seed_and_B.low<A.high": True,
            "trend_A_requires_pre_T_qualification": True,
            "trend_A_requires_strict_pre_T_seed": (
                turn.first_reverse_pen_position - turn.segment_start_position == 3
            ),
        },
        trace=[
            f"第一特征序列 A-B-C 标准候选成立前，第 {pre_top_new_high_pen.position} 笔起点 "
            f"{pre_top_new_high_pen.start.price} 已严格高于 T={turn.endpoint.price}",
            "此前尚未出现满足 BASIS-033 的 T 前分范围资格、T 后紧邻种子及 "
            "B.low<A.high 的合法走势 A；"
            "旧 T 自动取消普通候选资格",
            "该更高起点就是前一根真实向上笔终点 T'；旧 T 作废后，"
            "T 与 T' 之间的候选仍按时间顺序逐个检查",
        ],
        basis_ids=[
            "BASIS-013",
            "BASIS-015",
            "BASIS-021",
            "BASIS-023",
            "BASIS-032",
            "BASIS-033",
        ],
    )

    pre_top_rejection.trace.insert(
        0,
        "普通高起点取消事件严格早于走势 A 进入事件",
    )
    return pre_top_rejection


def judge_one_candidate(
    candidate: CandidateTop,
    segment_start: Point,
    pens: Sequence[Pen],
) -> CandidateDecision:
    """一个候选顶从建立到确认/否定的完整流程。"""

    # A-B-C 的 available_at 是冻结 B/C 全部来源笔完整结束的审计时点。
    # 走势 A 与 A-B-C 在同一时间轴上同步监听；若固定走势 A 种子不成立，
    # 第一种情况的 confirmed_at 可以回填到 C 最后来源笔内更早的严格跌破事件。
    #
    # 若本候选确实存在走势 A，定义已经保证它必在 A-B-C 可用之前
    # 完成：走势 A 种子的中间向下笔是 B 后首个原始特征元素；它被
    # B 包含，所以当时右侧标准序列不足两个元素。走势 A 到随后向上
    # 种子笔终点进入，而新的 C 至少要等下一根向下笔终点才能可用。
    # 下面的显式时点比较用于审计这一不变量并落实 EPS 规则。
    trend_a_pattern = find_trend_a_pattern(candidate, pens)
    if trend_a_pattern is not None:
        assert (
            trend_a_pattern.entered_at.order < candidate.available_at.order
        ), "走势 A 若存在，进入事件必须先于同一候选 A-B-C 的可用时点"
    if (
        trend_a_pattern is not None
        and trend_a_pattern.entered_at.order
        < candidate.available_at.order - EPS
    ):
        decision = judge_trend_a_special(candidate, pens)
        if "BASIS-015" not in decision.basis_ids:
            decision.basis_ids.append("BASIS-015")
        decision.trace.insert(
            0,
            f"走势 A 进入时点 {trend_a_pattern.entered_at.order} 早于 "
            f"A-B-C 确立时点 {candidate.available_at.order}；"
            "不再考虑第一或第二种情况",
        )
        return decision

    case_1 = judge_case_1(candidate, segment_start, pens)
    if case_1.status in (CandidateStatus.PENDING, CandidateStatus.REJECTED):
        return case_1
    if case_1.status == CandidateStatus.CONFIRMED:
        case_1.proof.setdefault(
            "trend_A_entry",
            trend_a_pattern.entered_at if trend_a_pattern is not None else None,
        )
        case_1.proof.setdefault("trend_A_entered_before_abc", False)
        case_1.proof.setdefault(
            "ordinary_branch_locked_at",
            case_1.confirmed_at,
        )
        case_1.proof.setdefault(
            "same_candidate_cannot_enter_trend_A_later",
            True,
        )
        case_1.proof.setdefault(
            "trend_A_requires_immediate_seed_and_B.low<A.high",
            True,
        )
        case_1.proof.setdefault("trend_A_requires_pre_T_qualification", True)
        case_1.proof.setdefault("trend_A_requires_strict_pre_T_seed", (
            candidate.first_reverse_pen_position - candidate.segment_start_position == 3
        ))
        if "BASIS-015" not in case_1.basis_ids:
            case_1.basis_ids.append("BASIS-015")
        if "BASIS-021" not in case_1.basis_ids:
            case_1.basis_ids.append("BASIS-021")
        if "BASIS-033" not in case_1.basis_ids:
            case_1.basis_ids.append("BASIS-033")
        case_1.trace.insert(
            0,
            "A-B-C 可用前不存在满足 BASIS-033 的 T 前分范围资格、T 后紧邻种子及 "
            "B.low<A.high 的合法走势 A 进入事件；"
            "在 C 最后来源向下笔首次严格跌破冻结标准 B.low 时锁定普通第一种情况；"
            "同一候选以后不得再转入走势 A",
        )
        return case_1

    assert interval_gap(candidate.A, candidate.B), "A/B 应当非重叠即有缺口"
    ordinary_case_2 = judge_secondary_sequence(candidate, pens)
    ordinary_case_2.proof.setdefault(
        "trend_A_entry",
        trend_a_pattern.entered_at if trend_a_pattern is not None else None,
    )
    ordinary_case_2.proof.setdefault("trend_A_entered_before_abc", False)
    ordinary_case_2.proof.setdefault(
        "ordinary_branch_locked_at",
        candidate.available_at,
    )
    ordinary_case_2.proof.setdefault(
        "same_candidate_cannot_enter_trend_A_later",
        True,
    )
    ordinary_case_2.proof.setdefault(
        "trend_A_requires_immediate_seed_and_B.low<A.high",
        True,
    )
    ordinary_case_2.proof.setdefault("trend_A_requires_pre_T_qualification", True)
    ordinary_case_2.proof.setdefault("trend_A_requires_strict_pre_T_seed", (
        candidate.first_reverse_pen_position - candidate.segment_start_position == 3
    ))
    if "BASIS-015" not in ordinary_case_2.basis_ids:
        ordinary_case_2.basis_ids.append("BASIS-015")
    if "BASIS-021" not in ordinary_case_2.basis_ids:
        ordinary_case_2.basis_ids.append("BASIS-021")
    if "BASIS-033" not in ordinary_case_2.basis_ids:
        ordinary_case_2.basis_ids.append("BASIS-033")
    ordinary_case_2.trace.insert(
        0,
        "A-B-C 可用前不存在满足 BASIS-033 的 T 前分范围资格、T 后紧邻种子及 "
        "B.low<A.high 的合法走势 A 进入事件；"
        "在 available_at 锁定普通第二种情况，同一候选以后不得再转入走势 A",
    )
    return ordinary_case_2


# ============================================================================
# 9. 多候选点状态机：A/B/C 候选不会被机械切成多段
# ============================================================================


def find_last_pen_ending_at_or_before(pens: Sequence[Pen], order: float) -> int:
    """
    找到真正属于旧线段的最后一笔。

    候选端点 T 同时是旧向上笔的终点、下一根反向向下笔的起点；因此必须
    比较 pen.end.order，不能用 pen.start.order，否则会把下一段第一笔误算进来。
    """

    positions = [pen.position for pen in pens if pen.end.order <= order + EPS]
    assert positions, "已确认端点之前必须至少存在一根已完成笔"
    return max(positions)


def build_final_segment_result(
    pens: Sequence[Pen],
    start_pos: int,
    decision: CandidateDecision,
    candidate_traces: Sequence[str],
) -> SegmentResult:
    assert decision.endpoint is not None
    assert decision.confirmed_at is not None
    assert decision.case_name is not None

    endpoint_pos = find_last_pen_ending_at_or_before(pens, decision.endpoint.order)
    included = tuple(range(start_pos, endpoint_pos + 1))
    included_pens = [pens[position] for position in included]
    assert len(included) >= 3, "线段至少包含三笔"
    assert len(included) % 2 == 1, "向上线段从向上笔开始并结束于向上笔，笔数应为奇数"
    assert has_strict_segment_price_direction(
        Direction.UP,
        pens[start_pos].start,
        decision.endpoint,
    ), "已确认向上线段的结构端点必须严格高于起点"

    next_segment_t_not_before = None
    result_basis_ids = ["BASIS-002", "BASIS-011", "BASIS-028"] + decision.basis_ids
    result_trace = list(candidate_traces) + decision.trace
    if decision.case_name == EndCase.TREND_A:
        next_segment_t_not_before = decision.confirmed_at
        result_basis_ids.append("BASIS-018")
        result_trace.append(
            "本段由走势 A 特殊分支确认；下一反向线段仍从本段端点开始"
            "重新执行完整流程，其候选 T 不得早于本段确认触发笔终点"
        )

    return SegmentResult(
        direction=Direction.UP,
        status=SegmentStatus.CONFIRMED,
        start=pens[start_pos].start,
        endpoint=decision.endpoint,
        confirmed_at=decision.confirmed_at,
        case_name=decision.case_name,
        included_pen_positions=included,
        structural_low=min(pen.low for pen in included_pens),
        structural_high=max(pen.high for pen in included_pens),
        proof=decision.proof,
        trace=result_trace,
        basis_ids=sorted(set(result_basis_ids)),
        next_segment_t_not_before=next_segment_t_not_before,
    )


def _judge_canonical_up_segment(
    pens: Sequence[Pen],
    start_pos: int,
    candidate_t_not_before: Optional[Point] = None,
) -> SegmentResult:
    """
    内部规范引擎：从一个已知向上线段起点出发，寻找唯一结构端点。

    外部调用者应使用 judge_one_segment_offline；只有向下镜像和特殊分支
    的隔离反应走势会直接复用本函数。

    完整流程：
        1. 校验标准笔流和前三笔；
        2. 建立向下笔组成的第一特征序列；
        3. 从早到晚列出每个假设转折点；若上段走势 A 提供了时间下限，
           忽略严格早于下限的候选 T；
        4. 走势 A 与 A-B-C 在同一时间轴上同步检查；
        5. 走势 A 若早于 A-B-C 确立：转入走势 A，不再考虑第一/第二种情况；
         6. 特殊分支把 q1 后全部笔（含走势 A 三笔种子）隔离为新的向上线段；
             向上笔越过外层 T 时，先核查同一笔是否完成内层第二种情况底分型；
             完成则确认外层 T，否则由外层上界守卫否定 T 并记录该笔终点 T'；
             其余前缀递归运行完整流程；
        7. A-B-C 若未被走势 A 抢先：无缺口走第一种情况，有缺口走第二种情况；
        8. 第二种情况先记录每根原始笔是否创新高，再处理当前前缀包含；
           A2 取 B2 前全部标准元素中的最高 high；同事件底分型优先，
           否则原始创新高笔按 A2/B* 资格进入立即延续或等待独立 C2；
        9. 无第一特征序列标准候选时，在走势 A 进入与普通候选成立前高起点取消之间比较谁先发生；
        10. 普通原始创新高分支或走势 A 外层上界守卫否定旧 T -> 记录
             各自规则指定的真实向上笔终点 T'，并继续检查 T/T' 之间候选；
             其他分支否定才按各自规则继续；
        11. 第一个被结构规则确认的假设点成为本向上线段端点。

    A/B/C 连续候选的含义自然落在第 10 步：
         - 原始创新高 U 同事件未完成底分型，且无合格 A2/B*，或等待中
           后来原始笔起点低于固定 U.start -> A 被否定；T' 始终取 U.end，
           并仍按时间顺序检查 T/T' 之间的端点；
        - 组成底分型的最后一根原始向上笔即使终点高于 T，仍先认
          底分型并确认 A；
        - A 确认后，后续 B 是否结束必须重新、独立走完整流程；
        - B/C 无缺口可直接确认 B；有缺口则继续等待下一候选 D 的证书。
    """

    working_pens = tuple(pens)
    assert working_pens, "笔流不能为空"
    assert 0 <= start_pos < len(working_pens), "线段起始笔编号越界"

    stream_errors = validate_pen_stream(working_pens)
    if stream_errors:
        return SegmentResult(
            direction=Direction.UP,
            status=SegmentStatus.INVALID_INPUT,
            start=working_pens[start_pos].start,
            endpoint=None,
            confirmed_at=None,
            case_name=None,
            included_pen_positions=(),
            structural_low=None,
            structural_high=None,
            proof={"input_errors": stream_errors},
            trace=["标准 Pen 流违反输入不变量，停止判断"],
            basis_ids=["BASIS-002"],
        )

    start_errors = validate_up_segment_start(working_pens, start_pos)
    if start_errors:
        return SegmentResult(
            direction=Direction.UP,
            status=SegmentStatus.PENDING,
            start=working_pens[start_pos].start,
            endpoint=None,
            confirmed_at=None,
            case_name=None,
            included_pen_positions=(),
            structural_low=None,
            structural_high=None,
            proof={"segment_seed_not_ready": tuple(start_errors)},
            trace=[
                "从共享起点尚未形成当前方向的三笔基础结构；"
                "这是线段待定，不是通达信输入错误"
            ],
            basis_ids=["BASIS-002"],
        )

    segment_start = working_pens[start_pos].start
    all_attempts = find_hypothetical_turn_attempts(working_pens, start_pos)
    if candidate_t_not_before is None:
        time_eligible_attempts = all_attempts
    else:
        time_eligible_attempts = [
            attempt
            for attempt in all_attempts
            if attempt.turn.endpoint.order
            >= candidate_t_not_before.order - EPS
        ]
    ignored_before_floor = len(all_attempts) - len(time_eligible_attempts)
    attempts = [
        attempt
        for attempt in time_eligible_attempts
        if has_strict_segment_price_direction(
            Direction.UP,
            segment_start,
            attempt.turn.endpoint,
        )
    ]
    ignored_wrong_price_direction = len(time_eligible_attempts) - len(attempts)
    standard_top_count = sum(
        attempt.primary_top is not None
        for attempt in attempts
    )
    trace: list[str] = [
        "输入笔通过方向、连续性和前三笔公共重叠校验",
        f"按时间共找到 {len(attempts)} 个价格方向合格的假设转折点，其中 "
        f"{standard_top_count} 个已有第一特征序列标准候选",
    ]
    if ignored_wrong_price_direction:
        trace.append(
            f"另有 {ignored_wrong_price_direction} 个假设点未严格高于当前向上线段"
            "起点，按候选资格规则直接跳过；它们不进入普通分支、走势 A 或"
            "候选接续，也不属于输入错误"
        )
    explicitly_rejected_ts: list[Point] = []
    successor_events: list[CandidateSuccessorEvent] = []
    unresolved_successor_ts: list[Point] = []
    if candidate_t_not_before is not None:
        trace.append(
            f"本段候选 T 的最早允许时点为 order="
            f"{candidate_t_not_before.order}；已忽略其前方 "
            f"{ignored_before_floor} 个假设点"
        )

    for attempt_number, attempt in enumerate(attempts, start=1):
        turn = attempt.turn

        # T' 只是上一次否定事件产生的新候选身份，不是跳过游标。
        # 在抵达 T' 之前，所有中间假设点仍按完整流程检查；如果出现
        # 比 T' 更晚的假设点，说明候选枚举漏掉了真实的 T'。
        if unresolved_successor_ts:
            skipped_successors = [
                point
                for point in unresolved_successor_ts
                if point.order < turn.endpoint.order - EPS
            ]
            assert not skipped_successors, (
                "候选枚举不得越过尚未按正常流程检查的接续候选 T'"
            )
            reached_successors = [
                point
                for point in unresolved_successor_ts
                if points_equal(turn.endpoint, point)
            ]
            if reached_successors:
                unresolved_successor_ts = [
                    point
                    for point in unresolved_successor_ts
                    if not points_equal(turn.endpoint, point)
                ]
                trace.append(
                    f"抵达已记录的创新高接续候选 T'："
                    f"order={turn.endpoint.order}, price={turn.endpoint.price}；"
                    "按正常候选流程检查，不跳过此前的中间候选"
                )
            else:
                next_successor_t = min(
                    unresolved_successor_ts,
                    key=lambda point: point.order,
                )
                assert turn.endpoint.order < next_successor_t.order - EPS, (
                    "同一时点的接续候选必须与真实假设点价格一致"
                )
                trace.append(
                    f"尚未抵达已记录的 T' order={next_successor_t.order}；"
                    f"中间候选 T.order={turn.endpoint.order} 不跳过，继续完整判断"
                )

        trace.append(
            f"检查假设点 {attempt_number}：T.order={turn.endpoint.order}, "
            f"T.price={float(turn.endpoint.price)}"
        )

        if attempt.primary_top is not None:
            trace.append(
                "该假设点已形成标准 A-B-C 对象；"
                "先比较特殊模式开启时点与 A-B-C 确立时点"
            )
            decision = judge_one_candidate(
                attempt.primary_top,
                working_pens[start_pos].start,
                working_pens,
            )
        else:
            trace.append(
                "该假设点尚无第一特征序列标准 A-B-C 候选，"
                "检查走势 A 进入时点及普通候选成立前创新高"
            )
            decision = resolve_without_primary_top(
                turn,
                working_pens,
                attempt.pre_top_new_high_pen,
            )

        decision.proof["left_A_selection_reason"] = turn.a_selection_reason
        decision.proof["left_primary_A"] = turn.A
        decision.basis_ids.append("BASIS-034")
        if turn.a_selection_reason == "no_new_high_special":
            decision.basis_ids.extend(["BASIS-035", "BASIS-036"])
        trace.append(f"左侧 A 选择路径：{turn.a_selection_reason}")

        if candidate_t_not_before is not None:
            decision.proof.setdefault(
                "candidate_T_not_before",
                candidate_t_not_before,
            )
        if successor_events:
            decision.proof.setdefault(
                "candidate_successor_events",
                tuple(successor_events),
            )
            if any(
                event.source_case != EndCase.TREND_A
                for event in successor_events
            ) and "BASIS-023" not in decision.basis_ids:
                decision.basis_ids.append("BASIS-023")
            if any(
                event.source_case == EndCase.TREND_A
                for event in successor_events
            ) and "BASIS-025" not in decision.basis_ids:
                decision.basis_ids.append("BASIS-025")

        if decision.status == CandidateStatus.NOT_APPLICABLE:
            trace.append(f"假设点 {attempt_number} 没有端点证书，继续")
            continue

        if decision.status == CandidateStatus.CONFIRMED:
            trace.append(f"假设点 {attempt_number} 已确认；停止搜索更晚端点")
            return build_final_segment_result(
                working_pens,
                start_pos,
                decision,
                trace,
            )

        if decision.status == CandidateStatus.REJECTED:
            explicitly_rejected_ts.append(turn.endpoint)
            trace.extend(decision.trace)
            successor_t = decision.successor_candidate_t
            if successor_t is not None:
                assert successor_t.order > turn.endpoint.order + EPS
                assert successor_t.price > turn.endpoint.price + EPS
                assert decision.case_name in (
                    None,
                    EndCase.CASE_2_WITH_GAP,
                    EndCase.TREND_A,
                ), (
                    "接续候选记录只允许来自普通原始创新高分支或走势 A 外层"
                    "上界守卫；同事件内层第二种情况确认不会产生接续"
                )

                future_attempts = attempts[attempt_number:]
                first_not_before_successor = next(
                    (
                        future.turn.endpoint
                        for future in future_attempts
                        if future.turn.endpoint.order
                        >= successor_t.order - EPS
                    ),
                    None,
                )
                if first_not_before_successor is not None:
                    assert points_equal(first_not_before_successor, successor_t), (
                        "候选枚举中存在 T' 之后的假设点时，第一个不得越过 T'"
                    )

                successor_event = CandidateSuccessorEvent(
                    rejected_t=turn.endpoint,
                    successor_t=successor_t,
                    source_case=decision.case_name,
                )
                successor_events.append(successor_event)
                decision.proof["candidate_successor_event"] = successor_event
                if decision.case_name == EndCase.TREND_A:
                    trace.append(
                        f"走势 A 外层上界守卫接续：T={float(turn.endpoint.price)} "
                        f"被 T'={float(successor_t.price)} 取代；下一次候选"
                        "检查仍按时间顺序继续，不跳过两者之间的候选"
                    )
                else:
                    trace.append(
                        f"普通创新高接续：T={float(turn.endpoint.price)} 被 "
                        f"T'={float(successor_t.price)} 取代；下一次候选检查"
                        "仍按时间顺序继续，不跳过两者之间的候选"
                    )
                if not any(
                    points_equal(existing, successor_t)
                    for existing in unresolved_successor_ts
                ):
                    unresolved_successor_ts.append(successor_t)
                continue

            trace.append(
                f"假设点 {attempt_number} 已按当前分支明确否定，继续"
            )
            continue

        trace.append(f"假设点 {attempt_number} 截至数据末尾仍待定")

        # 假设点尚待定时，不能越过它机械采用一个更晚点；否则会偷用未解决边界。
        return SegmentResult(
            direction=Direction.UP,
            status=SegmentStatus.PENDING,
            start=working_pens[start_pos].start,
            endpoint=turn.endpoint,
            confirmed_at=None,
            # 已进入走势 A 却尚未终局时，仍要明确暴露当前分支；
            # 不能因为状态是 PENDING 就把 case_name 丢掉。
            case_name=decision.case_name,
            included_pen_positions=(),
            structural_low=None,
            structural_high=None,
            proof=decision.proof,
            wait_reason=("trend_a_waiting" if decision.case_name == EndCase.TREND_A else
                         "case_2_waiting" if attempt.primary_top is not None and interval_gap(attempt.primary_top.A, attempt.primary_top.B) else
                         "case_1_waiting" if attempt.primary_top is not None else "active_candidate"),
            explicitly_rejected_ts=tuple(explicitly_rejected_ts),
            trace=trace + decision.trace,
            basis_ids=sorted(set(["BASIS-002", "BASIS-011"] + decision.basis_ids)),
        )

    if unresolved_successor_ts:
        terminal_successor_t = unresolved_successor_ts[0]
        assert all(
            points_equal(point, terminal_successor_t)
            for point in unresolved_successor_ts
        ), "数据结束时不得留下多个不同的未检查接续候选 T'"
        return SegmentResult(
            direction=Direction.UP,
            status=SegmentStatus.PENDING,
            start=working_pens[start_pos].start,
            endpoint=terminal_successor_t,
            wait_reason="successor_first_reverse_not_ready",
            explicitly_rejected_ts=tuple(explicitly_rejected_ts),
            confirmed_at=None,
            case_name=None,
            included_pen_positions=(),
            structural_low=None,
            structural_high=None,
            proof={
                "candidate_T_not_before": candidate_t_not_before,
                "candidate_successor_events": tuple(successor_events),
                "awaiting_first_reverse_pen_after_successor_T": True,
            },
            trace=trace + [
                f"已记录的接续候选 T' order={terminal_successor_t.order}, "
                f"price={terminal_successor_t.price} 尚无对应假设点；当前数据"
                "尚无从 T' 开始的第一反向笔，固定流程保持待定"
            ],
            basis_ids=[
                "BASIS-002",
                "BASIS-003",
                "BASIS-004",
                "BASIS-008",
                "BASIS-021",
                "BASIS-033",
                *(
                    ["BASIS-023"]
                    if any(
                        event.source_case != EndCase.TREND_A
                        for event in successor_events
                    )
                    else []
                ),
                *(
                    ["BASIS-025"]
                    if any(
                        event.source_case == EndCase.TREND_A
                        for event in successor_events
                    )
                    else []
                ),
            ],
        )

    return SegmentResult(
        direction=Direction.UP,
        status=SegmentStatus.PENDING,
        start=working_pens[start_pos].start,
        endpoint=None,
        confirmed_at=None,
        case_name=None,
        included_pen_positions=(),
        structural_low=None,
        structural_high=None,
        proof={
            "candidate_T_not_before": candidate_t_not_before,
            "candidate_successor_events": tuple(successor_events),
        },
        explicitly_rejected_ts=tuple(explicitly_rejected_ts),
        trace=trace + ["所有假设转折点都没有得到终局证书；向上线段暂视为继续"],
        basis_ids=[
            "BASIS-002",
            "BASIS-003",
            "BASIS-004",
            "BASIS-008",
            "BASIS-021",
            "BASIS-033",
            *(
                ["BASIS-023"]
                if any(
                    event.source_case != EndCase.TREND_A
                    for event in successor_events
                )
                else []
            ),
            *(
                ["BASIS-025"]
                if any(
                    event.source_case == EndCase.TREND_A
                    for event in successor_events
                )
                else []
            ),
        ],
    )


# ============================================================================
# 10. 向下线段镜像与上下线段交替状态机
# ============================================================================


def opposite_direction(direction: Direction) -> Direction:
    return Direction.DOWN if direction == Direction.UP else Direction.UP


def mirror_point(point: Point) -> Point:
    """价格镜像 p'=-p；时间顺序和追溯标签不变。该函数连续调用两次即还原。"""

    return Point(order=point.order, price=-point.price, label=point.label)


def mirror_pen(pen: Pen) -> Pen:
    """把实际向下问题变成规范向上问题：价格取负，UP/DOWN 互换。"""

    return Pen(
        position=pen.position,
        start=mirror_point(pen.start),
        end=mirror_point(pen.end),
        direction=opposite_direction(pen.direction),
        price_bars=tuple(
            PriceBar(
                order=bar.order,
                high=-bar.low,
                low=-bar.high,
            )
            for bar in pen.price_bars
        ),
    )


def restore_segment_result_from_price_mirror(
    canonical_result: SegmentResult,
) -> SegmentResult:
    """
    把“价格取负后由向上引擎得到的结果”还原到实际价格坐标。

    start、endpoint、confirmed_at 和结构高低值全部还原；所含原始笔编号
    与时间顺序不变。canonical_mirror_proof 明确保留为镜像坐标证据，避免
    通过字符串替换把“顶/底、向上/向下”翻译错。读者按 p'=-p 即可逐项复核。
    """

    actual_direction = opposite_direction(canonical_result.direction)
    return SegmentResult(
        direction=actual_direction,
        status=canonical_result.status,
        start=mirror_point(canonical_result.start),
        endpoint=(
            mirror_point(canonical_result.endpoint)
            if canonical_result.endpoint is not None
            else None
        ),
        confirmed_at=(
            mirror_point(canonical_result.confirmed_at)
            if canonical_result.confirmed_at is not None
            else None
        ),
        wait_reason=canonical_result.wait_reason,
        explicitly_rejected_ts=tuple(mirror_point(p) for p in canonical_result.explicitly_rejected_ts),
        case_name=canonical_result.case_name,
        included_pen_positions=canonical_result.included_pen_positions,
        structural_low=(
            -canonical_result.structural_high
            if canonical_result.structural_high is not None
            else None
        ),
        structural_high=(
            -canonical_result.structural_low
            if canonical_result.structural_low is not None
            else None
        ),
        proof={
            "mirror_formula": "p' = -p；UP 与 DOWN 互换",
            "canonical_mirror_proof": canonical_result.proof,
            "canonical_proof_uses_mirrored_prices": True,
        },
        trace=[
            f"由镜像坐标还原实际 {actual_direction.value} 线段；"
            "镜像公式为 p'=-p，且笔方向互换",
            "以下带【镜像坐标】前缀的日志由同一个向上线段引擎产生；"
            "其中顶/底、向上/向下均指镜像坐标",
        ]
        + [f"【镜像坐标】{line}" for line in canonical_result.trace],
        basis_ids=sorted(set(canonical_result.basis_ids + ["BASIS-017"])),
        next_segment_t_not_before=(
            mirror_point(canonical_result.next_segment_t_not_before)
            if canonical_result.next_segment_t_not_before is not None
            else None
        ),
    )


def judge_one_segment_offline(
    pens: Sequence[Pen],
    start_pos: int,
    segment_direction: Direction,
    candidate_t_not_before: Optional[Point] = None,
) -> SegmentResult:
    """
    对外双向单段入口。

    - UP：直接调用规范向上线段引擎；
    - DOWN：把价格取负、方向互换后调用同一引擎，再还原结果；
    - 两个方向禁止维护两份独立判断代码，以免包含、分型或优先级漂移。

    接口约束：
        - pens 必须非空，start_pos 必须指向其中一根笔；越界属于调用错误；
        - Pen 流本身违反连续、交替等输入不变量时返回 INVALID_INPUT；
        - 从共享起点尚未形成三笔基础结构时返回 PENDING，不算输入错误；
        - 已找到结构端点并取得终局证书时返回 CONFIRMED；
        - 数据结束仍缺证书时返回 PENDING；
        - endpoint 与 confirmed_at 始终分别保存。
        - candidate_t_not_before 非空时，候选 T 的 order 不得早于该点。
    """

    if segment_direction == Direction.UP:
        return _judge_canonical_up_segment(
            pens,
            start_pos,
            candidate_t_not_before,
        )

    assert segment_direction == Direction.DOWN
    mirrored_pens = [mirror_pen(pen) for pen in pens]
    canonical_result = _judge_canonical_up_segment(
        mirrored_pens,
        start_pos,
        (
            mirror_point(candidate_t_not_before)
            if candidate_t_not_before is not None
            else None
        ),
    )
    return restore_segment_result_from_price_mirror(canonical_result)


def find_pen_starting_at_point(
    pens: Sequence[Pen],
    point: Point,
    required_direction: Direction,
) -> Optional[int]:
    """找到从已确认线段端点出发、方向与下一线段一致的第一根笔。"""

    matches = [
        pen.position
        for pen in pens
        if pen.direction == required_direction
        and points_equal(pen.start, point)
    ]
    assert len(matches) <= 1, "同一端点和方向不得对应多根起始笔"
    return matches[0] if matches else None


def assert_alternating_chain(segments: Sequence[SegmentResult]) -> None:
    """核查单段净方向、共享端点、方向交替和确认时点。"""

    for segment in segments:
        assert segment.status == SegmentStatus.CONFIRMED
        assert segment.endpoint is not None
        assert segment.confirmed_at is not None
        assert has_strict_segment_price_direction(
            segment.direction,
            segment.start,
            segment.endpoint,
        ), "已确认线段的语义方向必须与起止价格的严格净方向一致"

    for previous, current in zip(segments, segments[1:]):
        assert current.direction == opposite_direction(previous.direction)
        assert points_equal(previous.endpoint, current.start), (
            "前一线段端点必须与下一反向线段起点完全一致"
        )
        assert current.confirmed_at.order >= previous.confirmed_at.order - EPS, (
            "后一线段只有在前一线段已确认后才可对外生效；"
            "链级确认时点不得倒序"
        )


def apply_chain_confirmation_floor(
    result: SegmentResult,
    previous_confirmed_at: Optional[Point],
) -> SegmentResult:
    """把单段固有证据时点转换为连续线段链中的最早可用时点。

    单独回看后一段时，它的结构证据可能早于前一段最终获得确认。连续
    输出不能让后一段先于前一段生效，所以只在交替编排层取两者较晚者。
    端点、分支和单段固有证据均不改变；固有时点保存在 proof 中供审计。
    """

    assert result.status == SegmentStatus.CONFIRMED
    assert result.confirmed_at is not None
    if (
        previous_confirmed_at is None
        or result.confirmed_at.order >= previous_confirmed_at.order - EPS
    ):
        return result

    intrinsic_confirmed_at = result.confirmed_at
    adjusted_confirmed_at = Point(
        order=previous_confirmed_at.order,
        price=previous_confirmed_at.price,
        label="chain_confirmation_not_before_previous_segment",
    )
    adjusted_proof = dict(result.proof)
    adjusted_proof["intrinsic_segment_confirmed_at"] = intrinsic_confirmed_at
    adjusted_proof["chain_confirmation_floor"] = previous_confirmed_at
    return replace(
        result,
        confirmed_at=adjusted_confirmed_at,
        proof=adjusted_proof,
        basis_ids=sorted(set(result.basis_ids + ["BASIS-024"])),
        trace=result.trace + [
            f"本段独立证据时点 order={intrinsic_confirmed_at.order} 早于"
            f"前段确认时点 order={previous_confirmed_at.order}；"
            "连续链对外确认时点取前段确认时点，结构结论不变"
        ],
    )


def _recovery_t_qualifies(pens, count, direction):
    # SPEC-R03 / BASIS-R03: this extra filter does not alter ordinary branches.
    if count < 3 or count % 2 != 1:
        return False
    if count == 3:
        return _three_pens_meet_strict_initial_progress(pens, 0, direction)
    sign = 1 if direction == Direction.UP else -1
    first_extreme = sign * pens[0].end.price
    for k in range(0, count - 2, 2):
        if sign * pens[k + 2].end.price > first_extreme + EPS:
            return True
        if (_three_pens_define_segment(pens, k, direction)
            and sign * pens[k + 1].end.price >= sign * pens[k].start.price - EPS
            and sign * pens[k + 2].end.price > sign * pens[k].end.price + EPS):
            return True
    return False


class _RecoveryPrefixOracle:
    """Call-local causal certificate index, shared by all gate windows.

    Fixed completed pens have an append-stable CONFIRMED exit. Locate its first
    visible prefix by bisection, then retain the result of that actual prefix.
    No intrinsic crossing timestamp is substituted for visibility.
    """
    def __init__(self, pens):
        self.pens = tuple(pens)
        self.cache = {}
        self.first_cache = {}

    def result(self, start, direction, count, floor=None):
        key = (start, direction, count, floor)
        if key not in self.cache:
            local = tuple(replace(p, position=i) for i, p in enumerate(self.pens[start:count]))
            result = judge_one_segment_offline(local, 0, direction, floor)
            self.cache[key] = replace(result, included_pen_positions=tuple(
                start + k for k in result.included_pen_positions))
        return self.cache[key]

    def first_confirmed(self, start, direction, maximum, floor=None):
        key = (start, direction, maximum, floor)
        if key in self.first_cache:
            return self.first_cache[key]
        if maximum < start + 3 or self.result(start, direction, maximum, floor).status != SegmentStatus.CONFIRMED:
            self.first_cache[key] = None
            return None
        lo, hi = start + 3, maximum
        while lo < hi:
            mid = (lo + hi) // 2
            if self.result(start, direction, mid, floor).status == SegmentStatus.CONFIRMED:
                hi = mid
            else:
                lo = mid + 1
        self.first_cache[key] = lo
        return lo


def _try_long_stall_recovery(pens, start_pos, direction, floor, effective_floor, policy, normal, diagnostic=None):
    diagnostic = [] if diagnostic is None else diagnostic
    diagnostic.append("观察笔数未超过长度门槛或输入无效")
    # SPEC-R02: counters only consume completed pens. Raw K-line intervals,
    # rather than endpoint intervals alone, include gaps and intrapen extremes.
    local = tuple(replace(p, position=i) for i, p in enumerate(pens[start_pos:]))
    maximum = len(local) - int(policy.last_pen_unfinished)
    if maximum <= policy.length or validate_pen_stream(local):
        return None
    if not _three_pens_define_segment(local, 0, direction):
        diagnostic.append("起点前三笔基础未成立")
        return None
    sign = 1 if direction == Direction.UP else -1
    S = local[0].start
    favorable = sign * S.price
    last_record = residence = 0
    monitors = {}
    runs = []
    run_start = None
    for count in range(1, maximum + 1):
        pen = local[count - 1]
        values = [(b.low, b.high) for b in pen.price_bars] or [(pen.low, pen.high)]
        high = max(h if sign == 1 else -l for l, h in values)
        if high > favorable + EPS:
            favorable, last_record = high, count
        residence = residence + 1 if high < sign * S.price - EPS else 0
        stagnant = count - last_record
        # Last completed pen becomes fixed on the arrival of the following pen.
        visible = local[count].end if policy.last_pen_unfinished else pen.end
        gate = (count > policy.length and stagnant >= policy.stagnation
                and residence >= policy.residence
                and (effective_floor is None or visible.order >= effective_floor.order - EPS))
        monitors[count] = (stagnant, residence, visible)
        if gate and run_start is None:
            run_start = count
        if not gate and run_start is not None:
            runs.append((run_start, count - 1)); run_start = None
    if run_start is not None:
        runs.append((run_start, maximum))
    if not runs:
        diagnostic.append("停滞与反向驻留未同时满足")
        return None
    oracle = _RecoveryPrefixOracle(local)
    # Normal-rule priority uses an actually confirmed prefix, including when
    # the complete input would eventually confirm much later.
    normal_visible = oracle.first_confirmed(0, direction, maximum + int(policy.last_pen_unfinished), floor)
    if normal_visible is not None:
        normal_visible -= int(policy.last_pen_unfinished)
    diagnostic.append("原规则已先取得确认")
    for begin, end in runs:
        if normal_visible is not None:
            end = min(end, normal_visible - 1)
        if begin > end:
            continue
        # Within a residence run every subsequent interval is beyond S; no
        # legal T or innovation above an old T can be introduced in this run.
        legal = [q for q in range(3, begin, 2)
                 if has_strict_segment_price_direction(direction, S, local[q - 1].end)
                 and (floor is None or local[q - 1].end.order >= floor.order - EPS)
                 and _recovery_t_qualifies(local, q, direction)]
        diagnostic.append("没有合格的真实 T")
        certificates = {}
        checkpoints = {begin}
        for q in legal:
            diagnostic.append("反向段未确认，或确认终点尚未越过旧起点")
            first = oracle.first_confirmed(q, opposite_direction(direction), end)
            if first is None:
                continue
            reverse = oracle.result(q, opposite_direction(direction), first)
            if not has_strict_segment_price_direction(opposite_direction(direction), S, reverse.endpoint):
                continue
            certificates[q] = first
            checkpoints.add(max(begin, first))
        for count in sorted(checkpoints):
            pending = oracle.result(0, direction, count + int(policy.last_pen_unfinished), floor)
            if pending.status != SegmentStatus.PENDING:
                break
            eligible = [q for q in legal if q in certificates and certificates[q] <= count
                        and not any(points_equal(local[q - 1].end, p) for p in pending.explicitly_rejected_ts)]
            if pending.endpoint is not None:
                eligible = [q for q in eligible if points_equal(local[q - 1].end, pending.endpoint)]
            if not eligible:
                if pending.endpoint is not None:
                    diagnostic.append("活动 T 未取得反向证书；不能跳过未解决边界")
                continue
            # Global max, not chainwise fuzzy comparisons; EPS tie chooses late.
            peak = max(sign * local[q - 1].end.price for q in eligible)
            q = max(q for q in eligible if sign * local[q - 1].end.price >= peak - EPS)
            reverse = oracle.result(q, opposite_direction(direction), count)
            if reverse.status != SegmentStatus.CONFIRMED or not has_strict_segment_price_direction(opposite_direction(direction), S, reverse.endpoint):
                continue
            stagnant, resident, visible = monitors[count]
            first = certificates[q]
            reverse_visible = monitors[first][2]
            evidence = LongStallEvidence(
                pending.wait_reason,
                S, local[q - 1].end, reverse.endpoint, count, stagnant, resident,
                reverse.case_name, pending.case_name, reverse.confirmed_at, reverse_visible, visible)
            old = SegmentResult(direction, SegmentStatus.CONFIRMED, S, evidence.T, visible,
                EndCase.LONG_STALL_RECOVERY, tuple(range(start_pos, start_pos + q)),
                min(p.low for p in local[:q]), max(p.high for p in local[:q]),
                {"long_stall_recovery": evidence}, ["工程托底：独立反向段证书已可见"],
                ["BASIS-R01", "BASIS-R02", "BASIS-R03", "BASIS-R04"], recovery_evidence=evidence)
            reverse = replace(reverse, included_pen_positions=tuple(start_pos + k for k in reverse.included_pen_positions),
                              recovery_evidence=evidence)
            reverse = apply_chain_confirmation_floor(reverse, visible)
            return old, reverse
    return None


def _judge_recovery_chain(pens, start_pos, initial_direction, policy):
    working = tuple(pens)
    confirmed = []
    floor = previous = None
    direction = initial_direction
    while True:
        bounded = tuple(replace(p, position=i) for i, p in enumerate(working[start_pos:start_pos + policy.length]))
        quick = judge_one_segment_offline(bounded, 0, direction, floor)
        if quick.status == SegmentStatus.CONFIRMED:
            normal = replace(quick, included_pen_positions=tuple(start_pos + k for k in quick.included_pen_positions))
            recovered = None
            normal = replace(normal, recovery_diagnostic="原规则已先取得确认")
        else:
            normal = judge_one_segment_offline(working, start_pos, direction, floor)
            diagnostic = []
            recovered = _try_long_stall_recovery(working, start_pos, direction, floor, previous, policy, normal, diagnostic)
            normal = replace(normal, recovery_diagnostic=diagnostic[-1])
        additions = recovered or ((normal,) if normal.status == SegmentStatus.CONFIRMED else ())
        if not additions:
            assert_alternating_chain(confirmed)
            return AlternatingSegmentsResult(tuple(confirmed), normal, working[start_pos].start, direction, floor,
                                              ["未满足托底条件：" + normal.recovery_diagnostic])
        for result in additions:
            result = apply_chain_confirmation_floor(result, previous)
            confirmed.append(result); previous = result.confirmed_at
        last = confirmed[-1]
        direction = opposite_direction(last.direction)
        floor = last.next_segment_t_not_before
        next_pos = find_pen_starting_at_point(working, last.endpoint, direction)
        assert_alternating_chain(confirmed)
        if next_pos is None:
            return AlternatingSegmentsResult(tuple(confirmed), None, last.endpoint, direction, floor, [])
        assert next_pos > start_pos
        start_pos = next_pos


def calculate_tdx_segment_events_v2(endpoint_markers, highs, lows, policy=None):
    policy = policy or LongStallPolicy(enabled=True, last_pen_unfinished=True)
    return calculate_tdx_segment_events(endpoint_markers, highs, lows, policy)


def judge_alternating_segments_offline(
    pens: Sequence[Pen],
    start_pos: int,
    initial_direction: Direction,
    recovery_policy: Optional[LongStallPolicy] = None,
) -> AlternatingSegmentsResult:
    """
    对外交替线段入口：从一个已知起点开始确认 UP、DOWN、UP、DOWN……。

    每一条已确认线段的端点都成为下一反向线段起点，下一段重新执行
    完整流程。若上段由走势 A 确认，下一段候选 T 还必须满足：其 order
    不得早于最后触发上段确认的原始笔终点。

    本入口只返回一条连续前缀。默认策略在原规则尚未确认时检查最终托底；
    两者都不能确认、输入无效或尚无下一起始笔时停止，并保存已确认前缀、
    未解决线段及下一起点/方向。显式关闭策略沿用原规则的停止条件。
    """

    if recovery_policy is None:
        recovery_policy = default_long_stall_policy()
    if recovery_policy.enabled:
        return _judge_recovery_chain(pens, start_pos, initial_direction, recovery_policy)

    working_pens = tuple(pens)
    assert working_pens, "笔流不能为空"
    assert 0 <= start_pos < len(working_pens), "交替状态机的起始笔编号越界"

    confirmed: list[SegmentResult] = []
    trace: list[str] = []
    current_start_pos = start_pos
    current_start = working_pens[start_pos].start
    current_direction = initial_direction
    current_t_not_before: Optional[Point] = None
    previous_confirmed_at: Optional[Point] = None

    while True:
        current_result = judge_one_segment_offline(
            working_pens,
            current_start_pos,
            current_direction,
            current_t_not_before,
        )
        assert current_result.direction == current_direction
        assert points_equal(current_result.start, current_start)

        if current_result.status != SegmentStatus.CONFIRMED:
            trace.append(
                f"{current_direction.value} 线段从 order={current_start.order} 起"
                f"返回 {current_result.status.value}；交替推进停止"
            )
            assert_alternating_chain(confirmed)
            return AlternatingSegmentsResult(
                confirmed_segments=tuple(confirmed),
                unresolved_segment=current_result,
                next_start=current_start,
                next_direction=current_direction,
                next_segment_t_not_before=current_t_not_before,
                trace=trace,
            )

        assert current_result.endpoint is not None
        current_result = apply_chain_confirmation_floor(
            current_result,
            previous_confirmed_at,
        )
        assert current_result.confirmed_at is not None
        previous_confirmed_at = current_result.confirmed_at
        confirmed.append(current_result)
        trace.append(
            f"确认 {current_direction.value} 线段："
            f"start.order={current_result.start.order} -> "
            f"endpoint.order={current_result.endpoint.order}"
        )

        current_start = current_result.endpoint
        current_direction = opposite_direction(current_direction)
        current_t_not_before = current_result.next_segment_t_not_before
        if current_t_not_before is not None:
            trace.append(
                f"当前段由走势 A 确认；下一 {current_direction.value} 线段"
                f"从共享端点重新执行完整流程，候选 T 不得早于 "
                f"order={current_t_not_before.order}"
            )

        assert_alternating_chain(confirmed)
        next_start_pos = find_pen_starting_at_point(
            working_pens,
            current_start,
            current_direction,
        )
        if next_start_pos is None:
            trace.append(
                f"已确认端点 order={current_start.order} 将作为下一 "
                f"{current_direction.value} 线段起点；当前数据尚无对应起始笔"
            )
            return AlternatingSegmentsResult(
                confirmed_segments=tuple(confirmed),
                unresolved_segment=None,
                next_start=current_start,
                next_direction=current_direction,
                next_segment_t_not_before=current_t_not_before,
                trace=trace,
            )

        assert next_start_pos > current_start_pos, "交替推进必须严格向未来移动"
        current_start_pos = next_start_pos


# ============================================================================
# 11. 通达信函数编号 1：深入口与事件压缩
# ============================================================================


def _point_bar_index(point: Point, data_len: int) -> int:
    """TDX 路径中的 order 必须是有效整数 K 线下标。"""

    bar_index = int(round(point.order))
    assert almost_equal(point.order, float(bar_index)), (
        "通达信证书的 Point.order 必须来自整数 K 线下标"
    )
    assert 0 <= bar_index < data_len, "通达信证书的 K 线下标越界"
    return bar_index


def flatten_confirmed_segments(
    data_len: int,
    segments: Sequence[SegmentResult],
) -> tuple[SegmentCertificate, ...]:
    """把丰富的参考结果压缩为生产 C++ 真正需要移植的证书。"""

    certificates: list[SegmentCertificate] = []
    for segment in segments:
        assert segment.status == SegmentStatus.CONFIRMED
        assert segment.endpoint is not None
        assert segment.confirmed_at is not None
        assert has_strict_segment_price_direction(
            segment.direction,
            segment.start,
            segment.endpoint,
        ), "不得把价格净方向错误的线段压缩为正式输出证书"
        certificates.append(
            SegmentCertificate(
                direction=segment.direction,
                start_bar=_point_bar_index(segment.start, data_len),
                endpoint_bar=_point_bar_index(segment.endpoint, data_len),
                confirmed_bar=_point_bar_index(segment.confirmed_at, data_len),
            )
        )
    return tuple(certificates)


def _event_bit_for_direction(
    direction: Direction,
    up_bit: SegmentEventBit,
    down_bit: SegmentEventBit,
) -> SegmentEventBit:
    return up_bit if direction == Direction.UP else down_bit


def _input_error_event_mask(data_len: int) -> tuple[float, ...]:
    if data_len <= 0:
        return ()
    output = [0.0] * data_len
    output[-1] = float(SegmentEventBit.INPUT_ERROR)
    return tuple(output)


def encode_segment_events(
    data_len: int,
    certificates: Sequence[SegmentCertificate],
    *,
    input_error: bool = False,
) -> tuple[float, ...]:
    """按位编码已确认连续链；行为与 C++ EncodeSegmentEvents 同构。"""

    if data_len <= 0:
        return ()
    if input_error:
        return _input_error_event_mask(data_len)

    output = [0] * data_len
    for index, certificate in enumerate(certificates):
        fields_in_range = all(
            0 <= value < data_len
            for value in (
                certificate.start_bar,
                certificate.endpoint_bar,
                certificate.confirmed_bar,
            )
        )
        event_order_valid = (
            certificate.start_bar < certificate.endpoint_bar
            <= certificate.confirmed_bar
        )
        if not fields_in_range or not event_order_valid:
            return _input_error_event_mask(data_len)

        if index > 0:
            previous = certificates[index - 1]
            chain_valid = (
                certificate.direction != previous.direction
                and certificate.start_bar == previous.endpoint_bar
                and certificate.confirmed_bar >= previous.confirmed_bar
            )
            if not chain_valid:
                return _input_error_event_mask(data_len)

        if index == 0:
            output[certificate.start_bar] |= int(
                _event_bit_for_direction(
                    certificate.direction,
                    SegmentEventBit.UP_CHAIN_START,
                    SegmentEventBit.DOWN_CHAIN_START,
                )
            )

        output[certificate.endpoint_bar] |= int(
            _event_bit_for_direction(
                certificate.direction,
                SegmentEventBit.UP_ENDPOINT,
                SegmentEventBit.DOWN_ENDPOINT,
            )
        )
        output[certificate.confirmed_bar] |= int(
            _event_bit_for_direction(
                certificate.direction,
                SegmentEventBit.UP_CONFIRMED,
                SegmentEventBit.DOWN_CONFIRMED,
            )
        )

    return tuple(float(value) for value in output)


def calculate_tdx_segment_events(
    endpoint_markers: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    recovery_policy: Optional[LongStallPolicy] = None,
) -> TdxSegmentCalculationResult:
    """通达信 DLL 函数编号 1 的唯一完整参考入口。

    固定顺序为：结构化解码；搜索最早初始线段；运行交替状态机；仅把
    已确认连续前缀压缩为位掩码。默认开启43/13/13托底，末根可能延伸笔
    不计入托底。笔数不足、尚无初始三笔或尚无已确认段均为合法快照，
    输出全零而不设置 INPUT_ERROR；已有确认段的待定尾部保留确认前缀。
    """

    data_len = len(endpoint_markers)
    decoded = decode_pens_from_tdx_series(endpoint_markers, highs, lows)
    if not decoded.ok:
        return TdxSegmentCalculationResult(
            event_mask=_input_error_event_mask(data_len),
            input_errors=decoded.errors,
            initial_start=None,
            chain=None,
        )

    initial_start = find_earliest_segment_start(decoded.pens)
    if initial_start is None:
        return TdxSegmentCalculationResult(
            event_mask=tuple(0.0 for _ in range(data_len)),
            input_errors=(),
            initial_start=None,
            chain=None,
        )

    chain = judge_alternating_segments_offline(
        decoded.pens,
        start_pos=initial_start.start_pos,
        initial_direction=initial_start.direction,
        recovery_policy=(default_long_stall_policy(last_pen_unfinished=True)
                         if recovery_policy is None else recovery_policy),
    )
    if chain.unresolved_segment is not None:
        assert chain.unresolved_segment.status != SegmentStatus.INVALID_INPUT, (
            "通过 TDX 适配器构造的 Pen 流不得在划段阶段重新变成输入错误"
        )
    certificates = flatten_confirmed_segments(
        data_len,
        chain.confirmed_segments,
    )
    return TdxSegmentCalculationResult(
        event_mask=encode_segment_events(data_len, certificates),
        input_errors=(),
        initial_start=initial_start,
        chain=chain,
    )


# ============================================================================
# 12. 分组自检：防止最容易写反的边界
# ============================================================================


def _feature(low: float, high: float, source: int) -> FeatureElement:
    return FeatureElement(
        low=low,
        high=high,
        source_pen_positions=(source,),
        first_order=float(source),
        last_order=float(source),
    )


def _pens_from_pivot_prices(prices: Sequence[float]) -> list[Pen]:
    """仅供自检：把连续拐点价格转成首尾相接、方向交替的笔。"""

    answer: list[Pen] = []
    for position, (start_price, end_price) in enumerate(zip(prices, prices[1:])):
        direction = Direction.UP if end_price > start_price else Direction.DOWN
        answer.append(
            Pen(
                position=position,
                start=Point(float(position), start_price, f"P{position}"),
                end=Point(float(position + 1), end_price, f"P{position + 1}"),
                direction=direction,
            )
        )
    return answer


def _case_1_intrapen_tdx_pens() -> tuple[Pen, ...]:
    """构造 C 最后来源笔在终点前首次严格跌破 B.low 的通达信样例。"""

    return _require_pens_from_tdx_series(
        endpoint_markers=(
            -1.0, 0.0, 1.0, 0.0, -1.0,
            0.0, 1.0, 0.0, -1.0, 0.0,
            1.0, 0.0, 0.0, 0.0, -1.0,
        ),
        highs=(
            0.5, 3.0, 5.0, 4.0, 3.0,
            6.0, 8.0, 7.0, 5.0, 5.5,
            6.0, 5.8, 5.0, 4.0, 2.0,
        ),
        lows=(
            0.0, 1.0, 4.0, 3.0, 2.0,
            3.0, 7.0, 5.0, 4.0, 4.5,
            5.0, 4.0, 3.5, 2.0, 1.0,
        ),
    )


def _case_1_standard_B_threshold_tdx_pens() -> tuple[Pen, ...]:
    """构造标准 B.low 与原始第一反向笔 B0.low 不同的辨析样例。

    原始向下特征元素 B0=[1,8] 包含其后的 [4,7]，高高合并后冻结的
    标准元素 B=[4,8]。C 的最后来源向下笔在 order=15 首次出现
    low=3<标准 B.low=4；它到 order=17 才跌破原始 B0.low=1。
    第一种情况必须在 order=15 确认，不能偷换成原始 B0 的阈值。
    """

    return _require_pens_from_tdx_series(
        endpoint_markers=(
            -1.0, 0.0, 1.0, 0.0, -1.0,
            0.0, 1.0, 0.0, -1.0, 0.0,
            1.0, 0.0, -1.0, 0.0, 1.0,
            0.0, 0.0, 0.0, -1.0,
        ),
        highs=(
            0.5, 3.0, 5.0, 4.0, 3.0,
            6.0, 8.0, 7.0, 3.0, 6.0,
            7.0, 6.0, 5.0, 5.5, 6.0,
            5.5, 4.5, 3.0, 2.0,
        ),
        lows=(
            0.0, 1.0, 4.0, 3.0, 2.0,
            3.0, 7.0, 4.0, 1.0, 2.0,
            6.0, 4.5, 4.0, 4.5, 5.0,
            3.0, 1.0, 0.5, 0.0,
        ),
    )


def _tdx_series_from_pens_for_selfcheck(
    pens: Sequence[Pen],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """仅供回归：把带逐 K 路径的 Pen 还原成三条 TDX 测试数组。"""

    assert pens and all(pen.price_bars for pen in pens)
    data_len = int(pens[-1].end.order) + 1
    bars = {
        int(bar.order): bar
        for pen in pens
        for bar in pen.price_bars
    }
    assert set(bars) == set(range(data_len))
    markers = [0.0] * data_len
    for pen in pens:
        start_marker = -1.0 if pen.direction == Direction.UP else 1.0
        end_marker = -start_marker
        start_bar = int(pen.start.order)
        end_bar = int(pen.end.order)
        assert markers[start_bar] in (0.0, start_marker)
        assert markers[end_bar] in (0.0, end_marker)
        markers[start_bar] = start_marker
        markers[end_bar] = end_marker
    return (
        tuple(markers),
        tuple(bars[index].high for index in range(data_len)),
        tuple(bars[index].low for index in range(data_len)),
    )


def _check_geometry_and_feature_sequences() -> None:
    """这些不是完整行情回测，只检查核心数学边界和订正规则。"""

    direction_start = Point(order=0.0, price=10.0)
    assert not has_strict_segment_price_direction(
        Direction.UP,
        direction_start,
        Point(order=1.0, price=10.0),
    )
    assert not has_strict_segment_price_direction(
        Direction.UP,
        direction_start,
        Point(order=1.0, price=10.0 + 0.5 * EPS),
    )
    assert has_strict_segment_price_direction(
        Direction.UP,
        direction_start,
        Point(order=1.0, price=10.0 + 2.0 * EPS),
    )
    assert not has_strict_segment_price_direction(
        Direction.DOWN,
        direction_start,
        Point(order=1.0, price=10.0),
    )
    assert not has_strict_segment_price_direction(
        Direction.DOWN,
        direction_start,
        Point(order=1.0, price=10.0 - 0.5 * EPS),
    )
    assert has_strict_segment_price_direction(
        Direction.DOWN,
        direction_start,
        Point(order=1.0, price=10.0 - 2.0 * EPS),
    )

    x = _feature(1.0, 3.0, 1)
    y_touch = _feature(3.0, 5.0, 2)
    y_gap = _feature(3.0001, 5.0, 3)
    assert interval_overlap(x, y_touch), "端点相等必须算重叠"
    assert not interval_gap(x, y_touch), "端点相等不能算缺口"
    assert interval_gap(x, y_gap), "严格分离才是缺口"

    # 通达信只传“端点标记、H、L”三个等长数组。相邻底/顶标记必须
    # 恢复成连续编号、共享端点且方向交替的标准 Pen。
    tdx_pens = _require_pens_from_tdx_series(
        endpoint_markers=(-1.0, 0.0, 1.0, 0.0, -1.0, 1.0),
        highs=(1.0, 2.0, 5.0, 4.0, 3.0, 8.0),
        lows=(0.0, 1.0, 4.0, 3.0, 1.0, 7.0),
    )
    assert [pen.position for pen in tdx_pens] == [0, 1, 2]
    assert [pen.direction for pen in tdx_pens] == [
        Direction.UP,
        Direction.DOWN,
        Direction.UP,
    ]
    assert points_equal(tdx_pens[0].start, Point(order=0.0, price=0.0))
    assert points_equal(tdx_pens[0].end, Point(order=2.0, price=5.0))
    assert points_equal(tdx_pens[0].end, tdx_pens[1].start)
    assert points_equal(tdx_pens[1].end, tdx_pens[2].start)
    assert not validate_pen_stream(tdx_pens)

    invalid_marker = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 0.5, 1.0),
        highs=(1.0, 2.0, 5.0),
        lows=(0.0, 1.0, 4.0),
    )
    assert not invalid_marker.ok
    assert invalid_marker.errors[0].code == PenInputErrorCode.INVALID_ENDPOINT_MARKER
    assert invalid_marker.errors[0].bar_index == 1

    near_integer_marker = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 0.0000001, 1.0),
        highs=(1.0, 2.0, 5.0),
        lows=(0.0, 1.0, 4.0),
    )
    assert not near_integer_marker.ok
    assert (
        near_integer_marker.errors[0].code
        == PenInputErrorCode.INVALID_ENDPOINT_MARKER
    )

    endpoint_extensions = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0, 1.0, -1.0, -1.0, 1.0),
        highs=(2.0, 5.0, 6.0, 4.0, 3.0, 7.0),
        lows=(1.0, 4.0, 5.0, 3.0, 2.0, 6.0),
    )
    assert endpoint_extensions.ok
    assert [pen.start.order for pen in endpoint_extensions.pens] == [0.0, 2.0, 4.0]
    assert [pen.end.order for pen in endpoint_extensions.pens] == [2.0, 4.0, 5.0]

    redundant_endpoint_update = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0, 1.0),
        highs=(2.0, 6.0, 5.0),
        lows=(1.0, 5.0, 4.0),
    )
    assert redundant_endpoint_update.ok
    assert redundant_endpoint_update.pens[0].end.order == 1.0
    assert redundant_endpoint_update.pens[0].end.price == 6.0

    equal_extreme_update = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0, 1.0, -1.0),
        highs=(2.0, 6.0, 6.0, 4.0),
        lows=(1.0, 5.0, 5.0, 3.0),
    )
    assert equal_extreme_update.ok
    assert equal_extreme_update.pens[0].end.order == 2.0
    assert equal_extreme_update.pens[1].start.order == 2.0

    empty_input = decode_pens_from_tdx_series((), (), ())
    assert not empty_input.ok
    assert (
        empty_input.errors[0].code
        == PenInputErrorCode.NON_POSITIVE_LENGTH
    )

    unequal_lengths = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0),
        highs=(1.0,),
        lows=(0.0, 2.0),
    )
    assert not unequal_lengths.ok
    assert unequal_lengths.errors[0].code == PenInputErrorCode.LENGTH_MISMATCH

    non_finite_price = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0),
        highs=(1.0, float("nan")),
        lows=(0.0, 2.0),
    )
    assert not non_finite_price.ok
    assert non_finite_price.errors[0].code == PenInputErrorCode.NON_FINITE_PRICE
    assert non_finite_price.errors[0].bar_index == 1

    high_below_low = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0),
        highs=(1.0, 2.0),
        lows=(0.0, 3.0),
    )
    assert not high_below_low.ok
    assert high_below_low.errors[0].code == PenInputErrorCode.HIGH_BELOW_LOW

    invalid_price_direction = decode_pens_from_tdx_series(
        endpoint_markers=(-1.0, 1.0),
        highs=(5.0, 4.0),
        lows=(4.0, 3.0),
    )
    assert not invalid_price_direction.ok
    assert (
        invalid_price_direction.errors[0].code
        == PenInputErrorCode.INVALID_PEN_PRICE_DIRECTION
    )

    # 完整 TDX 深入口必须一次完成解码、初始搜索、交替判断和位编码。
    # 这组第一种情况样例在 bar=6 结束向上线段，并在 bar=12 得到
    # 笔内严格跌破证据；首段起点、端点、确认位不得混成一个事件。
    tdx_series = _tdx_series_from_pens_for_selfcheck(
        _case_1_intrapen_tdx_pens()
    )
    tdx_calculation = calculate_tdx_segment_events(*tdx_series)
    assert tdx_calculation.ok
    assert tdx_calculation.initial_start is not None
    assert tdx_calculation.chain is not None
    assert len(tdx_calculation.chain.confirmed_segments) == 1
    nonzero_events = {
        index: int(value)
        for index, value in enumerate(tdx_calculation.event_mask)
        if value
    }
    assert nonzero_events == {
        0: int(SegmentEventBit.UP_CHAIN_START),
        6: int(SegmentEventBit.UP_ENDPOINT),
        12: int(SegmentEventBit.UP_CONFIRMED),
    }

    invalid_calculation = calculate_tdx_segment_events(
        endpoint_markers=(-1.0, 0.5, 1.0),
        highs=(1.0, 2.0, 5.0),
        lows=(0.0, 1.0, 4.0),
    )
    assert not invalid_calculation.ok
    assert invalid_calculation.event_mask == (
        0.0,
        0.0,
        float(SegmentEventBit.INPUT_ERROR),
    )

    # 合法但尚无初始线段是全零快照，不得误报输入错误。
    no_segment_calculation = calculate_tdx_segment_events(
        endpoint_markers=(-1.0, 0.0, 1.0),
        highs=(1.0, 2.0, 5.0),
        lows=(0.0, 1.0, 4.0),
    )
    assert no_segment_calculation.ok
    assert no_segment_calculation.event_mask == (0.0, 0.0, 0.0)

    decreasing_confirmation_mask = encode_segment_events(
        6,
        (
            SegmentCertificate(Direction.UP, 0, 2, 5),
            SegmentCertificate(Direction.DOWN, 2, 4, 4),
        ),
    )
    assert decreasing_confirmation_mask == (
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        float(SegmentEventBit.INPUT_ERROR),
    )

    # 候选失败、合法待定和 Pen 流错误必须属于不同状态层。
    short_valid_pens = _pens_from_pivot_prices([0, 5, 2])
    short_valid_result = judge_one_segment_offline(
        short_valid_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert short_valid_result.status == SegmentStatus.PENDING
    assert short_valid_result.structural_low is None
    assert short_valid_result.structural_high is None

    broken_stream = list(_pens_from_pivot_prices([0, 5, 2, 8]))
    broken_stream[1] = replace(
        broken_stream[1],
        start=Point(
            order=broken_stream[1].start.order,
            price=broken_stream[1].start.price + 0.25,
        ),
    )
    broken_stream_result = judge_one_segment_offline(
        broken_stream,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert broken_stream_result.status == SegmentStatus.INVALID_INPUT
    assert broken_stream_result.structural_low is None
    assert broken_stream_result.structural_high is None

    # 初始线段不固定从第 0 笔开始。扫描顺序先按起始位置，再由该位置
    # 第一笔的方向确定候选方向；第一个同时满足三笔基础结构、内侧端点
    # 严格推进以及第三笔严格越过第一笔终点的位置，才是全链初始锚点。
    earliest_up_pens = _pens_from_pivot_prices([0, 5, 2, 8])
    earliest_up = find_earliest_segment_start(earliest_up_pens)
    assert earliest_up is not None
    assert earliest_up.start_pos == 0
    assert earliest_up.direction == Direction.UP
    assert points_equal(earliest_up.start, earliest_up_pens[0].start)

    # 严格推进辅助函数同时锁定上下镜像和等号边界。这一判据既用于
    # 自动首段，也用于当前共享起点至 T 恰好三笔的走势 A 资格。
    strict_up = _pens_from_pivot_prices([1, 8, 2, 9])
    equal_up = _pens_from_pivot_prices([1, 8, 2, 8])
    weak_up = _pens_from_pivot_prices([1, 8, 2, 7])
    assert _three_pens_meet_strict_initial_progress(
        strict_up, 0, Direction.UP
    )
    assert not _three_pens_meet_strict_initial_progress(
        equal_up, 0, Direction.UP
    )
    assert not _three_pens_meet_strict_initial_progress(
        weak_up, 0, Direction.UP
    )

    strict_down = _pens_from_pivot_prices([-1, -8, -2, -9])
    equal_down = _pens_from_pivot_prices([-1, -8, -2, -8])
    weak_down = _pens_from_pivot_prices([-1, -8, -2, -7])
    assert _three_pens_meet_strict_initial_progress(
        strict_down, 0, Direction.DOWN
    )
    assert not _three_pens_meet_strict_initial_progress(
        equal_down, 0, Direction.DOWN
    )
    assert not _three_pens_meet_strict_initial_progress(
        weak_down, 0, Direction.DOWN
    )

    # 第一个向上三笔未创新高，自动锚点后移；从已知起点验证的
    # 三笔基础结构仍成立，不能把两种资格检查混为一谈。
    shifted_initial_pens = _pens_from_pivot_prices([0, 5, 4, 4.5, 2])
    shifted_initial = find_earliest_segment_start(shifted_initial_pens)
    assert shifted_initial is not None
    assert shifted_initial.start_pos == 1
    assert shifted_initial.direction == Direction.DOWN
    assert points_equal(shifted_initial.start, shifted_initial_pens[1].start)
    assert validate_up_segment_start(shifted_initial_pens, 0) == []

    for prices, direction in [
        ([0, 5, 0, 8, 4, 10], Direction.UP),
        ([10, 5, 10, 2, 6, 0], Direction.DOWN),
    ]:
        equal_seed = find_earliest_segment_start(_pens_from_pivot_prices(prices))
        assert equal_seed is not None
        assert equal_seed.start_pos == 2
        assert equal_seed.direction == direction

    no_initial = find_earliest_segment_start(
        _pens_from_pivot_prices([0, 5, -1, -0.5])
    )
    assert no_initial is None

    # 原文 d_j <= g_i：等号属于笔破坏；严格高于所有可比较早先顶则不是。
    equal_break_pens = _pens_from_pivot_prices([0, 5, 2, 8, 5])
    equal_break_turn = find_hypothetical_turn_attempts(equal_break_pens, 0)[0].turn
    assert first_reverse_pen_breaks_up_segment(equal_break_turn, equal_break_pens)
    no_break_pens = _pens_from_pivot_prices([0, 5, 2, 8, 5.1])
    no_break_turn = find_hypothetical_turn_attempts(no_break_pens, 0)[0].turn
    assert not first_reverse_pen_breaks_up_segment(no_break_turn, no_break_pens)

    outer = _feature(1.0, 5.0, 4)
    inner = _feature(2.0, 4.0, 5)
    merged_down_pen = merge_contained_elements(outer, inner, Direction.DOWN)
    merged_up_pen = merge_contained_elements(outer, inner, Direction.UP)
    assert almost_equal(merged_down_pen.low, 2.0) and almost_equal(
        merged_down_pen.high,
        5.0,
    )
    assert almost_equal(merged_up_pen.low, 1.0) and almost_equal(
        merged_up_pen.high,
        4.0,
    )

    # 第一特征序列的向下笔始终高高合并，并在新结果再次包含时向左回溯。
    primary_cascade = standardize_feature_sequence(
        [_feature(5.0, 10.0, 6), _feature(4.0, 9.0, 7), _feature(6.0, 8.0, 8)],
        feature_pen_direction=Direction.DOWN,
    )
    assert len(primary_cascade) == 1
    assert almost_equal(primary_cascade[0].low, 6.0)
    assert almost_equal(primary_cascade[0].high, 10.0)
    assert primary_cascade[0].source_pen_positions == (6, 7, 8)

    # T=8 的第一特征序列候选尚未成立时，后续向下特征笔从 9 开始，T 必须先失格。
    pre_top_new_high_pens = _pens_from_pivot_prices([0, 5, 2, 8, 4, 9, 3, 7, 1])
    pre_top_raw, pre_top_down_pens = collect_primary_down_features(
        pre_top_new_high_pens,
        0,
    )
    assert has_containment(pre_top_raw[1], pre_top_raw[2]), (
        "B 后首个原始特征元素即使与 B 包含，也必须先检查其原始起点"
    )
    assert build_candidate_at_boundary(
        pre_top_raw,
        pre_top_down_pens,
        b_raw_index=1,
    ) is None
    first_attempt = find_hypothetical_turn_attempts(pre_top_new_high_pens, 0)[0]
    assert first_attempt.pre_top_new_high_pen is not None
    assert almost_equal(first_attempt.pre_top_new_high_pen.start.price, 9.0)
    pre_top_rejection = resolve_without_primary_top(
        first_attempt.turn,
        pre_top_new_high_pens,
        first_attempt.pre_top_new_high_pen,
    )
    assert pre_top_rejection.status == CandidateStatus.REJECTED
    assert pre_top_rejection.successor_candidate_t is not None
    assert pre_top_rejection.successor_candidate_t.price == 9.0
    assert pre_top_rejection.successor_candidate_t.order == 5.0
    assert "BASIS-023" in pre_top_rejection.basis_ids
    pre_top_jump_result = judge_one_segment_offline(
        pre_top_new_high_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert pre_top_jump_result.status == SegmentStatus.CONFIRMED
    assert pre_top_jump_result.endpoint is not None
    assert points_equal(
        pre_top_jump_result.endpoint,
        pre_top_rejection.successor_candidate_t,
    )
    assert any(
        "抵达已记录的创新高接续候选 T'" in item
        for item in pre_top_jump_result.trace
    )

    # B 后首个原始特征元素若没有越过 T，必须立刻与 B 一起参加
    # 高高包含标准化。这里 B=[1,8] 包含首元素 [3,6]，最短前缀
    # 合并成一个标准元素 [3,8]；扩大到下一元素后才得到 C=[0,5]
    # 并形成第一特征序列顶分型。
    first_right_containment_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 1, 6, 3, 5, 0]
    )
    first_right_raw, first_right_down_pens = collect_primary_down_features(
        first_right_containment_pens,
        0,
    )
    assert first_right_down_pens[2].start.price <= 8.0 + EPS
    first_right_short = standardize_feature_sequence(
        first_right_raw[1:3],
        feature_pen_direction=Direction.DOWN,
    )
    assert len(first_right_short) == 1
    assert first_right_short[0].source_pen_positions == (3, 5)
    assert almost_equal(first_right_short[0].low, 3.0)
    assert almost_equal(first_right_short[0].high, 8.0)
    first_right_candidate = build_candidate_at_boundary(
        first_right_raw,
        first_right_down_pens,
        b_raw_index=1,
    )
    assert first_right_candidate is not None
    assert is_primary_top_fractal(
        first_right_candidate.A,
        first_right_candidate.B,
        first_right_candidate.C,
    )

    # 第一特征序列顶分型是专用的非对称判据。这里 A=[2,5]、
    # B=[1,8]、C=[0,6]，虽然 B.low=1<A.low=2，但 C.low=0<B.low=1，
    # 且 B.high=8 同时严格高于 A/C 的高点，所以最短前缀必须立即成立，
    # 不能错误等待更远元素把 B.low 抬到 A.low 之上。
    stronger_break_pens = _pens_from_pivot_prices([0, 5, 2, 8, 1, 6, 0])
    stronger_break_raw, stronger_break_down_pens = collect_primary_down_features(
        stronger_break_pens,
        0,
    )
    stronger_break_right = standardize_feature_sequence(
        stronger_break_raw[1:3],
        feature_pen_direction=Direction.DOWN,
    )
    assert len(stronger_break_right) == 2
    stronger_A = stronger_break_raw[0]
    stronger_B, stronger_C = stronger_break_right
    assert stronger_B.low < stronger_A.low
    assert stronger_C.low < stronger_B.low
    assert is_primary_top_fractal(stronger_A, stronger_B, stronger_C)
    assert not is_symmetric_top_fractal(stronger_A, stronger_B, stronger_C), (
        "通用四比较顶分型必须与第一特征序列专用判据保持区分"
    )
    stronger_break_candidate = build_candidate_at_boundary(
        stronger_break_raw,
        stronger_break_down_pens,
        b_raw_index=1,
    )
    assert stronger_break_candidate is not None
    assert stronger_break_candidate.available_at.order == 6.0

    # 放宽的只有 B.low 与 A.low 这一项。C.low 必须仍严格低于 B.low；
    # 等于或高于都不能成立。B.high 对 A/C 的两个严格比较也保留。
    assert not is_primary_top_fractal(
        _feature(2.0, 5.0, 20),
        _feature(1.0, 8.0, 21),
        _feature(1.0, 6.0, 22),
    )
    assert not is_primary_top_fractal(
        _feature(2.0, 5.0, 23),
        _feature(1.0, 8.0, 24),
        _feature(2.0, 6.0, 25),
    )
    assert not is_primary_top_fractal(
        _feature(2.0, 8.0, 26),
        _feature(1.0, 8.0, 27),
        _feature(0.0, 6.0, 28),
    )
    assert not is_primary_top_fractal(
        _feature(2.0, 5.0, 29),
        _feature(1.0, 8.0, 30),
        _feature(0.0, 8.0, 31),
    )

    # 最近标准元素 high=8>T=6，不能回退到更早的 high=6。
    # 虽然右侧 B/C 有下降关系，此候选仍没有合格 A；更远元素不能修复。
    unrepairable_left_high_pens = _pens_from_pivot_prices(
        [-1, 6, 0, 8, 2, 6, 1, 4, 0, 3, -1]
    )
    unrepairable_raw, unrepairable_down_pens = collect_primary_down_features(
        unrepairable_left_high_pens,
        0,
    )
    unrepairable_A = standardized_left_A_at_boundary(
        unrepairable_raw,
        b_raw_index=2,
        candidate_price=6.0,
    )
    assert unrepairable_A is None
    unrepairable_right = standardize_feature_sequence(
        unrepairable_raw[2:4],
        feature_pen_direction=Direction.DOWN,
    )
    assert len(unrepairable_right) == 2
    unrepairable_B, unrepairable_C = unrepairable_right
    assert unrepairable_B.high > unrepairable_C.high + EPS
    assert unrepairable_B.low > unrepairable_C.low + EPS
    assert build_candidate_at_boundary(
        unrepairable_raw,
        unrepairable_down_pens,
        b_raw_index=2,
    ) is None

    # 若 T=8 已先由第一特征序列 A-B-C 候选确立，之后才出现起点为 9 的向下特征笔，
    # 后来的新高不能回头取消已经更早成立的普通候选。
    post_top_new_high_pens = _pens_from_pivot_prices([0, 5, 2, 8, 4, 6, 1, 9, 0])
    post_top_raw, post_top_down_pens = collect_primary_down_features(
        post_top_new_high_pens,
        0,
    )
    established_before_new_high = build_candidate_at_boundary(
        post_top_raw,
        post_top_down_pens,
        b_raw_index=1,
    )
    assert established_before_new_high is not None
    assert almost_equal(established_before_new_high.endpoint.price, 8.0)
    assert established_before_new_high.available_at.order < 7.0

    # 第二特征序列的最新原始向上笔 U 终点为 11>T=10；低低合并后
    # B*=[3,7]，其左侧最高 A2=[6,8] 同时满足高点和低点资格。因此
    # 本轮不能立即否定 T，而要固定 U.start=3 等待独立 C2。
    absorbed_high_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 6, 8, 4, 7, 3, 11]
    )
    absorbed_raw = [
        pen_to_feature(absorbed_high_pens[position])
        for position in (4, 6, 8)
    ]
    absorbed_standard = standardize_feature_sequence(
        absorbed_raw,
        feature_pen_direction=Direction.UP,
    )
    assert almost_equal(absorbed_standard[-1].high, 7.0)
    absorbed_current_standard = find_standardized_element_containing_source_pen(
        absorbed_standard,
        source_pen_position=8,
    )
    assert almost_equal(
        standardized_feature_endpoint_price(
            absorbed_current_standard,
            feature_pen_direction=Direction.UP,
        ),
        7.0,
    )
    absorbed_candidate = CandidateTop(
        endpoint=absorbed_high_pens[2].end,
        boundary_b_raw_index=1,
        A=_feature(1.0, 5.0, 1),
        B=_feature(6.0, 10.0, 3),
        C=_feature(2.0, 4.0, 5),
        first_reverse_pen_position=3,
        segment_start_position=0,
        available_at=absorbed_high_pens[5].end,
    )
    absorbed_high_decision = judge_secondary_sequence(
        absorbed_candidate,
        absorbed_high_pens,
    )
    assert absorbed_high_decision.status == CandidateStatus.PENDING
    assert any(
        "等待独立 C2" in item
        for item in absorbed_high_decision.trace
    )
    assert absorbed_high_decision.proof["secondary_innovation_wait"] is not None
    assert "BASIS-016" in absorbed_high_decision.basis_ids

    # 完整价格镜像后，同一边界必须成为实际向下线段的对称等待结论。
    mirrored_absorbed_high_pens = [
        mirror_pen(pen)
        for pen in absorbed_high_pens
    ]
    mirrored_absorbed_high_result = judge_one_segment_offline(
        mirrored_absorbed_high_pens,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert mirrored_absorbed_high_result.status == SegmentStatus.PENDING
    assert mirrored_absorbed_high_result.endpoint is not None
    assert almost_equal(mirrored_absorbed_high_result.endpoint.price, -10.0)

    a = _feature(1.0, 3.0, 6)
    top = _feature(4.0, 7.0, 7)
    c = _feature(2.0, 5.0, 8)
    assert is_symmetric_top_fractal(a, top, c)

    assert bottom_rule_5_vs_7(10.0, 9.0), "5>7 时固定图的底分型应成立"
    assert not bottom_rule_5_vs_7(10.0, 10.0), "5=7 时包含，底分型不成立"
    assert not bottom_rule_5_vs_7(9.0, 10.0), "5<7 时底分型不成立"


def _check_left_a_scope_rules() -> None:
    """TEST-034/035：合成结构用例；不冒充作者原图的实际价格。"""
    single = _pens_from_pivot_prices([0, 100, 80, 95, 75, 110, 98, 105, 90])
    multiple = _pens_from_pivot_prices([0, 100, 80, 95, 75, 90, 70, 110, 98, 105, 90])
    touching = _pens_from_pivot_prices([0, 100, 80, 95, 75, 110, 100, 105, 90])
    not_broken = _pens_from_pivot_prices([0, 100, 80, 95, 75, 110, 101, 105, 90])
    t_equal = _pens_from_pivot_prices([0, 100, 80, 95, 75, 100, 90, 98, 70])
    middle_equal = _pens_from_pivot_prices([0, 100, 80, 100, 75, 110, 98, 105, 90])
    middle_higher = _pens_from_pivot_prices([0, 100, 80, 115, 85, 95, 75, 110, 98, 105, 90])
    contained_left = _pens_from_pivot_prices([0, 10, 0, 8, 2, 11, 1, 12, 4, 9, 0])
    unavailable = _pens_from_pivot_prices([0, 10, 2, 12, 4, 11, 3])
    right_gap = _pens_from_pivot_prices([0, 100, 80, 95, 75, 110, 98, 108, 102, 104, 90])
    trend_shared = _pens_from_pivot_prices([0, 100, 80, 95, 75, 90, 77, 110, 98, 105, 99, 108, 97])
    fixtures = (
        (single, 2, 110, (1,), "no_new_high_special"),
        (multiple, 3, 110, (1,), "no_new_high_special"),
        (touching, 2, 110, (1,), "no_new_high_special"),
        (not_broken, 2, 110, (3,), "nearest"),
        (t_equal, 2, 100, (3,), "nearest"),
        (middle_equal, 2, 110, (1, 3), "nearest"),
        (middle_higher, 3, 110, (5,), "nearest"),
        (contained_left, 3, 12, (1, 3, 5), "nearest"),
        (unavailable, 2, 11, None, "nearest_above_T"),
        (right_gap, 2, 110, (1,), "no_new_high_special"),
        (trend_shared, 3, 110, (1,), "no_new_high_special"),
    )
    for pens, boundary, price, sources, reason in fixtures:
        raw, down = collect_primary_down_features(pens, 0)
        selected, selected_reason = _left_A_selection_at_boundary(raw, boundary, price)
        assert selected_reason == reason
        assert (None if selected is None else selected.source_pen_positions) == sources
        left = standardize_feature_sequence(raw[:boundary], Direction.DOWN)
        expected = (_highest_eligible_left_A(left, price)
                    if reason == "no_new_high_special" else
                    None if sources is None else left[-1])
        assert selected == expected, "必须保留所选元素自己的低点、来源及时序"

        # B₀ 完成后添加未来笔不会改选该 T 的 A；有 C 和无 C 路径一致。
        for length in range(down[boundary].position + 1, len(pens) + 1):
            turn_attempt = next(
                attempt for attempt in find_hypothetical_turn_attempts(pens[:length], 0)
                if points_equal(attempt.turn.endpoint, down[boundary].start)
            )
            assert turn_attempt.turn.A == selected
            assert turn_attempt.turn.a_selection_reason == reason
            if turn_attempt.primary_top is not None:
                assert turn_attempt.primary_top.A == selected
                assert turn_attempt.primary_top.a_selection_reason == reason

        # 每个历史前缀独立运行已知起点引擎，镜像的端点与确认时钟对应。
        for length in range(3, len(pens) + 1):
            up = judge_one_segment_offline(pens[:length], 0, Direction.UP)
            down_result = judge_one_segment_offline(
                [mirror_pen(pen) for pen in pens[:length]], 0, Direction.DOWN,
            )
            assert (up.status, up.case_name) == (down_result.status, down_result.case_name)
            for original, mirrored in ((up.endpoint, down_result.endpoint),
                                       (up.confirmed_at, down_result.confirmed_at)):
                assert (original is None) == (mirrored is None)
                if original is not None and mirrored is not None:
                    assert almost_equal(original.order, mirrored.order)
                    assert almost_equal(original.price, -mirrored.price)

    single_raw, single_down = collect_primary_down_features(single, 0)
    single_candidate = build_candidate_at_boundary(single_raw, single_down, 2)
    assert single_candidate is not None
    assert (single_candidate.A.source_pen_positions, single_candidate.B.source_pen_positions,
            single_candidate.C.source_pen_positions) == ((1,), (5,), (7,))
    assert interval_overlap(single_candidate.A, single_candidate.B)

    gap_raw, gap_down = collect_primary_down_features(right_gap, 0)
    gap_candidate = build_candidate_at_boundary(gap_raw, gap_down, 2)
    assert gap_candidate is not None
    assert gap_candidate.A.source_pen_positions == (1,)
    assert gap_candidate.B.source_pen_positions == (5, 7)
    assert interval_gap(gap_candidate.A, gap_candidate.B)
    gap_decision = judge_one_candidate(gap_candidate, right_gap[0].start, right_gap)
    assert gap_decision.status == CandidateStatus.PENDING
    assert "A/B 有特征序列缺口，进入第二种情况" in gap_decision.trace
    assert gap_decision.proof["ordinary_branch_locked_at"] == gap_candidate.available_at

    trend_attempt = next(attempt for attempt in find_hypothetical_turn_attempts(trend_shared[:11], 0)
                         if almost_equal(attempt.turn.endpoint.price, 110))
    assert trend_attempt.primary_top is None
    assert trend_attempt.turn.A is not None
    assert trend_attempt.turn.A.source_pen_positions == (1,)
    pattern = find_trend_a_pattern(trend_attempt.turn, trend_shared[:11])
    assert pattern is not None and pattern.left_primary_A == trend_attempt.turn.A

    # 保留旧过滤边界；EPS 内合格 / 超过 EPS 不合格。
    high = _feature(2, 10 + 0.5 * EPS, 200)
    assert _highest_eligible_left_A([high], 10) is high
    assert _highest_eligible_left_A([_feature(2, 10 + 2 * EPS, 201)], 10) is None
    assert standardized_left_A_at_boundary([], 0, 10) is None


def _check_ordinary_candidate_rules() -> None:
    # 普通 A 是左侧最近标准元素。这里依次为 [10,20]、[3,12]，T=17；
    # 最近元素 [3,12] 合格，更早高于 T 的元素不影响它，也不跳过 T。
    highest_left_a_pens = _pens_from_pivot_prices(
        [11, 20, 10, 12, 3, 17, 12, 16, 3]
    )
    highest_left_raw, _ = collect_primary_down_features(
        highest_left_a_pens,
        0,
    )
    highest_left_standard = standardize_feature_sequence(
        highest_left_raw[:2],
        feature_pen_direction=Direction.DOWN,
    )
    assert [element.high for element in highest_left_standard] == [20, 12]
    highest_left_A = standardized_left_A_at_boundary(
        highest_left_raw,
        b_raw_index=2,
        candidate_price=17.0,
    )
    assert highest_left_A is not None
    assert almost_equal(highest_left_A.high, 12.0), (
        "普通 A 必须取最近标准元素，不能让更早高点取消 T"
    )

    # 最近元素 high=12>T=11：保留 T，但不能回退选择更早的 high=10。
    a_above_t_prices = [0, 10, 2, 12, 4, 11, 3]
    a_above_t_pens = _pens_from_pivot_prices(a_above_t_prices)
    a_above_t_attempts = find_hypothetical_turn_attempts(
        a_above_t_pens,
        start_pos=0,
    )
    a_above_t_attempt = next(
        attempt
        for attempt in a_above_t_attempts
        if almost_equal(attempt.turn.endpoint.price, 11.0)
        and almost_equal(attempt.turn.endpoint.order, 5.0)
    )
    assert a_above_t_attempt.turn.A is None
    assert a_above_t_attempt.turn.a_selection_reason == "nearest_above_T"
    assert a_above_t_attempt.primary_top is None

    # 若全部左侧标准元素都高于 T，本候选没有 A，但 T 仍必须出现在候选
    # 时序中。它不能建立 A-B-C 或走势 A；稍后的创新高仍可否定旧 T，
    # 并把真实创新高向上笔终点建立为 T'。
    no_eligible_a_pens = _pens_from_pivot_prices([0, 10, 2, 8, 1, 12, 3])
    no_eligible_a_attempt = next(
        attempt
        for attempt in find_hypothetical_turn_attempts(no_eligible_a_pens, 0)
        if almost_equal(attempt.turn.endpoint.price, 8.0)
    )
    assert no_eligible_a_attempt.turn.A is None
    assert no_eligible_a_attempt.primary_top is None
    assert no_eligible_a_attempt.pre_top_new_high_pen is not None
    no_eligible_a_decision = resolve_without_primary_top(
        no_eligible_a_attempt.turn,
        no_eligible_a_pens,
        no_eligible_a_attempt.pre_top_new_high_pen,
    )
    assert no_eligible_a_decision.status == CandidateStatus.REJECTED
    assert no_eligible_a_decision.successor_candidate_t is not None
    assert almost_equal(no_eligible_a_decision.successor_candidate_t.price, 12.0)
    assert "BASIS-032" in no_eligible_a_decision.basis_ids

    # 连续链回归：较早高于 T 的历史元素不能压过合格的最近元素。
    # 此例收窄后仍形成 UP(0->5)、DOWN(5->8)、UP(8->15) 三段。
    candidate_scoped_a_prices = [
        0, 10, 5, 12, 0, 19, 10, 17, 0, 18,
        3, 5, 4, 7, 2, 8, 4, 5, 0, 4,
    ]
    candidate_scoped_a_pens = _pens_from_pivot_prices(candidate_scoped_a_prices)
    candidate_scoped_a_start = find_earliest_segment_start(candidate_scoped_a_pens)
    assert candidate_scoped_a_start is not None
    candidate_scoped_a_chain = judge_alternating_segments_offline(
        candidate_scoped_a_pens,
        candidate_scoped_a_start.start_pos,
        candidate_scoped_a_start.direction,
    )
    assert [
        int(segment.endpoint.order)
        for segment in candidate_scoped_a_chain.confirmed_segments
        if segment.endpoint is not None
    ] == [5, 8, 15]

    # 最高起点在 EPS 内并列时取时间更晚的标准元素。相邻同高元素会
    # 因包含被合并，所以这里用一个向左下错位的元素隔开两个同高元素。
    tied_left_raw = [
        _feature(2.0, 12.0, 100),
        _feature(0.0, 10.0, 101),
        _feature(1.0, 12.0 - 0.75 * EPS, 102),
        _feature(-1.0, 9.0, 103),
        _feature(0.0, 12.0 - 1.5 * EPS, 104),
        _feature(3.0, 13.0, 105),
    ]
    tied_left_A = _highest_eligible_left_A(tied_left_raw[:5], 13.0)
    assert tied_left_A is not None
    # source=102 距全局最高点不足 EPS，属于较晚并列；source=104 只与
    # source=102 相差不足 EPS、但距全局最高点已超过 EPS，不能链式漂移。
    assert tied_left_A.source_pen_positions == (102,)
    # 中间 source=104 与所选 H 的 high 只差 0.75EPS，不满足严格低顶；
    # 统一入口不能套用特例，普通路径必须改取最近 source=104。
    nearest_tied_A, tied_reason = _left_A_selection_at_boundary(tied_left_raw, 5, 13.0)
    assert nearest_tied_A is not None
    assert nearest_tied_A.source_pen_positions == (104,)
    assert tied_reason == "nearest"

    highest_left_result = judge_one_segment_offline(
        highest_left_a_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert highest_left_result.status == SegmentStatus.CONFIRMED
    assert highest_left_result.endpoint is not None
    assert almost_equal(highest_left_result.endpoint.price, 17.0)
    assert highest_left_result.case_name == EndCase.CASE_1_NO_GAP
    mirrored_highest_left_result = judge_one_segment_offline(
        [mirror_pen(pen) for pen in highest_left_a_pens],
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert mirrored_highest_left_result.status == SegmentStatus.CONFIRMED
    assert mirrored_highest_left_result.endpoint is not None
    assert almost_equal(mirrored_highest_left_result.endpoint.price, -17.0)
    assert mirrored_highest_left_result.case_name == EndCase.CASE_1_NO_GAP

    # 第一种情况端到端：
    # A=[2,5]、B=[4,8] 重叠；A-B-C 形成第一特征序列顶分型。
    case_1_pens = _pens_from_pivot_prices([0, 5, 2, 8, 4, 6, 1])
    case_1_result = judge_one_segment_offline(
        case_1_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert case_1_result.status == SegmentStatus.CONFIRMED
    assert case_1_result.case_name == EndCase.CASE_1_NO_GAP
    assert "BASIS-015" in case_1_result.basis_ids
    assert case_1_result.proof["same_candidate_cannot_enter_trend_A_later"] is True
    assert case_1_result.proof["ordinary_branch_locked_at"] is not None
    assert case_1_result.confirmed_at is not None
    assert case_1_result.confirmed_at.order == 6.0
    assert case_1_result.proof["case_1_confirmed_before_C_source_pen_end"] is False
    assert case_1_result.endpoint is not None and almost_equal(case_1_result.endpoint.order, 3.0)
    assert case_1_result.included_pen_positions == (0, 1, 2), (
        "端点 T 之后开始的反向笔不能计入旧向上线段"
    )

    # 第一种情况的确认不必等到 C 最后来源向下笔的终点。通达信逐 K 线
    # H/L 表明：冻结 C 的最后来源笔从 order=10 向下运行至 order=14，
    # order=11 的 low=4 只与冻结标准 B.low 相等，不算严格跌破；
    # order=12 才第一次出现 low=3.5<标准 B.low=4。此时三个顶分型
    # 比较都已确定，confirmed_at 必须提前到 order=12；available_at
    # 仍保留为完整 C 来源笔结束的 order=14，供结构来源审计。
    case_1_intrapen_pens = _case_1_intrapen_tdx_pens()
    assert tuple(
        bar.order for bar in case_1_intrapen_pens[-1].price_bars
    ) == (10.0, 11.0, 12.0, 13.0, 14.0)
    case_1_intrapen_result = judge_one_segment_offline(
        case_1_intrapen_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert case_1_intrapen_result.status == SegmentStatus.CONFIRMED
    assert case_1_intrapen_result.case_name == EndCase.CASE_1_NO_GAP
    assert case_1_intrapen_result.confirmed_at is not None
    assert case_1_intrapen_result.confirmed_at.order == 12.0, (
        "第一种情况应在 C 最后来源向下笔首次严格跌破冻结标准 B.low 时确认，"
        "不得等待该笔终点"
    )
    assert case_1_intrapen_result.confirmed_at.price == 3.5
    assert (
        case_1_intrapen_result.proof[
            "case_1_break_threshold_standard_B.low"
        ]
        == 4.0
    )
    assert (
        case_1_intrapen_result.proof[
            "case_1_confirmed_before_C_source_pen_end"
        ]
        is True
    )
    assert case_1_intrapen_result.proof["ordinary_branch_locked_at"].order == 12.0
    assert case_1_intrapen_result.proof["primary_top_available_at"].order == 14.0
    assert "BASIS-022" in case_1_intrapen_result.basis_ids

    # 名称辨析：第一种情况比较的是冻结后的标准元素 B.low，而不是
    # 未合并的原始第一反向笔 B0.low。本例 B0=[1,8] 包含 [4,7]，
    # 高高合并后标准 B=[4,8]。C 最后来源笔在 order=15 首次跌破 4，
    # 但到 order=17 才跌破 1；确认时点必须是前者。
    standard_B_threshold_pens = _case_1_standard_B_threshold_tdx_pens()
    standard_B_threshold_result = judge_one_segment_offline(
        standard_B_threshold_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert standard_B_threshold_result.status == SegmentStatus.CONFIRMED
    assert standard_B_threshold_result.case_name == EndCase.CASE_1_NO_GAP
    assert standard_B_threshold_result.confirmed_at is not None
    assert standard_B_threshold_result.confirmed_at.order == 15.0
    assert (
        standard_B_threshold_result.proof[
            "case_1_break_threshold_standard_B.low"
        ]
        == 4.0
    )
    assert (
        standard_B_threshold_result.proof[
            "first_reverse_raw_pen_B0.low"
        ]
        == 1.0
    )
    assert standard_B_threshold_result.proof["primary_top_available_at"].order == 18.0

    # 更强破坏仍是有效的第一种情况：A=[2,5]、B=[1,8]、C=[0,6]。
    # B.low<A.low 不取消第一特征序列顶分型；C.low<B.low 仍必须严格成立。
    stronger_break_pens = _pens_from_pivot_prices([0, 5, 2, 8, 1, 6, 0])
    stronger_break_result = judge_one_segment_offline(
        stronger_break_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert stronger_break_result.status == SegmentStatus.CONFIRMED
    assert stronger_break_result.case_name == EndCase.CASE_1_NO_GAP
    assert stronger_break_result.endpoint is not None
    assert stronger_break_result.endpoint.order == 3.0
    assert almost_equal(stronger_break_result.endpoint.price, 8.0)
    assert "L3" in stronger_break_result.proof["first_pen_strength"]
    assert stronger_break_result.proof["stronger_break_B.low<A.low"] is True
    assert (
        stronger_break_result.proof[
            "B.low_vs_A.low_is_not_a_candidate_requirement"
        ]
        is True
    )
    assert all(
        stronger_break_result.proof[
            "primary_top_required_comparisons"
        ].values()
    )
    assert "BASIS-006" in stronger_break_result.basis_ids

    # 同一输入若由上一段走势 A 传入 order=4 的候选 T 下界，原本位于
    # order=3 的第一种情况端点必须被过滤；它不能因为自己的证书完整就
    # 绕过时间下界。过滤只作用于候选，不删除更早输入笔。
    floor_filtered_case_1 = judge_one_segment_offline(
        case_1_pens,
        start_pos=0,
        segment_direction=Direction.UP,
        candidate_t_not_before=Point(order=4.0, price=4.0),
    )
    assert floor_filtered_case_1.status == SegmentStatus.PENDING
    assert floor_filtered_case_1.endpoint is None
    assert any(
        "已忽略其前方 1 个假设点" in item
        for item in floor_filtered_case_1.trace
    )
    floor_equal_case_1 = judge_one_segment_offline(
        case_1_pens,
        start_pos=0,
        segment_direction=Direction.UP,
        candidate_t_not_before=Point(order=3.0, price=8.0),
    )
    assert floor_equal_case_1.status == SegmentStatus.CONFIRMED
    assert floor_equal_case_1.endpoint is not None
    assert floor_equal_case_1.endpoint.order == 3.0

    # 第二特征序列的 A2 不是 B2 左边紧邻的标准元素，而是 B2 之前
    # 全部标准元素中 high 最高者；最高 high 在 EPS 内并列时取较晚者。
    # 本例在 C2 到来前始终单向下降，不存在更早底分型；C2 到来后，
    # B2=[3,6]，其左侧为 [8,12]、[7,11]。两者都能证明 B2 左侧，
    # 但冻结证书中的 A2 必须是全局最高的 [8,12]，不能取左邻 [7,11]。
    secondary_global_a_pens = _pens_from_pivot_prices(
        [0, 5, 2, 20, 8, 12, 7, 11, 3, 6, 4, 7]
    )
    secondary_global_a_raw = [
        pen_to_feature(secondary_global_a_pens[position])
        for position in (4, 6, 8, 10)
    ]
    secondary_global_a_standard = standardize_feature_sequence(
        secondary_global_a_raw,
        feature_pen_direction=Direction.UP,
    )
    secondary_global_bottom = find_new_bottom_created_by_latest_element(
        secondary_global_a_standard,
        latest_raw_pen_position=10,
    )
    assert secondary_global_bottom is not None
    secondary_a2, secondary_b2, secondary_c2 = secondary_global_bottom
    assert secondary_a2.source_pen_positions == (4,), (
        "第二特征序列 A2 必须取 B2 前全部标准元素中的最高 high，"
        "不能取 B2 的左邻元素"
    )
    assert secondary_b2.source_pen_positions == (8,)
    assert secondary_c2.source_pen_positions == (10,)

    # 第二特征序列 A2 的 EPS 并列也必须相对于数值上的全局最高 high
    # 判断，不能把相邻近似值逐项串联，否则 A2 会沿着“每步都小于 EPS”
    # 的阶梯漂移到实际已经低于全局最高点超过 EPS 的元素。
    secondary_tied_standard = standardize_feature_sequence(
        [
            _feature(2.0, 12.0, 100),
            _feature(0.0, 10.0, 101),
            _feature(1.0, 12.0 - 0.75 * EPS, 102),
            _feature(-1.0, 9.0, 103),
            _feature(0.0, 12.0 - 1.5 * EPS, 104),
            _feature(-2.0, 8.0, 105),
        ],
        feature_pen_direction=Direction.UP,
    )
    assert len(secondary_tied_standard) == 6
    secondary_tied_A2 = select_secondary_A_before_B(
        secondary_tied_standard,
        b2_index=5,
    )
    assert secondary_tied_A2 is not None
    assert secondary_tied_A2.source_pen_positions == (102,), (
        "A2 的 EPS 并列必须锚定全局最高 high，不能发生链式漂移"
    )

    # 当前原始笔若触发低低包含，标准化后的最后三元素即使仍满足
    # 底分型四比较，也不是“由当前笔首次完成”的底分型：加入当前笔前，
    # 同一个 A2/B2 与尚未合并的右侧元素已经构成底分型。
    preexisting_bottom_raw = [
        _feature(8.0, 12.0, 4),
        _feature(3.0, 6.0, 6),
        _feature(4.0, 7.0, 8),
        _feature(3.5, 11.0, 10),
    ]
    preexisting_bottom_before_containment = standardize_feature_sequence(
        preexisting_bottom_raw[:-1],
        feature_pen_direction=Direction.UP,
    )
    assert find_new_bottom_created_by_latest_element(
        preexisting_bottom_before_containment,
        latest_raw_pen_position=8,
    ) is not None
    preexisting_bottom_after_containment = standardize_feature_sequence(
        preexisting_bottom_raw,
        feature_pen_direction=Direction.UP,
    )
    assert is_secondary_bottom_fractal(
        *preexisting_bottom_after_containment
    )
    assert preexisting_bottom_after_containment[-1].source_pen_positions == (
        8,
        10,
    )
    assert find_new_bottom_created_by_latest_element(
        preexisting_bottom_after_containment,
        latest_raw_pen_position=10,
    ) is None

    # 第二种情况端到端：
    # A=[2,5]、B=[6,8] 有缺口；第二特征序列的三个向上笔
    # [6,7]、[1,3]、[2,5] 构成严格底分型，确认早先顶 T=8。
    case_2_pens = _pens_from_pivot_prices([0, 5, 2, 8, 6, 7, 1, 3, 2, 5])
    case_2_result = judge_one_segment_offline(
        case_2_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert case_2_result.status == SegmentStatus.CONFIRMED
    assert case_2_result.case_name == EndCase.CASE_2_WITH_GAP
    assert "BASIS-015" in case_2_result.basis_ids
    assert "BASIS-016" in case_2_result.basis_ids
    assert case_2_result.endpoint is not None and almost_equal(case_2_result.endpoint.price, 8.0)
    assert case_2_result.confirmed_at is not None
    assert case_2_result.confirmed_at.order > case_2_result.endpoint.order, (
        "第二种情况的确认时点必须晚于结构端点"
    )

    # 组成底分型的最后一根原始向上笔终点为 9>T=8：同一事件已经
    # 完成底分型，必须先认底分型并确认 T=8。
    same_event_pens = _pens_from_pivot_prices([0, 5, 2, 8, 6, 7, 1, 3, 2, 9])
    same_event_candidate = find_hypothetical_turn_attempts(
        same_event_pens,
        0,
    )[0].primary_top
    assert same_event_candidate is not None
    same_event_decision = judge_secondary_sequence(
        same_event_candidate,
        same_event_pens,
    )
    assert same_event_decision.status == CandidateStatus.CONFIRMED
    assert same_event_decision.case_name == EndCase.CASE_2_WITH_GAP
    assert same_event_decision.endpoint is not None
    assert almost_equal(same_event_decision.endpoint.price, 8.0)
    assert same_event_decision.confirmed_at is not None
    assert almost_equal(same_event_decision.confirmed_at.price, 9.0)
    assert (
        same_event_decision.proof[
            "latest_standardized_up_endpoint_above_t"
        ]
        is True
    )
    assert (
        same_event_decision.proof[
            "bottom_precedes_same_event_raw_new_high"
        ]
        is True
    )
    assert same_event_decision.proof["latest_raw_up_endpoint_above_t"] is True
    assert (
        same_event_decision.proof["latest_raw_up_pen_triggered_containment"]
        is False
    )

    # 原始创新高 U=[3,11] 被前一标准元素 [4,7] 包含处理后成为
    # B*=[3,7]，其左侧 A*=[6,8]，因此不能立即延续，而要固定
    # U.start=3 等待独立 C。后来的 [4,6] 被 B* 包含，只能继续等；
    # 再后来的原始向上笔 [2,5] 起点严格低于固定下界 3，必须在该笔
    # 完成时否定旧 T，但接续候选仍是最初创新高笔 U.end=11。
    innovation_wait_lower_start_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 6, 8, 4, 7, 3, 11, 4, 6, 2, 5]
    )
    innovation_wait_candidate = CandidateTop(
        endpoint=innovation_wait_lower_start_pens[2].end,
        boundary_b_raw_index=1,
        A=_feature(1.0, 5.0, 1),
        B=_feature(6.0, 10.0, 3),
        C=_feature(2.0, 4.0, 5),
        first_reverse_pen_position=3,
        segment_start_position=0,
        available_at=innovation_wait_lower_start_pens[5].end,
    )
    innovation_wait_rejection = judge_secondary_sequence(
        innovation_wait_candidate,
        innovation_wait_lower_start_pens,
    )
    assert innovation_wait_rejection.status == CandidateStatus.REJECTED
    assert innovation_wait_rejection.successor_candidate_t is not None
    assert points_equal(
        innovation_wait_rejection.successor_candidate_t,
        innovation_wait_lower_start_pens[8].end,
    ), "等待状态被下破时，T' 必须固定为原始创新高笔 U.end"
    assert innovation_wait_rejection.confirmed_at is not None
    assert points_equal(
        innovation_wait_rejection.confirmed_at,
        innovation_wait_lower_start_pens[12].end,
    ), "否定事件必须记在首次起点严格低于 U.start 的后来原始向上笔终点"

    # 若后来原始向上笔的起点严格高于固定 U.start，且低低标准化后
    # 成为独立 C2，则 C2.low>B*.low 和 C2.high>B*.high 同时自动成立，
    # 立即用完整底分型确认旧 T。即使最初 U 创了新高，也不延续到 U。
    innovation_wait_confirm_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 6, 8, 4, 7, 3, 11, 4, 8]
    )
    innovation_wait_confirm_candidate = replace(
        innovation_wait_candidate,
        available_at=innovation_wait_confirm_pens[5].end,
    )
    innovation_wait_confirmation = judge_secondary_sequence(
        innovation_wait_confirm_candidate,
        innovation_wait_confirm_pens,
    )
    assert innovation_wait_confirmation.status == CandidateStatus.CONFIRMED
    assert innovation_wait_confirmation.successor_candidate_t is None
    assert (
        innovation_wait_confirmation.proof[
            "confirmed_after_raw_innovation_wait"
        ]
        is True
    )
    waited_bottom = innovation_wait_confirmation.proof["secondary_bottom"]
    assert is_secondary_bottom_fractal(*waited_bottom)
    assert waited_bottom[2].source_pen_positions == (10,)

    # 与固定 U.start 在 EPS 内相等不确认 T。相同低点会让后来元素继续
    # 与 B* 包含，数据结束时仍处于同一个等待状态。
    innovation_wait_equal_start_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 6, 8, 4, 7, 3, 11, 3, 8]
    )
    innovation_wait_equal_candidate = replace(
        innovation_wait_candidate,
        available_at=innovation_wait_equal_start_pens[5].end,
    )
    innovation_wait_equal = judge_secondary_sequence(
        innovation_wait_equal_candidate,
        innovation_wait_equal_start_pens,
    )
    assert innovation_wait_equal.status == CandidateStatus.PENDING
    assert innovation_wait_equal.proof["secondary_innovation_wait"] is not None
    assert any(
        "与固定 U.start 近似相等" in item
        for item in innovation_wait_equal.trace
    )

    # 固定 U.start 下界只属于原始创新高等待分支。这里所有向上笔终点
    # 都不高于 T=10，后续起点虽然持续降低，也只能按普通低低标准化和
    # 底分型流程处理，绝不能套用创新高等待分支的下界直接否定 T。
    ordinary_non_high_lower_starts = _pens_from_pivot_prices(
        [0, 5, 2, 10, 6, 8, 4, 7, 2, 6]
    )
    ordinary_non_high_candidate = CandidateTop(
        endpoint=ordinary_non_high_lower_starts[2].end,
        boundary_b_raw_index=1,
        A=_feature(1.0, 5.0, 1),
        B=_feature(6.0, 10.0, 3),
        C=_feature(2.0, 4.0, 5),
        first_reverse_pen_position=3,
        segment_start_position=0,
        available_at=ordinary_non_high_lower_starts[5].end,
    )
    ordinary_non_high_decision = judge_secondary_sequence(
        ordinary_non_high_candidate,
        ordinary_non_high_lower_starts,
    )
    assert ordinary_non_high_decision.status == CandidateStatus.PENDING
    assert ordinary_non_high_decision.successor_candidate_t is None

    # 即使第二特征序列尚不足三根，也必须逐笔处理原始创新高。这里
    # [6.5,9] 与 [6,7] 互不包含；前者作为 B* 时，最高 A2.high=7
    # 不高于 B*.high=9，因而直接延续到原始创新高笔终点 9。
    short_secondary_high_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 6, 7, 6.5, 9]
    )
    short_secondary_candidate = CandidateTop(
        endpoint=short_secondary_high_pens[2].end,
        boundary_b_raw_index=1,
        A=_feature(1.0, 5.0, 1),
        B=_feature(6.0, 8.0, 3),
        C=_feature(2.0, 4.0, 5),
        first_reverse_pen_position=3,
        segment_start_position=0,
        available_at=short_secondary_high_pens[5].end,
    )
    short_secondary_decision = judge_secondary_sequence(
        short_secondary_candidate,
        short_secondary_high_pens,
    )
    assert short_secondary_decision.status == CandidateStatus.REJECTED
    assert (
        short_secondary_decision.proof["raw_innovation_triggering_pen"].position
        == 6
    )
    assert short_secondary_decision.proof["innovation_exit_reason"] == (
        "B2_NOT_QUALIFIED"
    )
    assert short_secondary_decision.successor_candidate_t is not None
    assert points_equal(
        short_secondary_decision.successor_candidate_t,
        short_secondary_high_pens[6].end,
    )

    # 普通第二种情况的创新高接续：T=8 的第二特征序列依次得到
    # U1=[6,7]、U2=[1,9]、U3=[4,10]。U2 是第一根原始创新高笔；
    # 它与 U1 低低合并后只剩一个标准元素，B* 左侧没有 A2，所以
    # 立即延续到 U2.end=9，建立 T'=9。时间上位于 T/T' 之间的
    # 候选 7 仍须完整检查；抵达 9、后来再到 10 时也都正常检查。
    successor_jump_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 6, 7, 1, 9, 4, 10, 3]
    )
    successor_attempts = find_hypothetical_turn_attempts(
        successor_jump_pens,
        0,
    )
    assert [
        attempt.turn.endpoint.price
        for attempt in successor_attempts[:4]
    ] == [8.0, 7.0, 9.0, 10.0]
    successor_jump_result = judge_one_segment_offline(
        successor_jump_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert successor_jump_result.status == SegmentStatus.PENDING
    assert any(
        "T=8.0 被 T'=9.0 取代" in item
        for item in successor_jump_result.trace
    )
    for candidate_price in (7.0, 9.0, 10.0):
        assert any(
            "检查假设点" in item and f"T.price={candidate_price}" in item
            for item in successor_jump_result.trace
        )
    assert not any(
        "直接淘汰中间候选" in item
        for item in successor_jump_result.trace
    )
    successor_history = successor_jump_result.proof["candidate_successor_events"]
    assert [
        (event.rejected_t.price, event.successor_t.price, event.source_case)
        for event in successor_history
    ] == [
        (8.0, 9.0, EndCase.CASE_2_WITH_GAP),
        (7.0, 9.0, None),
        (9.0, 10.0, None),
    ]
    assert "BASIS-023" in successor_jump_result.basis_ids

    # 区分性回归：旧 T=9 被第二种情况第一根原始创新高笔否定并记录
    # T'=13；时间上位于两者之间的候选 T=5 仍须完整检查，抵达 T'=13
    # 后它再按第一种情况确认。若把 T' 当跳转游标，中间候选 5 会被漏掉。
    intermediate_confirming_pens = _pens_from_pivot_prices(
        [0, 3, 2, 9, 4, 5, 1, 13, 3, 12, 2]
    )
    intermediate_attempts = find_hypothetical_turn_attempts(
        intermediate_confirming_pens,
        0,
    )
    intermediate_first_candidate = intermediate_attempts[0].primary_top
    assert intermediate_first_candidate is not None
    first_intermediate_decision = judge_one_candidate(
        intermediate_first_candidate,
        intermediate_confirming_pens[0].start,
        intermediate_confirming_pens,
    )
    assert first_intermediate_decision.status == CandidateStatus.REJECTED
    assert first_intermediate_decision.successor_candidate_t is not None
    assert first_intermediate_decision.successor_candidate_t.price == 13.0
    intermediate_confirming_result = judge_one_segment_offline(
        intermediate_confirming_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert intermediate_confirming_result.status == SegmentStatus.CONFIRMED
    assert intermediate_confirming_result.case_name == EndCase.CASE_1_NO_GAP
    assert intermediate_confirming_result.endpoint is not None
    assert intermediate_confirming_result.endpoint.order == 7.0
    assert intermediate_confirming_result.endpoint.price == 13.0
    assert any(
        "T.price=5.0" in item
        for item in intermediate_confirming_result.trace
    )

    # 若数据恰好结束在创新高向上笔终点，T' 已经产生，但还没有从 T'
    # 开始的第一反向笔，无法构造 A-B-C 或走势 A。此时必须把 T' 作为
    # 当前待定候选保留下来；在到达它之前仍必须检查中间候选，也不能伪造确认。
    successor_without_reverse_pen = _pens_from_pivot_prices(
        [0, 5, 2, 8, 6, 7, 5, 9]
    )
    successor_waiting_result = judge_one_segment_offline(
        successor_without_reverse_pen,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert successor_waiting_result.status == SegmentStatus.PENDING
    assert successor_waiting_result.endpoint is not None
    assert successor_waiting_result.endpoint.order == 7.0
    assert successor_waiting_result.endpoint.price == 9.0
    assert (
        successor_waiting_result.proof[
            "awaiting_first_reverse_pen_after_successor_T"
        ]
        is True
    )
    assert any(
        "T.price=7.0" in item
        for item in successor_waiting_result.trace
    )
    assert "BASIS-023" in successor_waiting_result.basis_ids

    # q1 包含右侧首根向下笔时，先执行第一特征序列的普通高高合并；
    # 如果数据到此结束，右侧仍只有一个标准元素，没有独立 C，不能生成证书。
    ordinary_primary_containment_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 1, 7, 2]
    )
    ordinary_primary_containment_result = judge_one_segment_offline(
        ordinary_primary_containment_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert ordinary_primary_containment_result.status == SegmentStatus.PENDING
    assert ordinary_primary_containment_result.case_name is None
    assert ordinary_primary_containment_result.endpoint is None

    # 在上一前缀之后补入 C=[0,6]，包含后的 B=[2,8] 恰有
    # B.low=A.low=2。第一特征序列专用判据不比较这两个低点，
    # 而 C.low=0<B.low=2，所以候选必须成立并进入第一种情况。
    equal_a_b_low_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 1, 7, 2, 6, 0]
    )
    equal_a_b_low_result = judge_one_segment_offline(
        equal_a_b_low_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert equal_a_b_low_result.status == SegmentStatus.CONFIRMED
    assert equal_a_b_low_result.case_name == EndCase.CASE_1_NO_GAP
    assert (
        equal_a_b_low_result.proof["A"].low
        == equal_a_b_low_result.proof["B"].low
    )
    assert (
        equal_a_b_low_result.proof["C"].low
        < equal_a_b_low_result.proof["B"].low
    )
    assert (
        equal_a_b_low_result.proof[
            "B.low_vs_A.low_is_not_a_candidate_requirement"
        ]
        is True
    )


def _check_trend_a_entry_and_next_segment_floor() -> None:
    # 走势 A 的 B.low<A.high 必须严格成立。这里 B.low=5 与左侧
    # 第一特征序列标准元素 A.high=5 相等；虽然仍满足通用的“笔破坏”
    # 等号条件，且 B 后紧邻的上/下/上三笔具有公共重叠并由第三笔创新高，
    # 也绝不能据此进入走势 A。此输入到种子完成时仍没有独立 C，专门覆盖
    # 尚未建立 CandidateTop、只持有 HypotheticalTurn 的同步监听路径。
    trend_a_equal_a_high_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 5, 7, 6, 8]
    )
    trend_a_equal_attempt = find_hypothetical_turn_attempts(
        trend_a_equal_a_high_pens,
        0,
    )[0]
    assert trend_a_equal_attempt.primary_top is None
    trend_a_equal_B = trend_a_equal_a_high_pens[
        trend_a_equal_attempt.turn.first_reverse_pen_position
    ]
    trend_a_equal_seed = trend_a_equal_a_high_pens[
        trend_a_equal_attempt.turn.first_reverse_pen_position + 1 :
        trend_a_equal_attempt.turn.first_reverse_pen_position + 4
    ]
    assert almost_equal(
        trend_a_equal_B.low,
        trend_a_equal_attempt.turn.A.high,
    )
    assert [pen.direction for pen in trend_a_equal_seed] == [
        Direction.UP,
        Direction.DOWN,
        Direction.UP,
    ]
    assert common_overlap(trend_a_equal_seed)
    assert trend_a_equal_seed[2].high > trend_a_equal_seed[0].high + EPS
    assert first_reverse_pen_breaks_up_segment(
        trend_a_equal_attempt.turn,
        trend_a_equal_a_high_pens,
    )
    assert find_trend_a_pattern(
        trend_a_equal_attempt.turn,
        trend_a_equal_a_high_pens,
    ) is None, "B.low 必须严格低于左侧 A.high，等号不得进入走势 A"
    trend_a_equal_result = judge_one_segment_offline(
        trend_a_equal_a_high_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert trend_a_equal_result.status == SegmentStatus.PENDING
    assert trend_a_equal_result.case_name != EndCase.TREND_A
    assert "BASIS-021" in trend_a_equal_result.basis_ids

    # 走势 A 还要求以候选 T 为第三笔终点的前三根连续原始笔满足与
    # 自动初始化相同的严格推进标准。这里 T=7 前三笔为
    # 1->8、8->2、2->7：方向和公共重叠均成立、底部也抬高，但
    # T=7 没有严格越过第一笔终点 8，因此不得进入走势 A。
    trend_a_without_strict_pre_t_seed = _pens_from_pivot_prices(
        [0, 6, 1, 8, 2, 7, 0, 4, 2, 5]
    )
    trend_a_without_strict_pre_t_attempt = next(
        attempt
        for attempt in find_hypothetical_turn_attempts(
            trend_a_without_strict_pre_t_seed,
            0,
        )
        if almost_equal(attempt.turn.endpoint.price, 7.0)
    )
    assert trend_a_without_strict_pre_t_attempt.turn.A is None
    assert trend_a_without_strict_pre_t_attempt.turn.a_selection_reason == "nearest_above_T"
    assert find_trend_a_pattern(
        trend_a_without_strict_pre_t_attempt.turn,
        trend_a_without_strict_pre_t_seed,
    ) is None, (
        "走势 A 的候选 T 必须是严格初始化三笔中第三笔的终点；"
        "T 未越过第一笔终点时不得进入走势 A"
    )

    # BASIS-033 范围修订：本段到 T 已有五笔，较早向上笔 2->8 已突破首笔 0->6。
    # 尾部三笔的第二笔未抬高低点，仍通过长前缀资格；三笔原限制另测。
    invalid_pre_t_low = _pens_from_pivot_prices([0, 6, 2, 8, 1, 9, 0, 4, 2, 5])
    invalid_pre_t_low_attempt = next(
        attempt for attempt in find_hypothetical_turn_attempts(invalid_pre_t_low, 0)
        if almost_equal(attempt.turn.endpoint.price, 9)
    )
    assert invalid_pre_t_low_attempt.turn.A is not None
    assert find_trend_a_pattern(invalid_pre_t_low_attempt.turn, invalid_pre_t_low) is not None

    # 走势 A 与 A-B-C 同步检查：走势 A 在 order=7 完成，A-B-C 到
    # order=8 才确立。即使普通第一种情况届时也可确认，仍必须先转入
    # 走势 A。反应走势必须包含三笔 A 种子；其后一根向下笔终点低于
    # q1 终点，外层守卫立即在该原始笔终点确认旧向上线段结束。
    trend_a_pens = _pens_from_pivot_prices([0, 5, 2, 10, 1, 6, 3, 8, 0])
    trend_a_candidate = find_hypothetical_turn_attempts(
        trend_a_pens,
        0,
    )[0].primary_top
    assert trend_a_candidate is not None
    trend_a_pattern = find_trend_a_pattern(trend_a_candidate, trend_a_pens)
    assert trend_a_pattern is not None
    assert trend_a_pattern.q1.low < trend_a_candidate.A.high - EPS
    assert tuple(
        pen.position for pen in (trend_a_pattern.q1, *trend_a_pattern.a_pens)
    ) == tuple(range(trend_a_pattern.q1.position, trend_a_pattern.q1.position + 4))
    assert trend_a_pattern.entered_at.order == 7.0
    assert trend_a_pattern.entered_at.order < trend_a_candidate.available_at.order
    trend_a_raw, _ = collect_primary_down_features(trend_a_pens, 0)
    assert has_containment(trend_a_raw[1], trend_a_raw[2])
    assert len(
        standardize_feature_sequence(
            trend_a_raw[1:3],
            feature_pen_direction=Direction.DOWN,
        )
    ) == 1, (
        "走势 A 种子中间向下笔被 B 包含，故 ABC 不可能先于走势 A 进入"
    )
    assert trend_a_pattern.reactions[:3] == trend_a_pattern.a_pens
    assert tuple(pen.position for pen in trend_a_pattern.reactions) == (4, 5, 6, 7)
    assert judge_case_1(
        trend_a_candidate,
        trend_a_pens[0].start,
        trend_a_pens,
    ).status == SegmentStatus.CONFIRMED
    trend_a_result = judge_one_segment_offline(
        trend_a_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert trend_a_result.status == SegmentStatus.CONFIRMED
    assert trend_a_result.case_name == EndCase.TREND_A
    assert trend_a_result.endpoint is not None and almost_equal(trend_a_result.endpoint.price, 10.0)
    assert trend_a_result.confirmed_at is not None
    assert trend_a_result.confirmed_at.order == 8.0
    assert trend_a_result.proof["outer_guard"] == "DOWN_END_BELOW_Q1_END"
    assert trend_a_result.proof["trend_A_B.low<A.high"] is True
    assert trend_a_result.proof["trend_A_seed_immediately_after_B"] is True
    assert trend_a_result.proof["trend_A_pre_T_seed_ends_at_T"] is True
    assert trend_a_result.proof["trend_A_pre_T_seed_bottom_advances"] is True
    assert trend_a_result.proof["trend_A_pre_T_seed_top_advances"] is True
    assert trend_a_result.proof["temporary_inner_candidates_released"] is True
    assert "BASIS-015" in trend_a_result.basis_ids
    assert "BASIS-018" in trend_a_result.basis_ids
    assert "BASIS-021" in trend_a_result.basis_ids
    assert "BASIS-033" in trend_a_result.basis_ids
    assert trend_a_result.next_segment_t_not_before is not None
    assert points_equal(
        trend_a_result.next_segment_t_not_before,
        trend_a_result.confirmed_at,
    )
    assert trend_a_result.next_segment_t_not_before.order == 8.0

    # 即使调用内部函数时绕过顶层笔流校验，走势 A 入口本身也必须拒绝
    # 非连续来源编号，不能把 B 后隔号出现的三笔误称为“紧邻三笔种子”。
    gapped_positions = list(trend_a_pens)
    for index in range(trend_a_candidate.first_reverse_pen_position + 2, len(gapped_positions)):
        gapped_positions[index] = replace(
            gapped_positions[index],
            position=gapped_positions[index].position + 1,
        )
    assert find_trend_a_pattern(
        trend_a_candidate,
        gapped_positions,
    ) is None, "走势 A 的三笔种子必须在编号上紧邻 B，中间不得缺笔"


def _check_trend_a_nested_reactions() -> None:
    # 反应走势的每根笔都没有触发外层两条直接守卫，但反应走势本身从
    # 第一根 A 种子笔开始形成一条新的向上线段。该内层线段由普通
    # 第一种情况确认结束时，立即确认外层 T0；确认时点取触发该结果的
    # 当前原始笔终点。内层 T1=8 只在特殊分支局部使用，不写入外层结果。
    trend_a_inner_segment_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 1, 6, 3, 8, 4, 6, 2]
    )
    trend_a_inner_result = judge_one_segment_offline(
        trend_a_inner_segment_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert trend_a_inner_result.status == SegmentStatus.CONFIRMED
    assert trend_a_inner_result.case_name == EndCase.TREND_A
    assert trend_a_inner_result.endpoint is not None
    assert almost_equal(trend_a_inner_result.endpoint.price, 10.0)
    assert trend_a_inner_result.confirmed_at is not None
    assert trend_a_inner_result.confirmed_at.order == 10.0
    assert (
        trend_a_inner_result.proof["inner_segment_confirming_case"]
        == EndCase.CASE_1_NO_GAP
    )
    assert trend_a_inner_result.proof["temporary_inner_candidates_released"] is True
    assert "inner_segment_result" not in trend_a_inner_result.proof
    assert "inner_candidate" not in trend_a_inner_result.proof

    # 嵌套走势 A：外层 T0=20 的直接守卫始终不触发；内层 T1=10
    # 进入它自己的走势 A。最后一根向下笔终点 1 不低于外层 q1 终点 1，
    # 却低于内层 q1 终点 2，因此先确认内层线段结束，再由当前笔终点
    # 同步确认外层 T0。结果中仍不得泄露局部 T1。
    nested_trend_a_pens = _pens_from_pivot_prices(
        [0, 5, 2, 20, 1, 6, 3, 10, 2, 5, 3, 7, 1]
    )
    nested_trend_a_result = judge_one_segment_offline(
        nested_trend_a_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert nested_trend_a_result.status == SegmentStatus.CONFIRMED
    assert nested_trend_a_result.case_name == EndCase.TREND_A
    assert nested_trend_a_result.endpoint is not None
    assert almost_equal(nested_trend_a_result.endpoint.price, 20.0)
    assert nested_trend_a_result.confirmed_at is not None
    assert nested_trend_a_result.confirmed_at.order == 12.0
    assert nested_trend_a_result.proof["inner_segment_confirming_case"] == EndCase.TREND_A
    assert nested_trend_a_result.proof["temporary_inner_candidates_released"] is True
    nested_next_floor = nested_trend_a_result.next_segment_t_not_before
    assert nested_next_floor is not None
    assert points_equal(
        nested_next_floor,
        nested_trend_a_result.confirmed_at,
    )
    assert nested_next_floor.order == 12.0, (
        "内层 T1 及其局部状态不得泄露；下一反向段只继承外层确认触发时点"
    )


def _check_trend_a_event_priority_and_pending() -> None:
    # 同一根反应向上笔既严格超过外层 T0=20，又足以让内层第二特征
    # 序列完成底分型。特殊分支必须先采用同一事件的内层底分型证书，
    # 确认外层 T0；不得再用本笔创新高否定 T0。
    inner_case_2_precedes_outer_guard_pens = _pens_from_pivot_prices(
        [0, 5, 2, 20, 0, 6, 3, 10, 7, 8, 2, 4, 3, 21]
    )
    same_event_attempt = find_hypothetical_turn_attempts(
        inner_case_2_precedes_outer_guard_pens,
        0,
    )[0]
    assert same_event_attempt.primary_top is not None
    same_event_pattern = find_trend_a_pattern(
        same_event_attempt.primary_top,
        inner_case_2_precedes_outer_guard_pens,
    )
    assert same_event_pattern is not None
    assert (
        confirming_case_of_isolated_reaction_prefix(same_event_pattern.reactions)
        == EndCase.CASE_2_WITH_GAP
    )
    same_event_decision = judge_trend_a_special(
        same_event_attempt.primary_top,
        inner_case_2_precedes_outer_guard_pens,
    )
    assert same_event_decision.status == CandidateStatus.CONFIRMED
    assert same_event_decision.case_name == EndCase.TREND_A
    assert (
        same_event_decision.proof["inner_segment_confirming_case"]
        == EndCase.CASE_2_WITH_GAP
    )
    assert (
        same_event_decision.proof[
            "same_event_case_2_precedes_outer_up_guard"
        ]
        is True
    )
    assert same_event_decision.proof["outer_up_guard_would_trigger"] is True
    assert same_event_decision.confirmed_at is not None
    assert same_event_decision.confirmed_at.order == 13.0
    assert "BASIS-019" in same_event_decision.basis_ids

    same_event_result = judge_one_segment_offline(
        inner_case_2_precedes_outer_guard_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert same_event_result.status == SegmentStatus.CONFIRMED
    assert same_event_result.case_name == EndCase.TREND_A
    assert same_event_result.endpoint is not None
    assert almost_equal(same_event_result.endpoint.price, 20.0)
    assert same_event_result.confirmed_at is not None
    assert same_event_result.confirmed_at.order == 13.0
    assert same_event_result.next_segment_t_not_before is not None
    assert points_equal(
        same_event_result.next_segment_t_not_before,
        same_event_result.confirmed_at,
    )

    # 严格价格镜像后，同一例外也必须确认实际向下线段的外层候选底，
    # 并把同一个触发时点传给下一向上线段作为候选 T 的时间下界。
    mirrored_same_event_pens = [
        mirror_pen(pen)
        for pen in inner_case_2_precedes_outer_guard_pens
    ]
    mirrored_same_event_result = judge_one_segment_offline(
        mirrored_same_event_pens,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert mirrored_same_event_result.status == SegmentStatus.CONFIRMED
    assert mirrored_same_event_result.endpoint is not None
    assert almost_equal(mirrored_same_event_result.endpoint.price, -20.0)
    assert mirrored_same_event_result.confirmed_at is not None
    assert mirrored_same_event_result.confirmed_at.order == 13.0
    assert (
        mirrored_same_event_result.proof["canonical_mirror_proof"][
            "same_event_case_2_precedes_outer_up_guard"
        ]
        is True
    )
    mirrored_next_floor = mirrored_same_event_result.next_segment_t_not_before
    assert mirrored_next_floor is not None
    assert points_equal(
        mirrored_next_floor,
        mirrored_same_event_result.confirmed_at,
    )

    # 走势 A 例外：A 在 order=7 完成后已进入特殊状态。反应向上笔
    # 随后在 order=9 创出 11>T=10，但截至本笔没有完成内层第二种
    # 情况底分型。因此仍由走势 A 外层守卫否定 T，不得被
    # “顶分型前后续向下笔高起点”的 BASIS-013 普通规则抢占。
    trend_a_then_new_high_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 1, 6, 3, 8, 4, 11, 2]
    )
    trend_a_attempt = find_hypothetical_turn_attempts(
        trend_a_then_new_high_pens,
        0,
    )[0]
    assert trend_a_attempt.primary_top is None
    assert trend_a_attempt.pre_top_new_high_pen is not None
    trend_a_exception = resolve_without_primary_top(
        trend_a_attempt.turn,
        trend_a_then_new_high_pens,
        trend_a_attempt.pre_top_new_high_pen,
    )
    assert trend_a_exception.status == CandidateStatus.REJECTED
    assert trend_a_exception.case_name == EndCase.TREND_A
    assert trend_a_exception.proof["trend_A_entered_at"].order == 7.0
    assert trend_a_exception.confirmed_at is not None
    assert trend_a_exception.confirmed_at.order == 9.0
    assert trend_a_exception.proof["outer_guard"] == "UP_END_ABOVE_T0"
    assert trend_a_exception.proof["same_event_case_2_priority_checked"] is True
    assert trend_a_exception.proof["same_event_inner_confirming_case"] is None
    assert trend_a_exception.proof["temporary_inner_candidates_released"] is True
    assert trend_a_exception.successor_candidate_t is not None
    assert points_equal(
        trend_a_exception.successor_candidate_t,
        trend_a_then_new_high_pens[8].end,
    )
    assert trend_a_exception.successor_candidate_t.order == 9.0
    assert almost_equal(trend_a_exception.successor_candidate_t.price, 11.0)
    assert points_equal(
        trend_a_exception.proof["successor_candidate_T_prime"],
        trend_a_then_new_high_pens[8].end,
    )
    assert "BASIS-014" in trend_a_exception.basis_ids
    assert "BASIS-019" in trend_a_exception.basis_ids
    assert "BASIS-025" in trend_a_exception.basis_ids
    assert "BASIS-013" not in trend_a_exception.basis_ids

    # 走势 A 外层守卫否定后，旧 T=10 立即失效，触发创新高的真实向上笔
    # 终点 11 成为新的 T'。数据若恰好结束在 T'，不得退回旧 T；
    # T/T' 之间的候选 6、8 仍必须按时间顺序检查。
    trend_a_successor_without_reverse_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 1, 6, 3, 8, 4, 11]
    )
    trend_a_successor_waiting_result = judge_one_segment_offline(
        trend_a_successor_without_reverse_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert trend_a_successor_waiting_result.status == SegmentStatus.PENDING
    assert trend_a_successor_waiting_result.endpoint is not None
    assert points_equal(
        trend_a_successor_waiting_result.endpoint,
        trend_a_then_new_high_pens[8].end,
    )
    assert any(
        "走势 A 外层上界守卫接续" in item
        for item in trend_a_successor_waiting_result.trace
    )
    assert any(
        "中间候选" in item and "不跳过" in item
        for item in trend_a_successor_waiting_result.trace
    )
    trend_a_successor_history = trend_a_successor_waiting_result.proof[
        "candidate_successor_events"
    ]
    assert [
        (event.rejected_t.price, event.successor_t.price, event.source_case)
        for event in trend_a_successor_history
    ] == [
        (10.0, 11.0, EndCase.TREND_A),
        (6.0, 8.0, None),
    ]
    assert "BASIS-025" in trend_a_successor_waiting_result.basis_ids
    assert "BASIS-023" in trend_a_successor_waiting_result.basis_ids

    # 走势 A 进入后不得跳回外层普通分支。反应向上笔终点只回到
    # T0=10，向下笔终点也未低于外层 q1 终点，内层向上线段尚未
    # 确认结束，因此应保持走势 A 待定。
    trend_a_locked_pending_pens = _pens_from_pivot_prices(
        [0, 5, 2, 10, 1, 6, 3, 8, 4, 10]
    )
    trend_a_pending_attempt = find_hypothetical_turn_attempts(
        trend_a_locked_pending_pens,
        0,
    )[0]
    trend_a_pending = resolve_without_primary_top(
        trend_a_pending_attempt.turn,
        trend_a_locked_pending_pens,
        trend_a_pending_attempt.pre_top_new_high_pen,
    )
    assert trend_a_pending.status == CandidateStatus.PENDING
    assert trend_a_pending.case_name == EndCase.TREND_A
    assert "trend_A_entered_at" in trend_a_pending.proof
    assert "BASIS-014" in trend_a_pending.basis_ids
    trend_a_pending_result = judge_one_segment_offline(
        trend_a_locked_pending_pens,
        start_pos=0,
        segment_direction=Direction.UP,
    )
    assert trend_a_pending_result.status == SegmentStatus.PENDING
    assert trend_a_pending_result.case_name == EndCase.TREND_A
    assert "BASIS-014" in trend_a_pending_result.basis_ids


def _check_mirror_and_alternating_chain() -> None:
    trend_a_pens = _pens_from_pivot_prices([0, 5, 2, 10, 1, 6, 3, 8, 0])

    # 向下线段完全由价格镜像得到，不维护第二份分型代码。
    # 它是 case_1_pens 的逐点价格取负版本，应在同一 order 确认镜像端点。
    down_case_1_pens = _pens_from_pivot_prices([0, -5, -2, -8, -4, -6, -1])
    assert mirror_pen(mirror_pen(down_case_1_pens[0])) == down_case_1_pens[0]
    down_case_1_result = judge_one_segment_offline(
        down_case_1_pens,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_case_1_result.direction == Direction.DOWN
    assert down_case_1_result.status == SegmentStatus.CONFIRMED
    assert down_case_1_result.case_name == EndCase.CASE_1_NO_GAP
    assert down_case_1_result.endpoint is not None
    assert down_case_1_result.endpoint.order == 3.0
    assert almost_equal(down_case_1_result.endpoint.price, -8.0)
    assert down_case_1_result.included_pen_positions == (0, 1, 2)
    assert almost_equal(down_case_1_result.structural_low, -8.0)
    assert almost_equal(down_case_1_result.structural_high, 0.0)
    assert "BASIS-017" in down_case_1_result.basis_ids

    # 普通创新高接续必须严格镜像为实际向下线段的创新低接续。
    # 规范坐标中第一根原始创新高把 8 -> 9，随后继续检查 7、9、10；
    # 实际坐标中就是候选底 -8 先被原始创新低 -9 取代，并继续检查
    # 中间候选 -7、接续候选 -9 和更后的 -10。
    canonical_successor_jump = _pens_from_pivot_prices(
        [0, 5, 2, 8, 6, 7, 1, 9, 4, 10, 3]
    )
    actual_down_successor_jump = tuple(
        mirror_pen(pen)
        for pen in canonical_successor_jump
    )
    down_successor_result = judge_one_segment_offline(
        actual_down_successor_jump,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_successor_result.status == SegmentStatus.PENDING
    assert "BASIS-023" in down_successor_result.basis_ids
    down_successor_history = down_successor_result.proof[
        "canonical_mirror_proof"
    ]["candidate_successor_events"]
    assert [
        (-event.rejected_t.price, -event.successor_t.price)
        for event in down_successor_history
    ] == [
        (-8.0, -9.0),
        (-7.0, -9.0),
        (-9.0, -10.0),
    ]

    # 第一种情况的笔内提前确认也必须随价格轴镜像。实际向下线段中，
    # 冻结 C 的最后来源向上笔在 order=12 首次出现 high>实际 B.high，
    # 应在该 K 线确认候选底，而不是等待该笔 order=14 的终点。
    down_case_1_intrapen_pens = tuple(
        mirror_pen(pen)
        for pen in _case_1_intrapen_tdx_pens()
    )
    down_case_1_intrapen_result = judge_one_segment_offline(
        down_case_1_intrapen_pens,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_case_1_intrapen_result.status == SegmentStatus.CONFIRMED
    assert down_case_1_intrapen_result.case_name == EndCase.CASE_1_NO_GAP
    assert down_case_1_intrapen_result.confirmed_at is not None
    assert down_case_1_intrapen_result.confirmed_at.order == 12.0
    assert "BASIS-022" in down_case_1_intrapen_result.basis_ids
    assert (
        down_case_1_intrapen_result.proof["canonical_mirror_proof"][
            "primary_top_available_at"
        ].order
        == 14.0
    )

    # 第一特征序列专用判据必须随价格镜像完整反转。规范向上坐标中的
    # B.low<A.low 强破坏，变成实际向下坐标中的 B.high>A.high；它仍是
    # 有效的镜像候选底，而不是被四比较的对称底分型错误淘汰。
    canonical_stronger_break = _pens_from_pivot_prices([0, 5, 2, 8, 1, 6, 0])
    actual_down_stronger_break = [
        mirror_pen(pen)
        for pen in canonical_stronger_break
    ]
    down_stronger_break_result = judge_one_segment_offline(
        actual_down_stronger_break,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_stronger_break_result.status == SegmentStatus.CONFIRMED
    assert down_stronger_break_result.case_name == EndCase.CASE_1_NO_GAP
    assert down_stronger_break_result.endpoint is not None
    assert down_stronger_break_result.endpoint.order == 3.0
    assert almost_equal(down_stronger_break_result.endpoint.price, -8.0)
    assert "BASIS-006" in down_stronger_break_result.basis_ids
    assert "BASIS-017" in down_stronger_break_result.basis_ids

    # 走势 A 新增价格门槛也必须镜像。实际向下线段中，第一反向向上笔
    # B.high 必须严格高于左侧第一特征序列标准元素 A.low；这里二者同为
    # -5，只满足通用笔破坏的等号边界，不得进入镜像走势 A。
    down_trend_a_equal_a_low = _pens_from_pivot_prices(
        [0, -5, -2, -10, -5, -7, -6, -8]
    )
    down_trend_a_equal_result = judge_one_segment_offline(
        down_trend_a_equal_a_low,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_trend_a_equal_result.status == SegmentStatus.PENDING
    assert down_trend_a_equal_result.case_name != EndCase.TREND_A
    assert "BASIS-021" in down_trend_a_equal_result.basis_ids

    # 普通上下交替：向上段 [order 0 -> 3] 确认后，端点 (3,8)
    # 原样成为向下段起点；向下段 [order 3 -> 6] 确认后，端点 (6,0)
    # 原样成为下一向上段起点。第三段只有最小种子，保持待定。
    alternating_pens = _pens_from_pivot_prices(
        [0, 5, 2, 8, 3, 6, 0, 4, 2, 7]
    )
    alternating_result = judge_alternating_segments_offline(
        alternating_pens,
        start_pos=0,
        initial_direction=Direction.UP,
    )
    assert len(alternating_result.confirmed_segments) == 2
    alternating_up, alternating_down = alternating_result.confirmed_segments
    assert alternating_up.direction == Direction.UP
    assert alternating_down.direction == Direction.DOWN
    assert alternating_up.endpoint is not None
    assert alternating_down.endpoint is not None
    assert points_equal(alternating_up.endpoint, alternating_down.start)
    assert almost_equal(alternating_up.endpoint.price, 8.0)
    assert almost_equal(alternating_down.endpoint.price, 0.0)
    assert alternating_result.unresolved_segment is not None
    assert alternating_result.unresolved_segment.direction == Direction.UP
    assert alternating_result.unresolved_segment.status == SegmentStatus.PENDING
    assert points_equal(
        alternating_down.endpoint,
        alternating_result.unresolved_segment.start,
    )

    # 特殊衔接：向上段 T=10 被特殊分支确认后，下一向下段从 T=10
    # 重新执行完整流程；触发旧段确认的 order=8 只作为下一候选 T 的
    # 最早允许时点，绝不直接生成一条向下段证书。
    special_chain = judge_alternating_segments_offline(
        trend_a_pens,
        start_pos=0,
        initial_direction=Direction.UP,
    )
    assert len(special_chain.confirmed_segments) == 1
    (special_up,) = special_chain.confirmed_segments
    assert special_up.case_name == EndCase.TREND_A
    assert special_up.endpoint is not None
    assert special_chain.unresolved_segment is not None
    assert special_chain.unresolved_segment.direction == Direction.DOWN
    assert points_equal(special_up.endpoint, special_chain.unresolved_segment.start)
    assert special_chain.next_direction == Direction.DOWN
    assert points_equal(special_chain.next_start, special_up.endpoint)
    assert special_chain.next_segment_t_not_before is not None
    assert special_chain.next_segment_t_not_before.order == 8.0
    if special_chain.unresolved_segment.endpoint is not None:
        assert (
            special_chain.unresolved_segment.endpoint.order
            >= special_chain.next_segment_t_not_before.order - EPS
        )

    # 完全镜像的特殊衔接：向下段底 T=-10 被确认后，下一向上段也从
    # -10 重新执行完整流程，并继承同一 order=8 候选 T 下界。
    down_trend_a_pens = _pens_from_pivot_prices(
        [0, -5, -2, -10, -1, -6, -3, -8, 0]
    )
    down_special_result = judge_one_segment_offline(
        down_trend_a_pens,
        start_pos=0,
        segment_direction=Direction.DOWN,
    )
    assert down_special_result.status == SegmentStatus.CONFIRMED
    assert down_special_result.direction == Direction.DOWN
    assert down_special_result.case_name == EndCase.TREND_A
    assert down_special_result.endpoint is not None
    assert almost_equal(down_special_result.endpoint.price, -10.0)
    assert "BASIS-021" in down_special_result.basis_ids
    assert (
        down_special_result.proof["canonical_mirror_proof"][
            "trend_A_seed_immediately_after_B"
        ]
        is True
    )
    assert (
        down_special_result.proof["canonical_mirror_proof"][
            "trend_A_B.low<A.high"
        ]
        is True
    )
    assert down_special_result.next_segment_t_not_before is not None
    assert points_equal(
        down_special_result.next_segment_t_not_before,
        down_special_result.confirmed_at,
    )

    down_special_chain = judge_alternating_segments_offline(
        down_trend_a_pens,
        start_pos=0,
        initial_direction=Direction.DOWN,
    )
    assert [
        segment.direction
        for segment in down_special_chain.confirmed_segments
    ] == [Direction.DOWN]
    assert down_special_chain.unresolved_segment is not None
    assert down_special_chain.unresolved_segment.direction == Direction.UP
    assert down_special_chain.next_direction == Direction.UP
    assert points_equal(
        down_special_chain.next_start,
        down_special_result.endpoint,
    )
    assert down_special_chain.next_segment_t_not_before is not None
    assert down_special_chain.next_segment_t_not_before.order == 8.0

    # 链级时钟反例：首段第二种情况到 order=15 才确认；随后几段若
    # 单独回看，固有证据分别早在 order=9、12 出现。连续链不能把这些
    # 后段输出到前段确认之前，必须把它们的对外确认时点抬到 15，同时
    # 保留原固有证据供审计。端点和分支均不得改变。
    chain_clock_pens = _pens_from_pivot_prices(
        [
            0, -5, -2, -17, -6, -10, 2, -10, -4, -12, -2, -8,
            -1, -16, -14, -20, -10, -24, -18, -22, -7, -22, -10, -25,
        ]
    )
    chain_clock_start = find_earliest_segment_start(chain_clock_pens)
    assert chain_clock_start is not None
    chain_clock = judge_alternating_segments_offline(
        chain_clock_pens,
        chain_clock_start.start_pos,
        chain_clock_start.direction,
    )
    assert len(chain_clock.confirmed_segments) >= 4
    assert [
        segment.confirmed_at.order
        for segment in chain_clock.confirmed_segments[:4]
        if segment.confirmed_at is not None
    ] == [15.0, 15.0, 15.0, 15.0]
    assert (
        chain_clock.confirmed_segments[1].proof[
            "intrinsic_segment_confirmed_at"
        ].order
        == 9.0
    )
    assert (
        chain_clock.confirmed_segments[2].proof[
            "intrinsic_segment_confirmed_at"
        ].order
        == 12.0
    )
    assert_alternating_chain(chain_clock.confirmed_segments)

    # 线段语义方向必须与起止价格的严格净方向一致。以下第一个最小
    # 反例曾经通过“尚无标准 A-B-C -> 走势 A”把 T=9 确认为从 10
    # 开始的向上线段端点；这会产生语义 UP、价格却 10->9 的错误段。
    wrong_net_first_pens = _pens_from_pivot_prices(
        [10, 15, 12, 13, 11, 14, 5, 6, 2, 9, 3, 7, 4, 8, 1]
    )
    wrong_net_first_start = find_earliest_segment_start(wrong_net_first_pens)
    assert wrong_net_first_start is not None
    wrong_net_first_chain = judge_alternating_segments_offline(
        wrong_net_first_pens,
        wrong_net_first_start.start_pos,
        wrong_net_first_start.direction,
    )
    for segment in wrong_net_first_chain.confirmed_segments:
        assert segment.endpoint is not None
        assert has_strict_segment_price_direction(
            segment.direction,
            segment.start,
            segment.endpoint,
        )

    # 净方向漏洞不只发生在初始段。本例第一段是合法 UP 1->9，旧实现
    # 随后又通过镜像走势 A 确认语义 DOWN 9->14，视觉上形成连续两段
    # 向上。整条已确认链中的每一段都必须独立满足严格净方向。
    wrong_net_later_pens = _pens_from_pivot_prices(
        [
            1, 8, 7, 9, 2, 6, 4, 5, 3, 11, 10,
            15, 12, 16, 13, 19, 14, 21, 18, 20, 17, 22,
        ]
    )
    wrong_net_later_start = find_earliest_segment_start(wrong_net_later_pens)
    assert wrong_net_later_start is not None
    wrong_net_later_chain = judge_alternating_segments_offline(
        wrong_net_later_pens,
        wrong_net_later_start.start_pos,
        wrong_net_later_start.direction,
    )
    assert wrong_net_later_chain.confirmed_segments
    for segment in wrong_net_later_chain.confirmed_segments:
        assert segment.endpoint is not None
        assert has_strict_segment_price_direction(
            segment.direction,
            segment.start,
            segment.endpoint,
        )


def _check_trend_a_pre_turn_scope() -> None:
    three_bad = _pens_from_pivot_prices([0, 8, -1, 9, -2, 4, 1, 5])
    only_T_breaks = _pens_from_pivot_prices([0, 10, 2, 9, 1, 11, 0, 4, 2, 5])
    earlier_break = _pens_from_pivot_prices([0, 10, 2, 12, -2, 8, -3, 9, -4, 4, 0, 5])
    tail_local_only = _pens_from_pivot_prices([0, 10, 3, 9, 2, 8, 1, 7, 2, 9.5, 0, 4, 2, 5])
    middle_only = _pens_from_pivot_prices([0, 10, 3, 9, 2, 8, 1, 7, 2, 9.5, 0, 8, -1, 9.8, -2, 4, 0, 5])
    equal_local_low = _pens_from_pivot_prices([0, 10, 4, 9, 3, 8, 3, 9.5, 2, 5, 4, 6])
    eps_local_low = _pens_from_pivot_prices([0, 10, 4, 9, 3, 8, 3 - 0.5 * EPS, 9.5, 2, 5, 4, 6])
    no_either_path = _pens_from_pivot_prices([0, 10, 3, 9, 2, 8, 1, 9.5, 0, 4, 2, 5])
    three_equal_low = _pens_from_pivot_prices([0, 8, 0, 9, -1, 4, 1, 5])
    three_eps_high = _pens_from_pivot_prices([0, 10, 2, 10 + 0.5 * EPS, 0, 4, 2, 5])
    for pens, t_order, allowed in (
        (three_bad, 3, False), (only_T_breaks, 5, True),
        (earlier_break, 7, True), (tail_local_only, 9, True),
        (middle_only, 13, True), (equal_local_low, 7, True), (no_either_path, 7, False),
        (eps_local_low, 7, True), (three_equal_low, 3, False), (three_eps_high, 3, False),
    ):
        attempt = next(a for a in find_hypothetical_turn_attempts(pens, 0)
                       if a.turn.endpoint.order == t_order)
        assert attempt.turn.A is not None
        pattern = find_trend_a_pattern(attempt.turn, pens)
        assert (pattern is not None) == allowed
        for length in range(t_order + 1, len(pens) + 1):
            prefix_pattern = find_trend_a_pattern(attempt.turn, pens[:length])
            assert (prefix_pattern is not None) == (allowed and length >= t_order + 4)
        if pattern is not None:
            proof = trend_a_entry_proof(pattern)
            assert proof["trend_A_pre_T_pen_count"] == t_order
            assert proof["trend_A_pre_T_qualified"]
            assert not proof["trend_A_pre_T_tail_strict_required"]
            assert pattern.pre_t_first_pen == pens[0]
            for witness in (pattern.pre_t_breakthrough_pen,):
                if witness is not None:
                    assert 0 <= witness.position < pattern.q1.position
            if pens is middle_only:
                assert pattern.pre_t_breakthrough_pen is None
                assert pattern.pre_t_qualification_mode == "local_three_pen_progress"
                assert pattern.pre_t_progress_seed[-1].position < t_order - 3
                assert not proof["trend_A_pre_T_seed_bottom_advances"]
            if pens is equal_local_low:
                assert pattern.pre_t_progress_seed[1].end.price == pattern.pre_t_progress_seed[0].start.price

    valid_three = _pens_from_pivot_prices([0, 8, 2, 9, 0, 4, 1, 5])
    turn = next(a.turn for a in find_hypothetical_turn_attempts(valid_three, 0)
                if a.turn.endpoint.order == 3)
    pattern = find_trend_a_pattern(turn, valid_three)
    assert pattern is not None
    proof = trend_a_entry_proof(pattern)
    assert proof["trend_A_pre_T_tail_strict_required"]
    assert proof["trend_A_pre_T_qualification_mode"] == "strict_three_pens"

    # 新共享起点不能借用上一段的突破或合格三笔。
    previous_segment = _pens_from_pivot_prices([0, 20, 2, 21, 1, 10, 4, 9, 3, 8, 2, 9.5, 0, 4, 2, 5])
    turn = next(a.turn for a in find_hypothetical_turn_attempts(previous_segment, 4)
                if a.turn.endpoint.order == 11)
    assert turn.A is not None
    assert find_trend_a_pattern(turn, previous_segment) is None


def run_grouped_self_checks() -> None:
    """按规则模块运行全部合成回归，失败时可直接定位到对应分组。"""

    _check_trend_a_pre_turn_scope()
    _check_geometry_and_feature_sequences()
    _check_left_a_scope_rules()
    _check_ordinary_candidate_rules()
    _check_trend_a_entry_and_next_segment_floor()
    _check_trend_a_nested_reactions()
    _check_trend_a_event_priority_and_pending()
    _check_mirror_and_alternating_chain()


if __name__ == "__main__":
    run_grouped_self_checks()
    print("伪代码的分组规则自检通过。")

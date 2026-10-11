# 规则与实现追溯

以下表保留既有 BASIS 标识和函数关系；历史附件和验证通过记录未随此表重新声明。原文附件不随包提供。

## BASIS—函数追溯

| 知识编号 | 代码落点 | 审阅要点 |
|---|---|---|
| BASIS-001 | 文件总说明、`__all__`、`calculate_tdx_segment_events`、`_judge_canonical_up_segment`、`judge_one_segment_offline` | 对外以完整 TDX 深入口为生产基准；结构入口用于对拍；向上规则为唯一规范实现，向下通过严格价格镜像复用 |
| BASIS-002 | `validate_up_segment_start`、`first_reverse_pen_breaks_up_segment`、`build_final_segment_result` | 已知起点三笔方向交替且具有公共重叠，第三笔不要求沿段方向创新高/创新低；普通确认路径至少三笔且为奇数笔；`d_j≤g_i` 的笔破坏公式 |
| BASIS-029 | `find_earliest_segment_start` | 全窗口自动首锚点另加第二笔严格内收与第三笔严格延伸；对应 CZSC 万线段活动、31 根最小起点反例及等值边界控制 |
| BASIS-003 | `collect_primary_down_features` | 向上线段第一特征序列只取向下笔 |
| BASIS-004 | `standardize_feature_sequence`、`is_primary_top_fractal`、`is_symmetric_top_fractal`、`is_secondary_bottom_fractal` | 包含处理先于分型判断；第一特征序列专用三比较与第二特征序列对称四比较不得混用 |
| BASIS-005 | `judge_case_1`、`judge_secondary_sequence` | 无缺口进入第一种情况；有缺口须第二序列底分型 |
| BASIS-006 | `standardized_left_A_at_boundary`、`is_primary_top_fractal`、`build_candidate_at_boundary`、`classify_first_pen_break_strength`；C++：`PrimaryLeftPrefix`、`BuildCandidateAtBoundary`、`JudgeCanonicalUpSegmentImpl` | A/B 不跨边界合并；左侧先独立标准化，再按 SPEC-034 选择普通最近 / 特殊最高 A；第一特征序列候选只要求 `B.high>A.high、C.high` 且 `B.low>C.low`，不比较 `B.low/A.low`；右侧不足两个标准元素时才继续扩大 |
| BASIS-008 | `find_trend_a_pattern`、`judge_trend_a_special`、`confirming_case_of_isolated_reaction_prefix` | 走势 A 至少三笔；反应走势包含三笔种子；从第一根种子笔开始按真实时间逐原始前缀重放隔离的完整内层流程 |
| BASIS-009 | `SecondaryPrefixSnapshot`、`analyze_secondary_prefix`、`judge_secondary_sequence` | 同一快照保存当前前缀标准化和底分型结论；原始创新高是分开的真实事件属性，同一原始笔完成底分型时底分型优先 |
| BASIS-010 | `bottom_rule_5_vs_7` 及相应断言 | 等号属于包含，不能保留底分型 |
| BASIS-011 | `SegmentResult`、`build_final_segment_result` | 结构端点和整段价格范围分离，结果显式保存实际方向 |
| BASIS-013 | `build_candidate_at_boundary`、`find_first_later_primary_start_above_t`、`resolve_without_primary_top` | 每次扩展右侧前缀时先查向下特征笔起点，最短前缀中的 `R₁` 也不例外；第一特征序列标准候选成立前高于 T 取消普通候选，但不得跨过已经进入的走势 A 状态 |
| BASIS-014 | `TrendAPattern.entered_at`、`find_trend_a_pattern`、`judge_trend_a_special`、`confirming_case_of_isolated_reaction_prefix` | 走势 A 是特殊状态；反应前缀复用完整向上线段引擎并允许嵌套；T1 仅为分支局部候选，外层终局后随分支释放 |
| BASIS-015 | `TrendAPattern.entered_at`、`find_trend_a_pattern`、`judge_trend_a_special`、`resolve_without_primary_top`、`judge_one_candidate` | 同步监听走势 A 与 A-B-C；若同时满足 BASIS-021、BASIS-033 的合法走势 A 存在，其中间向下种子 `R₁` 必被原始 B₀ 包含，因此进入时点严格早于 `available_at`；否则永久锁定同一候选的普通分支，第一种情况的最早确认事件再按 BASIS-022 回填 |
| BASIS-016 | `SecondaryPrefixSnapshot`、`find_standardized_element_containing_source_pen`、`analyze_secondary_prefix`、`_confirm_from_secondary_bottom`、`judge_secondary_sequence` | 每根第二特征序列原始向上笔分开记录真实创新高事件和同前缀标准化结果；同事件底分型优先，未创新高笔只走普通流程，创新高未成底才转 BASIS-027 |
| BASIS-017 | `mirror_point`、`mirror_pen`、`restore_segment_result_from_price_mirror`、`judge_one_segment_offline`、`judge_alternating_segments_offline` | 向下线段以 `p'=-p`、方向互换严格镜像；每段端点成为下一反向线段起点，上下方向交替 |
| BASIS-018 | `build_final_segment_result`、`SegmentResult.next_segment_t_not_before`、`_judge_canonical_up_segment`、`judge_alternating_segments_offline` | 特殊分支只确认本段 T；下一反向段从 T 重新运行完整流程，并过滤严格早于本段确认触发时点的候选 T |
| BASIS-019 | `judge_trend_a_special`、`_confirm_outer_t0_from_inner_segment`、`confirming_case_of_isolated_reaction_prefix` | 同一向上反应笔既越过外层 T₀、又使内层第二种情况完成底分型时，内层底分型优先确认 T₀；只有没有该底分型证书时，外层创新高守卫才否定 T₀；价格镜像结论相同 |
| BASIS-020 | Python：`PriceBar`、`PenEndpointKind`、`PenInputErrorCode`、`SegmentEventBit`、`decode_pens_from_tdx_series`、`calculate_tdx_segment_events`；C++：`TdxSeriesView`、`InputErrorCode`、`DecodePens`、`FindEarliestSegmentStart`、`CalculateSegmentEventsV1`、`EncodeSegmentEvents` | 三列输入固定为端点标记、H、L；结构化恢复 Pen 并保留逐 K 路径；函数编号 1 搜索最早合格三笔，再把连续已确认前缀压缩为独立事件位 |
| BASIS-021 | `HypotheticalTurn.A`、`standardized_left_A_at_boundary`、`find_trend_a_pattern`、`trend_a_entry_proof` | 普通候选与无 C 的假设点共享 SPEC-034 选出的候选作用域 A；无合格 A 时不得进入走势 A。走势 A 只截取原始 B₀ 后连续三个列表元素并逐号核对，且必须严格满足 `B₀.low<A.high-EPS`；向下方向严格镜像 |
| BASIS-022 | `PriceBar`、`first_bar_low_strictly_below`、`judge_case_1`、`mirror_pen`、`FindFirstBarLowStrictlyBelow`、`FindFirstBarHighStrictlyAbove` | 冻结 B 只有一个候选极值来源时，扫描冻结 C 最后来源笔并以标准 B 阈值定位首次严格越界；等号跳过，`confirmed_at≤available_at`，向下方向严格镜像；合成 Pen 无路径时只回退到终点，不插值 |
| BASIS-023 | `CandidateDecision.successor_candidate_t`、`CandidateSuccessorEvent`、`_reject_from_secondary_raw_innovation`、`resolve_without_primary_top`、`_judge_canonical_up_segment`、`restore_segment_result_from_price_mirror` | 普通第二种情况退出时固定以原始创新高 U.end 建立 T′，延后退出只改变否定时点；A-B-C 成立前高起点仍取对应真实向上笔终点。旧 T 只否定自身，T/T′ 之间候选逐个判断；实际向下镜像，走势 A 外层守卫排除 |
| BASIS-024 | `apply_chain_confirmation_floor`、`assert_alternating_chain`、`flatten_confirmed_segments`、`encode_segment_events` | 后一段只可在前段确认后对外生效；若固有证据更早，只抬高链级确认时点并保存固有证据，编码器再次验证确认时点单调不减 |
| BASIS-025 | `CandidateDecision.successor_candidate_t`、`CandidateSuccessorEvent`、`judge_trend_a_special`、`_judge_canonical_up_segment`；C++：`JudgeTrendASpecial`、`JudgeCanonicalUpSegmentImpl`、`EvaluateCanonicalWithExplicitStack` | 走势 A 外层向上创新高守卫在同事件内层第二种情况未确认时，把触发守卫的真实向上笔终点建立为 T′；T/T′ 之间候选仍逐个判断，到达 T′ 时执行完整流程；同事件内层第二种情况确认仍优先确认旧 T；实际向下严格镜像 |
| BASIS-026 | `select_secondary_A_before_B`、`find_new_bottom_created_by_latest_element`、`judge_secondary_sequence`；C++：`SelectSecondaryABeforeB`、`FindNewBottomCreatedByLatestElement` | B₂ 固定为 C₂ 左邻；A₂ 从 B₂ 前全部标准元素中取最高 high，EPS 并列取较晚者；完整四比较不变。当前笔触发包含时不能首次完成底分型 |
| BASIS-027 | `SecondaryInnovationWait`、`_reject_from_secondary_raw_innovation`、`judge_secondary_sequence`；C++：`SecondaryInnovationWait`、`RejectFromSecondaryRawInnovation`、`JudgeSecondarySequence` | 原始创新高 U 未完成底分型时检查 A₂/B* 资格；有资格则固定 U.start/U.end 等待独立 C₂。后来起点下破在后来笔终点否定但 T′ 固定为 U.end；相等/被包含继续；独立 C₂ 确认；未创新高不适用 |
| BASIS-028 | `has_strict_segment_price_direction`、`_judge_canonical_up_segment`、`build_final_segment_result`、`assert_alternating_chain`、`flatten_confirmed_segments`；C++：`HasStrictSegmentPriceDirection`、`JudgeCanonicalUpSegmentImpl`、`BuildFinalSegmentResult`、`ValidateAlternatingChain`、`FlattenConfirmedSegmentsImpl` | 向上段端点严格高于起点、向下段严格低于起点；价格方向不合格的 T 在所有候选分支前跳过且不产生状态或事件，结果构造、交替链和证书压缩再作防御性核查 |
| BASIS-030 | `judge_case_1`、`JudgeCase1` | 冻结 B 有多个同价极值来源时，结构端点采用其中时间最晚者；只从 B 来源选择端点，不从 C 或后续行情取点；向下严格镜像 |
| BASIS-031 | `judge_case_1`、`JudgeCase1`、`CandidateDecision.successor_candidate_t` | 重复同价极值来源取标准 B 与最后同价来源笔原始边界的更深阈值；冻结 C 未越过时检查后续同方向反向笔，先越界确认旧 T，先出现新极值则否定并接续，数据结束则待定；向下严格镜像。证据为 122 根最小反例、3,606 次缩减重算、226 项红绿回归、3,188 旧输入及 1,200 新输入无原正确例退化 |
| BASIS-032（收窄） | `_highest_eligible_left_A`；C++ `PrimaryLeftPrefix::HighestEligibleA` | 原最高合格 / EPS 并列较晚选法只供 SPEC-034 完整特例使用；普通最近元素不合格时不回退历史元素 |
| BASIS-033 | `_three_pens_meet_strict_initial_progress`、`find_trend_a_pattern`、`trend_a_entry_proof`、`_check_trend_a_pre_turn_scope`；C++：`ThreePensMeetStrictInitialProgress`、`FindTrendAPattern`、`JudgeCanonicalUpSegmentImpl` | 以当前共享起点 S 至 T 的连续原始笔计数，包含终点为 T 的笔、不含 B₀，不借用上一段或 T 后行情。恰好三笔保留原严格初始化判据。超过三笔时，下列任一路径成立即可：某根同向笔严格突破整段首笔极值；或任意位置的一组连续三笔具有上—下—上方向与公共重叠，第二笔终点不低于该组第一笔起点减 EPS，第三笔终点严格高于该组第一笔终点加 EPS。局部三笔可在中间或尾部，无须突破整段首笔。向下由价格镜像执行。Python 保存实际见证用于资格证书；C++ 随候选枚举维护两项累计资格，进入新段时重置，避免每个候选重扫。失败只关闭当前 T 的走势 A 资格。USER_REQUIREMENT，不扩充作者原文规则 |
| BASIS-034 | `_left_A_selection_at_boundary`；C++ `PrimaryLeftPrefix::CurrentA` | 普通路径取包含后的最近元素；不合格时无 A，保留 T，不回退历史元素 |
| BASIS-035 | `_highest_eligible_left_A` 与特例门槛；C++ `HighestEligibleA` | 作者 PDF180 页12、56、78具体结构；原选法在完整特例内保留 |
| BASIS-036 | `HypotheticalTurn.A`、`CandidateTop.A` 及分支共享 | 用户要求：连续低顶、严格比较、以原始 B₀ 判断特例、第二种和走势 A 共用特殊 A |


参考实现见 [segment_reference.py](../../reference/segment_reference.py)，现行外层恢复策略见 [恢复补充](recovery.md)。

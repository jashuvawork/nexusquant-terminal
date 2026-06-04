export type MarketSymbol = 'NIFTY' | 'SENSEX';
export type Regime = 'TREND_EXPANSION' | 'RANGE_ABSORPTION' | 'VOLATILITY_COMPRESSION' | 'REVERSAL_RISK' | 'CLOSED_MARKET_ANALYSIS';
export type VolatilityRegime = 'LOW_IV' | 'NORMAL_IV' | 'IV_EXPANSION' | 'EVENT_SPIKE';
export type StreamStatus = 'connecting' | 'live' | 'status' | 'error';

export interface ActiveTrade {
  id: string;
  symbol: MarketSymbol;
  side: 'CALL' | 'PUT';
  strike: number;
  qty: number;
  entry: number;
  ltp: number;
  pnl: number;
  tqs: number;
  stop: number;
  target: number;
  status: 'SCALPING' | 'TRAILING' | 'PARTIAL_EXIT' | 'SAFE_MODE' | 'BROKER_POSITION';
}

export interface HeatmapCell {
  id: string;
  strike: number;
  side: 'CALL' | 'PUT' | 'FUTURE';
  liquidity: number;
  absorption: number;
  gammaWall: number;
  stopDensity: number;
  sweepRisk: number;
  label: string;
}

export interface OrderflowState {
  cumulativeDelta: number;
  deltaVelocity: number;
  aggressiveBuyers: number;
  aggressiveSellers: number;
  domImbalance: number;
  liquidityShift: number;
  sweepDetection: number;
  volumeAcceleration: number;
  breakoutVelocity: number;
}

export interface GreeksState {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  ivRank: number;
  ivPercentile: number;
  ivExpansion: number;
}

export interface MarketProfileState {
  poc: number;
  vah: number;
  val: number;
  acceptanceZone: string;
  volumeProfile: Array<{ level: number; volume: number }>;
  hvn?: number;
  lvn?: number;
  openingRangeHigh?: number;
  openingRangeLow?: number;
}

export interface EngineScore {
  engine: string;
  score: number;
  weight: number;
  status: 'pass' | 'watch' | 'fail';
}

export interface AdaptiveRiskState {
  profile: {
    key: string;
    label: string;
    minimum_tqs: number;
    safe_mode_tqs: number;
    max_exposure_pct: number;
    daily_drawdown_pct: number;
    cooldown_seconds: number;
    behavior: string;
    account_size: string;
  };
  sessionBucket: string;
  sessionNote: string;
  minimumTqs: number;
  safeModeTqs: number;
  maxExposurePct: number;
  dailyDrawdownPct: number;
  cooldownSeconds: number;
  dynamicExposurePct: number;
  adjustments: string[];
  benchmarks: Record<string, string | number>;
}

export interface RiskState {
  safeMode: boolean;
  dailyDrawdownPct: number;
  maxDrawdownPct: number;
  slippageBps: number;
  staleDataMs: number;
  apiDisconnects: number;
  latencyMs: number;
  spreadWideningPct: number;
  maxExposurePct: number;
  cooldownSeconds: number;
}

export interface InfraState {
  brokerHealth: number;
  websocketLatencyMs: number;
  orderRouterLatencyMs: number;
  upstoxLatencyMs?: number;
  redisHealth: number;
  postgresHealth: number;
  prometheusHealth: number;
}

export interface QualityFilters {
  chopFilter?: { blocked: boolean; reasons: string[]; score: number };
  volumeState?: {
    source: string;
    candleVolume: number;
    optionChainVolume: number;
    ltpVolume: number;
    effectiveVolume: number;
    score: number;
    volumeAvailable: boolean;
  };
}

export interface PortfolioState {
  capital: number;
  margin: number;
  availableMargin?: number;
  usedMargin?: number;
  payinAmount?: number;
  exposureMargin?: number;
  fundsSource?: string;
  pledgeAvailable?: number;
  unsettledProfit?: number;
  fundsRawShape?: string;
  fundsBreakdown?: Record<string, number>;
  realizedPnl: number;
  unrealizedPnl: number;
  executionQuality: number;
  positions: number;
  orders: number;
}

export interface ExpiryState {
  symbol: MarketSymbol;
  underlyingInstrumentKey: string;
  selectedExpiry: string;
  source: 'configured' | 'upstox_nearest' | string;
  configuredExpiry?: string | null;
  availableExpiries: string[];
  availableExpiryCount: number;
  selectedContractCount: number;
  lastCheckedAt: string;
}

export interface UpstoxConnectionState {
  connected: boolean;
  dataSource: string;
  marketDataVerified?: boolean;
  fundsVerified?: boolean;
  fundsAvailable: number;
  fundsUsed: number;
  positionsCount: number;
  ordersCount: number;
}

export interface PremarketAnalysis {
  readiness: string;
  bias: string;
  pcr: number;
  keyLevels: { poc: number; vah: number; val: number };
  checklist: string[];
  score: number;
  spreadQuality: number;
}

export interface TomorrowTradePlan {
  generatedFor: string;
  symbol: MarketSymbol;
  expiry: string;
  primaryBias: string;
  candidate: { side: 'CALL' | 'PUT'; strike: number; instrumentKey?: string; lastPremium: number };
  entryRules: string[];
  invalidations: string[];
  levels: { poc: number; vah: number; val: number };
  tqs: number;
  safeMode: boolean;
}

export interface StrategyRoute {
  selected: string;
  aggression: number;
  sizeMultiplier: number;
  threshold: number;
  router: 'SMART_LIMIT' | 'PASSIVE_JOIN' | 'AGGRESSIVE_SWEEP' | 'SAFE_MODE';
}

export interface TelemetryPoint {
  time: string;
  pnl: number;
  tqs: number;
  latency: number;
  volume: number;
  price: number;
}

export interface JournalEntry {
  time: string;
  instrument: string;
  tqs: number;
  pnl: number;
  exitReason: string;
}

export interface BacktestMetric {
  name: string;
  value: number;
  unit: string;
}

export interface SuggestedTrade {
  id: string;
  mode: 'ANALYSIS_BACKTEST_ONLY' | 'AUTO_EXECUTION_READY' | string;
  action: 'SUGGEST_ONLY' | 'EXECUTION_READY' | string;
  symbol: MarketSymbol;
  side: 'CALL' | 'PUT';
  strike: number;
  expiry: string;
  instrumentKey?: string;
  lastPremium: number;
  tradingCapital?: number;
  quantityEstimate?: number;
  allocationPct?: number;
  chopBlocked?: boolean;
  chopReasons?: string[];
  volumeSource?: string;
  effectiveVolume?: number;
  tqs: number;
  confidence: 'LOW' | 'MEDIUM' | 'HIGH' | string;
  bias: string;
  pcr: number;
  safeMode: boolean;
  entryRules: string[];
  invalidations: string[];
  levels: { poc: number; vah: number; val: number };
}

export interface PressureModeState {
  level: 'NORMAL' | 'ELEVATED' | 'CRITICAL' | string;
  triggers: string[];
  actions: string[];
  score: number;
}

export interface PrecisionChecklistState {
  passed: boolean;
  passedCount: number;
  total: number;
  criticalFailed: Array<Record<string, unknown>>;
  checks: Array<{ name: string; passed: boolean; value: unknown; required: unknown; critical: boolean }>;
}

export interface AdaptiveExitState {
  targetPoints: number;
  stopPoints: number;
  trailPoints: number;
  partialExitAt: number;
  atrPoints?: number;
  rules: Array<{ name: string; active: boolean; action: string }>;
}

export interface ProductionReadinessState {
  readyForFullCapital: boolean;
  readyForSmallLive: boolean;
  passed: number;
  total: number;
  checks: Array<{ name: string; passed: boolean; value: unknown; required: unknown }>;
  recommendation: string;
  maxSuggestedLiveCapital?: number | null;
}

export interface NoTradeZoneState {
  blocked: boolean;
  activeZones: Array<{ name: string; severity: string; reason: string }>;
  hardBlocks: Array<{ name: string; severity: string; reason: string }>;
}

export interface TqsBreakdownState {
  total: number;
  components: Array<EngineScore & { contribution: number }>;
  topContributors: Array<EngineScore & { contribution: number }>;
  weakComponents: Array<EngineScore & { contribution: number }>;
  explanation: string;
}

export interface AutoTraderState {
  paperTrading: boolean;
  liveTradingEnabled: boolean;
  autoTradingStopped: boolean;
  signalsThisTick: number;
  skippedSignals: Array<{ candidate?: string; reason: string; quality?: Record<string, unknown> }>;
  openPaperTrades: Array<Record<string, unknown>>;
  closedPaperTrades: Array<Record<string, unknown>>;
  orderLifecycle: Array<{ state: string; timestamp: string; reason: string; payload?: Record<string, unknown> }>;
  replay: { storedSnapshots: number; latestTimestamp?: string };
  exitEngine: { rules: string[]; exitsThisTick: Array<Record<string, unknown>> };
  slippageModel: { averageExpectedSlippage: number; minimumRequiredMovePoints: number; model: string };
  positionSizing: { capital: number; candidates: Array<Record<string, unknown>> };
  profitLock?: { capital: number; netPnl: number; tiers: Array<Record<string, unknown>>; activeTier?: Record<string, unknown> | null; lockedProfit: number; givebackAvailable: number; blockNewTrades: boolean; message: string };
  onlineLearning: { enabled: boolean; pretrained?: boolean; priorVersion?: string; mode: string; samples: number; score?: number; learningScore?: number; paperSamples?: number; liveSamples?: number; profitFactor?: number; calibration?: Record<string, unknown>; lastUpdatedAt?: string; note: string };
  dailyReport: {
    totalSignals: number;
    paperTrades: number;
    openTrades: number;
    wins: number;
    losses: number;
    winRate: number;
    grossProfit: number;
    grossLoss: number;
    profitFactor: number;
    maxDrawdown: number;
    bestSession?: string;
    worstSession?: string;
    reasonForLosses: Record<string, number>;
  };
}

export interface TerminalSnapshot {
  type?: 'snapshot';
  timestamp: string;
  marketPhase?: 'PRE_MARKET_ANALYSIS' | 'LIVE_MARKET' | 'POST_MARKET_ANALYSIS' | 'CLOSED_MARKET';
  sessionLabel?: string;
  sessionReason?: string;
  executionAllowed?: boolean;
  liveTradingEnabled?: boolean;
  aggressiveMode?: boolean;
  autoTradingStopped?: boolean;
  tradingControl?: { autoTradingStopped: boolean; reason?: string; updatedAt?: string };
  tradingCapital?: { tradingCapital: number; reason?: string; updatedAt?: string };
  tradeMode?: 'ANALYSIS_BACKTEST_ONLY' | 'AUTO_EXECUTION_READY' | string;
  qualityFilters?: QualityFilters;
  pressureMode?: PressureModeState;
  precisionChecklist?: PrecisionChecklistState;
  adaptiveExit?: AdaptiveExitState;
  noTradeZones?: NoTradeZoneState;
  tqsBreakdown?: TqsBreakdownState;
  productionReadiness?: ProductionReadinessState;
  dataSource?: string;
  dataWarnings?: string[];
  upstoxConnection?: UpstoxConnectionState;
  expiryState?: ExpiryState;
  premarketAnalysis?: PremarketAnalysis;
  tomorrowTradePlan?: TomorrowTradePlan;
  suggestedTrades?: SuggestedTrade[];
  autoTrader?: AutoTraderState;
  symbol: MarketSymbol;
  spot: number;
  atmStrike: number;
  premiumFocusZone: string;
  aiConfidence: number;
  tradeQualityScore: number;
  pnl: number;
  liveExposurePct: number;
  spreadQuality: number;
  executionLatencyMs: number;
  deltaVelocity: number;
  trailingStopState: string;
  regime: Regime;
  volatilityRegime: VolatilityRegime;
  activeTrades: ActiveTrade[];
  heatmap: HeatmapCell[];
  orderflow: OrderflowState;
  greeks: GreeksState;
  marketProfile: MarketProfileState;
  aiMatrix: EngineScore[];
  adaptiveRisk?: AdaptiveRiskState;
  risk: RiskState;
  infra: InfraState;
  portfolio: PortfolioState;
  strategy: StrategyRoute;
  telemetry: TelemetryPoint[];
  journal: JournalEntry[];
  backtest: BacktestMetric[];
}

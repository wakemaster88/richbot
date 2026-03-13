/** Shared types for the RichBot dashboard. */

export interface BotStatus {
  id: string;
  botId: string;
  status: string;
  lastHeartbeat: string;
  pairs: string[];
  pairStatuses: Record<string, PairMetrics | WalletData | CorrelationData | CBGlobalData | SentimentData>;
  startedAt: string;
  version: string;
  dbConnected?: boolean;
}

export interface OpenOrder {
  side: string;
  price: number;
  amount: number;
  id: string;
  status?: string;
  fill_pct?: number;
  filled_amount?: number;
}

export interface FeeMetrics {
  maker_fee_pct: number;
  taker_fee_pct: number;
  fee_source: string;
  min_profitable_spacing_pct: number;
  current_spacing_pct: number;
  net_profit_per_trade_pct: number;
  spacing_is_profitable: boolean;
}

export interface PairMetrics {
  pair: string;
  price: number;
  range: string;
  range_source: string;
  grid_levels: number;
  grid_configured?: number;
  grid_buy_count?: number;
  grid_sell_count?: number;
  active_orders: number;
  filled_orders: number;
  partially_filled_orders?: number;
  unplaced_orders?: number;
  grid_issue?: string;
  allocation?: {
    equity: number;
    reserve: number;
    amount_per_order: number;
    rebalance_needed: boolean;
  };
  regime?: {
    regime: string;
    rsi: number;
    adx: number;
    boll_width: number;
    avg_boll_width: number;
    confidence?: number;
    trend_score?: number;
    volatility_score?: number;
    ranging_score?: number;
    sentiment_score?: number;
    sentiment_confidence?: number;
    mtf_alignment?: number;
    mtf_quality?: number;
    transition_pending?: string | null;
    transition_countdown?: number;
  };
  trailing_tp?: Array<{
    pair: string;
    side: string;
    entry_price: number;
    amount: number;
    highest: number;
    lowest: number;
    age_sec: number;
  }>;
  trailing_tp_active?: boolean;
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  trade_count: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
  current_equity: number;
  buy_count?: number;
  sell_count?: number;
  annualized_return_pct?: number;
  fees_paid?: number;
  fee_metrics?: FeeMetrics;
  avg_slippage_bps?: number;
  max_slippage_bps?: number;
  slippage_cost?: number;
  maker_fill_pct?: number;
  inventory?: {
    base_inventory: number;
    avg_cost_basis: number;
    total_cost: number;
    market_value: number;
    unrealized_pnl: number;
    realized_pnl: number;
    total_pnl: number;
    total_fees: number;
    trade_count: number;
    buy_count: number;
    sell_count: number;
  };
  skew?: {
    skew_factor: number;
    skew_pct: number;
    current_ratio: number;
    target_ratio: number;
    base_value: number;
    quote_value: number;
    description: string;
    needs_rebalance: boolean;
  };
  circuit_breaker?: CBPairStatus;
  spread?: SpreadData;
  open_orders?: OpenOrder[];
}

export interface SpreadData {
  current_bps: number;
  avg_60m_bps: number;
  percentile: number;
  is_wide: boolean;
  history: { t: number; bps: number }[];
}

export interface CBPairStatus {
  level: string;
  drawdown_pct: number;
  peak_equity: number;
  vol_adj: number;
  yellow_threshold: number;
  orange_threshold: number;
  red_threshold: number;
  size_factor: number;
  spacing_mult: number;
  can_buy: boolean;
  can_sell: boolean;
  resume_in_sec: number;
  triggered_at: number;
}

export interface CBGlobalData {
  global_halt: boolean;
  cascade_threshold: number;
  pairs_at_orange_plus: number;
  pairs_at_red: number;
  pairs: Record<string, CBPairStatus>;
  history: Array<{
    timestamp: number;
    pair: string;
    level: string;
    drawdown_pct: number;
    threshold_pct: number;
    vol_adj: number;
  }>;
}

export interface Trade {
  id: string;
  timestamp: string;
  pair: string;
  side: string;
  price: number;
  amount: number;
  pnl: number;
  fee?: number;
  fillPrice?: number;
  slippageBps?: number;
  isMaker?: boolean;
  gridLevel?: number;
  orderId?: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface CommandRecord {
  id: string;
  type: string;
  status: string;
  createdAt: string;
  result: Record<string, unknown> | null;
}

export interface BotEvent {
  id: string;
  timestamp: string;
  level: string;
  category: string;
  message: string;
  detail: Record<string, unknown> | null;
}

export interface WalletEntry {
  free: number;
  locked: number;
  total: number;
  usdc_value: number;
  price?: number;
}

export type WalletData = Record<string, WalletEntry> & { _total_usdc?: number };

export interface CorrelationData {
  matrix: number[][];
  pairs: string[];
  portfolio_var_pct: number;
  portfolio_var_abs: number;
  high_corr_warnings: Array<{
    pair_a: string;
    pair_b: string;
    correlation: number;
    extreme: boolean;
  }>;
  size_adjustments: Record<string, number>;
}

export interface SentimentData {
  score: number;
  confidence: number;
  consensus: string;
  fear_greed: number | null;
  headlines: string[];
  sources?: Record<
    string,
    { score: number; confidence: number; sample?: string[] }
  >;
}

export interface AnalyticsData {
  summary: {
    total_trades: number;
    wins: number;
    losses: number;
    win_rate: number;
    total_pnl: number;
    total_fees: number;
    net_pnl: number;
    avg_win: number;
    avg_loss: number;
    profit_factor: number;
    max_win_streak: number;
    max_loss_streak: number;
  } | null;
  pair_stats: Record<
    string,
    { trades: number; pnl: number; wins: number; losses: number; volume: number }
  >;
  hourly_pnl: { hour: string; pnl: number; count: number }[];
  event_counts_24h: Record<string, number>;
  snapshots: { timestamp: string; detail: Record<string, unknown> }[];
}

export interface PiSystem {
  cpu_temp?: number;
  cpu_percent?: number;
  ram_total_mb?: number;
  ram_used_mb?: number;
  ram_percent?: number;
  load_1m?: number;
  disk_total_gb?: number;
  disk_used_gb?: number;
  disk_percent?: number;
  hostname?: string;
  rss_kb?: number;
  public_ip?: string;
}

export interface PiStatus {
  connected: boolean;
  lastSeen?: string;
  uptime?: number;
  system: PiSystem | null;
}

export interface RLStats {
  rewards: Array<{
    episode: number;
    reward: number;
    exploration: number;
    timestamp: string;
  }>;
  latestAction: {
    action: {
      spacing_delta: number;
      size_delta: number;
      range_delta: number;
      distance_delta: number;
      action_idx: number;
      was_exploration: boolean;
    } | null;
    reward: number;
    was_exploration: boolean;
    episode: number;
    heuristic_adj: Record<string, number> | null;
    merged_adj: Record<string, number> | null;
    timestamp: string;
  } | null;
  explorationRate: number;
  episodes: number;
  policyHints: Record<string, unknown>;
}

export interface Kline {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

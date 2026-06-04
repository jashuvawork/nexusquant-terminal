import { useState } from 'react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from './Card';
import { MetricCard } from './MetricCard';
import { ScoreBar } from './ScoreBar';
import type { TerminalSnapshot } from '../types';
import { formatCurrency, formatNumber } from '../utils/format';


function TradingCapitalControl({ snapshot }: { snapshot: TerminalSnapshot }) {
  const apiUrl = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
  const currentCapital = snapshot.tradingCapital?.tradingCapital ?? 0;
  const [amount, setAmount] = useState(currentCapital ? String(currentCapital) : '');
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const saveCapital = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch(`${apiUrl}/api/capital`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: Number(amount || 0), reason: 'Capital set from Strategy Router' }),
      });
      if (!response.ok) throw new Error(`Backend returned ${response.status}`);
      const payload = await response.json();
      setMessage(`Capital saved: ${formatCurrency(payload.tradingCapital ?? 0)}`);
    } catch (error) {
      setMessage(`Capital save failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-5 rounded-2xl border border-violet-300/20 bg-violet-300/10 p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.24em] text-violet-200">Trading capital for live/backtest</p>
          <p className="mt-1 text-sm text-slate-400">Used for quantity estimate, backtesting context, and live order capital guard.</p>
        </div>
        <div className="flex gap-2">
          <input
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            inputMode="decimal"
            placeholder="Capital INR"
            className="w-40 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-violet-300"
          />
          <button
            type="button"
            disabled={saving}
            onClick={() => void saveCapital()}
            className="rounded-xl border border-violet-300/30 bg-violet-400/15 px-4 py-2 text-xs font-bold uppercase tracking-[0.18em] text-violet-100 disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
      <p className="mt-2 text-xs text-slate-300">Current backend capital: {formatCurrency(currentCapital)}</p>
      {message && <p className="mt-2 text-xs text-slate-300">{message}</p>}
    </div>
  );
}

export function StrategyRouter({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Adaptive Strategy Router" eyebrow="Backtest suggestions when auto trading is off; execution-ready only when risk gates pass">
      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard label="Trade Mode" value={(snapshot.tradeMode ?? 'ANALYSIS_BACKTEST_ONLY').replaceAll('_', ' ')} helper={snapshot.liveTradingEnabled ? 'Auto trading variable enabled' : 'Auto trading off: suggest only'} tone={snapshot.executionAllowed ? 'emerald' : 'amber'} />
        <MetricCard label="Selected Strategy" value={snapshot.strategy.selected} helper="Regime-aware scalp model" tone="cyan" />
        <MetricCard label="Aggression" value={`${snapshot.strategy.aggression}%`} helper={snapshot.strategy.router.replaceAll('_', ' ')} tone="amber" />
        <MetricCard label="Size Multiplier" value={`${snapshot.strategy.sizeMultiplier}x`} helper={`TQS threshold ${snapshot.strategy.threshold}`} tone="emerald" />
      </div>
      <TradingCapitalControl snapshot={snapshot} />
      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <ScoreBar label="Momentum Expansion" value={snapshot.orderflow.breakoutVelocity} />
        <ScoreBar label="Liquidity Confirmation" value={snapshot.orderflow.liquidityShift} />
        <ScoreBar label="Spread Quality" value={snapshot.spreadQuality} />
        <ScoreBar label="Option Chain Bias" value={snapshot.aiMatrix.find((item) => item.engine === 'Option Chain Bias')?.score ?? 0} />
      </div>
      {snapshot.suggestedTrades && snapshot.suggestedTrades.length > 0 && (
        <div className="mt-5 space-y-3">
          {snapshot.suggestedTrades.map((trade) => (
            <div key={trade.id} className="rounded-2xl border border-cyan-300/20 bg-slate-950/60 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-bold text-white">{trade.symbol} {trade.strike} {trade.side} | {trade.expiry}</p>
                  <p className="text-xs text-slate-400">{trade.instrumentKey ?? 'Instrument unavailable'} | Last premium {trade.lastPremium}</p>
                </div>
                <span className={`rounded-full px-3 py-1 text-xs font-bold ${trade.action === 'EXECUTION_READY' ? 'bg-emerald-300/10 text-emerald-200' : 'bg-amber-300/10 text-amber-200'}`}>{trade.action.replaceAll('_', ' ')}</span>
              </div>
              <div className="mt-3 grid gap-2 text-xs text-slate-300 md:grid-cols-4">
                <div>TQS <span className="font-mono text-white">{trade.tqs}</span></div>
                <div>Confidence <span className="font-mono text-white">{trade.confidence}</span></div>
                <div>Bias <span className="font-mono text-white">{trade.bias}</span></div>
                <div>PCR <span className="font-mono text-white">{trade.pcr}</span></div>
                <div>Qty Estimate <span className="font-mono text-white">{trade.quantityEstimate ?? 0}</span></div>
                <div>Allocation <span className="font-mono text-white">{trade.allocationPct ?? 0}%</span></div>
              </div>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-slate-400">
                {trade.entryRules.map((rule) => <li key={rule}>{rule}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function PortfolioPanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  const fundsVerified = snapshot.portfolio.fundsSource?.startsWith('upstox') ?? false;
  const fundsHelper = fundsVerified ? `Live ${snapshot.portfolio.fundsSource?.replaceAll('_', ' ').toUpperCase()} funds` : 'Funds unavailable - check account-summary endpoint';

  return (
    <Card title="Upstox Portfolio" eyebrow="Broker, funds, positions, orders">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Available Funds" value={fundsVerified ? formatCurrency(snapshot.portfolio.availableMargin ?? 0) : 'UNAVAILABLE'} helper={fundsHelper} tone={fundsVerified ? 'cyan' : 'rose'} />
        <MetricCard label="Margin Used" value={fundsVerified ? formatCurrency(snapshot.portfolio.usedMargin ?? 0) : 'UNAVAILABLE'} helper={`Exposure ${snapshot.liveExposurePct}%`} tone="amber" />
        <MetricCard label="Realized PnL" value={formatCurrency(snapshot.portfolio.realizedPnl)} tone="emerald" />
        <MetricCard label="Unrealized PnL" value={formatCurrency(snapshot.portfolio.unrealizedPnl)} tone="violet" />
        <MetricCard label="Positions" value={snapshot.portfolio.positions} helper="Open index option legs" tone="cyan" />
        <MetricCard label="Orders" value={snapshot.portfolio.orders} helper="Session order count" tone="emerald" />
        <MetricCard label="Upstox Link" value={snapshot.upstoxConnection?.connected ? 'CONNECTED' : 'CHECK'} helper={snapshot.upstoxConnection?.dataSource ?? 'Waiting for broker data'} tone={snapshot.upstoxConnection?.connected ? 'emerald' : 'rose'} />
        <MetricCard label="Payin / Exposure" value={fundsVerified ? formatCurrency(snapshot.portfolio.payinAmount ?? 0) : 'UNAVAILABLE'} helper={`Pledge ${formatCurrency(snapshot.portfolio.pledgeAvailable ?? 0)} | Unsettled ${formatCurrency(snapshot.portfolio.unsettledProfit ?? 0)}`} tone="violet" />
      </div>
      {!fundsVerified && (
        <div className="mt-4 rounded-2xl border border-rose-300/20 bg-rose-300/10 p-4 text-sm text-rose-100">
          Funds API did not return verified capital. Market data, dynamic expiry, and tomorrow analysis are still using real Upstox data. Test backend: <span className="font-mono">/api/upstox/account-summary</span>
        </div>
      )}
      {snapshot.expiryState && (
        <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-4 text-sm text-slate-200">
          <p className="font-bold uppercase tracking-[0.18em] text-cyan-200">Dynamic expiry check</p>
          <p className="mt-2">
            Selected {snapshot.expiryState.symbol} expiry <span className="font-mono text-white">{snapshot.expiryState.selectedExpiry}</span> from{' '}
            <span className="font-mono text-white">{snapshot.expiryState.availableExpiryCount}</span> Upstox expiries ({snapshot.expiryState.source.replaceAll('_', ' ')}).
          </p>
          <p className="mt-1 text-xs text-slate-400">Available: {snapshot.expiryState.availableExpiries.slice(0, 6).join(', ')}</p>
        </div>
      )}
    </Card>
  );
}

export function RiskEnginePanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  const adaptive = snapshot.adaptiveRisk;
  return (
    <Card title="Professional Risk Engine" eyebrow="Adaptive TQS, exposure, drawdown and cooldown">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-3xl border border-slate-700/70 bg-slate-950/50 p-5">
          <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Risk State</p>
          <h3 className={`mt-3 text-4xl font-black ${snapshot.risk.safeMode ? 'text-rose-300' : 'text-emerald-300'}`}>{snapshot.risk.safeMode ? 'SAFE MODE' : 'NORMAL MODE'}</h3>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            {adaptive ? `${adaptive.profile.label} | ${adaptive.sessionBucket.replaceAll('_', ' ')} | ${adaptive.sessionNote}` : 'Adaptive profile unavailable.'}
          </p>
        </div>
        <div className="grid gap-3">
          <ScoreBar label="Daily Drawdown Used" value={(snapshot.risk.dailyDrawdownPct / snapshot.risk.maxDrawdownPct) * 100} dangerBelow={100} />
          <ScoreBar label="Slippage Kill Switch" value={Math.max(0, 100 - snapshot.risk.slippageBps * 4)} />
          <ScoreBar label="Latency Protection" value={Math.max(0, 100 - snapshot.risk.latencyMs / 2)} />
          <ScoreBar label="Spread Widening Guard" value={Math.max(0, 100 - snapshot.risk.spreadWideningPct * 3)} />
          <ScoreBar label="Exposure Headroom" value={Math.max(0, 100 - snapshot.liveExposurePct)} />
        </div>
      </div>
      {adaptive && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <MetricCard label="Minimum TQS" value={adaptive.minimumTqs} helper="Current trade threshold" tone="cyan" />
          <MetricCard label="Safe Mode TQS" value={adaptive.safeModeTqs} helper="Raised threshold in risk" tone="amber" />
          <MetricCard label="Max Exposure" value={`${adaptive.maxExposurePct}%`} helper={`Dynamic ${adaptive.dynamicExposurePct}%`} tone="violet" />
          <MetricCard label="Daily DD" value={`${adaptive.dailyDrawdownPct}%`} helper="Hard stop target" tone="rose" />
          <MetricCard label="Cooldown" value={`${adaptive.cooldownSeconds}s`} helper="Post-trade delay" tone="emerald" />
        </div>
      )}
      {adaptive?.adjustments && adaptive.adjustments.length > 0 && (
        <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-4 text-sm text-cyan-100">
          <p className="font-bold uppercase tracking-[0.2em]">Auto-switch adjustments</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {adaptive.adjustments.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {adaptive?.benchmarks && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Backtest Target" value={`${adaptive.benchmarks.minimumTrades}+`} helper="Minimum meaningful trades" tone="cyan" />
          <MetricCard label="Profit Factor" value={adaptive.benchmarks.targetProfitFactor} helper="Professional target" tone="emerald" />
          <MetricCard label="Max DD Goal" value={`<${adaptive.benchmarks.maxDrawdownGoalPct}%`} helper="Survival first" tone="amber" />
          <MetricCard label="Trades / Day" value={adaptive.benchmarks.targetTradesPerDay} helper="Aggressive scalp range" tone="violet" />
        </div>
      )}
    </Card>
  );
}

export function InfrastructureTelemetry({ snapshot }: { snapshot: TerminalSnapshot }) {
  const data = [
    { name: 'Broker', value: snapshot.infra.brokerHealth },
    { name: 'Redis', value: snapshot.infra.redisHealth },
    { name: 'Postgres', value: snapshot.infra.postgresHealth },
    { name: 'Prometheus', value: snapshot.infra.prometheusHealth },
  ];
  return (
    <Card title="Infrastructure Telemetry" eyebrow="Cloud-native execution observability">
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="grid gap-3 sm:grid-cols-2">
          <MetricCard label="WS Latency" value={`${snapshot.infra.websocketLatencyMs} ms`} tone="cyan" />
          <MetricCard label="Router Latency" value={`${snapshot.infra.orderRouterLatencyMs} ms`} tone="violet" />
          <MetricCard label="Stale Data" value={`${snapshot.risk.staleDataMs} ms`} tone="amber" />
          <MetricCard label="Disconnects" value={snapshot.risk.apiDisconnects} tone={snapshot.risk.apiDisconnects ? 'rose' : 'emerald'} />
        </div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.08)" />
              <XAxis dataKey="name" stroke="#64748b" fontSize={10} />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: '#020617', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12 }} />
              <Bar dataKey="value" fill="#22d3ee" radius={[8, 8, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </Card>
  );
}

export function AiAnalytics({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="AI Learning Analytics" eyebrow="Score, pnl, latency and regime storage">
      <div className="h-80">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={snapshot.telemetry}>
            <defs>
              <linearGradient id="pnlGradient" x1="0" x2="0" y1="0" y2="1">
                <stop offset="5%" stopColor="#34d399" stopOpacity={0.55} />
                <stop offset="95%" stopColor="#34d399" stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(148, 163, 184, 0.08)" />
            <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
            <YAxis stroke="#64748b" fontSize={10} />
            <Tooltip contentStyle={{ background: '#020617', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12 }} />
            <Area type="monotone" dataKey="pnl" stroke="#34d399" fill="url(#pnlGradient)" />
            <Area type="monotone" dataKey="tqs" stroke="#22d3ee" fill="transparent" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

export function TradeJournal({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Trade Journal" eyebrow="Permanent execution analytics log">
      <div className="overflow-hidden rounded-2xl border border-slate-700/70">
        <table className="w-full min-w-[680px] text-left text-sm">
          <thead className="bg-slate-900/90 text-xs uppercase tracking-[0.2em] text-slate-400">
            <tr><th className="p-3">Time</th><th>Instrument</th><th>TQS</th><th>PnL</th><th>Exit Reason</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-950/40">
            {snapshot.journal.map((entry) => (
              <tr key={`${entry.time}-${entry.instrument}`} className="text-slate-300">
                <td className="p-3 font-mono text-slate-400">{entry.time}</td>
                <td>{entry.instrument}</td>
                <td>{entry.tqs}</td>
                <td className={entry.pnl >= 0 ? 'font-bold text-emerald-300' : 'font-bold text-rose-300'}>{formatCurrency(entry.pnl)}</td>
                <td>{entry.exitReason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function SessionIntelligence({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Session Intelligence" eyebrow="Regime, profile and acceptance zones">
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="grid gap-3 sm:grid-cols-2">
          <MetricCard label="Regime" value={snapshot.regime.replaceAll('_', ' ')} helper="Session classifier" tone="cyan" />
          <MetricCard label="POC" value={snapshot.marketProfile.poc} helper="Point of control" tone="emerald" />
          <MetricCard label="VAH" value={snapshot.marketProfile.vah} helper="Value area high" tone="violet" />
          <MetricCard label="VAL" value={snapshot.marketProfile.val} helper="Value area low" tone="amber" />
        </div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={snapshot.marketProfile.volumeProfile} layout="vertical">
              <CartesianGrid stroke="rgba(148, 163, 184, 0.08)" />
              <XAxis type="number" stroke="#64748b" fontSize={10} />
              <YAxis type="category" dataKey="level" stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: '#020617', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12 }} />
              <Bar dataKey="volume" fill="#a78bfa" radius={[0, 8, 8, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="mt-4 rounded-2xl bg-slate-950/60 p-4 text-sm text-slate-300">{snapshot.marketProfile.acceptanceZone}</p>
      {snapshot.premarketAnalysis && (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
            <p className="text-xs font-bold uppercase tracking-[0.24em] text-cyan-200">Pre-market / closed-market analysis</p>
            <div className="mt-3 grid gap-2 text-sm text-slate-300">
              <div className="flex justify-between"><span>Readiness</span><span className="font-mono text-white">{snapshot.premarketAnalysis.readiness.replaceAll('_', ' ')}</span></div>
              <div className="flex justify-between"><span>Bias</span><span className="font-mono text-white">{snapshot.premarketAnalysis.bias}</span></div>
              <div className="flex justify-between"><span>PCR</span><span className="font-mono text-white">{snapshot.premarketAnalysis.pcr}</span></div>
              <div className="flex justify-between"><span>Score</span><span className="font-mono text-white">{snapshot.premarketAnalysis.score}</span></div>
            </div>
            <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-slate-400">
              {snapshot.premarketAnalysis.checklist.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </div>
          {snapshot.tomorrowTradePlan && (
            <div className="rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-emerald-200">Tomorrow candidate plan</p>
              <p className="mt-3 text-sm text-slate-200">
                {snapshot.tomorrowTradePlan.symbol} {snapshot.tomorrowTradePlan.candidate.strike} {snapshot.tomorrowTradePlan.candidate.side} | Expiry{' '}
                <span className="font-mono text-white">{snapshot.tomorrowTradePlan.expiry}</span> | Last premium{' '}
                <span className="font-mono text-white">{snapshot.tomorrowTradePlan.candidate.lastPremium}</span>
              </p>
              <p className="mt-2 text-xs text-slate-400">Instrument: {snapshot.tomorrowTradePlan.candidate.instrumentKey ?? 'not available'}</p>
              <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-slate-300">
                {snapshot.tomorrowTradePlan.entryRules.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function BacktestingPanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Backtesting" eyebrow="Real Upstox candle replay when auto trading is off">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {snapshot.backtest.map((metric) => (
          <MetricCard key={metric.name} label={metric.name} value={`${formatNumber(metric.value)}${metric.unit}`} helper="Computed from real Upstox candles" tone="cyan" />
        ))}
      </div>
      {snapshot.suggestedTrades && snapshot.suggestedTrades.length > 0 && (
        <div className="mt-5 rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm text-amber-100">
          <p className="font-bold uppercase tracking-[0.2em]">Suggestion mode</p>
          <p className="mt-2">
            Auto trading is {snapshot.liveTradingEnabled ? 'enabled' : 'off'}; current snapshot mode is {(snapshot.tradeMode ?? 'ANALYSIS_BACKTEST_ONLY').replaceAll('_', ' ')}.
            Suggestions are derived from Upstox LTP, option chain, Greeks and candles. Orders remain blocked unless execution is explicitly allowed.
          </p>
        </div>
      )}
    </Card>
  );
}

const riskProfiles = [
  { mode: 'safe_beginner', label: 'Safe Beginner', min: '82-88', safe: '92', exposure: '15-20%', dd: '1.5-2%', cooldown: '45-60s', note: 'First live weeks, INR 5k-25k accounts' },
  { mode: 'balanced_pro', label: 'Balanced Pro', min: '72-78', safe: '86', exposure: '25-35%', dd: '3%', cooldown: '20-30s', note: 'Best overall default' },
  { mode: 'aggressive_scalping', label: 'Aggressive Scalping', min: '64-70', safe: '82', exposure: '40-50%', dd: '4-5%', cooldown: '5-15s', note: 'Open-drive momentum only' },
  { mode: 'extreme_prop', label: 'Extreme Prop-Desk', min: '58-64', safe: '78', exposure: '60-70%', dd: '6-8%', cooldown: '1-5s', note: 'Not recommended initially' },
  { mode: 'realistic_aggressive', label: 'Realistic Aggressive', min: '68-72', safe: '84', exposure: '35-40%', dd: '3%', cooldown: '10-15s', note: 'Recommended for your style' },
];

export function SettingsPanel() {
  return (
    <Card title="Settings" eyebrow="Institutional aggression profiles">
      <div className="grid gap-4">
        {riskProfiles.map((profile) => (
          <div key={profile.mode} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <p className="text-sm font-bold text-white">{profile.label}</p>
                <p className="mt-1 text-xs text-slate-400">{profile.note}</p>
                <p className="mt-1 text-[10px] uppercase tracking-[0.24em] text-cyan-300">AGGRESSION_PROFILE={profile.mode}</p>
              </div>
              <div className="grid gap-2 text-xs text-slate-300 sm:grid-cols-5">
                <span>Min TQS <b className="text-white">{profile.min}</b></span>
                <span>Safe TQS <b className="text-white">{profile.safe}</b></span>
                <span>Exposure <b className="text-white">{profile.exposure}</b></span>
                <span>DD <b className="text-white">{profile.dd}</b></span>
                <span>Cooldown <b className="text-white">{profile.cooldown}</b></span>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm text-amber-100">
        Set <span className="font-mono">AGGRESSION_PROFILE</span> in Railway variables to persist the active profile. Session intelligence automatically adjusts TQS, exposure and cooldown for open drive, midday chop and closing momentum.
      </div>
    </Card>
  );
}
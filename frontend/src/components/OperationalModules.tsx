import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from './Card';
import { MetricCard } from './MetricCard';
import { ScoreBar } from './ScoreBar';
import type { TerminalSnapshot } from '../types';
import { formatCurrency, formatNumber } from '../utils/format';

export function StrategyRouter({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Adaptive Strategy Router" eyebrow="Smart execution route selection">
      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard label="Selected Strategy" value={snapshot.strategy.selected} helper="Regime-aware scalp model" tone="cyan" />
        <MetricCard label="Aggression" value={`${snapshot.strategy.aggression}%`} helper={snapshot.strategy.router.replaceAll('_', ' ')} tone="amber" />
        <MetricCard label="Size Multiplier" value={`${snapshot.strategy.sizeMultiplier}x`} helper={`TQS threshold ${snapshot.strategy.threshold}`} tone="emerald" />
      </div>
      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <ScoreBar label="Momentum Expansion" value={snapshot.orderflow.breakoutVelocity} />
        <ScoreBar label="Liquidity Confirmation" value={snapshot.orderflow.liquidityShift} />
        <ScoreBar label="Spread Quality" value={snapshot.spreadQuality} />
        <ScoreBar label="Option Chain Bias" value={snapshot.aiMatrix.find((item) => item.engine === 'Option Chain Bias')?.score ?? 0} />
      </div>
    </Card>
  );
}

export function PortfolioPanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  return (
    <Card title="Upstox Portfolio" eyebrow="Broker, funds, positions, orders">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Available Funds" value={formatCurrency(snapshot.portfolio.availableMargin ?? 0)} helper="Verified live Upstox funds" tone="cyan" />
        <MetricCard label="Margin Used" value={formatCurrency(snapshot.portfolio.usedMargin ?? 0)} helper={`Exposure ${snapshot.liveExposurePct}%`} tone="amber" />
        <MetricCard label="Realized PnL" value={formatCurrency(snapshot.portfolio.realizedPnl)} tone="emerald" />
        <MetricCard label="Unrealized PnL" value={formatCurrency(snapshot.portfolio.unrealizedPnl)} tone="violet" />
        <MetricCard label="Positions" value={snapshot.portfolio.positions} helper="Open index option legs" tone="cyan" />
        <MetricCard label="Orders" value={snapshot.portfolio.orders} helper="Session order count" tone="emerald" />
        <MetricCard label="Upstox Link" value={snapshot.upstoxConnection?.connected ? 'CONNECTED' : 'CHECK'} helper={snapshot.upstoxConnection?.dataSource ?? 'Waiting for broker data'} tone={snapshot.upstoxConnection?.connected ? 'emerald' : 'rose'} />
        <MetricCard label="Payin / Exposure" value={formatCurrency(snapshot.portfolio.payinAmount ?? 0)} helper={`Exposure margin ${formatCurrency(snapshot.portfolio.exposureMargin ?? 0)}`} tone="violet" />
      </div>
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
  return (
    <Card title="Professional Risk Engine" eyebrow="Drawdown, stale-data, latency and spread protection">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-3xl border border-slate-700/70 bg-slate-950/50 p-5">
          <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Risk State</p>
          <h3 className={`mt-3 text-4xl font-black ${snapshot.risk.safeMode ? 'text-rose-300' : 'text-emerald-300'}`}>{snapshot.risk.safeMode ? 'SAFE MODE' : 'NORMAL MODE'}</h3>
          <p className="mt-3 text-sm leading-6 text-slate-300">
            Safe Mode reduces size, raises AI thresholds, lowers aggression, applies cooldown timers,
            and blocks new trades during broker, latency, stale-data, or slippage anomalies.
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
    <Card title="Backtesting" eyebrow="Institutional scalp model validation">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {snapshot.backtest.map((metric) => (
          <MetricCard key={metric.name} label={metric.name} value={`${formatNumber(metric.value)}${metric.unit}`} helper="Rolling research metric" tone="cyan" />
        ))}
      </div>
    </Card>
  );
}

export function SettingsPanel() {
  return (
    <Card title="Settings" eyebrow="Execution thresholds and broker credentials">
      <div className="grid gap-4 md:grid-cols-2">
        {[
          ['Minimum TQS', '76'],
          ['Safe Mode TQS', '86'],
          ['Max Exposure', '42%'],
          ['Daily Drawdown', '3%'],
          ['Cooldown', '25 sec'],
          ['Broker Adapter', 'Upstox V3'],
        ].map(([label, value]) => (
          <label key={label} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
            <span className="text-xs uppercase tracking-[0.22em] text-slate-500">{label}</span>
            <input className="mt-2 w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-cyan-300" defaultValue={value} />
          </label>
        ))}
      </div>
      <p className="mt-4 text-sm text-slate-400">Production broker credentials should be injected as environment variables on Render or AWS and never committed.</p>
    </Card>
  );
}

import { useEffect, useState } from 'react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from './Card';
import { MetricCard } from './MetricCard';
import { ScoreBar } from './ScoreBar';
import { apiUrl, displayApiUrl } from '../config/api';
import type { TerminalSnapshot } from '../types';
import { formatCurrency, formatNumber } from '../utils/format';


function TradingCapitalControl({ snapshot }: { snapshot: TerminalSnapshot }) {
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
      {snapshot.explosiveRunner && (
        <div className={`mt-5 rounded-2xl border p-4 text-sm ${snapshot.explosiveRunner.candidate ? 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100' : 'border-slate-700 bg-slate-950/60 text-slate-300'}`}>
          <p className="font-bold uppercase tracking-[0.2em]">Explosive Runner Engine | {snapshot.explosiveRunner.confidence}</p>
          <p className="mt-1 text-xs text-slate-300">
            Always-on scan: {snapshot.explosiveRunner.watchMode ?? 'OPEN_EXPLOSIVE_SCAN'} every {snapshot.explosiveRunner.monitoringCadenceSeconds ?? 1}s
            {snapshot.explosiveRunner.strike ? ` | Best: ${snapshot.explosiveRunner.symbol ?? snapshot.symbol} ${snapshot.explosiveRunner.strike} ${snapshot.explosiveRunner.side}` : ''}
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            <span>Score <b>{snapshot.explosiveRunner.score}</b></span>
            <span>Target <b>{snapshot.explosiveRunner.targetPremiumPct}%</b></span>
            <span>Stop <b>{snapshot.explosiveRunner.hardStopPct}%</b></span>
            <span>Trail <b>{snapshot.explosiveRunner.trailPct}%</b></span>
          </div>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs">
            {snapshot.explosiveRunner.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
          <p className="mt-3 text-xs text-emerald-200/80">Ideal available data: {Array.isArray(snapshot.explosiveRunner.dataStatus.idealAvailable) ? snapshot.explosiveRunner.dataStatus.idealAvailable.join(', ') || 'n/a' : 'n/a'}</p>
          <p className="mt-1 text-xs text-amber-200/80">Still missing: {Array.isArray(snapshot.explosiveRunner.dataStatus.idealMissing) ? snapshot.explosiveRunner.dataStatus.idealMissing.join(', ') || 'none' : 'n/a'}</p>
        </div>
      )}
      {snapshot.explosiveRunnerWatchlist && snapshot.explosiveRunnerWatchlist.length > 0 && (
        <div className="mt-5 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-4">
          <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-100">Second-by-second explosive watchlist</p>
          <div className="mt-3 grid gap-2 lg:grid-cols-2">
            {snapshot.explosiveRunnerWatchlist.slice(0, 6).map((runner) => (
              <div key={`${runner.symbol}-${runner.expiry}-${runner.strike}-${runner.side}`} className={`rounded-xl border p-3 text-xs ${runner.candidate ? 'border-emerald-300/30 bg-emerald-300/10 text-emerald-100' : 'border-slate-700 bg-slate-950/60 text-slate-300'}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="font-bold text-white">{runner.symbol} {runner.strike} {runner.side}</span>
                  <span>{runner.confidence} / {runner.score}</span>
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2">
                  <span>LTP {runner.premium ?? runner.lastPremium ?? 0}</span>
                  <span>Target {runner.targetPremiumPct}%</span>
                  <span>Trail {runner.trailPct}%</span>
                </div>
                {runner.reasons.length > 0 && <p className="mt-2 text-slate-400">{runner.reasons.slice(0, 2).join(' | ')}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
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
                  <p className="text-xs text-slate-400">
                    {trade.instrumentKey ?? 'Instrument unavailable'} | Last premium {trade.lastPremium}
                    {trade.lotSize ? ` | Lot ${trade.lotSize} x ${trade.estimatedLots ?? 0}` : ''}
                  </p>
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
          {snapshot.optimizedProfile && <MetricCard label="Stored Profile" value={String(snapshot.optimizedProfile.mode ?? 'optimized')} helper={`Target ${snapshot.optimizedProfile.targetPoints} | Stop ${snapshot.optimizedProfile.stopPoints}`} tone="cyan" />}
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
      {snapshot.noTradeZones && snapshot.noTradeZones.activeZones.length > 0 && (
        <div className="mt-4 rounded-2xl border border-rose-300/20 bg-rose-300/10 p-4 text-sm text-rose-100">
          <p className="font-bold uppercase tracking-[0.2em]">No-Trade Zone Detector {snapshot.noTradeZones.blocked ? 'BLOCKING' : 'WATCH'}</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {snapshot.noTradeZones.activeZones.map((zone) => <li key={`${zone.name}-${zone.reason}`}>{zone.name}: {zone.reason}</li>)}
          </ul>
        </div>
      )}
      {snapshot.adaptiveExit && (
        <div className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4 text-sm text-emerald-100">
          <p className="font-bold uppercase tracking-[0.2em]">Adaptive Exit Engine {snapshot.adaptiveExit.executionStyle ? `| ${snapshot.adaptiveExit.executionStyle.replaceAll('_', ' ')}` : ''}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            <span>Target <b>{snapshot.adaptiveExit.targetPoints}</b></span>
            <span>Stop <b>{snapshot.adaptiveExit.stopPoints}</b></span>
            <span>BE <b>{snapshot.adaptiveExit.breakevenShiftPoints ?? 0}</b></span>
            <span>Trail <b>{snapshot.adaptiveExit.trailPoints}</b></span>
            <span>Partial <b>{snapshot.adaptiveExit.partialExitAt}</b></span>
            <span>ATR <b>{snapshot.adaptiveExit.atrPoints ?? 0}</b></span>
            <span>Partial <b>{Math.round((snapshot.adaptiveExit.partialExitPct ?? 0) * 100)}%</b></span>
            <span>Runner <b>{Math.round((snapshot.adaptiveExit.runnerPct ?? 0) * 100)}%</b></span>
          </div>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs">
            {snapshot.adaptiveExit.rules.filter((rule) => rule.active).map((rule) => <li key={rule.name}>{rule.name}: {rule.action}</li>)}
          </ul>
        </div>
      )}
      {snapshot.institutionalReadiness && (
        <div className={`mt-4 rounded-2xl border p-4 text-sm ${snapshot.institutionalReadiness.overall >= 9.5 ? 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100' : 'border-amber-300/20 bg-amber-300/10 text-amber-100'}`}>
          <p className="font-bold uppercase tracking-[0.2em]">Institutional Readiness Score: {snapshot.institutionalReadiness.overall}/10</p>
          <p className="mt-2">Target: {snapshot.institutionalReadiness.target}/10 | Full capital allowed: {snapshot.institutionalReadiness.liveFullCapitalAllowed ? 'YES' : 'NO'}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(snapshot.institutionalReadiness.scores).map(([area, score]) => (
              <div key={area} className="flex justify-between rounded-xl bg-slate-950/40 px-3 py-2 text-xs"><span>{area}</span><span>{score}</span></div>
            ))}
          </div>
          {snapshot.institutionalReadiness.nextActions.length > 0 && (
            <ul className="mt-3 list-disc pl-5 text-xs">
              {snapshot.institutionalReadiness.nextActions.map((action) => <li key={action}>{action}</li>)}
            </ul>
          )}
        </div>
      )}
      {snapshot.productionReadiness && (
        <div className={`mt-4 rounded-2xl border p-4 text-sm ${snapshot.productionReadiness.readyForFullCapital ? 'border-emerald-300/20 bg-emerald-300/10 text-emerald-100' : snapshot.productionReadiness.readyForSmallLive ? 'border-amber-300/20 bg-amber-300/10 text-amber-100' : 'border-rose-300/20 bg-rose-300/10 text-rose-100'}`}>
          <p className="font-bold uppercase tracking-[0.2em]">Production Readiness: {snapshot.productionReadiness.recommendation}</p>
          <p className="mt-2">{snapshot.productionReadiness.passed}/{snapshot.productionReadiness.total} readiness checks passed.</p>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {snapshot.productionReadiness.checks.map((check) => (
              <div key={check.name} className="flex justify-between rounded-xl bg-slate-950/40 px-3 py-2 text-xs">
                <span>{check.name}</span><span className={check.passed ? 'text-emerald-300' : 'text-rose-300'}>{check.passed ? 'OK' : 'FAIL'}</span>
              </div>
            ))}
          </div>
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
          <MetricCard label="Backend Latency" value={`${snapshot.infra.websocketLatencyMs} ms`} tone="cyan" />
          <MetricCard label="Upstox Latency" value={`${snapshot.infra.upstoxLatencyMs ?? 0} ms`} tone="violet" />
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
  const events = snapshot.eventJournal ?? [];
  return (
    <Card title="Institutional Event Journal" eyebrow="Signals, entries, exits, rejections, risk gates, latency and API errors">
      {events.length > 0 ? (
        <div className="overflow-hidden rounded-2xl border border-slate-700/70">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-slate-900/90 text-xs uppercase tracking-[0.2em] text-slate-400">
              <tr><th className="p-3">Time</th><th>Type</th><th>Severity</th><th>Symbol</th><th>Message</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-950/40">
              {events.map((event) => (
                <tr key={event.eventId} className="text-slate-300">
                  <td className="p-3 font-mono text-slate-400">{new Date(event.timestamp).toLocaleTimeString()}</td>
                  <td className="font-bold text-cyan-200">{event.type}</td>
                  <td className={event.severity === 'ERROR' ? 'font-bold text-rose-300' : event.severity === 'WARN' ? 'font-bold text-amber-300' : 'font-bold text-emerald-300'}>{event.severity}</td>
                  <td>{event.symbol ?? '-'}</td>
                  <td>{event.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-5 text-sm text-slate-300">
          No journal events yet. Events will appear for signals, rejections, entries, exits, risk gates, latency spikes and API errors.
        </div>
      )}
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
          <MetricCard label="Opening High" value={snapshot.marketProfile.openingRangeHigh ?? snapshot.marketProfile.vah} helper="Opening range" tone="cyan" />
          <MetricCard label="Opening Low" value={snapshot.marketProfile.openingRangeLow ?? snapshot.marketProfile.val} helper="Opening range" tone="amber" />
          <MetricCard label="HVN" value={snapshot.marketProfile.hvn ?? snapshot.marketProfile.poc} helper="High volume node" tone="emerald" />
          <MetricCard label="LVN" value={snapshot.marketProfile.lvn ?? snapshot.marketProfile.poc} helper="Low volume node" tone="violet" />
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
      {snapshot.newsState && (
        <div className={`mt-4 rounded-2xl border p-4 text-sm ${snapshot.newsState.eventRisk === 'HIGH' ? 'border-rose-300/20 bg-rose-300/10 text-rose-100' : snapshot.newsState.eventRisk === 'MEDIUM' ? 'border-amber-300/20 bg-amber-300/10 text-amber-100' : 'border-cyan-300/20 bg-cyan-300/10 text-cyan-100'}`}>
          <p className="font-bold uppercase tracking-[0.2em]">News / Event Intelligence | {snapshot.newsState.eventRisk}</p>
          <p className="mt-2">Sentiment: <b>{snapshot.newsState.sentiment}</b> | Score {snapshot.newsState.score}</p>
          {snapshot.newsState.unavailableReason && <p className="mt-2 text-xs text-slate-300">News unavailable: {snapshot.newsState.unavailableReason}</p>}
          <div className="mt-3 space-y-2">
            {snapshot.newsState.articles.slice(0, 3).map((article) => (
              <div key={article.title} className="rounded-xl bg-slate-950/50 p-3 text-xs text-slate-200">{article.title || 'Untitled news'}</div>
            ))}
          </div>
        </div>
      )}
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
              {snapshot.tomorrowTradePlan.premiumRange && (
                <p className={`mt-2 text-xs ${snapshot.tomorrowTradePlan.premiumRange.withinRange ? 'text-emerald-200' : 'text-amber-200'}`}>
                  LTP range {snapshot.tomorrowTradePlan.premiumRange.min}-{snapshot.tomorrowTradePlan.premiumRange.max}: {snapshot.tomorrowTradePlan.premiumRange.withinRange ? 'inside considerable range' : 'outside range, watch only'} | Source {snapshot.tomorrowTradePlan.source ?? 'plan'}
                </p>
              )}
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
  const auto = snapshot.autoTrader;
  const slippageModel = auto?.slippageModel ?? { averageExpectedSlippage: 0, minimumRequiredMovePoints: 0, model: 'unavailable after market close' };
  const positionSizing = auto?.positionSizing ?? { capital: 0, candidates: [] };
  const exitRules = auto?.exitEngine?.rules ?? [];
  return (
    <Card title="Backtesting Hub" eyebrow="Paper trading, replay, lifecycle, online learning and daily report">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {snapshot.backtest.map((metric) => (
          <MetricCard key={metric.name} label={metric.name} value={`${formatNumber(metric.value)}${metric.unit}`} helper="Computed from real Upstox candles" tone="cyan" />
        ))}
      </div>
      {auto && (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="Paper Trading" value={auto.paperTrading ? 'ON' : 'OFF'} helper="Shadow execution safety" tone={auto.paperTrading ? 'emerald' : 'amber'} />
            <MetricCard label="Signals / Tick" value={auto.signalsThisTick} helper="NIFTY + SENSEX candidates" tone="cyan" />
            <MetricCard label="Replay Buffer" value={auto.replay.storedSnapshots} helper="Stored market snapshots" tone="violet" />
            <MetricCard label="AI Learning" value={auto.onlineLearning.samples} helper={`Score ${auto.onlineLearning.learningScore ?? auto.onlineLearning.score ?? 0} | ${auto.onlineLearning.priorVersion ?? 'prior'}`} tone="emerald" />
            <MetricCard label="Paper Trades" value={auto.dailyReport.paperTrades} helper={`${auto.dailyReport.openTrades} open`} tone="cyan" />
            <MetricCard label="Win Rate" value={`${auto.dailyReport.winRate}%`} helper={`${auto.dailyReport.wins}W / ${auto.dailyReport.losses}L`} tone="emerald" />
            <MetricCard label="Profit Factor" value={auto.dailyReport.profitFactor} helper="Paper outcomes" tone="amber" />
            <MetricCard label="Max DD" value={formatCurrency(auto.dailyReport.maxDrawdown)} helper="Paper drawdown" tone="rose" />
            <MetricCard label="Profit Lock" value={auto.profitLock?.activeTier ? `${auto.profitLock.activeTier.pct}%` : 'WAIT'} helper={auto.profitLock?.message ?? 'No profit tier locked'} tone={auto.profitLock?.blockNewTrades ? 'rose' : 'emerald'} />
            <MetricCard label="Giveback Buffer" value={formatCurrency(auto.profitLock?.givebackAvailable ?? 0)} helper={`Locked ${formatCurrency(auto.profitLock?.lockedProfit ?? 0)}`} tone="violet" />
          </div>
          <div className="mt-5 grid gap-4 xl:grid-cols-2">
            <div className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-cyan-200">Order lifecycle</p>
              <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1 text-xs text-slate-300">
                {auto.orderLifecycle.slice(-12).map((event, index) => (
                  <div key={`${event.timestamp}-${index}`} className="rounded-xl bg-slate-900/80 p-3">
                    <div className="flex justify-between gap-3"><span className="font-bold text-white">{event.state}</span><span className="font-mono text-slate-500">{new Date(event.timestamp).toLocaleTimeString()}</span></div>
                    <p className="mt-1 text-slate-400">{event.reason}</p>
                  </div>
                ))}
                {auto.orderLifecycle.length === 0 && <p>No lifecycle events yet.</p>}
              </div>
            </div>
            <div className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.24em] text-emerald-200">Exit engine + slippage</p>
              <div className="mt-3 grid gap-2 text-sm text-slate-300">
                <div className="flex justify-between"><span>Avg expected slippage</span><span className="font-mono text-white">{slippageModel.averageExpectedSlippage}</span></div>
                <div className="flex justify-between"><span>Min required move</span><span className="font-mono text-white">{slippageModel.minimumRequiredMovePoints}</span></div>
                <div className="flex justify-between"><span>Position capital</span><span className="font-mono text-white">{formatCurrency(positionSizing.capital)}</span></div>
              </div>
              <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-slate-400">
                {exitRules.length > 0 ? exitRules.map((rule) => <li key={rule}>{rule}</li>) : <li>Exit engine details available during live snapshot processing.</li>}
              </ul>
            </div>
          </div>
          <div className="mt-5 rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm text-amber-100">
            <p className="font-bold uppercase tracking-[0.2em]">Pretrained + continuous AI learning</p>
            <p className="mt-2">{auto.onlineLearning.note}</p>
            <p className="mt-2 text-xs text-amber-200/80">Pretrained: {auto.onlineLearning.pretrained ? 'YES' : 'NO'} | Prior {auto.onlineLearning.priorVersion ?? 'n/a'} | Paper samples {auto.onlineLearning.paperSamples ?? 0} | Live samples {auto.onlineLearning.liveSamples ?? 0} | PF {auto.onlineLearning.profitFactor ?? 0}</p>
            <p className="mt-2 text-xs text-slate-300">Historical trainer endpoint: <span className="font-mono">/api/ai-learning/train-now?target_trades=1000</span><br />Optimizer endpoint: <span className="font-mono">/api/strategy-optimizer/run-both?target_samples=1000&objective=high_win_scalp</span></p>
          </div>
        </>
      )}
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


export function PaperTradingPanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  const auto = snapshot.autoTrader;
  if (!auto) {
    return (
      <Card title="Paper Trading" eyebrow="Shadow execution status">
        <p className="text-sm text-slate-300">No paper trading state available yet. Wait for the next real Upstox snapshot.</p>
      </Card>
    );
  }

  const openPaperTrades = auto.openPaperTrades ?? [];
  const closedPaperTrades = auto.closedPaperTrades ?? [];
  const skippedSignals = auto.skippedSignals ?? [];
  const orderLifecycle = auto.orderLifecycle ?? [];
  const replay = auto.replay ?? { storedSnapshots: 0 };
  const dailyReport = auto.dailyReport ?? { paperTrades: 0, openTrades: 0, wins: 0, losses: 0, winRate: 0, grossProfit: 0, grossLoss: 0, profitFactor: 0, maxDrawdown: 0, reasonForLosses: {}, totalSignals: 0 };
  const paperSessions = auto.paperSessions;
  const performance = auto.performanceAnalysis;
  const profilePlan = performance?.institutionalAggressionProfiles;
  const targetLock = auto.targetLock;
  const dayAggregate = paperSessions?.dayAggregate ?? dailyReport.dayAggregate;
  const currentSession = paperSessions?.currentSession;
  const completedSessions = paperSessions?.completedSessionsToday ?? [];
  const breadth = snapshot.marketSnapshot?.breadth;
  const openPnl = openPaperTrades.reduce((sum, trade) => sum + (trade.pnl ?? 0), 0);
  const recentClosedPnl = closedPaperTrades.reduce((sum, trade) => sum + (trade.pnl ?? 0), 0);
  const dayNetPnl = dailyReport.netPnl ?? dayAggregate?.netPnl ?? 0;
  const dayClosedTrades = dailyReport.paperTrades ?? dayAggregate?.paperTrades ?? closedPaperTrades.length;
  const dayProfitFactor = dailyReport.profitFactor ?? dayAggregate?.profitFactor ?? 0;
  const dayWinRate = dailyReport.winRate ?? 0;

  return (
    <div className="space-y-4">
      <Card title="Paper Trading Control" eyebrow="Shadow execution without broker orders">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Paper Mode" value={auto.paperTrading ? 'ON' : 'OFF'} helper={auto.shadowTradeAllSignals ? 'Shadow all signals' : 'Quality-gated'} tone={auto.paperTrading ? 'emerald' : 'amber'} />
          <MetricCard label="Open Paper Trades" value={openPaperTrades.length} helper={`Open PnL ${formatCurrency(openPnl)}`} tone="cyan" />
          <MetricCard label="Day Paper Trades" value={dayClosedTrades} helper={`Day PnL ${formatCurrency(dayNetPnl)}`} tone={dayNetPnl >= 0 ? 'emerald' : 'rose'} />
          <MetricCard label="Day Profit Factor" value={dayProfitFactor} helper={`${dayWinRate}% win rate`} tone={dayProfitFactor >= 2 ? 'emerald' : dayProfitFactor >= 1 ? 'amber' : 'rose'} />
          <MetricCard label="Signals / Tick" value={auto.signalsThisTick ?? 0} helper={`${skippedSignals.length} skipped shown`} tone="cyan" />
          <MetricCard label="Replay Buffer" value={replay.storedSnapshots} helper="Stored snapshots" tone="violet" />
          <MetricCard label="Learning Samples" value={auto.onlineLearning.samples} helper={`Score ${auto.onlineLearning.learningScore ?? auto.onlineLearning.score ?? 0}`} tone="emerald" />
          <MetricCard label="Profit Lock" value={auto.profitLock?.activeTier ? `${auto.profitLock.activeTier.pct}%` : 'WAIT'} helper={auto.profitLock?.message ?? 'No locked tier'} tone={auto.profitLock?.blockNewTrades ? 'rose' : 'amber'} />
          {breadth && (
            <MetricCard label="Market Breadth" value={`${breadth.bias} ${breadth.score}`} helper={`${breadth.advancing} advancing / ${breadth.declining} declining`} tone={breadth.bias === 'BULLISH' ? 'emerald' : breadth.bias === 'BEARISH' ? 'rose' : 'amber'} />
          )}
          {targetLock?.enabled && (
            <MetricCard label="Daily Target Lock" value={targetLock.locked || targetLock.projectedLocked ? 'LOCKED' : formatCurrency(targetLock.remainingToTarget)} helper={`${targetLock.dayQuality ?? 'DAY'} ${targetLock.targetPct ?? ''}% | Projected ${formatCurrency(targetLock.projectedNetPnl)}`} tone={targetLock.locked || targetLock.projectedLocked ? 'emerald' : 'amber'} />
          )}
          {paperSessions?.rotationEnabled && (
            <>
              <MetricCard label="Session" value={`#${currentSession?.sessionNumber ?? dailyReport.sessionNumber ?? 1}`} helper={currentSession?.id ? `${currentSession.id.slice(-12)}` : 'Active paper session'} tone="cyan" />
              <MetricCard label="Sessions Today" value={dayAggregate?.sessionsIncludingCurrent ?? 1} helper={`${completedSessions.length} completed`} tone="violet" />
              <MetricCard label="Day Net PnL" value={formatCurrency(dayAggregate?.netPnl ?? 0)} helper={`${dayAggregate?.paperTrades ?? 0} trades across sessions`} tone={(dayAggregate?.netPnl ?? 0) >= 0 ? 'emerald' : 'rose'} />
            </>
          )}
        </div>
        {auto.sessionRotation?.rotated && (
          <div className="mt-4 rounded-2xl border border-amber-300/30 bg-amber-300/10 p-4 text-sm text-amber-100">
            Session rotated: {auto.sessionRotation.reason ?? 'limits reached'} → new session #{String((auto.sessionRotation.newSession as { sessionNumber?: number } | undefined)?.sessionNumber ?? '?')}
          </div>
        )}
        <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-4 text-sm text-slate-300">
          Reset endpoint: <span className="font-mono text-cyan-200">/api/auto-trader/reset</span> | Status endpoint: <span className="font-mono text-cyan-200">/api/auto-trader/status</span>
          {' '}| Recent closed shown: <span className="font-mono text-cyan-200">{closedPaperTrades.length}</span> ({formatCurrency(recentClosedPnl)})
          {paperSessions?.rotationEnabled && <> | Sessions: <span className="font-mono text-cyan-200">/api/auto-trader/paper-sessions</span></>}
          {performance && <> | Analysis: <span className="font-mono text-cyan-200">/api/auto-trader/performance-analysis</span></>}
        </div>
      </Card>

      {snapshot.marketSnapshot && (
        <Card title="Market Breadth Universe" eyebrow="Participation confirmation used by paper trade quality gate">
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard label="Breadth Bias" value={snapshot.marketSnapshot.breadth?.bias ?? 'N/A'} helper={`Score ${snapshot.marketSnapshot.breadth?.score ?? 0}`} tone={snapshot.marketSnapshot.breadth?.bias === 'BULLISH' ? 'emerald' : snapshot.marketSnapshot.breadth?.bias === 'BEARISH' ? 'rose' : 'amber'} />
            <MetricCard label="Coverage" value={`${snapshot.marketSnapshot.count ?? 0}/${snapshot.marketSnapshot.breadthQuality?.minimumRecommended ?? 15}`} helper={snapshot.marketSnapshot.breadthQuality?.sufficient ? 'Sufficient' : 'Limited'} tone={snapshot.marketSnapshot.breadthQuality?.sufficient ? 'emerald' : 'amber'} />
            <MetricCard label="Advancing" value={snapshot.marketSnapshot.breadth?.advancing ?? 0} helper="Configured universe" tone="emerald" />
            <MetricCard label="Declining" value={snapshot.marketSnapshot.breadth?.declining ?? 0} helper="Configured universe" tone="rose" />
          </div>
          <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-4 text-xs text-slate-300">
            <p className="font-bold uppercase tracking-[0.18em] text-cyan-200">Tracked indices/sectors</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(snapshot.marketSnapshot.configuredInstruments ?? []).map((item) => (
                <span key={item} className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1 font-mono text-[11px] text-slate-300">{item.replace('NSE_INDEX|', '').replace('BSE_INDEX|', '')}</span>
              ))}
            </div>
            {snapshot.marketSnapshot.breadthQuality?.message && <p className="mt-3 text-amber-200">{snapshot.marketSnapshot.breadthQuality.message}</p>}
          </div>
        </Card>
      )}

      {performance && profilePlan && (
        <Card title="Paper Performance Optimizer" eyebrow="Best observed bucket, side, symbol and time-based aggression schedule">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <MetricCard label="Daily Target" value={formatCurrency(performance.target.dailyProfitAmount)} helper={`${performance.target.dailyProfitPct}% of capital`} tone="emerald" />
            <MetricCard label="Current Day Net" value={formatCurrency(performance.target.currentNetPnl)} helper={`Remaining ${formatCurrency(performance.target.remainingToTarget)}`} tone={performance.target.currentNetPnl >= 0 ? 'emerald' : 'rose'} />
            <MetricCard label="Best Window" value={performance.bestObserved.bucket ?? 'n/a'} helper={`PF ${performance.bestObserved.bucket ? performance.byBucket[performance.bestObserved.bucket]?.profitFactor ?? 0 : 0}`} tone="cyan" />
            <MetricCard label="Best Symbol / Side" value={`${performance.bestObserved.symbol ?? 'n/a'} ${performance.bestObserved.side ?? ''}`} helper="From today's paper trades" tone="violet" />
            <MetricCard label="Base Profile" value={profilePlan.recommendedBaseProfile.replaceAll('_', ' ')} helper="Dynamic by time window" tone="amber" />
            {performance.rollingProof && <MetricCard label="Rolling Proof PF" value={performance.rollingProof.profitFactor} helper={`${performance.rollingProof.paperTrades}/${performance.rollingProof.windowTrades} trades | DD ${performance.rollingProof.maxDrawdownPct}%`} tone={performance.rollingProof.profitFactor >= 2 ? 'emerald' : 'amber'} />}
            {performance.liveReadiness && <MetricCard label="Live Readiness" value={performance.liveReadiness.ready ? 'PASS' : 'NO'} helper={performance.liveReadiness.mode.replaceAll('_', ' ')} tone={performance.liveReadiness.ready ? 'emerald' : 'rose'} />}
            {performance.breadthReadiness && <MetricCard label="Breadth Coverage" value={`${performance.breadthReadiness.count}/${performance.breadthReadiness.recommendedCount}`} helper={performance.breadthReadiness.sufficient ? 'Institutional-grade' : 'Add more instruments'} tone={performance.breadthReadiness.sufficient ? 'emerald' : 'amber'} />}
          </div>
          {performance.liveReadiness && (
            <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-4 text-xs text-slate-300">
              <p className="font-bold uppercase tracking-[0.18em] text-cyan-200">100-trade proof gate</p>
              <p className="mt-2">{performance.liveReadiness.message}</p>
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {performance.liveReadiness.checks.map((check) => (
                  <div key={check.name} className={check.passed ? 'text-emerald-300' : 'text-rose-300'}>
                    {check.passed ? 'PASS' : 'FAIL'} {check.name}: {JSON.stringify(check.value)} / {JSON.stringify(check.required)}
                  </div>
                ))}
              </div>
            </div>
          )}
          {profilePlan.bestTiming && (
            <div className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4 text-sm text-emerald-100">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-emerald-200">Best timing profile</p>
              <p className="mt-2 font-semibold">{profilePlan.bestTiming.primaryWindowIst} IST | {profilePlan.bestTiming.primaryProfile.replaceAll('_', ' ')} | {profilePlan.bestTiming.primarySetup}</p>
              <p className="mt-1 text-xs text-emerald-100/80">{profilePlan.bestTiming.rule}</p>
              <p className="mt-1 text-xs text-amber-100/80">Avoid: {profilePlan.bestTiming.avoidWindowsIst.join(', ')}</p>
            </div>
          )}
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {Object.entries(profilePlan.timeWindowSettings).map(([bucket, plan]) => (
              <div key={bucket} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4 text-sm text-slate-300">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-bold text-white">{bucket.replaceAll('_', ' ')}</span>
                  <span className="text-cyan-200">{plan.profile.replaceAll('_', ' ')}</span>
                </div>
                <div className="mt-2 grid gap-2 text-xs sm:grid-cols-4">
                  <span>{plan.windowIst ?? 'Timing n/a'} IST</span>
                  <span>{plan.permission?.replaceAll('_', ' ') ?? 'Selective'}</span>
                  <span>Alloc x{plan.allocationPctMultiplier}</span>
                  <span>TQS {plan.minEntryTqs}</span>
                  <span>Runner {plan.minRunnerScore}</span>
                  <span>Hold {plan.maxHoldSeconds}s</span>
                </div>
                <p className="mt-2 text-xs text-slate-400">{plan.note}</p>
              </div>
            ))}
          </div>
          <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-slate-300">
            {profilePlan.why.map((item) => <li key={item}>{item}</li>)}
          </ul>
          {performance.recentPostmortems && performance.recentPostmortems.length > 0 && (
            <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-4 text-xs text-slate-300">
              <p className="font-bold uppercase tracking-[0.18em] text-violet-200">Recent AI postmortems</p>
              <div className="mt-3 space-y-2">
                {performance.recentPostmortems.slice(-3).reverse().map((item) => (
                  <div key={item.id} className="rounded-xl border border-slate-800 p-3">
                    <p className={item.pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'}>{item.symbol} {item.side} | {formatCurrency(item.pnl)} | {item.quality}</p>
                    <p className="mt-1 text-slate-400">{item.findings.join(' | ')}</p>
                    <p className="mt-1 text-cyan-200">{item.nextActions.join(' | ')}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {paperSessions?.rotationEnabled && completedSessions.length > 0 && (
        <Card title="Completed Paper Sessions Today" eyebrow="Saved when 8 losses or profit target hits — fresh session starts immediately">
          <div className="space-y-3">
            {completedSessions.slice().reverse().map((session) => (
              <div key={session.id} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-bold text-white">Session #{session.sessionNumber}</span>
                  <span className={((session.netPnl ?? 0) >= 0 ? 'text-emerald-300' : 'text-rose-300')}>{formatCurrency(session.netPnl ?? 0)}</span>
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  {session.endReason?.replaceAll('_', ' ') ?? 'ENDED'} | {session.paperTrades ?? 0} trades | {session.wins ?? 0}W / {session.losses ?? 0}L | PF {session.profitFactor ?? '-'}
                </p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {auto.psychology && (
        <Card title="Psychology Manager" eyebrow="Discipline, patience, revenge-risk and execution mindset">
          <div className="grid gap-3 md:grid-cols-3">
            <MetricCard label="Mindset State" value={auto.psychology.state.replaceAll('_', ' ')} helper={auto.psychology.tradePermission.replaceAll('_', ' ')} tone={auto.psychology.tradePermission === 'WAIT' || auto.psychology.tradePermission === 'BLOCK_NEW_TRADES' ? 'rose' : 'emerald'} />
            <MetricCard label="Discipline Score" value={auto.psychology.disciplineScore} helper="Higher means calmer selection" tone={auto.psychology.disciplineScore >= 80 ? 'emerald' : auto.psychology.disciplineScore >= 60 ? 'amber' : 'rose'} />
            <MetricCard label="Emotional Risks" value={auto.psychology.emotionalRisks.length} helper={auto.psychology.emotionalRisks.join(', ') || 'None'} tone={auto.psychology.emotionalRisks.length ? 'amber' : 'emerald'} />
            {auto.psychology.aiCoach && (
              <>
                <MetricCard label="AI Coach Mode" value={auto.psychology.aiCoach.mode.replaceAll('_', ' ')} helper={`Urgency ${auto.psychology.aiCoach.urgency}`} tone={auto.psychology.aiCoach.urgency === 'HIGH' ? 'rose' : auto.psychology.aiCoach.urgency === 'MEDIUM' ? 'amber' : 'emerald'} />
                <MetricCard label="Coach Confidence" value={auto.psychology.aiCoach.confidenceScore} helper={`Cooldown ${auto.psychology.aiCoach.cooldownMinutes}m`} tone={auto.psychology.aiCoach.confidenceScore >= 70 ? 'emerald' : auto.psychology.aiCoach.confidenceScore >= 45 ? 'amber' : 'rose'} />
                <MetricCard label="Best Mindset Edge" value={`${auto.psychology.aiCoach.profileGuidance.bestSymbol ?? 'n/a'} ${auto.psychology.aiCoach.profileGuidance.bestSide ?? ''}`} helper={auto.psychology.aiCoach.profileGuidance.bestBucket ?? 'Waiting for data'} tone="cyan" />
              </>
            )}
            {auto.psychology.exitAdjustments && (
              <>
                <MetricCard label="Psych Stop" value={auto.psychology.exitAdjustments.adjustedStopPoints} helper={`Base ${auto.psychology.exitAdjustments.baseStopPoints}`} tone="amber" />
                <MetricCard label="Psych Hold" value={`${auto.psychology.exitAdjustments.adjustedMaxHoldSeconds}s`} helper={`Base ${auto.psychology.exitAdjustments.baseMaxHoldSeconds}s`} tone="violet" />
                <MetricCard label="Stop Reason" value={auto.psychology.exitAdjustments.reason ? 'ACTIVE' : 'NORMAL'} helper={auto.psychology.exitAdjustments.reason ?? 'No psychology tightening'} tone={auto.psychology.exitAdjustments.reason ? 'rose' : 'emerald'} />
              </>
            )}
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-cyan-200">Behavioral Findings</p>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-300">
                {auto.psychology.behavioralFindings.length ? auto.psychology.behavioralFindings.map((item) => <li key={item}>{item}</li>) : <li>Behavior is currently stable.</li>}
              </ul>
            </div>
            <div className="rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-emerald-200">Coach Actions</p>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-200">
                {auto.psychology.coachActions.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          </div>
          {auto.psychology.aiCoach && (
            <div className="mt-4 rounded-2xl border border-violet-300/20 bg-violet-300/10 p-4">
              <p className="text-xs font-bold uppercase tracking-[0.22em] text-violet-200">AI Psychological Coach</p>
              <p className="mt-2 text-sm font-semibold text-white">{auto.psychology.aiCoach.nextAction}</p>
              <div className="mt-4 grid gap-4 lg:grid-cols-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.18em] text-cyan-200">Diagnosis</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-300">
                    {auto.psychology.aiCoach.diagnosis.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.18em] text-amber-200">Intervention Script</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-300">
                    {auto.psychology.aiCoach.interventionScript.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div>
                  <p className="text-xs font-bold uppercase tracking-[0.18em] text-rose-200">Anti-Revenge Rules</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-300">
                    {auto.psychology.aiCoach.antiRevengeRules.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              </div>
              <div className="mt-4 grid gap-3 lg:grid-cols-2">
                <div className="rounded-xl border border-slate-700 bg-slate-950/60 p-3 text-xs text-slate-300">
                  <p className="font-bold text-cyan-200">Pre-trade checklist</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {auto.psychology.aiCoach.preTradeChecklist.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
                <div className="rounded-xl border border-slate-700 bg-slate-950/60 p-3 text-xs text-slate-300">
                  <p><span className="font-bold text-emerald-200">Breathing:</span> {auto.psychology.aiCoach.breathingProtocol}</p>
                  <p className="mt-2"><span className="font-bold text-violet-200">Journal:</span> {auto.psychology.aiCoach.journalPrompt}</p>
                  <p className="mt-2 text-emerald-200">{auto.psychology.aiCoach.positiveReinforcement}</p>
                </div>
              </div>
            </div>
          )}
          <p className="mt-4 rounded-2xl bg-slate-950/60 p-3 text-sm text-slate-300">{auto.psychology.mantra}</p>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Open Paper Trades" eyebrow="Currently shadow-open">
          <div className="space-y-3">
            {openPaperTrades.length === 0 && <p className="text-sm text-slate-400">No open paper trades.</p>}
            {openPaperTrades.map((trade) => (
              <div key={trade.id} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
                <div className="flex justify-between gap-3"><span className="font-bold text-white">{trade.symbol} {trade.strike} {trade.side}</span><span className="text-cyan-200">{trade.status}</span></div>
                <div className="mt-2 grid gap-2 text-xs text-slate-300 sm:grid-cols-3">
                  <span>Entry {trade.entryPrice}</span><span>Qty {trade.quantity}</span><span>TQS {trade.entryTqs}</span>
                  <span>Spread {trade.spreadCost}</span><span>Slip {trade.slippageEstimate}</span><span>PnL {formatCurrency(trade.pnl)}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Closed Paper Trades" eyebrow="Recent paper exits">
          <div className="space-y-3">
            {closedPaperTrades.length === 0 && <p className="text-sm text-slate-400">No closed paper trades yet.</p>}
            {closedPaperTrades.slice(-10).reverse().map((trade) => (
              <div key={trade.id} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-4">
                <div className="flex justify-between gap-3"><span className="font-bold text-white">{trade.symbol} {trade.strike} {trade.side}</span><span className={trade.pnl >= 0 ? 'text-emerald-300' : 'text-rose-300'}>{formatCurrency(trade.pnl)}</span></div>
                <p className="mt-1 text-xs text-slate-400">Exit: {trade.exitReason ?? 'n/a'} | Entry {trade.entryPrice} → Exit {trade.exitPrice ?? '-'}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="Paper Lifecycle Log" eyebrow="Signal, risk, paper-open, exit events">
        <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
          {orderLifecycle.slice(-40).reverse().map((event, index) => (
            <div key={`${event.timestamp}-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300">
              <div className="flex justify-between gap-3"><span className="font-bold text-cyan-200">{event.state}</span><span className="font-mono text-slate-500">{new Date(event.timestamp).toLocaleTimeString()}</span></div>
              <p className="mt-1">{event.reason}</p>
            </div>
          ))}
          {orderLifecycle.length === 0 && <p className="text-sm text-slate-400">No lifecycle events yet.</p>}
        </div>
      </Card>
    </div>
  );
}

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

interface HeatmapStock {
  symbol: string; instrumentKey: string; ltp: number; prevClose: number;
  changePct: number; volume: number; weight: number; tone: string;
}
interface HeatmapData {
  index: string; available: boolean; reason?: string; stockCount?: number;
  advancing?: number; declining?: number; breadthScore?: number; breadthBias?: string;
  stocks?: HeatmapStock[];
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function MarketHeatmapPanel(_props: { snapshot: TerminalSnapshot }) {
  const [index, setIndex] = useState<'NIFTY' | 'SENSEX' | 'BANKNIFTY'>('NIFTY');
  const [data, setData] = useState<HeatmapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(`${apiUrl}/api/market/heatmap?index=${index}`);
        const d: HeatmapData = await r.json();
        if (!cancelled) { setData(d); setLoading(false); }
      } catch (e) {
        if (!cancelled) { setError(String(e)); setLoading(false); }
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [index]);

  const stocks = data?.stocks ?? [];
  const maxWeight = Math.max(...stocks.map(s => s.weight), 1);

  return (
    <Card title="Market Heatmap" eyebrow="Constituent stock performance — weight-sized tiles, color by % change">
      <div className="flex gap-2 mb-4">
        {(['NIFTY', 'SENSEX', 'BANKNIFTY'] as const).map(idx => (
          <button key={idx} type="button" onClick={() => setIndex(idx)}
            className={`rounded-xl border px-4 py-2 text-xs font-bold uppercase tracking-widest transition ${index === idx ? 'border-cyan-300/50 bg-cyan-300/15 text-cyan-100' : 'border-slate-700 bg-slate-950/70 text-slate-400 hover:border-cyan-300/30'}`}>
            {idx}
          </button>
        ))}
      </div>

      {data && data.available && (
        <div className="mb-4 grid grid-cols-4 gap-2 text-center text-xs">
          <div className="rounded-xl bg-slate-900 p-2"><p className="text-slate-500">Stocks</p><p className="text-lg font-black text-white">{data.stockCount}</p></div>
          <div className="rounded-xl bg-emerald-900/40 p-2"><p className="text-slate-500">Advancing</p><p className="text-lg font-black text-emerald-300">{data.advancing}</p></div>
          <div className="rounded-xl bg-rose-900/40 p-2"><p className="text-slate-500">Declining</p><p className="text-lg font-black text-rose-300">{data.declining}</p></div>
          <div className={`rounded-xl p-2 ${data.breadthBias === 'BULLISH' ? 'bg-emerald-900/40' : data.breadthBias === 'BEARISH' ? 'bg-rose-900/40' : 'bg-slate-900'}`}>
            <p className="text-slate-500">Breadth</p>
            <p className={`text-lg font-black ${data.breadthBias === 'BULLISH' ? 'text-emerald-300' : data.breadthBias === 'BEARISH' ? 'text-rose-300' : 'text-slate-300'}`}>{data.breadthScore?.toFixed(1)}%</p>
          </div>
        </div>
      )}

      {loading && <div className="py-8 text-center text-slate-500 text-sm">Loading {index} constituent stocks...</div>}
      {!loading && error && <div className="py-4 text-center text-rose-400 text-sm">Error: {error}</div>}
      {!loading && data && !data.available && (
        <div className="py-4 rounded-2xl border border-amber-300/20 bg-amber-300/8 text-center text-sm text-amber-200">
          <p className="font-bold">Stock data unavailable</p>
          <p className="text-xs text-slate-400 mt-1">{data.reason}</p>
          <p className="text-xs text-slate-500 mt-1">Upstox subscription may not include equity market data. Sector index breadth is still active.</p>
        </div>
      )}

      {!loading && stocks.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {stocks.map(stock => {
            const size = Math.max(48, Math.round((stock.weight / maxWeight) * 96));
            const pct = stock.changePct;
            const bg = pct > 2 ? 'bg-emerald-500' : pct > 0.5 ? 'bg-emerald-700/80' : pct > 0 ? 'bg-emerald-900/60' : pct < -2 ? 'bg-rose-500' : pct < -0.5 ? 'bg-rose-700/80' : pct < 0 ? 'bg-rose-900/60' : 'bg-slate-700/80';
            const textC = Math.abs(pct) > 0.5 ? 'text-white' : 'text-slate-300';
            const pctColor = pct > 0 ? 'text-emerald-200' : pct < 0 ? 'text-rose-200' : 'text-slate-300';
            return (
              <div key={stock.symbol} title={`${stock.symbol}: ₹${stock.ltp} (${pct > 0 ? '+' : ''}${pct}%)`}
                className={`${bg} rounded-lg flex flex-col items-center justify-center cursor-default transition-all hover:opacity-90 border border-white/10`}
                style={{ width: size, height: size, minWidth: 44, minHeight: 44 }}>
                <p className={`font-black text-[9px] leading-none ${textC} px-1 text-center`}>{stock.symbol}</p>
                <p className={`font-bold text-[10px] mt-0.5 ${pctColor}`}>{pct > 0 ? '+' : ''}{pct.toFixed(1)}%</p>
              </div>
            );
          })}
        </div>
      )}

      <p className="mt-3 text-[10px] text-slate-600">Tile size = index weight. Green = advancing, Red = declining. Data via Upstox LTP API.</p>
    </Card>
  );
}

export function LiveReadinessGate({ snapshot }: { snapshot: TerminalSnapshot }) {
  const auto = snapshot.autoTrader;
  const perf = auto?.performanceAnalysis;
  const lr = perf?.liveReadiness;
  const rp = perf?.rollingProof;
  const current = rp?.paperTrades ?? 0;
  const pf = rp?.profitFactor ?? 0;
  const winRate = rp?.winRate ?? 0;
  const dd = rp?.maxDrawdownPct ?? 0;
  const avgWin = rp?.avgWin ?? 0;
  const avgLoss = rp?.avgLoss ?? 0;
  const isReady = lr?.ready ?? false;

  const checks = [
    { name: '100 clean trades', value: current, required: 100, passed: current >= 100, pct: Math.min(100, (current / 100) * 100) },
    { name: 'Profit factor ≥ 2.0', value: pf.toFixed(3), required: '≥ 2.0', passed: pf >= 2.0, pct: Math.min(100, (pf / 2.0) * 100) },
    { name: 'Win rate ≥ 50%', value: `${winRate.toFixed(1)}%`, required: '≥ 50%', passed: winRate >= 50, pct: Math.min(100, (winRate / 50) * 100) },
    { name: 'Max drawdown ≤ 5%', value: `${dd.toFixed(2)}%`, required: '≤ 5%', passed: dd <= 5 && dd > 0 || dd === 0, pct: dd === 0 ? 100 : Math.min(100, ((5 - Math.min(dd, 5)) / 5) * 100) },
    { name: 'Avg win > avg loss', value: avgLoss > 0 ? `${(avgWin / avgLoss).toFixed(2)}×` : 'n/a', required: '> 1.0×', passed: avgWin > avgLoss && avgLoss > 0, pct: avgLoss > 0 ? Math.min(100, (avgWin / avgLoss) * 50) : 0 },
  ];

  return (
    <Card
      title={isReady ? '✅ Live Trading Gate — PASSED' : '🔒 Live Trading Gate — Paper Proof Required'}
      eyebrow="All 5 checks must pass before ENABLE_LIVE_TRADING=true"
    >
      <div className={`mb-4 rounded-2xl border p-4 text-center ${isReady ? 'border-emerald-300/30 bg-emerald-300/10' : 'border-rose-300/20 bg-rose-300/8'}`}>
        <p className={`text-2xl font-black ${isReady ? 'text-emerald-300' : 'text-rose-300'}`}>{isReady ? 'LIVE READY' : 'PAPER ONLY'}</p>
        <p className="mt-1 text-xs text-slate-400">{lr?.message ?? 'Complete 100 clean paper trades with PF ≥ 2.0 to unlock live trading'}</p>
      </div>
      <div className="space-y-3">
        {checks.map((c) => (
          <div key={c.name} className="rounded-xl border border-slate-700 bg-slate-950/60 p-3">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-semibold text-slate-300">{c.name}</span>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-white">{c.value}</span>
                <span className={`text-xs font-bold ${c.passed ? 'text-emerald-300' : 'text-rose-300'}`}>{c.passed ? '✓' : '✗'}</span>
              </div>
            </div>
            <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
              <div className={`h-full rounded-full transition-all ${c.passed ? 'bg-emerald-400' : 'bg-rose-400/60'}`} style={{ width: `${c.pct}%` }} />
            </div>
            <p className="mt-1 text-[10px] text-slate-500">Target: {c.required}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-3 text-xs text-slate-400">
        Rolling proof: <span className="font-mono text-cyan-200">{current}/{rp?.windowTrades ?? 100}</span> trades.
        {' '}Expectancy: <span className={`font-mono ${(rp?.expectancy ?? 0) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatCurrency(rp?.expectancy ?? 0)}/trade</span>.
        {' '}When all gates pass → set <span className="font-mono text-amber-200">ENABLE_LIVE_TRADING=true</span>.
      </div>
    </Card>
  );
}

export function MorningChecklistPanel() {
  const [tokenStatus, setTokenStatus] = useState<{ hasToken: boolean; expiresAtIst: string; source: string } | null>(null);
  const [expiryStatus, setExpiryStatus] = useState<{ symbols: string[]; expiries: Record<string, string | null> } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch(`${apiUrl}/api/upstox/token/status`).then((r) => r.json()).catch(() => null),
      fetch(`${apiUrl}/api/deployment/expiry-status`).then((r) => r.json()).catch(() => null),
    ]).then(([ts, es]) => { setTokenStatus(ts); setExpiryStatus(es); setLoading(false); });
  }, []);

  const tokenOk = tokenStatus?.hasToken ?? false;
  const expiresStr = tokenStatus?.expiresAtIst ? tokenStatus.expiresAtIst.slice(0, 19).replace('T', ' ') + ' IST' : 'unknown';

  return (
    <Card title="Morning Checklist" eyebrow="Complete before 09:15 IST every trading day — bookmark /api/upstox/login for 1-tap token refresh">
      <div className="space-y-3">
        <div className={`rounded-2xl border p-4 ${tokenOk ? 'border-emerald-300/25 bg-emerald-300/8' : 'border-rose-300/25 bg-rose-300/8'}`}>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-bold text-white">1. Upstox Token</p>
              {loading ? <p className="text-xs text-slate-500">Checking…</p>
                : tokenOk ? <p className="text-xs text-emerald-300">✓ Valid until {expiresStr} — refreshes needed daily at 03:30 IST</p>
                : <p className="text-xs text-rose-300">⚠ Missing — tap to refresh before market opens</p>}
            </div>
            <a href={`${displayApiUrl}/api/upstox/login`} target="_blank" rel="noreferrer"
              className={`rounded-xl border px-4 py-2 text-xs font-bold transition ${tokenOk ? 'border-emerald-300/30 bg-emerald-300/10 text-emerald-200 hover:bg-emerald-300/20' : 'border-rose-300/30 bg-rose-300/15 text-rose-200 hover:bg-rose-300/25'}`}>
              {tokenOk ? 'Refresh ↗' : 'Login ↗'}
            </a>
          </div>
        </div>
        <div className="rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
          <p className="text-sm font-bold text-white mb-2">2. Option Expiry Dates</p>
          {loading ? <p className="text-xs text-slate-500">Checking…</p>
            : expiryStatus ? (
              <div className="grid gap-2 sm:grid-cols-3">
                {expiryStatus.symbols.map((sym) => (
                  <div key={sym} className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2">
                    <p className="text-[10px] uppercase tracking-widest text-slate-500">{sym}</p>
                    <p className="font-mono text-sm text-cyan-200">{expiryStatus.expiries[sym] ?? 'Auto-resolve ✓'}</p>
                  </div>
                ))}
              </div>
            ) : <p className="text-xs text-slate-500">Unavailable</p>}
          <p className="mt-2 text-xs text-slate-500">NIFTY/BANKNIFTY: weekly Thursday · SENSEX: weekly Thursday. Update env file on rollover day.</p>
        </div>
        <div className="rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
          <p className="text-sm font-bold text-white mb-1">3. Reset Paper Trades</p>
          <p className="text-xs text-slate-400 mb-3">Fresh start each day for clean daily performance tracking.</p>
          <a href={`${apiUrl}/api/auto-trader/reset`} target="_blank" rel="noreferrer"
            className="inline-block rounded-lg border border-rose-300/30 bg-rose-300/10 px-3 py-1.5 text-xs font-bold text-rose-200 hover:bg-rose-300/20">
            Reset Paper Trades →
          </a>
        </div>
      </div>
    </Card>
  );
}
export function RunnerOpportunityPanel({ snapshot }: { snapshot: TerminalSnapshot }) {
  const rawSnapshots = (snapshot as unknown as Record<string, Record<string, unknown>>)['snapshots'] ?? {};

  type RunnerSig = { score?: number; eliteRunner?: boolean; confidence?: string; momentumAligned?: boolean; momentumSurge?: boolean; metrics?: Record<string, number>; };
  type WLItem = { sym?: string; side?: string; strike?: number; lastPremium?: number; nearExpiry?: boolean; daysToExpiry?: number; expiry?: string; runnerSignal?: RunnerSig; };
  type SymSnap = { explosiveRunner?: RunnerSig & { side?: string; }; explosiveRunnerWatchlist?: WLItem[]; tradeQualityScore?: number; regime?: string; marketPhase?: string; expiryState?: { selectedExpiry?: string; }; };

  const symbols = Object.entries(rawSnapshots as Record<string, SymSnap>);
  const allRunners: (WLItem & { sym: string; score: number })[] = [];
  for (const [sym, snap] of symbols) {
    for (const w of (snap.explosiveRunnerWatchlist ?? []).slice(0, 4)) {
      const r = w.runnerSignal ?? {};
      allRunners.push({ ...w, sym, score: r.score ?? 0 });
    }
  }
  const strong = allRunners.filter(r => r.score >= 75).sort((a, b) => b.score - a.score);
  const elite = strong.filter(r => r.runnerSignal?.eliteRunner);
  const at = snapshot.autoTrader;
  const dr = (at?.dailyReport ?? {}) as Record<string, number>;

  return (
    <Card title="Explosive Runner Opportunities" eyebrow="Real-time across NIFTY · SENSEX · BANKNIFTY — near-expiry high-gamma scanner active">
      <div className="grid gap-3 sm:grid-cols-3 mb-4">
        <div className={`rounded-xl border p-3 text-center ${elite.length > 0 ? 'border-emerald-300/30 bg-emerald-300/10' : 'border-slate-700 bg-slate-900'}`}>
          <p className="text-xs text-slate-500 uppercase tracking-widest">Elite</p>
          <p className={`text-3xl font-black mt-1 ${elite.length > 0 ? 'text-emerald-300' : 'text-slate-500'}`}>{elite.length}</p>
          <p className="text-xs text-slate-500 mt-1">score ≥ 88 + eliteRunner</p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-3 text-center">
          <p className="text-xs text-slate-500 uppercase tracking-widest">Strong</p>
          <p className={`text-3xl font-black mt-1 ${strong.length > 0 ? 'text-cyan-300' : 'text-slate-500'}`}>{strong.length}</p>
          <p className="text-xs text-slate-500 mt-1">score ≥ 75</p>
        </div>
        <div className="rounded-xl border border-slate-700 bg-slate-900 p-3 text-center">
          <p className="text-xs text-slate-500 uppercase tracking-widest">Day PnL</p>
          <p className={`text-xl font-black mt-1 ${(dr.netPnl ?? 0) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatCurrency(dr.netPnl ?? 0)}</p>
          <p className="text-xs text-slate-500 mt-1">{dr.paperTrades ?? 0} trades · {dr.wins ?? 0}W/{dr.losses ?? 0}L</p>
        </div>
      </div>

      {symbols.map(([sym, snap]) => {
        const er = snap.explosiveRunner;
        const score = er?.score ?? 0;
        const pct = Math.min(100, score);
        return (
          <div key={sym} className="mb-3 rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className={`rounded-lg px-2 py-1 text-xs font-black ${snap.marketPhase === 'LIVE_MARKET' ? 'bg-emerald-400/20 text-emerald-300' : 'bg-slate-800 text-slate-500'}`}>{sym}</span>
                {er?.eliteRunner && <span className="text-xs font-bold text-amber-300 animate-pulse">⚡ ELITE</span>}
                <span className={`text-xs ${er?.confidence === 'HIGH' ? 'text-emerald-300' : er?.confidence === 'MEDIUM' ? 'text-amber-300' : 'text-slate-500'}`}>{er?.confidence ?? 'LOW'}</span>
              </div>
              <div className="text-right text-xs text-slate-500">{snap.regime?.replace(/_/g, ' ')} | {snap.expiryState?.selectedExpiry} | TQS {snap.tradeQualityScore}</div>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex-1">
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-400">Runner Score (need 88+ for elite)</span>
                  <span className={`font-bold ${score >= 88 ? 'text-emerald-300' : score >= 75 ? 'text-cyan-300' : score >= 60 ? 'text-amber-300' : 'text-slate-500'}`}>{score.toFixed(0)}</span>
                </div>
                <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
                  <div className={`h-full rounded-full ${score >= 88 ? 'bg-emerald-400' : score >= 75 ? 'bg-cyan-400' : score >= 60 ? 'bg-amber-400' : 'bg-slate-600'}`} style={{ width: `${pct}%` }} />
                </div>
                <p className="text-[10px] text-slate-600 mt-0.5">{er?.momentumSurge ? '⚡ momentum surge active' : er?.momentumAligned ? '→ direction aligned, waiting for surge' : '○ no momentum — typical in range absorption'}</p>
              </div>
            </div>
          </div>
        );
      })}

      {strong.length > 0 && (
        <div className="mt-2 space-y-1.5">
          <p className="text-xs font-bold text-cyan-200 uppercase tracking-widest mb-2">Best Watchlist Signals</p>
          {strong.slice(0, 5).map((r, i) => (
            <div key={i} className={`flex items-center justify-between rounded-lg border px-3 py-2 text-xs ${r.runnerSignal?.eliteRunner ? 'border-emerald-300/30 bg-emerald-300/8' : 'border-slate-700 bg-slate-900'}`}>
              <span className="font-mono text-white">{r.sym} {r.side} {r.strike}</span>
              <span className="text-slate-400">₹{r.lastPremium}</span>
              <span className={`font-bold ${r.runnerSignal?.eliteRunner ? 'text-emerald-300' : 'text-cyan-300'}`}>{r.score.toFixed(0)}</span>
              {r.nearExpiry && <span className="text-amber-300">⚡ {r.daysToExpiry}d-expiry</span>}
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 rounded-2xl border border-slate-700 bg-slate-950/60 p-3 text-xs text-slate-400 space-y-1">
        <p className="font-bold text-white">Why runners haven't fired today:</p>
        <p>• Regime = RANGE_ABSORPTION — runners need TREND_EXPANSION or sudden breakout</p>
        <p>• Near-expiry scanner (≤5 days) now active → catches high-gamma bursts from cheap options</p>
        <p className="text-emerald-300/80 font-medium mt-2">✅ Background monitor runs 24/7 — website does NOT need to be open. Trades open/close automatically during market hours.</p>
      </div>
    </Card>
  );
}

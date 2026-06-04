import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Card } from './Card';
import { ScoreBar } from './ScoreBar';
import type { TerminalSnapshot } from '../types';

interface OrderflowAnalyticsProps {
  snapshot: TerminalSnapshot;
}

export function OrderflowAnalytics({ snapshot }: OrderflowAnalyticsProps) {
  const metrics = [
    ['Cumulative Delta', Math.min(100, Math.abs(snapshot.orderflow.cumulativeDelta) / 900)],
    ['Delta Velocity', Math.abs(snapshot.orderflow.deltaVelocity)],
    ['Aggressive Buyers', snapshot.orderflow.aggressiveBuyers],
    ['Aggressive Sellers', snapshot.orderflow.aggressiveSellers],
    ['DOM Imbalance', Math.abs(snapshot.orderflow.domImbalance)],
    ['Liquidity Shift', snapshot.orderflow.liquidityShift],
    ['Sweep Detection', snapshot.orderflow.sweepDetection],
    ['Volume Acceleration', snapshot.orderflow.volumeAcceleration],
    ['Breakout Velocity', snapshot.orderflow.breakoutVelocity],
  ] as const;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Orderflow Engine" eyebrow="Delta, DOM imbalance, sweeps">
        <div className="grid gap-4 md:grid-cols-2">
          {metrics.map(([label, value]) => <ScoreBar key={label} label={label} value={Number(value)} />)}
        </div>
        {snapshot.qualityFilters?.volumeState && (
          <div className="mt-4 rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-4 text-sm text-cyan-100">
            <p className="font-bold uppercase tracking-[0.18em]">Volume source: {snapshot.qualityFilters.volumeState.source.replaceAll('_', ' ')}</p>
            <p className="mt-2 text-xs text-slate-300">
              Candle {snapshot.qualityFilters.volumeState.candleVolume} | Option chain {snapshot.qualityFilters.volumeState.optionChainVolume} | LTP {snapshot.qualityFilters.volumeState.ltpVolume} | Effective {snapshot.qualityFilters.volumeState.effectiveVolume}
            </p>
          </div>
        )}
        {snapshot.qualityFilters?.chopFilter?.blocked && (
          <div className="mt-4 rounded-2xl border border-rose-300/20 bg-rose-300/10 p-4 text-sm text-rose-100">
            <p className="font-bold uppercase tracking-[0.18em]">Chop filter blocked trade</p>
            <ul className="mt-2 list-disc pl-5 text-xs">
              {snapshot.qualityFilters.chopFilter.reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </div>
        )}
      </Card>
      <Card title="Volume Acceleration" eyebrow="One-second telemetry">
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={snapshot.telemetry}>
              <defs>
                <linearGradient id="volumeGradient" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.55} />
                  <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.04} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.08)" />
              <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: '#020617', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12 }} />
              <Area type="monotone" dataKey="volume" stroke="#22d3ee" fill="url(#volumeGradient)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card title="Breakout Velocity" eyebrow="TQS vs latency" className="xl:col-span-2">
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={snapshot.telemetry.slice(-18)}>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.08)" />
              <XAxis dataKey="time" stroke="#64748b" fontSize={10} />
              <YAxis stroke="#64748b" fontSize={10} />
              <Tooltip contentStyle={{ background: '#020617', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12 }} />
              <Bar dataKey="tqs" fill="#67e8f9" radius={[8, 8, 0, 0]} />
              <Bar dataKey="latency" fill="#a78bfa" radius={[8, 8, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  );
}

import { motion } from 'framer-motion';
import { Cpu, RadioTower, ShieldCheck } from 'lucide-react';
import type { StreamStatus, TerminalSnapshot } from '../types';
import { formatCurrency } from '../utils/format';
import { TradingControlButtons } from './TradingControlButtons';

interface TerminalHeaderProps {
  snapshot: TerminalSnapshot;
  status: StreamStatus;
}

export function TerminalHeader({ snapshot, status }: TerminalHeaderProps) {
  const statusTone = status === 'live' ? 'bg-emerald-400' : status === 'status' ? 'bg-amber-300' : status === 'error' ? 'bg-rose-400' : 'bg-cyan-300';
  const stopped = snapshot.autoTradingStopped || snapshot.tradingControl?.autoTradingStopped;

  return (
    <header className="glass-panel rounded-3xl p-5">
      <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.32em] text-cyan-200">{snapshot.symbol}</span>
            <span className="text-xs uppercase tracking-[0.24em] text-slate-500">{new Date(snapshot.timestamp).toLocaleTimeString()}</span>
            <span className="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-slate-300">
              <span className={`h-2 w-2 rounded-full ${statusTone}`} /> {status === 'live' ? 'Real Upstox stream live' : status === 'status' ? 'Waiting for Upstox data' : status === 'error' ? 'Stream error' : 'Connecting'}
            </span>
          </div>
          <h2 className="mt-3 text-3xl font-black tracking-tight text-white md:text-5xl">{snapshot.spot.toFixed(2)}</h2>
          <p className="mt-2 text-sm text-slate-400">ATM {snapshot.atmStrike} | {snapshot.premiumFocusZone} | {(snapshot.marketPhase ?? snapshot.regime).replaceAll('_', ' ')}</p>
          {snapshot.sessionReason && <p className="mt-1 text-xs text-amber-200/80">{snapshot.sessionReason}</p>}
          {snapshot.tradingControl?.reason && <p className="mt-1 text-xs text-rose-200/80">Trading control: {snapshot.tradingControl.reason}</p>}
        </div>
        <div className="flex flex-col gap-3">
          <TradingControlButtons stopped={Boolean(stopped)} />
          <div className="grid gap-3 sm:grid-cols-3">
          <motion.div layout className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-cyan-200"><Cpu className="h-4 w-4" /> TQS</div>
            <div className="mt-2 text-3xl font-black text-white">{snapshot.tradeQualityScore}</div>
          </motion.div>
          <motion.div layout className="rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-emerald-200"><RadioTower className="h-4 w-4" /> PnL</div>
            <div className="mt-2 text-3xl font-black text-white">{formatCurrency(snapshot.pnl)}</div>
          </motion.div>
          <motion.div layout className="rounded-2xl border border-violet-300/20 bg-violet-300/10 p-4">
            <div className="flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-violet-200"><ShieldCheck className="h-4 w-4" /> Risk</div>
            <div className="mt-2 text-xl font-black text-white">{stopped ? 'STOPPED' : snapshot.executionAllowed ? 'LIVE ARMED' : snapshot.liveTradingEnabled ? 'RISK GATED' : 'READ ONLY'}</div>
          </motion.div>
          </div>
        </div>
      </div>
    </header>
  );
}

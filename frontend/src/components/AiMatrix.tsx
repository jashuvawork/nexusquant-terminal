import { Card } from './Card';
import { ScoreBar } from './ScoreBar';
import type { TerminalSnapshot } from '../types';

interface AiMatrixProps {
  snapshot: TerminalSnapshot;
}

export function AiMatrix({ snapshot }: AiMatrixProps) {
  return (
    <Card title="AI Matrix Engine" eyebrow="Weighted institutional trade quality scoring">
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-3xl border border-cyan-300/20 bg-cyan-300/10 p-6">
          <p className="text-xs uppercase tracking-[0.28em] text-cyan-200">Trade Quality Score</p>
          <div className="mt-3 text-7xl font-black text-white">{snapshot.tradeQualityScore}</div>
          <p className="mt-4 text-sm leading-6 text-slate-300">
            Execution is permitted only when momentum expansion, aggressive delta, heatmap support,
            option-chain structure, spread quality, gamma alignment, and market profile confirmation are all above threshold.
          </p>
          <div className="mt-5 rounded-2xl bg-slate-950/70 p-4 text-sm text-slate-300">
            Current router: <span className="font-bold text-cyan-200">{snapshot.strategy.router.replaceAll('_', ' ')}</span> | Threshold: {snapshot.strategy.threshold}
          </div>
          {snapshot.tqsBreakdown && (
            <div className="mt-4 rounded-2xl border border-violet-300/20 bg-violet-300/10 p-4 text-sm text-slate-200">
              <p className="font-bold uppercase tracking-[0.2em] text-violet-200">TQS Breakdown</p>
              <p className="mt-2 text-xs text-slate-400">{snapshot.tqsBreakdown.explanation}</p>
              <div className="mt-3 space-y-2">
                {snapshot.tqsBreakdown.weakComponents.slice(0, 3).map((item) => (
                  <div key={item.engine} className="flex justify-between text-xs"><span>{item.engine}</span><span className="text-rose-300">{item.score}</span></div>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="grid gap-3">
          {snapshot.aiMatrix.map((engine) => (
            <div key={engine.engine} className="rounded-2xl border border-slate-700/70 bg-slate-950/50 p-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-slate-100">{engine.engine}</p>
                  <p className="text-xs text-slate-500">Weight {(engine.weight * 100).toFixed(0)}%</p>
                </div>
                <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.2em] ${engine.status === 'pass' ? 'bg-emerald-300/10 text-emerald-200' : engine.status === 'watch' ? 'bg-amber-300/10 text-amber-200' : 'bg-rose-300/10 text-rose-200'}`}>{engine.status}</span>
              </div>
              <ScoreBar value={engine.score} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

import { type ReactNode, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { AlertTriangle, KeyRound, RadioTower } from 'lucide-react';
import { AiMatrix } from './components/AiMatrix';
import { Card } from './components/Card';
import { ExecutionHud } from './components/ExecutionHud';
import { GreeksIv } from './components/GreeksIv';
import { HeatmapTerminal } from './components/HeatmapTerminal';
import { OrderflowAnalytics } from './components/OrderflowAnalytics';
import {
  AiAnalytics,
  BacktestingPanel,
  InfrastructureTelemetry,
  PortfolioPanel,
  RiskEnginePanel,
  SessionIntelligence,
  SettingsPanel,
  StrategyRouter,
  TradeJournal,
} from './components/OperationalModules';
import { Sidebar } from './components/Sidebar';
import { navItems, type ModuleId } from './components/navItems';
import { TerminalChart } from './components/TerminalChart';
import { TerminalHeader } from './components/TerminalHeader';
import { TradingControlButtons } from './components/TradingControlButtons';
import { useMarketStream } from './hooks/useMarketStream';

function WaitingForRealData({ status, issue }: { status: string; issue: { status: string; message: string } | null }) {
  const apiUrl = import.meta.env.VITE_API_URL ?? 'https://your-render-api.onrender.com';
  return (
    <main className="terminal-grid min-h-screen p-4 text-slate-100">
      <div className="mx-auto flex min-h-[calc(100vh-2rem)] max-w-5xl items-center justify-center">
        <Card title="Waiting for real Upstox market data" eyebrow="No dummy or random values are displayed" className="w-full">
          <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
            <div>
              <div className="flex items-center gap-3">
                <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan-300/10 text-cyan-200">
                  {status === 'connecting' ? <RadioTower className="h-6 w-6" /> : <AlertTriangle className="h-6 w-6" />}
                </span>
                <div>
                  <p className="text-xs uppercase tracking-[0.28em] text-slate-500">Stream status</p>
                  <h1 className="text-2xl font-black text-white">{issue?.status ?? status.toUpperCase()}</h1>
                </div>
              </div>
              <p className="mt-5 text-sm leading-6 text-slate-300">
                {issue?.message ?? 'Connecting to the Render backend WebSocket. The terminal will stay blank until a real Upstox snapshot is received.'}
              </p>
              <div className="mt-6 rounded-2xl border border-amber-300/20 bg-amber-300/10 p-4 text-sm text-amber-100">
                This build intentionally removed local simulated prices. Configure/redeploy Railway backend, Upstox token, and Vercel URLs to see live or closed-market analysis.
              </div>
              <div className="mt-4 rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
                <p className="mb-3 text-xs font-bold uppercase tracking-[0.24em] text-slate-400">Emergency trading control</p>
                <TradingControlButtons compact />
              </div>
            </div>
            <div className="rounded-2xl border border-slate-700 bg-slate-950/70 p-4">
              <div className="flex items-center gap-2 text-cyan-200"><KeyRound className="h-4 w-4" /> Required checks</div>
              <ol className="mt-4 list-decimal space-y-3 pl-5 text-sm text-slate-300">
                <li>Open <span className="font-mono text-cyan-200">{apiUrl}/health</span>.</li>
                <li>Open <span className="font-mono text-cyan-200">{apiUrl}/api/upstox/token/status</span>.</li>
                <li>If token is missing, open <span className="font-mono text-cyan-200">{apiUrl}/api/upstox/login-url</span>.</li>
                <li>Open <span className="font-mono text-cyan-200">{apiUrl}/api/deployment/status</span> and confirm the latest Upstox-only API is deployed.</li>
              </ol>
            </div>
          </div>
        </Card>
      </div>
    </main>
  );
}

function App() {
  const { snapshot, status, issue } = useMarketStream();
  const [activeModule, setActiveModule] = useState<ModuleId>('execution');

  const moduleTitle = useMemo(() => navItems.find((item) => item.id === activeModule)?.label ?? 'Execution HUD', [activeModule]);

  if (!snapshot) {
    return <WaitingForRealData status={status} issue={issue} />;
  }

  const content = {
    execution: <ExecutionHud snapshot={snapshot} />,
    heatmap: <HeatmapTerminal snapshot={snapshot} />,
    orderflow: <OrderflowAnalytics snapshot={snapshot} />,
    ai: <AiMatrix snapshot={snapshot} />,
    greeks: <GreeksIv snapshot={snapshot} />,
    strategy: <StrategyRouter snapshot={snapshot} />,
    portfolio: <PortfolioPanel snapshot={snapshot} />,
    risk: <RiskEnginePanel snapshot={snapshot} />,
    infra: <InfrastructureTelemetry snapshot={snapshot} />,
    analytics: <AiAnalytics snapshot={snapshot} />,
    journal: <TradeJournal snapshot={snapshot} />,
    session: <SessionIntelligence snapshot={snapshot} />,
    backtesting: <BacktestingPanel snapshot={snapshot} />,
    settings: <SettingsPanel />,
  } satisfies Record<ModuleId, ReactNode>;

  return (
    <main className="terminal-grid min-h-screen p-3 text-slate-100 md:p-4">
      <div className="mx-auto flex max-w-[1800px] gap-4">
        <Sidebar active={activeModule} onChange={setActiveModule} />
        <div className="min-w-0 flex-1 space-y-4">
          <TerminalHeader snapshot={snapshot} status={status} />
          {snapshot.dataWarnings && snapshot.dataWarnings.length > 0 && (
            <div className="rounded-3xl border border-amber-300/25 bg-amber-300/10 p-4 text-sm text-amber-100">
              <p className="font-bold uppercase tracking-[0.18em]">Upstox data warnings</p>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {snapshot.dataWarnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </div>
          )}
          <div className="glass-panel rounded-3xl p-2 lg:hidden">
            <select
              className="w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-slate-100 outline-none"
              value={activeModule}
              onChange={(event) => setActiveModule(event.target.value as ModuleId)}
            >
              {navItems.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </div>
          <div className="grid gap-4 2xl:grid-cols-[1fr_470px]">
            <motion.section
              key={activeModule}
              initial={{ opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22 }}
              className="space-y-4"
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.34em] text-cyan-300/80">Active Module</p>
                  <h2 className="mt-1 text-2xl font-black uppercase tracking-[0.14em] text-white">{moduleTitle}</h2>
                </div>
                <span className="hidden rounded-full border border-slate-700 bg-slate-950/70 px-4 py-2 text-xs uppercase tracking-[0.22em] text-slate-400 sm:block">
                  {(snapshot.marketPhase ?? snapshot.volatilityRegime).replaceAll('_', ' ')}
                </span>
              </div>
              {content[activeModule]}
            </motion.section>
            <aside className="space-y-4">
              <TerminalChart snapshot={snapshot} />
              <AiMatrix snapshot={snapshot} />
            </aside>
          </div>
        </div>
      </div>
    </main>
  );
}

export default App;

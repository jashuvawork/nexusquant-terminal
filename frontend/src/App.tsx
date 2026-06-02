import { type ReactNode, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { AiMatrix } from './components/AiMatrix';
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
import { useMarketStream } from './hooks/useMarketStream';

function App() {
  const { snapshot, status } = useMarketStream();
  const [activeModule, setActiveModule] = useState<ModuleId>('execution');

  const moduleTitle = useMemo(() => navItems.find((item) => item.id === activeModule)?.label ?? 'Execution HUD', [activeModule]);

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
                  {snapshot.volatilityRegime.replaceAll('_', ' ')}
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

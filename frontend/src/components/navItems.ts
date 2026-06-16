import type { LucideIcon } from 'lucide-react';
import { Activity, BarChart3, Bot, BrainCircuit, CheckSquare, DatabaseZap, Gauge, Grid, History, LineChart, Lock, Network, Radar, ReceiptText, Route, Settings, ShieldCheck, Zap, WalletCards } from 'lucide-react';

export type ModuleId =
  | 'execution'
  | 'runner'
  | 'marketHeatmap'
  | 'heatmap'
  | 'orderflow'
  | 'ai'
  | 'greeks'
  | 'strategy'
  | 'portfolio'
  | 'risk'
  | 'infra'
  | 'analytics'
  | 'journal'
  | 'session'
  | 'backtesting'
  | 'paperTrading'
  | 'liveGate'
  | 'morning'
  | 'settings';

interface NavItem {
  id: ModuleId;
  label: string;
  icon: LucideIcon;
}

export const navItems: NavItem[] = [
  { id: 'execution', label: 'Execution HUD', icon: Gauge },
  { id: 'runner', label: 'Explosive Runner', icon: Zap },
  { id: 'marketHeatmap', label: 'Market Heatmap', icon: Grid },
  { id: 'heatmap', label: 'Option Heatmap', icon: Radar },
  { id: 'orderflow', label: 'Orderflow Analytics', icon: Activity },
  { id: 'ai', label: 'AI Matrix', icon: BrainCircuit },
  { id: 'greeks', label: 'Greeks & IV', icon: LineChart },
  { id: 'strategy', label: 'Strategy Router', icon: Route },
  { id: 'portfolio', label: 'Upstox Portfolio', icon: WalletCards },
  { id: 'risk', label: 'Risk Engine', icon: ShieldCheck },
  { id: 'infra', label: 'Infrastructure Telemetry', icon: Network },
  { id: 'analytics', label: 'AI Analytics', icon: Bot },
  { id: 'journal', label: 'Trade Journal', icon: DatabaseZap },
  { id: 'session', label: 'Session Intelligence', icon: BarChart3 },
  { id: 'backtesting', label: 'Backtesting', icon: History },
  { id: 'paperTrading', label: 'Paper Trading', icon: ReceiptText },
  { id: 'liveGate', label: 'Live Trading Gate', icon: Lock },
  { id: 'morning', label: 'Morning Checklist', icon: CheckSquare },
  { id: 'settings', label: 'Settings', icon: Settings },
];

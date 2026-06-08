import { useEffect, useRef } from 'react';
import { ColorType, LineSeries, createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts';
import { Card } from './Card';
import type { TerminalSnapshot } from '../types';

interface TerminalChartProps {
  snapshot: TerminalSnapshot;
}

export function TerminalChart({ snapshot }: TerminalChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.08)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.08)' },
      },
      rightPriceScale: { borderColor: 'rgba(148, 163, 184, 0.16)' },
      timeScale: { borderColor: 'rgba(148, 163, 184, 0.16)', timeVisible: true },
    });

    const series = chart.addSeries(LineSeries, {
      color: '#22d3ee',
      lineWidth: 2,
      crosshairMarkerVisible: true,
      lastValueVisible: true,
      priceLineVisible: true,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const resize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    resize();
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current) return;
    const baseTs = Math.floor(Date.now() / 1000) - snapshot.telemetry.length * 60;
    seriesRef.current.setData(
      snapshot.telemetry.map((point, index) => ({
        time: (baseTs + index * 60) as Time,
        value: point.price,
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [snapshot]);

  return (
    <Card title="Market Microstructure Chart" eyebrow="Realtime price telemetry" className="min-h-[396px]">
      {snapshot.chartAnalysis && (
        <div className="mb-4 grid gap-3 text-xs text-slate-300 sm:grid-cols-3">
          <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-3">
            <p className="font-bold uppercase tracking-[0.22em] text-cyan-200">Trend</p>
            <p className="mt-1 text-sm text-white">{snapshot.chartAnalysis.trend.replaceAll('_', ' ')}</p>
            <p>Bias {snapshot.chartAnalysis.bias} | Strength {snapshot.chartAnalysis.strength}</p>
          </div>
          <div className="rounded-2xl border border-emerald-300/20 bg-emerald-300/10 p-3">
            <p className="font-bold uppercase tracking-[0.22em] text-emerald-200">Chart Stack</p>
            <p>EMA {snapshot.chartAnalysis.emaFast ?? '-'} / {snapshot.chartAnalysis.emaSlow ?? '-'}</p>
            <p>VWAP {snapshot.chartAnalysis.vwap ?? '-'} | RSI {snapshot.chartAnalysis.rsi ?? '-'}</p>
          </div>
          <div className="rounded-2xl border border-violet-300/20 bg-violet-300/10 p-3">
            <p className="font-bold uppercase tracking-[0.22em] text-violet-200">Levels</p>
            <p>S {snapshot.chartAnalysis.levels?.support ?? '-'} | R {snapshot.chartAnalysis.levels?.resistance ?? '-'}</p>
            <p>{snapshot.chartAnalysis.pattern?.replaceAll('_', ' ') ?? 'Pattern pending'}</p>
          </div>
          <p className="sm:col-span-3 rounded-2xl bg-slate-950/60 p-3">{snapshot.chartAnalysis.recommendation}</p>
        </div>
      )}
      <div ref={containerRef} className="h-80 w-full" />
    </Card>
  );
}

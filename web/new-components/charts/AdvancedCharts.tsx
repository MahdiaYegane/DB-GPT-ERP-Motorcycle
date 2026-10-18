/**
 * AdvancedCharts.tsx  (shell)
 * A unified chart component that supports multiple chart types with a premium,
 * clean design. Rendering lives in `AntvChartsBody.tsx` and is loaded via
 * next/dynamic so the heavy @ant-design/plots (AntV) bundle is a separate
 * chunk, fetched only when a chart is actually mounted — this keeps AntV out
 * of the initial chat-page bundle.
 *
 * The type exports and the pure helpers (`detectChartType`,
 * `createChartConfig`) stay statically importable — no AntV code is evaluated
 * by this module at import time.
 */

import dynamic from 'next/dynamic';
import React from 'react';

// Chart type definitions
export type ChartType = 'line' | 'column' | 'bar' | 'pie' | 'area' | 'scatter' | 'donut' | 'dual-axes';

export interface ChartConfig {
  chartType: ChartType;
  data: any[];
  // Common fields
  xField?: string;
  yField?: string;
  seriesField?: string;
  colorField?: string;
  // Pie chart specific
  angleField?: string;
  // Dual axes specific
  yFields?: [string, string];
  geometries?: any[];
  // Appearance
  title?: string;
  description?: string;
  smooth?: boolean;
  autoFit?: boolean;
  height?: number;
  colors?: string[];
  showLegend?: boolean;
  showGrid?: boolean;
  animate?: boolean;
  // Interaction options
  enableZoom?: boolean;
  enableBrush?: boolean;
  enableTooltipCrosshairs?: boolean;
  onDataPointClick?: (data: any, event: any) => void;
  onBrushSelection?: (selectedData: any[]) => void;
  // Toolbar options
  showToolbar?: boolean;
  enableFullscreen?: boolean;
}

// Main AdvancedChart Component (dynamically loaded)
const AdvancedChartLoading = () => (
  <div
    className='advanced-chart-container relative flex items-center justify-center'
    style={{ height: 300 }}
  >
    <div className='text-xs text-gray-400'>Loading chart…</div>
  </div>
);

const AdvancedChart: React.FC<AdvancedChartProps> = dynamic(
  () => import('./AntvChartsBody'),
  { ssr: false, loading: AdvancedChartLoading },
);

// Individual chart components (dynamically loaded, same contract as before)
const pickChart = (name: string) =>
  dynamic(
    () => import('./AntvChartsBody').then(m => ({ default: (m as any)[name] })),
    { ssr: false, loading: AdvancedChartLoading },
  );

export interface AdvancedChartProps {
  config: ChartConfig;
  className?: string;
  style?: React.CSSProperties;
}

export const AreaChart = pickChart('AreaChart');
export const BarChart = pickChart('BarChart');
export const ColumnChart = pickChart('ColumnChart');
export const DualAxesChart = pickChart('DualAxesChart');
export const LineChart = pickChart('LineChart');
export const PieChart = pickChart('PieChart');
export const ScatterChart = pickChart('ScatterChart');

// Helper function to auto-detect best chart type based on data
export const detectChartType = (data: any[], xField: string, yField: string): ChartType => {
  if (!data || data.length === 0) return 'line';

  const uniqueXValues = new Set(data.map(d => d[xField])).size;
  const hasNegativeValues = data.some(d => d[yField] < 0);
  const isTimeSeries = data.some(d => {
    const val = d[xField];
    return val instanceof Date || !isNaN(Date.parse(val));
  });

  // If time series data, use line or area
  if (isTimeSeries) {
    return 'area';
  }

  // If few categories (< 6), pie chart might be good
  if (uniqueXValues <= 5 && !hasNegativeValues && data.length <= 10) {
    return 'pie';
  }

  // If many categories with comparison, use column
  if (uniqueXValues > 5 && uniqueXValues <= 15) {
    return 'column';
  }

  // Default to line for continuous data
  return 'line';
};

// Helper to convert simple data to chart config
export const createChartConfig = (data: any[], options: Partial<ChartConfig> = {}): ChartConfig => {
  const xField = options.xField || 'x';
  const yField = options.yField || 'y';
  const chartType = options.chartType || detectChartType(data, xField, yField);

  return {
    chartType,
    data,
    xField,
    yField,
    smooth: true,
    autoFit: true,
    showLegend: true,
    showGrid: true,
    animate: true,
    enableZoom: true,
    enableTooltipCrosshairs: true,
    showToolbar: true,
    enableFullscreen: true,
    ...options,
  };
};

export default AdvancedChart;

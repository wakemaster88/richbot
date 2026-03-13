/** Chart utilities: EMA, Bollinger Bands, etc. */

export interface OHLC {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export function ema(data: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else if (i === period - 1) {
      const sum = data.slice(0, period).reduce((a, b) => a + b, 0);
      result.push(sum / period);
    } else {
      result.push(data[i] * k + result[i - 1] * (1 - k));
    }
  }
  return result;
}

export function sma(data: number[], period: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else {
      const slice = data.slice(i - period + 1, i + 1);
      result.push(slice.reduce((a, b) => a + b, 0) / period);
    }
  }
  return result;
}

export function stdDev(data: number[], period: number, smaValues: number[]): number[] {
  const result: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(NaN);
    } else {
      const slice = data.slice(i - period + 1, i + 1);
      const mean = smaValues[i];
      const variance =
        slice.reduce((sum, v) => sum + (v - mean) ** 2, 0) / period;
      result.push(Math.sqrt(variance));
    }
  }
  return result;
}

export function bollingerBands(
  closes: number[],
  period = 20,
  mult = 2
): { upper: number[]; middle: number[]; lower: number[] } {
  const middle = sma(closes, period);
  const std = stdDev(closes, period, middle);
  return {
    middle,
    upper: middle.map((m, i) => m + (Number.isFinite(std[i]) ? std[i]! * mult : 0)),
    lower: middle.map((m, i) => m - (Number.isFinite(std[i]) ? std[i]! * mult : 0)),
  };
}

export function parseRange(rangeStr: string): { low: number; high: number } | null {
  const m = rangeStr.match(/\[([\d.]+),\s*([\d.]+)\]/);
  if (!m) return null;
  const low = parseFloat(m[1]!);
  const high = parseFloat(m[2]!);
  return Number.isFinite(low) && Number.isFinite(high) ? { low, high } : null;
}

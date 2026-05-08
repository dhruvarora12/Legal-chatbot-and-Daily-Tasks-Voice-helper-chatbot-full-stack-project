import * as chrono from 'chrono-node';

export interface DateHint {
  text: string;
  isoDate: string;
}

export function extractDateHints(text: string): DateHint[] {
  const results = chrono.parse(text, new Date(), { forwardDate: true });
  return results.map((r) => ({
    text: r.text,
    isoDate: r.start.date().toISOString().split('T')[0],
  }));
}

export function parseIsoToUnix(isoDate: string): number {
  return Math.floor(new Date(isoDate).getTime() / 1000);
}

export function unixToIso(unix: number): string {
  return new Date(unix * 1000).toISOString().split('T')[0];
}

export function nowUnix(): number {
  return Math.floor(Date.now() / 1000);
}

import axios from 'axios';

export interface ReportOptions {
  format: string;
}

export async function sendReport(url: string, opts: ReportOptions): Promise<void> {
  await axios.post(url, { format: opts.format }, { timeout: 2000 });
}

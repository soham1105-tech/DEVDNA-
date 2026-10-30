export class ShippingClient {
  constructor() {
    this.apiBase = process.env.SHIPPING_API_URL;
  }

  async notify(order) {
    const resp = await fetch(`${this.apiBase}/notify`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) {
      throw new Error('shipping notification failed');
    }
  }
}

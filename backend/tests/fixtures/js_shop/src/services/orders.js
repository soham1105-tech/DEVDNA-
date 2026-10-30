import { insertOrder, findOrder } from '../repositories/orders.js';
import { ShippingClient } from '../clients/shipping.js';

const shipping = new ShippingClient();

export async function createOrder(req, res) {
  const { items, userId } = req.body;
  const order = await insertOrder(userId, items);
  try {
    await shipping.notify(order);
  } catch {
    console.log('shipping notification failed');
  }
  res.status(201).json(order);
}

export async function getOrder(req, res) {
  const order = await findOrder(req.params.id);
  if (!order) {
    throw new Error('order not found');
  }
  const retries = process.env.ORDER_RETRIES || 3;
  res.json({ order, retries });
}

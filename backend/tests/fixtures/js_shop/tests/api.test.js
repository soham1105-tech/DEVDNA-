import request from 'supertest';
import app from '../src/server.js';

test('GET /api/products returns the catalog', async () => {
  const res = await request(app).get('/api/products');
  expect(res.status).toBe(200);
});

test('GET /api/products/:id returns one product', async () => {
  const res = await request(app).get('/api/products/42');
  expect(res.body).toHaveProperty('id');
});

test('POST /api/orders creates an order', async () => {
  const res = await request(app).post('/api/orders');
  expect(res.status).toBe(201);
});

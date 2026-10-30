import axios from 'axios';
import { pool } from '../db.js';

const api = axios.create({ baseURL: 'https://catalog.internal', timeout: 5000 });

export async function listProducts(req, res) {
  const { rows } = await pool.query('SELECT * FROM products');
  res.json(rows);
}

export async function getProduct(req, res) {
  const id = req.params.id;
  const base = process.env.CATALOG_BASE_URL;
  const detail = await api.get(`/products/${id}`);
  const extra = await fetch(`${base}/v1/extra/${id}`);
  res.json({ detail: detail.data, extra: await extra.json() });
}

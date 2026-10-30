import { pool } from '../db.js';

export async function insertOrder(userId, items) {
  const { rows } = await pool.query('INSERT INTO orders (user_id, items) VALUES ($1, $2) RETURNING *', [userId, items]);
  return rows[0];
}

export async function findOrder(id) {
  const { rows } = await pool.query('SELECT * FROM orders WHERE id = $1', [id]);
  return rows[0];
}

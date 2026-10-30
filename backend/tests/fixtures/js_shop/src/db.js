import pg from 'pg';

export const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });

export async function healthCheck(req, res) {
  await pool.query('SELECT 1');
  res.json({ ok: true });
}

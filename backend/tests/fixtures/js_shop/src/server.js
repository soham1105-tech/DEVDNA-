import express from 'express';
import productsRouter from './routes/products.js';
import ordersRouter from './routes/orders.js';
import { registerHooks } from './hooks.js';
import { healthCheck } from './db.js';

const app = express();
app.use(express.json());
app.use('/api/products', productsRouter);
app.use('/api/orders', ordersRouter);
app.get('/health', healthCheck);
registerHooks(app);

export default app;

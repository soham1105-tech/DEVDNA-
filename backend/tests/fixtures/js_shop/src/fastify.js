import Fastify from 'fastify';
import adminRoutes from './plugins/admin.js';

const app = Fastify({ logger: true });

app.get('/fastify/ping', async () => ({ pong: true }));

app.register(adminRoutes, { prefix: '/admin' });

export default app;

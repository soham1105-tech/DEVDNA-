export default async function adminRoutes(fastify) {
  fastify.get('/users', async () => []);
  fastify.post('/users/:id/roles', async () => ({ ok: true }));
}

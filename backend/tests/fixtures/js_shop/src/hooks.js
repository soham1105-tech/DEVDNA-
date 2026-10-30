export function registerHooks(app) {
  app.use((req, res, next) => {
    next();
  });
}

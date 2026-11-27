import { createApp, shutdown } from './app';
import { config } from './store';
import { DATA_DIR, HOST, PORT } from './env';

async function main(): Promise<void> {
  const app = createApp();

  await app.listen({ port: PORT, host: HOST });

  console.log('[classfix-api] listening on', HOST + ':' + PORT, 'data =', DATA_DIR);
  console.log(
    '[classfix-api] ai =',
    Boolean(config.aiKey),
    'otp =',
    Boolean(config.otpKey),
    'admin =',
    Boolean(config.adminPassword),
  );

  const stop = async (signal: string) => {
    console.log('[classfix-api] shutting down on', signal);
    shutdown();
    await app.close();
    process.exit(0);
  };
  process.on('SIGTERM', () => void stop('SIGTERM'));
  process.on('SIGINT', () => void stop('SIGINT'));
}

main().catch((error) => {
  console.error('[classfix-api] 启动失败', error);
  process.exit(1);
});

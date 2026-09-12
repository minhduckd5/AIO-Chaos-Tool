import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'python -m uvicorn chaosgen.api.app:create_app --factory --host 127.0.0.1 --port 8765',
    url: 'http://127.0.0.1:8765/health',
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
});

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:8001', trace: 'retain-on-failure' },
  projects: [{ name: process.env.CI ? 'chromium' : 'edge', use: process.env.CI ? devices['Desktop Chrome'] : { ...devices['Desktop Edge'], channel: 'msedge' } }],
  webServer: {
    command: process.platform === 'win32' ? '.\\venv\\Scripts\\python.exe -u -m scripts.serve_test' : 'python -u -m scripts.serve_test',
    cwd: '..',
    url: 'http://127.0.0.1:8001/api/health',
    reuseExistingServer: false,
    timeout: 90000,
  },
})

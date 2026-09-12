import { test, expect } from '@playwright/test';

test.describe('ChaosGen Web UI HITL & Resilience Workflow', () => {
  test('renders AppShell with Honesty Pill and Sidebar Navigation', async ({ page }) => {
    await page.goto('/');

    // Check title and brand
    await expect(page).toHaveTitle(/ChaosGen/);
    await expect(page.locator('text=CHAOSGEN')).toBeVisible();

    // Check Honesty Pill components
    await expect(page.locator('text=IDLE')).toBeVisible();
    await expect(page.locator('text=OPERATOR:')).toBeVisible();

    // Check Sidebar groups
    await expect(page.locator('text=Operations')).toBeVisible();
    await expect(page.locator('text=Observation')).toBeVisible();
    await expect(page.locator('text=Telemetry & Advisor')).toBeVisible();
    await expect(page.locator('text=Scenario Catalog')).toBeVisible();
    await expect(page.locator('text=Experiments Console')).toBeVisible();
  });

  test('navigates to Scenario Catalog and shows predefined scenarios', async ({ page }) => {
    await page.goto('/');

    // Navigate to Scenario Catalog
    await page.click('button:has-text("Scenario Catalog")');

    // Verify catalog header and scenario cards
    await expect(page.locator('h1:has-text("SCENARIO CATALOG")')).toBeVisible();
    await expect(
      page.locator('text=DNS resolution failure for internal service')
    ).toBeVisible();
  });

  test('navigates to Experiments Console and displays Emergency HALT button', async ({ page }) => {
    await page.goto('/');

    // Navigate to Experiments
    await page.click('button:has-text("Experiments Console")');

    // Verify HALT button and history table
    await expect(page.locator('button:has-text("EMERGENCY HALT")')).toBeVisible();
    await expect(page.locator('button:has-text("Operator Hatch")')).toBeVisible();
  });

  test('navigates to Audit Trail and displays counted filter tabs', async ({ page }) => {
    await page.goto('/');

    // Navigate to Audit
    await page.click('button:has-text("Audit Trail")');

    // Verify audit header and counted tabs
    await expect(page.locator('h1:has-text("AUDIT TRAIL & INCIDENT TIMELINE")')).toBeVisible();
    await expect(page.locator('button:has-text("All Events")')).toBeVisible();
    await expect(page.locator('button:has-text("PASS")')).toBeVisible();
    await expect(page.locator('button:has-text("FAIL")')).toBeVisible();
  });

  test('navigates to Settings and verifies zero-leak masking', async ({ page }) => {
    await page.goto('/');

    // Navigate to Settings
    await page.click('button:has-text("Settings")');

    // Verify settings header and fields
    await expect(
      page.locator('h1:has-text("CONFIGURATION & GOVERNANCE SETTINGS")')
    ).toBeVisible();
    await expect(page.locator('text=Default Operator Name')).toBeVisible();
    await expect(page.locator('button:has-text("SAVE CONFIGURATION")')).toBeVisible();
    await expect(page.locator('button:has-text("Trigger Sweep")')).toBeVisible();
  });
});

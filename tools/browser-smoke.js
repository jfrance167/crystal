// Run with the installed Playwright CLI:
// playwright-cli -s=crystal run-code --filename=tools/browser-smoke.js
// Requires a disposable running demo at 127.0.0.1:8765. Leaves scheduling paused.
// Output contains observations and fingerprints only, never raw keys or tokens.
async page => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  await page.setViewportSize({width: 1440, height: 1100});
  await page.goto('http://127.0.0.1:8765/');
  const rotate = page.getByRole('button', {name: 'Rotate key'});
  await page.waitForFunction(() => !document.getElementById('rotate').disabled);
  if (await page.getByRole('button', {name: 'Pause schedule'}).isVisible()) {
    await page.getByRole('button', {name: 'Pause schedule'}).click();
    await page.getByRole('button', {name: 'Resume schedule'}).waitFor();
  }
  await rotate.click();
  await page.waitForFunction(() => document.getElementById('message').textContent.startsWith('Rotation complete.'));
  assert(await page.locator('#revocation-proof').textContent() === 'Rejected', 'Old key must be rejected.');
  assert(await page.locator('#active-count').textContent() === '1', 'One active key after success.');
  const before = Number(await page.locator('#success-count').textContent());
  await page.getByLabel('Rotate every').selectOption('10');
  await page.getByRole('button', {name: 'Set interval'}).click();
  await page.waitForFunction(() => document.getElementById('message').textContent === 'Rotation interval updated.');
  await page.getByRole('button', {name: 'Resume schedule'}).click();
  await page.waitForFunction(count => Number(document.getElementById('success-count').textContent) > count, before, {timeout: 18000});
  await page.getByRole('button', {name: 'Pause schedule'}).click();
  await page.getByRole('button', {name: 'Resume schedule'}).waitFor();
  const original = await page.locator('#fingerprint').textContent();
  await page.getByText('Explore a failed rotation', {exact: true}).click();
  await page.getByLabel('Failure for the next manual rotation').selectOption('candidate_check');
  await rotate.click();
  await page.waitForFunction(() => document.getElementById('message').textContent.startsWith('Verification failed.'));
  assert(await page.locator('#fingerprint').textContent() === original, 'Failed verification preserves key.');
  assert(await page.locator('#active-count').textContent() === '1', 'Failed candidate is removed.');
  await page.screenshot({path: 'verification/desktop.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: 'verification/mobile.png', fullPage: true});
  const metrics = await page.evaluate(() => ({width: innerWidth, documentWidth: document.documentElement.scrollWidth,
    loadMs: performance.getEntriesByType('navigation')[0].duration}));
  assert(metrics.width === metrics.documentWidth, 'Mobile document must not overflow horizontally.');
  await page.getByLabel('Failure for the next manual rotation').selectOption('old_cleanup');
  await rotate.click();
  await page.waitForFunction(() => document.getElementById('health').textContent === 'CLEANUP REQUIRED');
  assert(await rotate.isDisabled(), 'Cleanup failure blocks further rotation.');
  assert(await page.locator('#active-count').textContent() === '2', 'Cleanup failure exposes both active keys.');
  // Public results only; authorization material stays inside the application.
  return {manualRotation: 'passed', scheduledRotation: 'passed', failedVerification: 'passed',
    cleanupBlocking: 'passed', mobileOverflow: false, metrics};
}

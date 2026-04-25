const { getVersion, getName } = window.__TAURI__.app;
const { ask, message } = window.__TAURI__.dialog;
const { relaunch } = window.__TAURI__.process;

// Dev detection: http: in dev, tauri: in prod
const isDev = window.location.protocol === 'http:';

const check = isDev
 ? async () => {
      console.log('[updater] DEV: Mocking check()');
      await new Promise(r => setTimeout(r, 500));
      return {
        available: true,
        version: "9.9.9",
        currentVersion: await getVersion(),
        body: "DEV MODE: Fake update\n\n- Test UI\n- Test download flow",
        downloadAndInstall: async (progress) => {
          for (let i = 0; i <= 100; i += 25) {
            await new Promise(r => setTimeout(r, 150));
            progress({ event: 'Progress', data: { chunkLength: i, contentLength: 100 } });
          }
          await message('DEV: Fake install done', { title: 'Dev Mode' });
        }
      };
    }
  : window.__TAURI__.updater.check;

const updateButton = document.getElementById('update-button');
const versionLabel = document.getElementById('backend-label');
let latestUpdate = null;

export async function installUpdate() {
  if (!latestUpdate) return;
  try {
    const shouldUpdate = await ask(`Update to v${latestUpdate.version}?`, {
      title: 'Update Available',
      kind: 'info',
      okLabel: 'Update Now',
      cancelLabel: 'Later',
    });
    if (shouldUpdate) {
      updateButton.textContent = 'Downloading...';
      updateButton.disabled = true;
      await latestUpdate.downloadAndInstall((e) => {
        if (e.event === 'Progress') {
          const pct = Math.round((e.data.chunkLength / e.data.contentLength) * 100);
          updateButton.textContent = `Downloading ${pct}%`;
        }
      });
      if (!isDev) await relaunch();
    }
  } catch (error) {
    console.error('[updater] install failed:', error);
    await message(`Update failed: ${error}`, { title: 'Error', kind: 'error' });
    updateButton.disabled = false;
    updateButton.textContent = 'Update New Version';
  }
}

export async function initUpdater() {
  try {
    const [name, version] = await Promise.all([getName(), getVersion()]);
    if (versionLabel) versionLabel.textContent = `${name} v${version}`;
    console.log(`[updater] Running ${name} v${version} | DEV: ${isDev}`);
  } catch (e) {
    console.error('[updater] Failed to get version:', e);
  }

  if (!updateButton) return;
  updateButton.style.display = 'none';

  try {
    console.log('[updater] Calling check()...');
    const update = await check();
    console.log('[updater] check() result:', update);

    if (update?.available) {
      latestUpdate = update;
      updateButton.style.display = 'inline-block';
      updateButton.textContent = `Update to v${update.version}`;
    } else if (update === null &&!isDev) {
      console.log('[updater] check() = null. Check: signature, version, network, platform key');
    }
  } catch (err) {
    console.error('[updater] check() threw:', err);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (updateButton) updateButton.addEventListener('click', installUpdate);
});
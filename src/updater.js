const { check } = window.__TAURI__.updater;
const { ask, message } = window.__TAURI__.dialog;
const { relaunch } = window.__TAURI__.process;

const updateButton = document.getElementById('update-button');
let latestUpdate = null;

// Function to handle the actual download and install
export async function installUpdate() {
  if (!latestUpdate) {
    console.error('installUpdate called but latestUpdate is null');
    await message('No update available.', { title: 'Update', kind: 'info' });
    updateButton.style.display = 'none';
    return;
  }

  try {
    const shouldUpdate = await ask(
      `Update to version ${latestUpdate.version} is available! (Current: ${latestUpdate.currentVersion})\n\nRelease Notes:\n${latestUpdate.body || 'No release notes provided.'}\n\nDo you want to download and install the update now?`,
      {
        title: 'Update Available',
        kind: 'info',
        okLabel: 'Update Now',
        cancelLabel: 'Later',
      }
    );

    if (shouldUpdate) {
      updateButton.textContent = 'Downloading...';
      updateButton.disabled = true;
      
      // Hook progress if you want
      await latestUpdate.downloadAndInstall((event) => {
        if (event.event === 'Progress') {
          const pct = Math.round((event.data.chunkLength / event.data.contentLength) * 100);
          updateButton.textContent = `Downloading ${pct}%`;
        }
      });
      
      await message('Update installed! The application will now restart.', { title: 'Update Complete' });
      await relaunch(); // This is why you need process:allow-restart
    }
  } catch (error) {
    console.error('Error during update:', error);
    await message(`Failed to install updates: ${error}`, { title: 'Update Error', kind: 'error' });
    updateButton.style.display = 'none';
    updateButton.disabled = false;
    updateButton.textContent = 'Update New Version';
  }
}

// Function to check for updates on startup
export async function initUpdater() {
  if (!updateButton) {
    console.warn("Update button not found");
    return;
  }
  
  // Hide by default
  updateButton.style.display = 'none';
  
  try {
    console.log('Checking for updates...');
    const update = await check();
    console.log('Update check result:', update);
    
    if (update?.available) {
      latestUpdate = update;
      updateButton.style.display = 'inline-block';
      updateButton.textContent = `Update to v${update.version}`;
      console.log('Update available:', update.version);
    } else {
      console.log('No update available. Current:', update?.currentVersion);
      updateButton.style.display = 'none';
    }
  } catch (error) {
    console.error('Error during update check:', error);
    updateButton.style.display = 'none';
  }
}

// Attach listener once DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  if (updateButton) {
    updateButton.addEventListener('click', installUpdate);
  }
});
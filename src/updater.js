import { check } from "@tauri-apps/plugin-updater";
import { ask, message } from "@tauri-apps/plugin-dialog";
import { relaunch } from "@tauri-apps/plugin-process";

export async function checkForUpdatesAndInstall() {
    try {
        const update = await check();
        
        if (update?.available) {
            const shouldUpdate = await ask(
                `Update to version ${update.latestVersion} is available! (Current: ${update.currentVersion})\n\nRelease Notes:\n${update.body || 'No release notes provided.'}\n\nDo you want to download and install the update now?`,
                {
                    title: 'Update Available',
                    kind: 'info',
                    okLabel: 'Update Now',
                    cancelLabel: 'Later',
                }
            );

            if (shouldUpdate) {
                await message('Downloading and installing update...', { title: 'Updating App' });
                await update.downloadAndInstall();
                await message('Update installed! The application will now restart.', { title: 'Update Complete' });
                await relaunch();
            } else {
                await message('Update cancelled. You can check for updates later from the settings.', { title: 'Update Cancelled' });
            }
        } else {
            await message(`You are running the latest version (${update.currentVersion}).`, { title: 'No Updates' });
        }
    } catch (error) {
        console.error('Error during update process:', error);
        await message(`Failed to check for or install updates: ${error.message}`, { title: 'Update Error', kind: 'error' });
    }
}
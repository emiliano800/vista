const { contextBridge, ipcRenderer } = require('electron');

const on = (channel) => (fn) => {
  const h = (_e, ...args) => fn(...args);
  ipcRenderer.on(channel, h);
  return () => ipcRenderer.off(channel, h);
};

contextBridge.exposeInMainWorld('vista', {
  start: () => ipcRenderer.invoke('rec:start'),
  pause: () => ipcRenderer.invoke('rec:pause'),
  resume: () => ipcRenderer.invoke('rec:resume'),
  stop: () => ipcRenderer.invoke('rec:stop'),
  toggle: () => ipcRenderer.invoke('rec:toggle'),
  status: () => ipcRenderer.invoke('rec:status'),
  onStatus: on('rec:status'),
  recordings: () => ipcRenderer.invoke('recordings:list'),
  onRecordings: on('recordings:changed'),
  openRecording: (id) => ipcRenderer.invoke('recordings:open', id),
  annotate: (id, ann) => ipcRenderer.invoke('recordings:annotate', id, ann),
  sections: (id) => ipcRenderer.invoke('recordings:sections', id),
  clarify: (id, section, answers) => ipcRenderer.invoke('recordings:clarify', id, section, answers),
  openDashboard: () => ipcRenderer.invoke('dashboard:open'),
  resizeOverlay: (mode) => ipcRenderer.invoke('overlay:resize', mode),
  onReview: on('dashboard:review'),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (patch) => ipcRenderer.invoke('settings:set', patch),
  defaultSettings: () => ipcRenderer.invoke('settings:defaults'),
  info: () => ipcRenderer.invoke('app:info'),
  permissions: () => ipcRenderer.invoke('permissions:get'),
  openPermission: (kind) => ipcRenderer.invoke('permissions:open', kind),
  onPermissions: on('permissions:changed'),
  // capture window only
  onVideoStart: on('video:start'),
  onVideoPause: on('video:pause'),
  onVideoResume: on('video:resume'),
  onVideoStop: on('video:stop'),
  videoChunk: (dir, buf) => ipcRenderer.send('video:chunk', dir, buf),
  videoDone: () => ipcRenderer.send('video:done'),
});

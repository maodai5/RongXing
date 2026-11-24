const byId = (id) => document.getElementById(id);
const form = byId('studio');
const inputs = byId('inputs');
const fileInput = byId('image');
const drop = byId('drop');
const thumb = byId('upload-thumb');
const desc = byId('pet-desc');
const portraitBtn = byId('portrait-generate');
const generateBtn = byId('generate');
const still = byId('still');
const video = byId('video');
const empty = byId('empty');
const busyBox = byId('stage-busy');
const busyText = byId('busy-text');
const confirmBar = byId('portrait-confirm');
const acceptBtn = byId('portrait-accept');
const retryBtn = byId('portrait-retry');
const downloadLink = byId('download');
const portraitDownload = byId('download-portrait');
const ready = form.dataset.ready === 'true';
const portraitReady = form.dataset.portraitReady === 'true';
const storageKey = 'rongxing-wan-active-job';

let job;
let submitting = false;
let portraitBusy = false;
let originalFile = null;
let portraitBlob = null;
let portraitUrl = null;
let photoUrl = null;
let accepted = false;

function setStatus(text, error = false) {
  const status = byId('status');
  status.textContent = text;
  status.classList.toggle('error', error);
}

function setBadge(id, text, tone = '') {
  const badge = byId(id);
  badge.textContent = text;
  badge.classList.toggle('is-done', tone === 'done');
  badge.classList.toggle('is-busy', tone === 'busy');
}

let timer = null;
let startedAt = 0;
let phase = '';

function tickTimer() {
  const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
  byId('progress-text').textContent = phase + ' · 已用时 ' + seconds + ' 秒';
}

function trackProgress(text) {
  phase = text;
  if (!timer) {
    startedAt = Date.now();
    byId('progress').hidden = false;
    timer = setInterval(tickTimer, 1000);
  }
  tickTimer();
}

function stopTimer() {
  if (timer) clearInterval(timer);
  timer = null;
  byId('progress').hidden = true;
}

function setBusy(on, text) {
  busyBox.hidden = !on;
  if (text) busyText.textContent = text;
  if (!on) stopTimer();
}

function syncGate() {
  portraitBtn.disabled = !portraitReady || !originalFile || portraitBusy || !!job;
  generateBtn.disabled = !ready || !accepted || submitting || !!job;
}

function lock(locked) {
  inputs.disabled = locked;
  syncGate();
}

function remember(value) {
  job = value;
  try {
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
  } catch { /* polling works without storage */ }
}

function dataURLtoBlob(dataURL) {
  const [meta, b64] = dataURL.split(',', 2);
  const mime = (meta.match(/data:([^;]+)/) || [])[1] || 'image/png';
  const bytes = atob(b64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i += 1) arr[i] = bytes.charCodeAt(i);
  return new Blob([arr], { type: mime });
}

function dropPortrait() {
  accepted = false;
  confirmBar.hidden = true;
  acceptBtn.hidden = false;
  portraitDownload.hidden = true;
  portraitDownload.removeAttribute('href');
  if (portraitUrl) URL.revokeObjectURL(portraitUrl);
  portraitUrl = null;
  portraitBlob = null;
  setBadge('portrait-state', '未生成');
}

function clearResult() {
  if (video.hasAttribute('src')) {
    video.removeAttribute('src');
    video.load();
  }
  video.removeAttribute('poster');
  video.hidden = byId('result').hidden = downloadLink.hidden = byId('save-tip').hidden = true;
  downloadLink.removeAttribute('href');
  downloadLink.removeAttribute('download');
  byId('result').removeAttribute('href');
}

function showPhotoPreview() {
  clearResult();
  if (photoUrl) {
    still.src = photoUrl;
    still.hidden = false;
    empty.hidden = true;
    byId('preview-type').textContent = '原图 · 待生成形象';
  } else {
    still.removeAttribute('src');
    still.hidden = true;
    empty.hidden = false;
    byId('preview-type').textContent = '等待照片';
  }
}

function takeFile(file) {
  if (!file) return;
  if (!file.type.startsWith('image/')) {
    setStatus('请选择 JPG、PNG、WebP 或 BMP 图片。', true);
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    setStatus('照片超过 10 MB，请换一张小一些的。', true);
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  originalFile = file;
  dropPortrait();
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = URL.createObjectURL(file);
  thumb.src = photoUrl;
  thumb.hidden = false;
  drop.classList.add('has-file');
  byId('drop-title').textContent = '已选择：' + file.name;
  byId('drop-hint').textContent = '点击可更换照片 · 生成形象后会以形象作为视频首帧';
  showPhotoPreview();
  drop.classList.remove('is-drag');
  setBadge('upload-state', '已上传', 'done');
  setStatus('照片已就位，下一步生成 2.5D 形象。');
  syncGate();
}

fileInput.addEventListener('change', () => takeFile(fileInput.files[0]));

for (const type of ['dragenter', 'dragover']) {
  drop.addEventListener(type, (event) => {
    event.preventDefault();
    drop.classList.add('is-drag');
  });
}
for (const type of ['dragleave', 'drop']) {
  drop.addEventListener(type, () => drop.classList.remove('is-drag'));
}
drop.addEventListener('drop', (event) => {
  event.preventDefault();
  takeFile(event.dataTransfer.files[0]);
});

async function call(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || '请求失败，请检查工作台连接。');
    error.status = response.status;
    throw error;
  }
  return data;
}

async function generatePortrait() {
  if (!portraitReady || portraitBusy || job) return;
  if (!originalFile) {
    setStatus('请先上传一张宠物照片。', true);
    fileInput.focus();
    return;
  }
  portraitBusy = true;
  clearResult();
  dropPortrait();
  syncGate();
  setBusy(true, '正在重绘形象，约 20–60 秒…');
  trackProgress('形象生成中');
  setBadge('portrait-state', '生成中', 'busy');
  setStatus('正在按系统提示词生成 2.5D 形象，请勿关闭页面…');
  try {
    const body = new FormData();
    body.append('image', originalFile, originalFile.name);
    body.append('description', desc.value.trim());
    const data = await call('./api/portrait', {
      method: 'POST',
      headers: { 'X-Studio-Request': '1' },
      body,
    });
    if (!data.image) throw new Error('未收到形象图片。');
    portraitBlob = dataURLtoBlob(data.image);
    portraitUrl = URL.createObjectURL(portraitBlob);
    still.src = portraitUrl;
    still.hidden = false;
    empty.hidden = true;
    clearResult();
    portraitDownload.href = data.download_url || '';
    portraitDownload.hidden = !data.download_url;
    byId('preview-type').textContent = '形象 · 待确认';
    byId('portrait-note').textContent = '这张形象满意吗？确认后用它生成动作视频。';
    acceptBtn.hidden = false;
    confirmBar.hidden = false;
    setBadge('portrait-state', '待确认', 'busy');
    setStatus('形象已生成并自动存到本机，确认后即可生成动作视频。');
  } catch (error) {
    setBadge('portrait-state', '生成失败');
    setStatus(error.status ? error.message : '生成形象失败，请稍后再试。', true);
  } finally {
    portraitBusy = false;
    setBusy(false);
    syncGate();
  }
}

portraitBtn.addEventListener('click', () => void generatePortrait());

retryBtn.addEventListener('click', () => {
  if (portraitBusy) return;
  dropPortrait();
  if (originalFile) showPhotoPreview();
  syncGate();
  void generatePortrait();
});

acceptBtn.addEventListener('click', () => {
  if (!portraitBlob) return;
  const ext = portraitBlob.type === 'image/png' ? 'png' : 'jpg';
  const file = new File([portraitBlob], '绒星形象.' + ext, { type: portraitBlob.type });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  accepted = true;
  still.src = portraitUrl;
  still.hidden = false;
  empty.hidden = true;
  clearResult();
  byId('preview-type').textContent = '已确认形象';
  byId('portrait-note').textContent = '已使用这张形象生成视频；想换可以点「重新生成」。';
  acceptBtn.hidden = true;
  confirmBar.hidden = false;
  setBadge('portrait-state', '已确认', 'done');
  setBadge('action-state', '待生成');
  setStatus('形象已确认，选好动作后生成视频。');
  syncGate();
});

form.addEventListener('change', (event) => {
  if (event.target.name === 'action') {
    byId('action-summary').textContent = event.target.dataset.summary;
    byId('action-prompt').textContent = event.target.dataset.prompt;
    setBadge('action-state', accepted ? '待生成' : '待确认形象');
  }
});

for (const id of ['portrait-prompt', 'portrait-negative']) {
  const box = byId(id);
  if (box) box.addEventListener('focus', () => box.select());
}

const delay = () => new Promise((resolve) => setTimeout(resolve, 15000));

function finish() {
  remember(null);
  setBusy(false);
  lock(false);
  syncGate();
}

async function poll() {
  byId('resume').hidden = true;
  trackProgress('等待受理');
  let errors = 0;
  for (;;) {
    try {
      const data = await call('./api/jobs/' + encodeURIComponent(job.token));
      errors = 0;
      if (data.status === 'SUCCEEDED') {
        const source = data.video_url;
        const savedAs = data.download_url || source;
        const local = savedAs.startsWith('/api/');
        if (still.getAttribute('src')) video.poster = still.src;
        video.src = source;
        video.load();
        still.hidden = empty.hidden = true;
        video.hidden = false;
        downloadLink.href = savedAs;
        downloadLink.textContent = local ? '下载 MP4' : '打开 MP4 ↗';
        if (local) downloadLink.setAttribute('download', '绒星-宠物动作.mp4');
        else downloadLink.removeAttribute('download');
        downloadLink.hidden = byId('save-tip').hidden = false;
        byId('result').href = source;
        byId('result').hidden = false;
        byId('preview-type').textContent = '成片 · MP4';
        setBadge('action-state', '已完成', 'done');
        setStatus(job.action + ' · 已生成并缓存在本机，点「下载 MP4」保存');
        const playing = video.play();
        if (playing && playing.catch) playing.catch(() => { /* 浏览器可能拦截带声音的自动播放 */ });
        finish();
        return;
      }
      if (!['PENDING', 'RUNNING'].includes(data.status)) {
        setBadge('action-state', '失败');
        setStatus(data.detail, true);
        finish();
        return;
      }
      trackProgress(data.status === 'PENDING' ? '排队中' : '生成中');
      const stageText = data.status === 'PENDING' ? '任务排队中…' : '正在生成视频…';
      setBusy(true, stageText + '通常需要 1–3 分钟');
      setBadge('action-state', data.status === 'PENDING' ? '排队中' : '生成中', 'busy');
      setStatus((data.status === 'PENDING' ? '排队中' : '生成中') + '，通常需要几分钟');
    } catch (error) {
      if (error.status === 404) {
        setStatus('查询凭据失效，请用下方任务编号到百炼控制台核对。', true);
        finish();
        return;
      }
      if (++errors >= 3) {
        setBusy(false);
        setStatus('暂时无法查询，可继续查询原任务，不会重复生成。', true);
        byId('resume').hidden = false;
        return;
      }
      setStatus('查询暂时失败，正在重试（不会重复生成）…');
    }
    await delay();
  }
}

function showJob() {
  byId('task').textContent = '任务编号：' + job.id;
  byId('task').hidden = false;
  setBusy(true, '正在提交视频任务…');
  void poll();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!ready || job || submitting) return;
  if (!accepted) {
    setStatus('请先生成并确认 2.5D 形象，再生成动作视频。', true);
    return;
  }
  const selected = form.querySelector('input[name="action"]:checked');
  if (!selected) {
    setStatus('请选择一个动作。', true);
    return;
  }
  const action = selected.labels[0].textContent.trim();
  // 先构建请求体再禁用 fieldset，否则 action 与图片会被排除，服务端返回 400。
  const body = new FormData(form);
  body.set('action', selected.value);
  submitting = true;
  lock(true);
  byId('task').hidden = true;
  setBusy(true, '正在上传并提交…');
  setStatus('正在上传并提交任务…');
  try {
    const data = await call('./api/jobs', { method: 'POST', headers: { 'X-Studio-Request': '1' }, body });
    if (!data.token || !data.task_id) throw new Error('未收到任务编号。');
    remember({ token: data.token, id: data.task_id, action });
    showJob();
  } catch (error) {
    setBusy(false);
    setStatus(error.status ? error.message : '未收到提交确认。请先到百炼控制台核对，避免重复计费。', true);
    lock(false);
  } finally {
    submitting = false;
  }
});

byId('resume').addEventListener('click', () => void poll());

video.addEventListener('error', () => {
  if (!video.hidden) setStatus('网页内暂时无法播放，请点「下载 MP4」保存到本地后用播放器查看。', true);
});

window.addEventListener('beforeunload', (event) => {
  if (!submitting && !portraitBusy) return;
  event.preventDefault();
  event.returnValue = true;
});

syncGate();

try {
  const previous = JSON.parse(sessionStorage.getItem(storageKey));
  if (ready && previous && previous.token && previous.id) {
    job = previous;
    lock(true);
    setStatus('正在恢复任务查询…');
    showJob();
  }
} catch { /* storage can be unavailable */ }

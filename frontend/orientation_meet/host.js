/**
 * SGP Orientation — Host Client
 *
 * Features:
 *  - See all patients joined (video tiles in sidebar)
 *  - Screen share (for playing orientation videos with audio)
 *  - Mute all patients
 *  - Mute/unmute self, camera toggle
 *  - End session for all → sends data message → navigates patients to quiz
 */

const API_BASE = window.location.origin + '/api/v1';

let room = null;
let timerInterval = null;
let startTime = null;
let micEnabled  = true;
let camEnabled  = true;
let screenSharing = false;
let currentSessionId = null;

// ── URL param pre-fill ────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  const p = new URLSearchParams(window.location.search);
  if (p.get('session')) {
    const el = document.getElementById('input-session-id');
    el.value = p.get('session');
    el.readOnly = true;
    el.style.opacity = '0.6';
  }
  document.getElementById('input-session-id')
    ?.addEventListener('keydown', e => { if (e.key === 'Enter') joinHost(); });
  document.getElementById('input-name')
    ?.addEventListener('keydown', e => { if (e.key === 'Enter') joinHost(); });
});

// ── Screen management ─────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => {
    s.classList.remove('active');
    s.style.display = 'none';
    s.style.opacity = '0';
  });
  const target = document.getElementById(id);
  target.style.display = 'flex';
  requestAnimationFrame(() => {
    target.style.opacity = '1';
    target.classList.add('active');
  });
}

function startTimer() {
  startTime = Date.now();
  timerInterval = setInterval(() => {
    const e = Math.floor((Date.now() - startTime) / 1000);
    const fmt = `${String(Math.floor(e / 60)).padStart(2, '0')}:${String(e % 60).padStart(2, '0')}`;
    document.getElementById('timer-display').textContent = fmt;
    const t2 = document.getElementById('timer-display-2');
    if (t2) t2.textContent = fmt;
  }, 1000);
}

// ── Join ──────────────────────────────────────────────────────────────────────
async function joinHost() {
  const name      = document.getElementById('input-name').value.trim();
  const sessionId = document.getElementById('input-session-id').value.trim();
  const errEl     = document.getElementById('join-error');
  const btn       = document.getElementById('btn-join');

  if (!name || !sessionId) {
    errEl.textContent = 'Please fill in all fields.';
    errEl.classList.remove('hidden');
    return;
  }
  errEl.classList.add('hidden');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⟳</span> Connecting…';

  try {
    const res = await fetch(
      `${API_BASE}/orientation/sessions/${sessionId}/host-token?host_name=${encodeURIComponent(name)}`,
      { method: 'POST' }
    );
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || 'Could not get host token');
    }
    const data = await res.json();

    currentSessionId = sessionId;
    document.getElementById('host-self-name').textContent = `🩺 ${name} (You)`;
    showScreen('meeting-screen');
    startTimer();
    await connectToLiveKit(data.token, data.livekit_url, name);

  } catch (err) {
    errEl.textContent = `Error: ${err.message}`;
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">▶</span> Start Session';
  }
}

// ── LiveKit connection ────────────────────────────────────────────────────────
async function connectToLiveKit(token, livekitUrl, displayName) {
  const { Room, RoomEvent, Track, VideoPresets } = LivekitClient;

  room = new Room({
    adaptiveStream: true,
    dynacast: true,
    videoCaptureDefaults: { resolution: VideoPresets.h720.resolution },
  });

  room
    .on(RoomEvent.ParticipantConnected,    onPatientJoined)
    .on(RoomEvent.ParticipantDisconnected, onPatientLeft)
    .on(RoomEvent.TrackSubscribed,         onTrackSubscribed)
    .on(RoomEvent.TrackUnsubscribed,       track => track.detach())
    .on(RoomEvent.Disconnected,            () => clearInterval(timerInterval));

  await room.connect(livekitUrl, token);

  // Enable camera + mic
  try {
    await room.localParticipant.enableCameraAndMicrophone();
  } catch (e) {
    console.warn('Camera/mic not available:', e.message);
    try { await room.localParticipant.setMicrophoneEnabled(true); } catch (_) {}
  }

  // Attach host self-view
  const localVideo = room.localParticipant.getTrackPublications()
    .find(p => p.track?.kind === Track.Kind.Video)?.track;
  if (localVideo) localVideo.attach(document.getElementById('host-self-video'));

  // Render already-present patients
  room.remoteParticipants.forEach(p => {
    if (!p.identity.startsWith('host:')) onPatientJoined(p);
  });

  updateCount();
}

// ── Patient event handlers ────────────────────────────────────────────────────
function onPatientJoined(participant) {
  if (participant.identity.startsWith('host:')) return;
  if (document.getElementById(`tile-${participant.sid}`)) return;

  const name = participant.identity.includes(':')
    ? participant.identity.split(':')[1].replace(/_/g, ' ')
    : participant.identity;

  const tile = document.createElement('div');
  tile.id = `tile-${participant.sid}`;
  tile.dataset.identity = participant.identity;
  tile.className = 'patient-tile';
  tile.innerHTML = `
    <div class="patient-video-wrap">
      <video autoplay playsinline></video>
      <div class="patient-overlay">
        <span class="patient-name">${name}</span>
        <span class="patient-mic-icon" id="mic-${participant.sid}">🎙</span>
      </div>
    </div>`;

  document.getElementById('patients-grid').appendChild(tile);
  document.getElementById('no-patients-msg').style.display = 'none';
  updateCount();
}

function onPatientLeft(participant) {
  if (participant.identity.startsWith('host:')) return;
  document.getElementById(`tile-${participant.sid}`)?.remove();
  updateCount();
}

function onTrackSubscribed(track, publication, participant) {
  const { Track } = LivekitClient;
  if (participant.identity.startsWith('host:')) return;
  const tile = document.getElementById(`tile-${participant.sid}`);
  if (!tile) return;
  if (track.kind === Track.Kind.Video) {
    track.attach(tile.querySelector('video'));
  }
  // Audio plays automatically through WebRTC
}

function updateCount() {
  if (!room) return;
  const patients = [...room.remoteParticipants.values()]
    .filter(p => !p.identity.startsWith('host:'));
  document.getElementById('patient-count').textContent =
    `${patients.length} patient${patients.length !== 1 ? 's' : ''} joined`;
  document.getElementById('sidebar-count').textContent = patients.length;
}

// ── Host controls ─────────────────────────────────────────────────────────────
async function toggleMic() {
  if (!room) return;
  micEnabled = !micEnabled;
  await room.localParticipant.setMicrophoneEnabled(micEnabled);
  const btn = document.getElementById('btn-mic');
  btn.className = `ctrl-btn ${micEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
  document.getElementById('mic-icon').textContent  = micEnabled ? '🎙' : '🔇';
  btn.querySelector('.ctrl-label').textContent     = micEnabled ? 'Mute' : 'Unmute';
}

async function toggleCam() {
  if (!room) return;
  camEnabled = !camEnabled;
  await room.localParticipant.setCameraEnabled(camEnabled);
  const btn = document.getElementById('btn-cam');
  btn.className = `ctrl-btn ${camEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
  document.getElementById('cam-icon').textContent = camEnabled ? '📷' : '🚫';
  btn.querySelector('.ctrl-label').textContent    = camEnabled ? 'Camera' : 'Camera off';
}

async function toggleScreenShare() {
  if (!room) return;
  const btn = document.getElementById('btn-screen');
  try {
    if (!screenSharing) {
      await room.localParticipant.setScreenShareEnabled(true, {
        audio: true,   // capture system audio for orientation videos
        video: { frameRate: 15, width: 1280, height: 720 },
      });
      screenSharing = true;
      btn.className = 'ctrl-btn ctrl-active';
      btn.querySelector('.ctrl-icon').textContent  = '🖥';
      btn.querySelector('.ctrl-label').textContent = 'Stop Share';
      document.getElementById('screen-share-indicator')?.classList.remove('hidden');
    } else {
      await room.localParticipant.setScreenShareEnabled(false);
      screenSharing = false;
      btn.className = 'ctrl-btn';
      btn.querySelector('.ctrl-icon').textContent  = '🖥';
      btn.querySelector('.ctrl-label').textContent = 'Share Screen';
      document.getElementById('screen-share-indicator')?.classList.add('hidden');
    }
  } catch (e) {
    console.warn('Screen share error:', e.message);
    screenSharing = false;
    btn.className = 'ctrl-btn';
    btn.querySelector('.ctrl-label').textContent = 'Share Screen';
  }
}

async function muteAllPatients() {
  if (!room) return;
  // Publish a mute-all data message to all participants
  const msg = new TextEncoder().encode(JSON.stringify({ type: 'mute_all' }));
  try {
    await room.localParticipant.publishData(msg, { reliable: true });
    document.getElementById('btn-mute-all').querySelector('.ctrl-label').textContent = 'Muted All';
    setTimeout(() => {
      document.getElementById('btn-mute-all').querySelector('.ctrl-label').textContent = 'Mute All';
    }, 2000);
  } catch (e) { console.warn('Mute all error:', e); }
}

async function endSessionForAll() {
  if (!confirm('End the session for all participants? This will trigger the assessment quiz for everyone.')) return;

  const btn = document.getElementById('btn-end');
  btn.disabled = true;
  btn.innerHTML = '<span class="ctrl-icon">⟳</span><span class="ctrl-label">Ending…</span>';

  // 1. Send session_ending data message — patients navigate to quiz on disconnect
  if (room) {
    try {
      const msg = new TextEncoder().encode(JSON.stringify({ type: 'session_ending' }));
      await room.localParticipant.publishData(msg, { reliable: true });
    } catch (e) { console.error('Data message error:', e); }

    // 2. Wait briefly for message to propagate, then disconnect
    await new Promise(r => setTimeout(r, 1500));
    try { await room.disconnect(); } catch (_) {}
  }

  clearInterval(timerInterval);

  // 3. Call FastAPI to finalize attendance (fire and forget from UI perspective)
  fetch(`${API_BASE}/orientation/sessions/${currentSessionId}/end`, { method: 'POST' })
    .catch(e => console.warn('FastAPI end session (non-critical):', e));

  showScreen('ended-screen');
}

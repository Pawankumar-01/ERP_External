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

  // Warn immediately if browser won't allow camera/mic/screen share.
  // navigator.mediaDevices is ONLY available on HTTPS or localhost.
  // Plain HTTP (e.g. http://122.x.x.x:8001) will make it undefined.
  if (!window.isSecureContext || !navigator.mediaDevices) {
    const warn = document.createElement('div');
    warn.style.cssText = `
      position:fixed; bottom:0; left:0; right:0; z-index:99999;
      background:#3a1010; color:#f87171; padding:12px 20px;
      font-size:13px; font-weight:500; text-align:center;
      border-top:2px solid #ef4444;
    `;
    warn.innerHTML = `
      ⚠️ <strong>Camera, microphone and screen sharing are blocked.</strong>
      This page is served over plain HTTP — browsers only allow media access on
      <strong>HTTPS or localhost</strong>.
      Please access this page via HTTPS or ask your administrator to enable HTTPS
      (e.g. using nginx + SSL certificate or a Cloudflare tunnel).
    `;
    document.body.appendChild(warn);
  }
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

function showHostBanner(message, type = 'info') {
  let banner = document.getElementById('host-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'host-banner';
    banner.style.cssText = `
      position:fixed; top:68px; left:50%; transform:translateX(-50%); z-index:9999;
      padding:10px 24px; border-radius:24px; font-size:13px; font-weight:500;
      box-shadow: 0 4px 24px rgba(0,0,0,0.5); transition: opacity 0.3s;
    `;
    document.body.appendChild(banner);
  }
  const colors = {
    info:    'background:#253027; color:#e2c47a; border:1px solid #c9a84c',
    success: 'background:#1a3a22; color:#6dbf80; border:1px solid #4a9a5e',
    warn:    'background:#3a2010; color:#e07a70; border:1px solid #c0564a',
  };
  banner.style.cssText += '; ' + (colors[type] || colors.info);
  banner.textContent = message;
  banner.style.opacity = '1';
  banner.style.display = 'block';
}

function hideHostBanner() {
  const banner = document.getElementById('host-banner');
  if (banner) {
    banner.style.opacity = '0';
    setTimeout(() => { banner.style.display = 'none'; }, 300);
  }
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
    .on(RoomEvent.LocalTrackPublished,     onLocalTrackPublished)
    .on(RoomEvent.TrackUnsubscribed, (track, pub, p) => {
      track.detach();
      if (track.kind === LivekitClient.Track.Kind.Audio) {
        document.getElementById(`audio-${p.sid}`)?.remove();
      }
    })
    .on(RoomEvent.Disconnected,            () => clearInterval(timerInterval));

  await room.connect(livekitUrl, token);

  // Enable camera + mic
  // navigator.mediaDevices is only available on HTTPS/localhost.
  // On plain HTTP it will be undefined — show a clear message instead of crashing.
  if (!navigator.mediaDevices) {
    showHostBanner(
      '⚠️ Camera & mic blocked — page must be served over HTTPS. You are still connected as host.',
      'warn'
    );
  } else {
    try {
      await room.localParticipant.enableCameraAndMicrophone();
    } catch (e) {
      console.warn('Camera/mic not available:', e.message);
      try { await room.localParticipant.setMicrophoneEnabled(true); } catch (_) {}
    }
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

  if (track.kind === Track.Kind.Audio) {
    const audioEl = document.createElement('audio');
    audioEl.autoplay = true;
    audioEl.id = `audio-${participant.sid}`;
    document.body.appendChild(audioEl);
    track.attach(audioEl);
    return;
  }

  const tile = document.getElementById(`tile-${participant.sid}`);
  if (tile && track.kind === Track.Kind.Video) {
    track.attach(tile.querySelector('video'));
  }
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
  if (!room) {
    showHostBanner('⚠️ Not connected to meeting room.', 'warn');
    setTimeout(hideHostBanner, 3000);
    return;
  }
  try {
    micEnabled = !micEnabled;
    await room.localParticipant.setMicrophoneEnabled(micEnabled);
    const btn = document.getElementById('btn-mic');
    btn.className = `ctrl-btn ${micEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
    document.getElementById('mic-icon').textContent  = micEnabled ? 'MIC' : 'MUTED';
    btn.querySelector('.ctrl-label').textContent     = micEnabled ? 'Mute' : 'Unmute';
  } catch (e) {
    showHostBanner('⚠️ Microphone error: ' + e.message, 'warn');
    setTimeout(hideHostBanner, 4000);
  }
}

async function toggleCam() {
  if (!room) {
    showHostBanner('⚠️ Not connected to meeting room.', 'warn');
    setTimeout(hideHostBanner, 3000);
    return;
  }
  try {
    camEnabled = !camEnabled;
    await room.localParticipant.setCameraEnabled(camEnabled);
    const btn = document.getElementById('btn-cam');
    btn.className = `ctrl-btn ${camEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
    document.getElementById('cam-icon').textContent = camEnabled ? 'CAM' : 'OFF';
    btn.querySelector('.ctrl-label').textContent    = camEnabled ? 'Camera' : 'Camera off';
  } catch (e) {
    showHostBanner('⚠️ Camera error: ' + e.message, 'warn');
    setTimeout(hideHostBanner, 4000);
  }
}

async function toggleScreenShare() {
  if (!room) {
    showHostBanner('⚠️ Not connected to meeting room.', 'warn');
    setTimeout(hideHostBanner, 3000);
    return;
  }

  // Screen sharing requires a secure context (HTTPS or localhost).
  // On plain HTTP, navigator.mediaDevices is undefined and getDisplayMedia will crash.
  if (!window.isSecureContext || !navigator.mediaDevices?.getDisplayMedia) {
    showHostBanner(
      '⚠️ Screen sharing requires HTTPS. ' +
      'Ask your administrator to enable SSL (e.g. nginx + SSL cert or Cloudflare tunnel).',
      'warn'
    );
    setTimeout(hideHostBanner, 8000);
    return;
  }

  const btn = document.getElementById('btn-screen');
  btn.disabled = true;
  try {
    if (!screenSharing) {
      try {
        await room.localParticipant.setScreenShareEnabled(true, {
          audio: true,
          video: { frameRate: 15, width: 1280, height: 720 },
        });
      } catch (audioErr) {
        console.warn('Screen share with audio rejected, falling back to video-only:', audioErr.message);
        await room.localParticipant.setScreenShareEnabled(true, {
          audio: false,
          video: { frameRate: 15, width: 1280, height: 720 },
        });
        showHostBanner('💡 Screen shared without audio (select a browser tab to include system audio)', 'info');
        setTimeout(hideHostBanner, 6000);
      }
      screenSharing = true;
      btn.className = 'ctrl-btn ctrl-active';
      btn.querySelector('.ctrl-icon').textContent  = 'SCR';
      btn.querySelector('.ctrl-label').textContent = 'Stop Share';
      document.getElementById('screen-share-indicator')?.classList.remove('hidden');
      // Track attached via onLocalTrackPublished once LiveKit confirms it's published
    } else {
      await room.localParticipant.setScreenShareEnabled(false);
      screenSharing = false;
      btn.className = 'ctrl-btn';
      btn.querySelector('.ctrl-icon').textContent  = 'SCR';
      btn.querySelector('.ctrl-label').textContent = 'Share Screen';
      document.getElementById('screen-share-indicator')?.classList.add('hidden');
      document.getElementById('host-screen-card')?.classList.add('hidden');
    }
  } catch (e) {
    const msg = e.name === 'NotAllowedError'
      ? '⚠️ Screen share permission denied by browser.'
      : `⚠️ Screen share failed: ${e.message}`;
    console.warn('Screen share error:', e);
    showHostBanner(msg, 'warn');
    setTimeout(hideHostBanner, 5000);
    screenSharing = false;
    btn.className = 'ctrl-btn';
    btn.querySelector('.ctrl-label').textContent = 'Share Screen';
    document.getElementById('host-screen-card')?.classList.add('hidden');
  } finally {
    btn.disabled = false;
  }
}

// Attach our own screen share track once it's confirmed published to the room
function onLocalTrackPublished(publication) {
  const { Track } = LivekitClient;
  if (publication.source === Track.Source.ScreenShare && publication.track) {
    publication.track.attach(document.getElementById('host-screen-video'));
    document.getElementById('host-screen-card')?.classList.remove('hidden');
  }
}

async function muteAllPatients() {
  if (!room) {
    showHostBanner('⚠️ Not connected to meeting room.', 'warn');
    setTimeout(hideHostBanner, 3000);
    return;
  }
  const msg = new TextEncoder().encode(JSON.stringify({ type: 'mute_all' }));
  try {
    await room.localParticipant.publishData(msg, { reliable: true });
    showHostBanner('🔇 Mute command broadcasted to all patients', 'success');
    setTimeout(hideHostBanner, 3000);
    const btn = document.getElementById('btn-mute-all');
    btn.querySelector('.ctrl-label').textContent = 'Muted All';
    setTimeout(() => {
      btn.querySelector('.ctrl-label').textContent = 'Mute All';
    }, 2000);
  } catch (e) { 
    console.warn('Mute all error:', e);
    showHostBanner('⚠️ Failed to send mute broadcast: ' + e.message, 'warn');
    setTimeout(hideHostBanner, 4000);
  }
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
/**
 * SGP Orientation — Patient Client
 *
 * Flow:
 *   1. Join form (auto-filled from URL params)
 *   2. Meeting room — host in spotlight, self + others in sidebar
 *   3. Session end detected via:
 *        a. Data message from host  → show "Session ended" banner → quiz
 *        b. RoomEvent.Disconnected  → (fallback) → quiz
 *   4. MCQ assessment
 *   5. Results screen
 */

const API_BASE = window.location.origin + '/api/v1';

let room = null;
let timerInterval = null;
let startTime = null;
let micEnabled = true;
let camEnabled = true;
let screenSharing = false;
let currentLeadId = null;
let currentSessionId = null;
let quizQuestions = [];
let quizAnswers = {};
let sessionEndedByHost = false;  // flag to distinguish intentional end from network drop

// ── URL param pre-fill ─────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  const p = new URLSearchParams(window.location.search);
  if (p.get('session')) {
    const el = document.getElementById('input-session-id');
    el.value = p.get('session');
    el.readOnly = true;
    el.style.opacity = '0.6';
  }
  if (p.get('lead')) document.getElementById('input-lead-id').value = p.get('lead');
  if (p.get('name')) document.getElementById('input-name').value = decodeURIComponent(p.get('name'));

  // Keyboard submit on join form
  ['input-name','input-lead-id','input-session-id'].forEach(id => {
    document.getElementById(id)?.addEventListener('keydown', e => {
      if (e.key === 'Enter') joinSession();
    });
  });
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

function showBanner(message, type = 'info') {
  let banner = document.getElementById('session-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'session-banner';
    banner.style.cssText = `
      position:fixed; top:0; left:0; right:0; z-index:9999;
      padding:14px 20px; text-align:center; font-size:14px; font-weight:500;
      transition: opacity 0.4s;
    `;
    document.body.appendChild(banner);
  }
  const colors = {
    info:    'background:#253027; color:#e2c47a; border-bottom:1px solid #c9a84c',
    success: 'background:#1a3a22; color:#6dbf80; border-bottom:1px solid #4a9a5e',
    warn:    'background:#3a2a10; color:#e2c47a; border-bottom:1px solid #c9a84c',
  };
  banner.style.cssText += '; ' + (colors[type] || colors.info);
  banner.textContent = message;
  banner.style.opacity = '1';
}

function hideBanner() {
  const banner = document.getElementById('session-banner');
  if (banner) banner.style.opacity = '0';
}

function startTimer() {
  startTime = Date.now();
  timerInterval = setInterval(() => {
    const e = Math.floor((Date.now() - startTime) / 1000);
    document.getElementById('timer-display').textContent =
      `${String(Math.floor(e / 60)).padStart(2, '0')}:${String(e % 60).padStart(2, '0')}`;
  }, 1000);
}

// ── Join ──────────────────────────────────────────────────────────────────────
async function joinSession() {
  const name      = document.getElementById('input-name').value.trim();
  const leadId    = document.getElementById('input-lead-id').value.trim();
  const sessionId = document.getElementById('input-session-id').value.trim();
  const errEl     = document.getElementById('join-error');
  const btn       = document.getElementById('btn-join');

  if (!name || !leadId || !sessionId) {
    errEl.textContent = 'Please fill in all fields.';
    errEl.classList.remove('hidden');
    return;
  }
  errEl.classList.add('hidden');
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-icon">⟳</span> Connecting…';

  try {
    const res = await fetch(`${API_BASE}/orientation/sessions/${sessionId}/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, lead_id: leadId, lead_name: name }),
    });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || 'Could not get meeting token');
    }
    const data = await res.json();

    currentLeadId    = leadId;
    currentSessionId = sessionId;
    document.getElementById('local-name-badge').textContent = name;

    showScreen('meeting-screen');
    startTimer();
    await connectToLiveKit(data.token, data.livekit_url, name);

  } catch (err) {
    startTime = null;
    clearInterval(timerInterval);
    if (room) { try { await room.disconnect(); } catch (_) {} room = null; }
    errEl.textContent = `Error: ${err.message}`;
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">▶</span> Enter Orientation';
  }
}

// ── LiveKit connection ────────────────────────────────────────────────────────
async function connectToLiveKit(token, livekitUrl, displayName) {
  const { Room, RoomEvent, Track, VideoPresets } = LivekitClient;

  room = new Room({
    adaptiveStream: true,
    dynacast: true,
    videoCaptureDefaults: { resolution: VideoPresets.h360.resolution },
  });

  room
    .on(RoomEvent.ParticipantConnected,    onParticipantJoined)
    .on(RoomEvent.ParticipantDisconnected, onParticipantLeft)
    .on(RoomEvent.TrackSubscribed,         onTrackSubscribed)
    .on(RoomEvent.TrackUnsubscribed,       onTrackUnsubscribed)
    .on(RoomEvent.DataReceived,            onDataReceived)
    .on(RoomEvent.Disconnected,            onRoomDisconnected);

  await room.connect(livekitUrl, token);

  // Enable local camera + mic (gracefully handle no device)
  try {
    await room.localParticipant.enableCameraAndMicrophone();
  } catch (e) {
    console.warn('Camera/mic not available:', e.message);
    try { await room.localParticipant.setMicrophoneEnabled(true); } catch (_) {}
  }

  // Attach local video
  attachLocalVideo();

  // Render any already-present participants (host may be there already)
  room.remoteParticipants.forEach(p => onParticipantJoined(p));

  updateParticipantCount();
}

function attachLocalVideo() {
  const { Track } = LivekitClient;
  const pub = room.localParticipant.getTrackPublications()
    .find(p => p.track?.kind === Track.Kind.Video);
  if (pub?.track) pub.track.attach(document.getElementById('local-video'));
}

// ── Room event handlers ───────────────────────────────────────────────────────
function onParticipantJoined(participant) {
  if (participant.identity.startsWith('host:')) {
    const name = participant.identity.replace('host:', '').replace(/_/g, ' ');
    document.getElementById('host-name-badge').textContent = `🩺 ${name}`;
    document.querySelector('#host-tile .tile-placeholder').style.display = 'none';
    showBanner(`🩺 ${name} has joined as presenter`, 'success');
    setTimeout(hideBanner, 3000);
    // Attach any already-published host tracks
    participant.getTrackPublications().forEach(pub => {
      if (pub.track) onTrackSubscribed(pub.track, pub, participant);
    });
  } else {
    addParticipantTile(participant);
  }
  updateParticipantCount();
}

function onParticipantLeft(participant) {
  document.getElementById(`tile-${participant.sid}`)?.remove();
  if (participant.identity.startsWith('host:')) {
    document.querySelector('#host-tile .tile-placeholder').style.display = 'flex';
    document.getElementById('host-video').style.display = 'none';
    showBanner('Presenter has left the session', 'warn');
  }
  updateParticipantCount();
}

function onTrackSubscribed(track, publication, participant) {
  const { Track } = LivekitClient;
  if (participant.identity.startsWith('host:')) {
    if (track.kind === Track.Kind.Video || track.kind === Track.Kind.Screen) {
      const vid = document.getElementById('host-video');
      vid.style.display = 'block';
      track.attach(vid);
      document.querySelector('#host-tile .tile-placeholder').style.display = 'none';
    }
    // Host audio plays through the video element automatically
  } else {
    const tile = document.getElementById(`tile-${participant.sid}`);
    if (tile && track.kind === Track.Kind.Video) {
      track.attach(tile.querySelector('video'));
    }
  }
}

function onTrackUnsubscribed(track, publication, participant) {
  track.detach();
  if (participant?.identity?.startsWith('host:')) {
    // Check if host still has any video track
    const hasVideo = [...participant.getTrackPublications().values()]
      .some(p => p.track?.kind === 'video' && !p.isMuted);
    if (!hasVideo) {
      document.getElementById('host-video').style.display = 'none';
      document.querySelector('#host-tile .tile-placeholder').style.display = 'flex';
    }
  }
}

async function onDataReceived(data) {
  try {
    const msg = JSON.parse(new TextDecoder().decode(data));
    if (msg.type === 'session_ending') {
      sessionEndedByHost = true;
      showBanner('✦ Session has ended. Thank you for attending!', 'success');
      clearInterval(timerInterval);
      document.getElementById('session-status').textContent = 'Session Ended';
      document.getElementById('session-status').classList.remove('pill-live');
      // Disconnect cleanly — onRoomDisconnected will handle the rest
      setTimeout(async () => {
        if (room) { try { await room.disconnect(); } catch (_) {} }
      }, 2000);
    }
  } catch (e) { console.warn('Data message error:', e); }
}

async function onRoomDisconnected() {
  clearInterval(timerInterval);
  await loadAndShowQuiz();
}

// ── Participant tiles ─────────────────────────────────────────────────────────
function addParticipantTile(participant) {
  if (document.getElementById(`tile-${participant.sid}`)) return;
  const name = participant.identity.includes(':')
    ? participant.identity.split(':')[1].replace(/_/g, ' ')
    : participant.identity;
  const tile = document.createElement('div');
  tile.id = `tile-${participant.sid}`;
  tile.className = 'video-tile tile-participant';
  tile.innerHTML = `
    <video autoplay playsinline></video>
    <div class="tile-overlay"><span class="name-badge">${name}</span></div>`;
  document.getElementById('participants-grid').appendChild(tile);
}

function updateParticipantCount() {
  if (!room) return;
  const total = room.remoteParticipants.size + 1;
  document.getElementById('participant-count').textContent =
    `${total} participant${total !== 1 ? 's' : ''}`;
  document.getElementById('sidebar-count').textContent =
    [...room.remoteParticipants.values()].filter(p => !p.identity.startsWith('host:')).length;
}

// ── Controls ──────────────────────────────────────────────────────────────────
async function toggleMic() {
  if (!room) return;
  micEnabled = !micEnabled;
  await room.localParticipant.setMicrophoneEnabled(micEnabled);
  const btn = document.getElementById('btn-mic');
  btn.className = `ctrl-btn ${micEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
  document.getElementById('mic-icon').textContent  = micEnabled ? '🎙' : '🔇';
  btn.querySelector('.ctrl-label').textContent      = micEnabled ? 'Mute' : 'Unmute';
}

async function toggleCam() {
  if (!room) return;
  camEnabled = !camEnabled;
  await room.localParticipant.setCameraEnabled(camEnabled);
  const btn = document.getElementById('btn-cam');
  btn.className = `ctrl-btn ${camEnabled ? 'ctrl-active' : 'ctrl-muted'}`;
  document.getElementById('cam-icon').textContent = camEnabled ? '📷' : '🚫';
  btn.querySelector('.ctrl-label').textContent     = camEnabled ? 'Camera' : 'Camera off';
}

async function leaveSession() {
  if (!confirm('Are you sure you want to leave? The session is still in progress.')) return;
  clearInterval(timerInterval);
  if (room) { try { await room.disconnect(); } catch (_) {} room = null; }
  showScreen('join-screen');
}

document.addEventListener('keydown', e => {
  if (!room) return;
  if (e.key === 'm' && !e.ctrlKey) toggleMic();
  if (e.key === 'v' && !e.ctrlKey) toggleCam();
});

// ── Quiz ──────────────────────────────────────────────────────────────────────
async function loadAndShowQuiz() {
  try {
    const res = await fetch(`${API_BASE}/assessment/questions`);
    if (!res.ok) throw new Error('Failed to load');
    quizQuestions = await res.json();
    renderQuiz();
    showScreen('quiz-screen');
  } catch (e) {
    console.error('Quiz load failed:', e);
    showScreen('complete-screen');
  }
}

function renderQuiz() {
  const container = document.getElementById('quiz-questions');
  container.innerHTML = '';
  quizQuestions.forEach((q, idx) => {
    const div = document.createElement('div');
    div.className = 'quiz-question';
    div.innerHTML = `
      <p class="q-text"><strong>${idx + 1}.</strong> ${q.question_text}</p>
      ${['A', 'B', 'C', 'D'].map(opt => `
        <label class="q-option" id="label-${q.id}-${opt}">
          <input type="radio" name="q_${q.id}" value="${opt}"
            onchange="selectAnswer('${q.id}','${opt}')">
          <span class="opt-key">${opt}</span>
          <span class="opt-text">${q['option_' + opt.toLowerCase()]}</span>
        </label>`).join('')}`;
    container.appendChild(div);
  });
}

function selectAnswer(questionId, option) {
  quizAnswers[questionId] = option;
  // Visual feedback — highlight selected option
  ['A','B','C','D'].forEach(opt => {
    const label = document.getElementById(`label-${questionId}-${opt}`);
    if (label) label.classList.toggle('q-option-selected', opt === option);
  });
}

async function submitQuiz() {
  const errEl = document.getElementById('quiz-error');
  const unanswered = quizQuestions.filter(q => !quizAnswers[q.id]);
  if (unanswered.length > 0) {
    errEl.textContent = `Please answer all questions (${unanswered.length} remaining).`;
    errEl.classList.remove('hidden');
    // Scroll to first unanswered
    const firstQ = document.getElementById(`label-${unanswered[0].id}-A`);
    firstQ?.closest('.quiz-question')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  errEl.classList.add('hidden');
  const btn = document.getElementById('btn-submit-quiz');
  btn.disabled = true;
  btn.textContent = 'Submitting…';

  try {
    const res = await fetch(`${API_BASE}/assessment/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lead_id:    currentLeadId,
        session_id: currentSessionId,
        answers:    quizQuestions.map(q => ({ question_id: q.id, selected_option: quizAnswers[q.id] })),
        language:   'en',
      }),
    });
    if (!res.ok) throw new Error('Submission failed');
    const data = await res.json();
    showResults(data);
  } catch (e) {
    errEl.textContent = 'Submission failed. Please try again.';
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.textContent = 'Submit Answers';
  }
}

function showResults(data) {
  document.getElementById('results-summary').innerHTML =
    `You answered <strong>${data.correct}</strong> out of <strong>${data.total}</strong> correctly.`;

  document.getElementById('results-answers').innerHTML = data.results.map((r, idx) => `
    <div class="result-item ${r.is_correct ? 'result-correct' : 'result-wrong'}">
      <p class="result-q"><strong>${idx + 1}.</strong> ${r.question_text}</p>
      <p class="result-a">
        Your answer: <strong>${r.selected_option}</strong>
        ${r.is_correct
          ? '<span class="badge-correct">✓ Correct</span>'
          : `<span class="badge-wrong">✗ Wrong</span>
             <span class="result-correct-hint">Correct answer: <strong>${r.correct_option}</strong></span>`}
      </p>
    </div>`).join('');

  showScreen('results-screen');
}

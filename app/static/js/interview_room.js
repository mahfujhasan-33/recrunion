const configElement = document.querySelector("#interview-room-config");
const roomConfig = JSON.parse(configElement.textContent);
const signalingTicket = roomConfig.signaling_ticket;
configElement.textContent = "";

const localVideo = document.querySelector("#local-interview-video");
const remoteVideo = document.querySelector("#remote-interview-video");
const localPlaceholder = document.querySelector("#local-video-placeholder");
const remotePlaceholder = document.querySelector("#remote-video-placeholder");
const prepareButton = document.querySelector("#prepare-interview-media");
const joinButton = document.querySelector("#join-interview-room");
const rejoinButton = document.querySelector("#rejoin-interview-room");
const microphoneButton = document.querySelector("#toggle-interview-microphone");
const cameraButton = document.querySelector("#toggle-interview-camera");
const leaveButton = document.querySelector("#leave-interview-room");
const statusElement = document.querySelector("#interview-room-status");
const lifecycleElement = document.querySelector("#interview-lifecycle-status");
const scheduledElement = document.querySelector("#candidate-interview-time");

const reconnectDelays = [1000, 2000, 4000, 8000];
const maximumIceRestartAttempts = 2;
let localStream = null;
let peerConnection = null;
let signalingSocket = null;
let pendingCandidates = [];
let reconnectAttempt = 0;
let reconnectTimer = null;
let disconnectTimer = null;
let iceRecoveryTimer = null;
let iceRestartAttempts = 0;
let recoveryInProgress = false;
let joined = false;
let closing = false;
let callEnded = !roomConfig.join_allowed;
let explicitlyLeft = false;

scheduledElement.textContent = new Intl.DateTimeFormat(undefined, {
  dateStyle: "full",
  timeStyle: "short",
  timeZone: roomConfig.timezone,
}).format(new Date(scheduledElement.textContent));

function setStatus(message, isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

function setLifecycle(status) {
  lifecycleElement.textContent = status;
}

function setCallControls(enabled) {
  microphoneButton.disabled = !enabled;
  cameraButton.disabled = !enabled;
  leaveButton.disabled = !enabled;
}

function showRejoin(message) {
  clearTimeout(reconnectTimer);
  clearTimeout(disconnectTimer);
  clearTimeout(iceRecoveryTimer);
  reconnectTimer = null;
  rejoinButton.hidden = false;
  joinButton.disabled = true;
  setCallControls(false);
  setStatus(message || "Automatic recovery stopped. Rejoin the interview to continue.", true);
}

async function prepareMedia() {
  prepareButton.disabled = true;
  setStatus("Requesting camera and microphone access…");
  try {
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      throw new Error("Camera and microphone access requires an HTTPS interview link.");
    }
    localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
    localVideo.srcObject = localStream;
    localPlaceholder.hidden = true;
    joinButton.disabled = false;
    prepareButton.textContent = "Devices ready";
    setStatus("Camera and microphone are ready. Join when you are prepared.");
  } catch (error) {
    prepareButton.disabled = false;
    setStatus(error.message || "Camera and microphone access was not available.", true);
  }
}

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/interviews/${roomConfig.interview_id}/signal`;
}

function clearPeerConnection() {
  clearTimeout(disconnectTimer);
  clearTimeout(iceRecoveryTimer);
  disconnectTimer = null;
  iceRecoveryTimer = null;
  recoveryInProgress = false;
  peerConnection?.close();
  peerConnection = null;
  pendingCandidates = [];
  remoteVideo.srcObject = null;
  remotePlaceholder.hidden = false;
}

function createPeerConnection() {
  if (peerConnection) return peerConnection;
  peerConnection = new RTCPeerConnection({
    iceServers: roomConfig.ice_servers,
    iceTransportPolicy: roomConfig.ice_transport_policy,
  });
  localStream.getTracks().forEach((track) => peerConnection.addTrack(track, localStream));
  peerConnection.addEventListener("track", (event) => {
    remoteVideo.srcObject = event.streams[0];
    remotePlaceholder.hidden = true;
  });
  peerConnection.addEventListener("icecandidate", (event) => {
    if (event.candidate) {
      sendSignal({ type: "ice-candidate", candidate: event.candidate.toJSON() });
    }
  });
  peerConnection.addEventListener("connectionstatechange", handleConnectionState);
  peerConnection.addEventListener("iceconnectionstatechange", handleIceConnectionState);
  return peerConnection;
}

function sendSignal(message) {
  if (signalingSocket?.readyState === WebSocket.OPEN) {
    signalingSocket.send(JSON.stringify(message));
    return true;
  }
  return false;
}

async function createOffer(iceRestart = false) {
  const connection = createPeerConnection();
  if (connection.signalingState !== "stable") return;
  const offer = await connection.createOffer({ iceRestart });
  await connection.setLocalDescription(offer);
  sendSignal({ type: "offer", sdp: offer.sdp });
  setStatus(iceRestart ? "Restoring the peer connection…" : "Connecting to the candidate…");
}

async function flushPendingCandidates() {
  if (!peerConnection?.remoteDescription) return;
  for (const candidate of pendingCandidates) {
    await peerConnection.addIceCandidate(candidate);
  }
  pendingCandidates = [];
}

function handleConnected() {
  clearTimeout(disconnectTimer);
  clearTimeout(iceRecoveryTimer);
  disconnectTimer = null;
  iceRecoveryTimer = null;
  recoveryInProgress = false;
  iceRestartAttempts = 0;
  sendSignal({ type: "peer-connected" });
  setStatus("Interview connected securely.");
}

function handleConnectionState() {
  if (!peerConnection) return;
  const state = peerConnection.connectionState;
  if (state === "connected") {
    handleConnected();
  } else if (state === "disconnected") {
    setStatus("Connection interrupted. Waiting briefly for recovery…", true);
    clearTimeout(disconnectTimer);
    disconnectTimer = setTimeout(attemptIceRecovery, 3000);
  } else if (state === "failed") {
    setStatus("Connection failed. Attempting recovery…", true);
    attemptIceRecovery();
  }
}

function handleIceConnectionState() {
  if (peerConnection?.iceConnectionState === "failed") attemptIceRecovery();
}

async function attemptIceRecovery() {
  if (closing || callEnded || recoveryInProgress) return;
  if (!joined || signalingSocket?.readyState !== WebSocket.OPEN) {
    scheduleSignalingReconnect();
    return;
  }
  if (iceRestartAttempts >= maximumIceRestartAttempts) {
    showRejoin("The media connection could not be restored automatically. Rejoin to continue.");
    return;
  }
  iceRestartAttempts += 1;
  recoveryInProgress = true;
  setStatus(`Reconnecting media (${iceRestartAttempts}/${maximumIceRestartAttempts})…`);
  try {
    if (roomConfig.role === "INTERVIEWER") {
      await createOffer(true);
    } else if (!sendSignal({ type: "ice-restart-request" })) {
      scheduleSignalingReconnect();
    }
  } catch {
    recoveryInProgress = false;
  }
  clearTimeout(iceRecoveryTimer);
  iceRecoveryTimer = setTimeout(() => {
    recoveryInProgress = false;
    if (peerConnection?.connectionState !== "connected") attemptIceRecovery();
  }, 8000);
}

async function handleSignal(message) {
  if (message.type === "authenticated") {
    joined = true;
    reconnectAttempt = 0;
    rejoinButton.hidden = true;
    clearPeerConnection();
    createPeerConnection();
    setCallControls(true);
    joinButton.disabled = true;
    setLifecycle(roomConfig.status === "SCHEDULED" ? "WAITING" : roomConfig.status);
    setStatus(
      message.peer_present
        ? "The other participant is ready. Connecting…"
        : roomConfig.role === "INTERVIEWER"
          ? "Waiting for candidate…"
          : "Waiting for interviewer…",
    );
    if (roomConfig.role === "INTERVIEWER" && message.peer_present) await createOffer();
    return;
  }
  if (message.type === "peer-joined") {
    clearPeerConnection();
    createPeerConnection();
    setStatus("The other participant joined. Establishing the call…");
    if (roomConfig.role === "INTERVIEWER") await createOffer();
    return;
  }
  if (message.type === "offer" && roomConfig.role === "CANDIDATE") {
    const connection = createPeerConnection();
    await connection.setRemoteDescription({ type: "offer", sdp: message.sdp });
    await flushPendingCandidates();
    const answer = await connection.createAnswer();
    await connection.setLocalDescription(answer);
    sendSignal({ type: "answer", sdp: answer.sdp });
    return;
  }
  if (message.type === "answer" && roomConfig.role === "INTERVIEWER") {
    await peerConnection.setRemoteDescription({ type: "answer", sdp: message.sdp });
    await flushPendingCandidates();
    return;
  }
  if (message.type === "ice-candidate") {
    if (peerConnection?.remoteDescription) {
      await peerConnection.addIceCandidate(message.candidate);
    } else {
      pendingCandidates.push(message.candidate);
    }
    return;
  }
  if (message.type === "ice-restart-request" && roomConfig.role === "INTERVIEWER") {
    await attemptIceRecovery();
    return;
  }
  if (message.type === "interview-active") {
    roomConfig.status = "ACTIVE";
    setLifecycle("ACTIVE");
    setStatus("Interview connected securely.");
    return;
  }
  if (message.type === "peer-left") {
    clearPeerConnection();
    setStatus("The other participant left. Waiting for them to rejoin.");
    return;
  }
  if (message.type === "call-ended") {
    callEnded = true;
    roomConfig.status = "ENDED";
    setLifecycle("ENDED");
    finishLocalCall();
    setStatus("The interviewer ended the interview.");
    return;
  }
  if (message.type === "error") {
    if (["AUTHENTICATION_FAILED", "INTERVIEW_ENDED"].includes(message.code)) {
      callEnded = message.code === "INTERVIEW_ENDED";
      explicitlyLeft = true;
      showRejoin(message.message);
      return;
    }
    setStatus(message.message || "Interview signalling failed.", true);
  }
}

function connectSignaling(isReconnect = false) {
  if (closing || callEnded || !signalingTicket) return;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  setStatus(isReconnect ? "Reconnecting signalling…" : "Connecting to the interview room…");
  const socket = new WebSocket(websocketUrl());
  signalingSocket = socket;
  socket.addEventListener("open", () => {
    if (socket === signalingSocket) sendSignal({ type: "authenticate", ticket: signalingTicket });
  });
  socket.addEventListener("message", async (event) => {
    if (socket !== signalingSocket) return;
    try {
      await handleSignal(JSON.parse(event.data));
    } catch {
      setStatus("The browser could not process an interview signalling message.", true);
    }
  });
  socket.addEventListener("close", () => {
    if (socket !== signalingSocket) return;
    joined = false;
    if (!closing && !callEnded && !explicitlyLeft) scheduleSignalingReconnect();
  });
  socket.addEventListener("error", () => {
    if (socket === signalingSocket && !closing) {
      setStatus("The signalling connection was interrupted.", true);
    }
  });
}

function scheduleSignalingReconnect() {
  if (closing || callEnded || explicitlyLeft || reconnectTimer) return;
  clearPeerConnection();
  if (reconnectAttempt >= reconnectDelays.length) {
    showRejoin("The signalling connection could not be restored automatically.");
    return;
  }
  const delay = reconnectDelays[reconnectAttempt];
  reconnectAttempt += 1;
  setStatus(`Reconnecting (${reconnectAttempt}/${reconnectDelays.length})…`);
  reconnectTimer = setTimeout(() => connectSignaling(true), delay);
}

function joinRoom() {
  joinButton.disabled = true;
  explicitlyLeft = false;
  closing = false;
  connectSignaling(false);
}

function finishLocalCall() {
  if (closing) return;
  closing = true;
  clearTimeout(reconnectTimer);
  clearPeerConnection();
  signalingSocket?.close(1000);
  signalingSocket = null;
  localStream?.getTracks().forEach((track) => track.stop());
  localStream = null;
  localVideo.srcObject = null;
  localPlaceholder.hidden = false;
  setCallControls(false);
  joined = false;
}

function leaveRoom() {
  if (roomConfig.role === "INTERVIEWER") {
    leaveButton.disabled = true;
    setStatus("Ending the interview…");
    sendSignal({ type: "end-call" });
    return;
  }
  explicitlyLeft = true;
  sendSignal({ type: "leave" });
  finishLocalCall();
  setStatus("You left the interview. You may reload the invitation link to rejoin.");
  rejoinButton.hidden = false;
}

if (!roomConfig.join_allowed) {
  prepareButton.disabled = true;
  joinButton.disabled = true;
  setCallControls(false);
  setLifecycle("ENDED");
  setStatus("This interview has ended.");
}

prepareButton.addEventListener("click", prepareMedia);
joinButton.addEventListener("click", joinRoom);
rejoinButton.addEventListener("click", () => window.location.reload());
microphoneButton.addEventListener("click", () => {
  const track = localStream?.getAudioTracks()[0];
  if (!track) return;
  track.enabled = !track.enabled;
  microphoneButton.textContent = track.enabled ? "Mute microphone" : "Unmute microphone";
  microphoneButton.setAttribute("aria-pressed", String(!track.enabled));
});
cameraButton.addEventListener("click", () => {
  const track = localStream?.getVideoTracks()[0];
  if (!track) return;
  track.enabled = !track.enabled;
  cameraButton.textContent = track.enabled ? "Turn camera off" : "Turn camera on";
  cameraButton.setAttribute("aria-pressed", String(!track.enabled));
});
leaveButton.addEventListener("click", leaveRoom);
window.addEventListener("beforeunload", () => {
  if (!callEnded && signalingSocket?.readyState === WebSocket.OPEN) {
    sendSignal({ type: "leave" });
  }
});

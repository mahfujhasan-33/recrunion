const configElement = document.querySelector("#interview-room-config");
const roomConfig = JSON.parse(configElement.textContent);
configElement.textContent = "";

const localVideo = document.querySelector("#local-interview-video");
const remoteVideo = document.querySelector("#remote-interview-video");
const localPlaceholder = document.querySelector("#local-video-placeholder");
const remotePlaceholder = document.querySelector("#remote-video-placeholder");
const prepareButton = document.querySelector("#prepare-interview-media");
const joinButton = document.querySelector("#join-interview-room");
const microphoneButton = document.querySelector("#toggle-interview-microphone");
const cameraButton = document.querySelector("#toggle-interview-camera");
const leaveButton = document.querySelector("#leave-interview-room");
const statusElement = document.querySelector("#interview-room-status");
const scheduledElement = document.querySelector("#candidate-interview-time");

let localStream = null;
let peerConnection = null;
let signalingSocket = null;
let pendingCandidates = [];
let joined = false;
let closing = false;

scheduledElement.textContent = new Intl.DateTimeFormat(undefined, {
  dateStyle: "full",
  timeStyle: "short",
  timeZone: roomConfig.timezone,
}).format(new Date(scheduledElement.textContent));

function setStatus(message, isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

function setCallControls(enabled) {
  microphoneButton.disabled = !enabled;
  cameraButton.disabled = !enabled;
  leaveButton.disabled = !enabled;
}

async function prepareMedia() {
  prepareButton.disabled = true;
  setStatus("Requesting camera and microphone access…");
  try {
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      throw new Error("Camera and microphone access requires an HTTPS interview link.");
    }
    localStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: true,
    });
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

function createPeerConnection() {
  if (peerConnection) return peerConnection;
  peerConnection = new RTCPeerConnection({ iceServers: roomConfig.ice_servers });
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
  peerConnection.addEventListener("connectionstatechange", () => {
    const state = peerConnection.connectionState;
    if (state === "connected") setStatus("Interview connected securely peer to peer.");
    if (["failed", "disconnected"].includes(state)) {
      setStatus("The peer connection was interrupted. Rejoin the room to try again.", true);
    }
  });
  return peerConnection;
}

function sendSignal(message) {
  if (signalingSocket?.readyState === WebSocket.OPEN) {
    signalingSocket.send(JSON.stringify(message));
  }
}

async function createOffer() {
  const connection = createPeerConnection();
  if (connection.signalingState !== "stable") return;
  const offer = await connection.createOffer();
  await connection.setLocalDescription(offer);
  sendSignal({ type: "offer", sdp: offer.sdp });
  setStatus("Calling the candidate…");
}

async function flushPendingCandidates() {
  if (!peerConnection?.remoteDescription) return;
  for (const candidate of pendingCandidates) {
    await peerConnection.addIceCandidate(candidate);
  }
  pendingCandidates = [];
}

async function handleSignal(message) {
  if (message.type === "authenticated") {
    joined = true;
    createPeerConnection();
    setCallControls(true);
    joinButton.disabled = true;
    setStatus(message.peer_present ? "The other participant is ready." : "Waiting for the other participant…");
    if (roomConfig.role === "INTERVIEWER" && message.peer_present) await createOffer();
    return;
  }
  if (message.type === "peer-joined") {
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
  if (message.type === "peer-left") {
    remoteVideo.srcObject = null;
    remotePlaceholder.hidden = false;
    setStatus("The other participant left the interview.");
    return;
  }
  if (message.type === "call-ended") {
    closeRoom(false);
    setStatus("The interviewer ended the interview.");
    return;
  }
  if (message.type === "error") {
    setStatus(message.message || "Interview signalling failed.", true);
  }
}

async function joinRoom() {
  joinButton.disabled = true;
  setStatus("Connecting to the interview room…");
  signalingSocket = new WebSocket(websocketUrl());
  signalingSocket.addEventListener("open", () => {
    sendSignal({ type: "authenticate", ticket: roomConfig.signaling_ticket });
    roomConfig.signaling_ticket = null;
  });
  signalingSocket.addEventListener("message", async (event) => {
    try {
      await handleSignal(JSON.parse(event.data));
    } catch {
      setStatus("The browser could not process an interview signalling message.", true);
    }
  });
  signalingSocket.addEventListener("close", () => {
    if (!closing && !joined) {
      joinButton.disabled = false;
      setStatus("The interview room connection closed before joining.", true);
    }
  });
  signalingSocket.addEventListener("error", () => {
    setStatus("The interview signalling service is unavailable.", true);
  });
}

function closeRoom(notifyServer = true) {
  if (closing) return;
  closing = true;
  if (notifyServer && signalingSocket?.readyState === WebSocket.OPEN) {
    sendSignal({ type: roomConfig.role === "INTERVIEWER" ? "end-call" : "leave" });
  }
  peerConnection?.close();
  peerConnection = null;
  signalingSocket?.close(1000);
  localStream?.getTracks().forEach((track) => track.stop());
  localStream = null;
  localVideo.srcObject = null;
  remoteVideo.srcObject = null;
  localPlaceholder.hidden = false;
  remotePlaceholder.hidden = false;
  setCallControls(false);
  joined = false;
}

prepareButton.addEventListener("click", prepareMedia);
joinButton.addEventListener("click", joinRoom);
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
leaveButton.addEventListener("click", () => {
  closeRoom(true);
  setStatus(roomConfig.role === "INTERVIEWER" ? "Interview ended." : "You left the interview.");
});
window.addEventListener("beforeunload", () => closeRoom(true));

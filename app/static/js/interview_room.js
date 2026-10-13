const configElement = document.querySelector("#interview-room-config");
const roomConfig = JSON.parse(configElement.textContent);
const participantToken = roomConfig.participant_token;
roomConfig.participant_token = null;
configElement.textContent = "";

const localVideo = document.querySelector("#local-interview-video");
const remoteVideo = document.querySelector("#remote-interview-video");
const remoteAudio = document.querySelector("#remote-interview-audio");
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

const livekit = window.LivekitClient;
const expectedRemoteIdentity = `${
  roomConfig.role === "INTERVIEWER" ? "candidate" : "interviewer"
}_${roomConfig.interview_id.replaceAll("-", "")}`;
let room = null;
let localTracks = [];
let joined = false;
let explicitlyLeft = false;
let callEnded = roomConfig.status === "ENDED";

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
  roomConfig.status = status;
  lifecycleElement.textContent = status;
}

function setCallControls(enabled) {
  microphoneButton.disabled = !enabled;
  cameraButton.disabled = !enabled;
  leaveButton.disabled = !enabled;
}

function showRejoin(message) {
  rejoinButton.hidden = false;
  joinButton.disabled = true;
  setCallControls(false);
  setStatus(message, true);
}

function localTrack(source) {
  return localTracks.find((track) => track.source === source);
}

async function prepareMedia() {
  prepareButton.disabled = true;
  setStatus("Requesting camera and microphone access…");
  try {
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      throw new Error("Camera and microphone access requires an HTTPS interview link.");
    }
    if (!livekit) throw new Error("The LiveKit browser client could not be loaded.");
    localTracks = await livekit.createLocalTracks({ audio: true, video: true });
    const videoTrack = localTrack(livekit.Track.Source.Camera);
    videoTrack?.attach(localVideo);
    localPlaceholder.hidden = true;
    joinButton.disabled = false;
    prepareButton.textContent = "Devices ready";
    setStatus("Camera and microphone are ready. Join when you are prepared.");
  } catch (error) {
    prepareButton.disabled = false;
    setStatus(error.message || "Camera and microphone access was not available.", true);
  }
}

async function synchronizeLifecycle() {
  const response = await fetch(roomConfig.lifecycle_sync_url, {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.message || "Interview status could not be synchronized.");
  }
  const lifecycle = await response.json();
  setLifecycle(lifecycle.status);
  return lifecycle;
}

async function syncLifecycleSafely() {
  try {
    await synchronizeLifecycle();
  } catch (error) {
    setStatus(error.message || "Interview status could not be synchronized.", true);
  }
}

function isHumanPeer(participant) {
  return participant.identity === expectedRemoteIdentity;
}

function hasRemoteVideo() {
  if (!room) return false;
  return Array.from(room.remoteParticipants.values()).some(
    (participant) =>
      isHumanPeer(participant) &&
      Array.from(participant.trackPublications.values()).some(
        (publication) =>
          publication.kind === livekit.Track.Kind.Video &&
          publication.isSubscribed &&
          publication.track,
      ),
  );
}

function attachRemoteTrack(track, _publication, participant) {
  if (!isHumanPeer(participant)) return;
  if (track.kind === livekit.Track.Kind.Video) {
    track.attach(remoteVideo);
    remotePlaceholder.hidden = true;
    setStatus("Interview connected securely through LiveKit.");
  } else if (track.kind === livekit.Track.Kind.Audio) {
    track.attach(remoteAudio);
    if (!hasRemoteVideo()) {
      remotePlaceholder.textContent = "Audio connected; waiting for the other camera";
    }
  }
}

function detachRemoteTrack(track, _publication, participant) {
  if (!isHumanPeer(participant)) return;
  if (track.kind === livekit.Track.Kind.Video) track.detach(remoteVideo);
  if (track.kind === livekit.Track.Kind.Audio) track.detach(remoteAudio);
  if (!hasRemoteVideo()) {
    remotePlaceholder.hidden = false;
    remotePlaceholder.textContent = "Waiting for the other participant's camera";
  }
}

function attachAvailableRemoteTracks() {
  if (!room) return;
  for (const participant of room.remoteParticipants.values()) {
    if (!isHumanPeer(participant)) continue;
    for (const publication of participant.trackPublications.values()) {
      if (publication.track && publication.isSubscribed) {
        attachRemoteTrack(publication.track, publication, participant);
      } else if (
        [livekit.Track.Kind.Audio, livekit.Track.Kind.Video].includes(publication.kind)
      ) {
        publication.setSubscribed(true);
      }
    }
  }
}

function configureRoomEvents() {
  room.on(livekit.RoomEvent.Reconnecting, () => {
    setStatus("Connection interrupted. LiveKit is reconnecting…", true);
  });
  room.on(livekit.RoomEvent.Reconnected, async () => {
    attachAvailableRemoteTracks();
    setStatus("Reconnected to the interview. Restoring media…");
    await syncLifecycleSafely();
  });
  room.on(livekit.RoomEvent.ParticipantConnected, async (participant) => {
    if (!isHumanPeer(participant)) return;
    setStatus("The other participant joined. Connecting media…");
    attachAvailableRemoteTracks();
    await syncLifecycleSafely();
  });
  room.on(livekit.RoomEvent.ParticipantDisconnected, async (participant) => {
    if (!isHumanPeer(participant)) return;
    remoteVideo.srcObject = null;
    remoteAudio.srcObject = null;
    remotePlaceholder.hidden = false;
    remotePlaceholder.textContent = "Waiting for the other participant";
    setStatus("The other participant left. Waiting for them to rejoin.");
    await syncLifecycleSafely();
  });
  room.on(livekit.RoomEvent.TrackPublished, (publication, participant) => {
    if (!isHumanPeer(participant)) return;
    if (![livekit.Track.Kind.Audio, livekit.Track.Kind.Video].includes(publication.kind)) {
      return;
    }
    setStatus("The other participant is sharing media. Connecting…");
    publication.setSubscribed(true);
  });
  room.on(livekit.RoomEvent.TrackSubscribed, attachRemoteTrack);
  room.on(livekit.RoomEvent.TrackUnsubscribed, detachRemoteTrack);
  room.on(livekit.RoomEvent.TrackSubscriptionFailed, (_trackSid, participant) => {
    if (!isHumanPeer(participant)) return;
    setStatus(
      "The other participant joined, but their media could not be received. Try rejoining.",
      true,
    );
  });
  room.on(livekit.RoomEvent.Disconnected, () => {
    joined = false;
    setCallControls(false);
    if (callEnded) {
      setStatus("The interview has ended.");
    } else if (!explicitlyLeft) {
      showRejoin("The LiveKit connection ended. Rejoin to continue this interview.");
    }
  });
}

async function joinRoom() {
  joinButton.disabled = true;
  explicitlyLeft = false;
  setStatus("Connecting to the LiveKit interview room…");
  try {
    room = new livekit.Room({ adaptiveStream: true, dynacast: true });
    configureRoomEvents();
    await room.connect(roomConfig.livekit_url, participantToken, { autoSubscribe: true });
    await room.startAudio();
    for (const track of localTracks) await room.localParticipant.publishTrack(track);
    attachAvailableRemoteTracks();
    joined = true;
    rejoinButton.hidden = true;
    setCallControls(true);
    await synchronizeLifecycle();
    const peerPresent = Array.from(room.remoteParticipants.values()).some(isHumanPeer);
    setStatus(
      peerPresent && hasRemoteVideo()
        ? "Interview connected securely through LiveKit."
        : peerPresent
          ? "The other participant joined. Connecting media…"
        : roomConfig.role === "INTERVIEWER"
          ? "Waiting for candidate…"
          : "Waiting for interviewer…",
    );
  } catch (error) {
    await disconnectRoom();
    showRejoin(error.message || "The LiveKit interview room is temporarily unavailable.");
  }
}

async function disconnectRoom() {
  joined = false;
  if (room) {
    await room.disconnect().catch(() => undefined);
    room = null;
  }
  for (const track of localTracks) {
    track.detach();
    track.stop();
  }
  localTracks = [];
  localVideo.srcObject = null;
  remoteVideo.srcObject = null;
  remoteAudio.srcObject = null;
  localPlaceholder.hidden = false;
  remotePlaceholder.hidden = false;
  remotePlaceholder.textContent = "Waiting for the other participant";
  setCallControls(false);
}

async function leaveRoom() {
  leaveButton.disabled = true;
  if (roomConfig.role === "INTERVIEWER") {
    setStatus("Ending the interview…");
    const response = await fetch(roomConfig.end_url, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok && payload.code !== "VIDEO_ROOM_CLEANUP_FAILED") {
      leaveButton.disabled = false;
      setStatus(payload.message || "The interview could not be ended.", true);
      return;
    }
    callEnded = true;
    setLifecycle("ENDED");
    await disconnectRoom();
    setStatus(
      response.ok
        ? "The interview has ended."
        : "The interview ended, but provider cleanup may still be pending.",
      !response.ok,
    );
    return;
  }
  explicitlyLeft = true;
  await disconnectRoom();
  setStatus("You left the interview. Reload the invitation link when you are ready to rejoin.");
  rejoinButton.hidden = false;
}

async function toggleMicrophone() {
  const publication = room?.localParticipant.getTrackPublication(livekit.Track.Source.Microphone);
  if (!publication?.track) return;
  if (publication.isMuted) await publication.unmute();
  else await publication.mute();
  microphoneButton.textContent = publication.isMuted ? "Unmute microphone" : "Mute microphone";
  microphoneButton.setAttribute("aria-pressed", String(publication.isMuted));
}

async function toggleCamera() {
  const publication = room?.localParticipant.getTrackPublication(livekit.Track.Source.Camera);
  if (!publication?.track) return;
  if (publication.isMuted) await publication.unmute();
  else await publication.mute();
  cameraButton.textContent = publication.isMuted ? "Turn camera on" : "Turn camera off";
  cameraButton.setAttribute("aria-pressed", String(publication.isMuted));
}

if (!roomConfig.join_allowed) {
  prepareButton.disabled = true;
  joinButton.disabled = true;
  setCallControls(false);
  if (callEnded) {
    setLifecycle("ENDED");
    setStatus("This interview has ended.");
  } else {
    setStatus(roomConfig.provider_error_message || "Live interviews are not configured.", true);
  }
}

prepareButton.addEventListener("click", prepareMedia);
joinButton.addEventListener("click", joinRoom);
rejoinButton.addEventListener("click", () => window.location.reload());
microphoneButton.addEventListener("click", toggleMicrophone);
cameraButton.addEventListener("click", toggleCamera);
leaveButton.addEventListener("click", leaveRoom);
window.addEventListener("beforeunload", () => {
  if (joined && room) room.disconnect();
});

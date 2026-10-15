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
const transcriptStatus = document.querySelector("#transcription-status");
const transcriptStatusMessage = document.querySelector("#transcription-status-message");
const transcriptSegments = document.querySelector("#interview-transcript-segments");
const transcriptEmpty = document.querySelector("#interview-transcript-empty");

const livekit = window.LivekitClient;
const expectedRemoteIdentity = `${
  roomConfig.role === "INTERVIEWER" ? "candidate" : "interviewer"
}_${roomConfig.interview_id.replaceAll("-", "")}`;
let room = null;
let localTracks = [];
let joined = false;
let explicitlyLeft = false;
let callEnded = roomConfig.status === "ENDED";
const liveTranscriptRows = new Map();

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

function setTranscriptionStatus(status) {
  if (!transcriptStatus || !transcriptStatusMessage) return;
  transcriptStatus.textContent = status.charAt(0) + status.slice(1).toLowerCase();
  const messages = {
    STARTING: "Transcription is starting. The interview can continue while the agent connects.",
    LIVE: "Live transcription is active.",
    UNAVAILABLE: "Transcription is temporarily unavailable. The interview can continue normally.",
    ENDED: "Transcription has ended. Persisted final segments remain available.",
  };
  transcriptStatusMessage.textContent = messages[status] || messages.UNAVAILABLE;
}

function transcriptSpeaker(identity) {
  const opaqueInterviewId = roomConfig.interview_id.replaceAll("-", "");
  if (identity === `candidate_${opaqueInterviewId}`) return "Candidate";
  if (identity === `interviewer_${opaqueInterviewId}`) return "Interviewer";
  return null;
}

function appendTranscriptRow({ key, identity, text, isFinal, startedAt }) {
  if (!transcriptSegments) return;
  const speaker = transcriptSpeaker(identity);
  const normalizedText = text.trim();
  if (!speaker || !normalizedText || !key) return;
  const nearBottom =
    transcriptSegments.scrollHeight - transcriptSegments.scrollTop - transcriptSegments.clientHeight <
    48;
  let row = liveTranscriptRows.get(key);
  if (!row) {
    row = document.createElement("article");
    row.className = "interview-transcript-segment";
    row.dataset.segmentKey = key;
    const header = document.createElement("div");
    header.className = "interview-transcript-segment-header";
    const label = document.createElement("strong");
    label.textContent = speaker;
    const time = document.createElement("time");
    if (startedAt) {
      time.dateTime = startedAt;
      time.textContent = new Intl.DateTimeFormat(undefined, { timeStyle: "medium" }).format(
        new Date(startedAt),
      );
    }
    header.append(label, time);
    const body = document.createElement("p");
    row.append(header, body);
    transcriptSegments.append(row);
    liveTranscriptRows.set(key, row);
  }
  row.querySelector("p").textContent = normalizedText;
  row.classList.toggle("interim", !isFinal);
  row.dataset.final = String(isFinal);
  if (transcriptEmpty) transcriptEmpty.hidden = true;
  if (nearBottom) transcriptSegments.scrollTop = transcriptSegments.scrollHeight;
}

async function loadPersistedTranscript() {
  if (!roomConfig.transcript_url || !transcriptSegments) return;
  try {
    const response = await fetch(roomConfig.transcript_url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Persisted transcript could not be loaded.");
    const payload = await response.json();
    for (const segment of payload.segments || []) {
      appendTranscriptRow({
        key: `persisted:${segment.id}`,
        identity: segment.participant_identity,
        text: segment.text,
        isFinal: true,
        startedAt: segment.started_at,
      });
    }
  } catch (_error) {
    setTranscriptionStatus("UNAVAILABLE");
  }
}

function configureTranscriptionStreams() {
  if (!room || roomConfig.role !== "INTERVIEWER" || !transcriptSegments) return;
  room.registerTextStreamHandler("lk.transcription", async (reader, participantInfo) => {
    try {
      const attributes = reader.info?.attributes || {};
      if (!attributes["lk.transcribed_track_id"]) return;
      const segmentId = attributes["lk.segment_id"];
      if (!segmentId) return;
      const text = await reader.readAll();
      appendTranscriptRow({
        key: `live:${participantInfo.identity}:${segmentId}`,
        identity: participantInfo.identity,
        text,
        isFinal:
          attributes["lk.transcription_final"] === true ||
          attributes["lk.transcription_final"] === "true",
      });
      setTranscriptionStatus("LIVE");
    } catch (_error) {
      setTranscriptionStatus("UNAVAILABLE");
    }
  });
  room.registerTextStreamHandler(
    "recrunion.transcription.status",
    async (reader, participantInfo) => {
      if (!participantInfo.identity.startsWith("agent-")) return;
      try {
        const status = (await reader.readAll()).trim().toUpperCase();
        if (["STARTING", "LIVE", "UNAVAILABLE", "ENDED"].includes(status)) {
          setTranscriptionStatus(status);
        }
      } catch (_error) {
        setTranscriptionStatus("UNAVAILABLE");
      }
    },
  );
}

function setCallControls(enabled) {
  microphoneButton.disabled = !enabled;
  cameraButton.disabled = !enabled;
  leaveButton.disabled = !enabled;
}

function setButtonLoading(button, loading) {
  if (loading) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
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
  setButtonLoading(prepareButton, true);
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
  } finally {
    setButtonLoading(prepareButton, false);
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
  setButtonLoading(joinButton, true);
  explicitlyLeft = false;
  setStatus("Connecting to the LiveKit interview room…");
  try {
    room = new livekit.Room({ adaptiveStream: true, dynacast: true });
    configureTranscriptionStreams();
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
  } finally {
    setButtonLoading(joinButton, false);
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
    setTranscriptionStatus("ENDED");
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

if (roomConfig.role === "INTERVIEWER") {
  setTranscriptionStatus(roomConfig.transcription_status || "UNAVAILABLE");
  loadPersistedTranscript();
}

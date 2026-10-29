// Direct live-view intercom actions; capabilities and permissions stay authoritative.
import { el, svgIcon } from "ccg/core";
import { t } from "ccg/i18n";
import { audioMessageButton } from "ccg/audio-message";
import { pushToTalkButton } from "ccg/push-to-talk";
import { allowed, permittedCamera } from "ccg/session-access";

export function quickAudioControls(camera) {
  if (!allowed("intercom")) return null;
  const cam = permittedCamera(camera);
  if (!cam.audio_streams && !cam.audio_messages) return null;
  const group = el("span", { className: "camera-quick-audio" });
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", t("panel.audio"));
  if (cam.audio_streams) group.append(pushToTalkButton(cam, { inline: true }));
  if (cam.audio_messages) {
    const record = audioMessageButton(cam);
    record.className = "icon-btn quick-audio-button";
    record.textContent = "";
    record.innerHTML = svgIcon("i-voice-message");
    record.title = t("intercom.open");
    record.setAttribute("aria-label", t("intercom.open"));
    group.append(record);
  }
  return group;
}

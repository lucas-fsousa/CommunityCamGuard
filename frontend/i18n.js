"use strict";
// Tiny i18n layer for the plain-JS dashboard. No build step, no deps.
//
// Usage:
//   - dynamic strings (app.js):  t("key", { param: value })
//   - static HTML:               data-i18n="key" (textContent),
//                                data-i18n-ph="key" (placeholder),
//                                data-i18n-title="key" (title attribute)
//     then call applyI18n() to fill them in for the current language.
//
// Language is auto-detected from the browser, overridable via the header selector, and
// persisted in localStorage. The product name ("Community Cam Guard" / "CCG") is intentionally
// NOT translated. English is the default so the open-source UI reads en-US out of the box.

const STRINGS = {
  en: {
    "login.prompt": "Enter the dashboard key",
    "login.keyPlaceholder": "secret key",
    "login.unlock": "Unlock",
    "login.invalid": "Invalid key",

    "nav.grid": "Grid",
    "nav.gridTitle": "Grid view",
    "nav.single": "Single",
    "nav.singleTitle": "Single camera",
    "nav.recordings": "Recordings",
    "nav.recordingsTitle": "Browse recordings",
    "nav.scan": "Scan network",
    "nav.cameras": "Cameras",
    "nav.camerasTitle": "Manage cameras",
    "nav.logout": "Logout",
    "lang.title": "Language",

    "storage.title": "storage usage",
    "storage.disk": "disk {pct}% · {gb}GB free",
    "storage.paused": " · SAVING PAUSED",

    "cams.empty": 'No cameras yet. Open the “Cameras” tab to scan for them.',
    "cams.filter_all": "All",
    "cams.filter_available": "Available",
    "cams.filter_configured": "Configured",
    "cams.headingConfigured": "Configured cameras",
    "cams.headingAvailable": "Available to add",
    "cams.noneConfigured": "No cameras configured yet.",
    "cams.noneAvailable": "No new cameras found. Run a scan.",
    "cams.scanFailed": "Scan failed: {msg}",
    "cam.recording": "recording",
    "cam.idle": "idle",
    "cam.remove": "remove",
    "cam.removeConfirm": "Remove {name}?",
    "cam.probe": "probe capabilities",
    "cam.probeFailed": "Probe failed: {msg}",
    "cam.restart": "restart stream and return to live",
    "cam.unnamed": "(unnamed)",

    "cap.videoCodec": "video codec",
    "cap.audioPresent": "audio track present",
    "cap.audio": "audio",
    "cap.ptz": "pan/tilt supported",
    "zoom.in": "zoom in",
    "zoom.out": "zoom out",
    "zoom.reset": "reset zoom (or double-click the image)",
    "quality.label": "Video quality",
    "quality.auto": "Auto (performance)",
    "quality.sharp": "HD (main)",
    "quality.smooth": "SD (substream)",
    "ptz.hold": "hold to pan / tilt",
    "ptz.holdDir": "hold to pan {dir}",
    "dir.left": "left",
    "dir.up": "up",
    "dir.down": "down",
    "dir.right": "right",

    "scan.title": "Network scan",
    "scan.close": "close",
    "scan.scanning": "Scanning gently… this can take ~30s.",
    "scan.refreshed": "{n} known camera(s) found and refreshed.",
    "scan.noNew": "No new cameras found.",
    "scan.newHeading": "New cameras — add credentials",

    "add.namePlaceholder": "name (e.g. Front door)",
    "add.username": "username",
    "add.password": "password",
    "add.pathPlaceholder": "stream path (e.g. /onvif1)",
    "add.add": "Add",
    "add.adding": "Adding…",
    "add.identified": "Identified: {bits}",
    "add.unknown": "Model unknown (no ONVIF response) — you can still add it",
    "add.portsLine": "{ip} · ports {ports}",
    "add.fw": "fw {v}",
    "add.driver": "driver: {d}",

    "provision.heading": "Set up a factory-new camera",
    "provision.title": "Connect a camera to Wi-Fi",
    "provision.description": "Scan the camera label, select a Wi-Fi network detected by this server and enter only its password.",
    "provision.open": "Set up new camera",
    "provision.localOnlyTitle": "Local access required",
    "provision.localOnly": "For security, factory setup is disabled when the dashboard is opened through a domain, LAN address or the internet. Open it at http://localhost:3200 on the server.",
    "provision.label": "label QR text (http://yoosee.co/?D=…)",
    "provision.deviceId": "device ID",
    "provision.capability": "label capability code",
    "provision.firmware": "printed firmware/version (optional)",
    "provision.mac": "printed MAC address (optional)",
    "provision.photo": "Read QR code from a photo",
    "provision.manualDetails": "Enter label details manually",
    "provision.inspect": "Validate label",
    "provision.inspecting": "Validating label…",
    "provision.decoding": "Reading QR code locally…",
    "provision.noDecoder": "This browser cannot decode QR images. Paste the QR text or enter the fields manually.",
    "provision.noQr": "No QR code was found in this image.",
    "provision.valid": "Camera {id} · setup modes: {modes}",
    "provision.chooseNetwork": "Select a detected Wi-Fi network",
    "provision.refreshNetworks": "Refresh networks",
    "provision.scanningNetworks": "Searching for Wi-Fi networks…",
    "provision.noNetworks": "No Wi-Fi networks detected",
    "provision.noWifiRadio": "The server cannot see a Wi-Fi radio. Connect a supported Wi-Fi adapter to use SoftAP provisioning.",
    "provision.wifiPassword": "Wi-Fi password",
    "provision.wifiHint": "The Wi-Fi password is kept only for this setup request and is never stored or logged.",
    "provision.start": "Start synchronization",
    "provision.started": "Synchronization started. Waiting for the camera…",
    "provision.transportPending": "Label validation is ready; camera synchronization will be enabled when the recovered transport is complete.",

    "rec.allCameras": "All cameras",
    "rec.search": "Search",
    "rec.prev": "‹ Prev",
    "rec.next": "Next ›",
    "rec.loading": "Loading…",
    "rec.range": "{first}–{last} of {total}",
    "rec.none": "No recordings in this range.",
    "rec.camera": "Camera",
    "rec.from": "From",
    "rec.to": "To",
    "rec.retention": "Retention: {days} days",
    "rec.retentionOff": "Retention: unlimited",
    "rec.retentionHint": "Recordings older than this are deleted automatically",
  },
  "pt-BR": {
    "login.prompt": "Digite a chave do painel",
    "login.keyPlaceholder": "chave secreta",
    "login.unlock": "Entrar",
    "login.invalid": "Chave inválida",

    "nav.grid": "Grade",
    "nav.gridTitle": "Visão em grade",
    "nav.single": "Única",
    "nav.singleTitle": "Câmera única",
    "nav.recordings": "Gravações",
    "nav.recordingsTitle": "Ver gravações",
    "nav.scan": "Escanear rede",
    "nav.cameras": "Câmeras",
    "nav.camerasTitle": "Gerenciar câmeras",
    "nav.logout": "Sair",
    "lang.title": "Idioma",

    "storage.title": "uso de disco",
    "storage.disk": "disco {pct}% · {gb}GB livres",
    "storage.paused": " · GRAVAÇÃO PAUSADA",

    "cams.empty": 'Nenhuma câmera ainda. Abra a aba “Câmeras” para escaneá-las.',
    "cams.filter_all": "Todas",
    "cams.filter_available": "Disponíveis",
    "cams.filter_configured": "Configuradas",
    "cams.headingConfigured": "Câmeras configuradas",
    "cams.headingAvailable": "Disponíveis para adicionar",
    "cams.noneConfigured": "Nenhuma câmera configurada ainda.",
    "cams.noneAvailable": "Nenhuma câmera nova encontrada. Rode um scan.",
    "cams.scanFailed": "Falha no scan: {msg}",
    "cam.recording": "gravando",
    "cam.idle": "parada",
    "cam.remove": "remover",
    "cam.removeConfirm": "Remover {name}?",
    "cam.probe": "detectar recursos",
    "cam.probeFailed": "Falha na detecção: {msg}",
    "cam.restart": "reiniciar stream e voltar ao vivo",
    "cam.unnamed": "(sem nome)",

    "cap.videoCodec": "codec de vídeo",
    "cap.audioPresent": "faixa de áudio presente",
    "cap.audio": "áudio",
    "cap.ptz": "pan/tilt suportado",
    "zoom.in": "aproximar",
    "zoom.out": "afastar",
    "zoom.reset": "restaurar zoom (ou duplo clique na imagem)",
    "quality.label": "Qualidade do vídeo",
    "quality.auto": "Auto (desempenho)",
    "quality.sharp": "HD (principal)",
    "quality.smooth": "SD (substream)",
    "ptz.hold": "segure para mover",
    "ptz.holdDir": "segure para mover {dir}",
    "dir.left": "esquerda",
    "dir.up": "cima",
    "dir.down": "baixo",
    "dir.right": "direita",

    "scan.title": "Escaneamento de rede",
    "scan.close": "fechar",
    "scan.scanning": "Escaneando com cuidado… pode levar ~30s.",
    "scan.refreshed": "{n} câmera(s) conhecida(s) encontrada(s) e atualizada(s).",
    "scan.noNew": "Nenhuma câmera nova encontrada.",
    "scan.newHeading": "Novas câmeras — adicione credenciais",

    "add.namePlaceholder": "nome (ex.: Porta da frente)",
    "add.username": "usuário",
    "add.password": "senha",
    "add.pathPlaceholder": "caminho do stream (ex.: /onvif1)",
    "add.add": "Adicionar",
    "add.adding": "Adicionando…",
    "add.identified": "Identificado: {bits}",
    "add.unknown": "Modelo desconhecido (sem resposta ONVIF) — você ainda pode adicioná-la",
    "add.portsLine": "{ip} · portas {ports}",
    "add.fw": "fw {v}",
    "add.driver": "driver: {d}",

    "provision.heading": "Configurar câmera de fábrica",
    "provision.title": "Conectar uma câmera ao Wi-Fi",
    "provision.description": "Leia a etiqueta da câmera, selecione uma rede detectada pelo servidor e informe somente a senha.",
    "provision.open": "Configurar nova câmera",
    "provision.localOnlyTitle": "Acesso local obrigatório",
    "provision.localOnly": "Por segurança, a configuração de fábrica fica desativada quando o painel é aberto por domínio, endereço da LAN ou internet. Abra http://localhost:3200 no servidor.",
    "provision.label": "texto do QR da etiqueta (http://yoosee.co/?D=…)",
    "provision.deviceId": "ID do dispositivo",
    "provision.capability": "código de recursos da etiqueta",
    "provision.firmware": "firmware/versão impressa (opcional)",
    "provision.mac": "endereço MAC impresso (opcional)",
    "provision.photo": "Ler QR code de uma foto",
    "provision.manualDetails": "Informar detalhes da etiqueta manualmente",
    "provision.inspect": "Validar etiqueta",
    "provision.inspecting": "Validando etiqueta…",
    "provision.decoding": "Lendo QR code localmente…",
    "provision.noDecoder": "Este navegador não decodifica imagens QR. Cole o texto do QR ou preencha os campos manualmente.",
    "provision.noQr": "Nenhum QR code foi encontrado nesta imagem.",
    "provision.valid": "Câmera {id} · modos de configuração: {modes}",
    "provision.chooseNetwork": "Selecione uma rede Wi-Fi detectada",
    "provision.refreshNetworks": "Atualizar redes",
    "provision.scanningNetworks": "Buscando redes Wi-Fi…",
    "provision.noNetworks": "Nenhuma rede Wi-Fi detectada",
    "provision.noWifiRadio": "O servidor não enxerga uma interface Wi-Fi. Conecte um adaptador Wi-Fi compatível para usar o provisionamento SoftAP.",
    "provision.wifiPassword": "senha do Wi-Fi",
    "provision.wifiHint": "A senha do Wi-Fi existe somente durante esta requisição e nunca é salva ou registrada em log.",
    "provision.start": "Iniciar sincronização",
    "provision.started": "Sincronização iniciada. Aguardando a câmera…",
    "provision.transportPending": "A validação da etiqueta está pronta; a sincronização será liberada quando o transporte recuperado estiver completo.",

    "rec.allCameras": "Todas as câmeras",
    "rec.search": "Buscar",
    "rec.prev": "‹ Anterior",
    "rec.next": "Próxima ›",
    "rec.loading": "Carregando…",
    "rec.range": "{first}–{last} de {total}",
    "rec.none": "Nenhuma gravação neste período.",
    "rec.camera": "Câmera",
    "rec.from": "De",
    "rec.to": "Até",
    "rec.retention": "Retenção: {days} dias",
    "rec.retentionOff": "Retenção: ilimitada",
    "rec.retentionHint": "Gravações mais antigas que isso são apagadas automaticamente",
  },
};

const LANGS = Object.keys(STRINGS);
const DEFAULT_LANG = "en";

function pickInitialLang() {
  const saved = localStorage.getItem("ccg_lang");
  if (saved && STRINGS[saved]) return saved;
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("pt")) return "pt-BR";
  return DEFAULT_LANG;
}

let currentLang = pickInitialLang();

// t(key, params) → translated string with {tokens} filled from params. Falls back to the
// English string, then to the raw key, so a missing translation never blanks the UI.
function t(key, params = {}) {
  const s = (STRINGS[currentLang] && STRINGS[currentLang][key]) ??
            STRINGS[DEFAULT_LANG][key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in params ? params[k] : `{${k}}`));
}

// Fill every element carrying a data-i18n* attribute for the current language.
function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.dataset.i18n); });
  root.querySelectorAll("[data-i18n-ph]").forEach((n) => { n.placeholder = t(n.dataset.i18nPh); });
  root.querySelectorAll("[data-i18n-title]").forEach((n) => { n.title = t(n.dataset.i18nTitle); });
  document.documentElement.lang = currentLang;
}

function setLang(lang) {
  if (!STRINGS[lang] || lang === currentLang) return;
  currentLang = lang;
  localStorage.setItem("ccg_lang", lang);
}

const getLang = () => currentLang;

export { applyI18n, getLang, LANGS as I18N_LANGS, setLang, t };

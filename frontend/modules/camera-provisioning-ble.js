// Yoosee/Gwell factory provisioning over Web Bluetooth.
//
// The browser owns only the GATT transport. The backend supplies already encrypted, short-lived
// frames so TanKey, configToken and Wi-Fi plaintext never enter this module.

export const BLE_SERVICE_UUID = "8922a5c3-1e44-403e-a587-bcf972e398b4";
export const BLE_UUIDS = Object.freeze({
  read: "0000fed4-0000-1000-8000-00805f9b34fb",
  write: "0000fed5-0000-1000-8000-00805f9b34fb",
  indicate: "0000fed6-0000-1000-8000-00805f9b34fb",
  writeWithoutResponse: "0000fed7-0000-1000-8000-00805f9b34fb",
  notify: "0000fed8-0000-1000-8000-00805f9b34fb",
});

export const BLE_COMMANDS = Object.freeze({
  sendRandom: 0x70,
  randomResponse: 0x71,
  wifiList: 0x80,
  wifiListResponse: 0x81,
  wifiConfig: 0x82,
  wifiConfigResponse: 0x83,
  wifiConnectionResult: 0x85,
  finish: 0x88,
  linkType: 0x72,
  linkTypeResponse: 0x73,
});

export function supportsWebBluetooth() {
  return Boolean(globalThis.navigator && navigator.bluetooth && navigator.bluetooth.requestDevice);
}

export function provisioningBleName(deviceId) {
  const normalized = String(deviceId || "").trim();
  if (!/^\d{6,20}$/.test(normalized)) throw new Error("Invalid BLE provisioning device ID");
  return `GW_BLE_${normalized}`;
}

export function fragmentBleMessage({ command, data = new Uint8Array(), encrypted = false,
  messageId, mtu = 23, version = 0 }) {
  const payload = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (!Number.isInteger(command) || command < 0 || command > 0xff) throw new Error("Invalid BLE command");
  if (!Number.isInteger(messageId) || messageId < 1 || messageId > 15) throw new Error("Invalid BLE message ID");
  if (!Number.isInteger(version) || version < 0 || version > 7) throw new Error("Invalid BLE frame version");
  if (!Number.isInteger(mtu) || mtu < 5 || mtu > 256) throw new Error("Invalid BLE MTU");

  const capacity = mtu - 4;
  const count = Math.max(1, Math.ceil(payload.length / capacity));
  if (count > 15) throw new Error("BLE payload requires more than 15 frames");
  const control = (version << 5) | (Number(Boolean(encrypted)) << 4) | messageId;
  return Array.from({ length: count }, (_, index) => {
    const chunk = payload.slice(index * capacity, (index + 1) * capacity);
    const frame = new Uint8Array(4 + chunk.length);
    frame.set([control, command, (count << 4) | index, chunk.length]);
    frame.set(chunk, 4);
    return frame;
  });
}

function bytesFrom(value) {
  if (value instanceof Uint8Array) return value.slice();
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice();
  }
  if (value instanceof ArrayBuffer) return new Uint8Array(value).slice();
  return new Uint8Array(value);
}

export function parseBleFrame(value) {
  const frame = bytesFrom(value);
  if (frame.length < 4) throw new Error("BLE frame is shorter than its four-byte header");
  const [control, command, sequence, dataLength] = frame;
  const totalFrames = sequence >> 4;
  const frameIndex = sequence & 0x0f;
  const messageId = control & 0x0f;
  if (!messageId) throw new Error("BLE message ID zero is reserved");
  if (!totalFrames) throw new Error("BLE frame count must be between 1 and 15");
  if (frameIndex >= totalFrames) throw new Error("BLE frame index is outside the declared message");
  if (frame.length !== 4 + dataLength) {
    throw new Error("BLE frame data length does not match its header");
  }
  return {
    version: (control >> 5) & 0x07,
    encrypted: Boolean(control & 0x10),
    messageId,
    command,
    totalFrames,
    frameIndex,
    data: frame.slice(4),
  };
}

export function createBleMessageAssembler() {
  let key = null;
  const parts = new Map();
  return {
    reset() {
      key = null;
      parts.clear();
    },
    add(value) {
      const frame = parseBleFrame(value);
      const nextKey = [frame.version, Number(frame.encrypted), frame.messageId,
        frame.command, frame.totalFrames].join(":");
      if (key !== nextKey) {
        key = nextKey;
        parts.clear();
      }
      const existing = parts.get(frame.frameIndex);
      if (existing && (existing.length !== frame.data.length
        || existing.some((byte, index) => byte !== frame.data[index]))) {
        throw new Error("Conflicting duplicate BLE frame");
      }
      parts.set(frame.frameIndex, frame.data);
      if (parts.size !== frame.totalFrames) return null;

      const length = Array.from(parts.values()).reduce((sum, part) => sum + part.length, 0);
      const data = new Uint8Array(length);
      let offset = 0;
      for (let index = 0; index < frame.totalFrames; index += 1) {
        const part = parts.get(index);
        data.set(part, offset);
        offset += part.length;
      }
      const message = { version: frame.version, encrypted: frame.encrypted,
        messageId: frame.messageId, command: frame.command, data };
      this.reset();
      return message;
    },
  };
}

export async function writeBleFrames(session, frames) {
  const withoutResponse = session?.writeWithoutResponse;
  const withResponse = session?.write;
  const characteristic = withoutResponse?.properties?.writeWithoutResponse
    ? withoutResponse : withResponse?.properties?.write ? withResponse : null;
  if (!characteristic) throw new Error("BLE write characteristic is unavailable");
  for (const value of frames) {
    const frame = bytesFrom(value);
    if (characteristic === withoutResponse && typeof characteristic.writeValueWithoutResponse === "function") {
      await characteristic.writeValueWithoutResponse(frame);
    } else if (typeof characteristic.writeValueWithResponse === "function") {
      await characteristic.writeValueWithResponse(frame);
    } else if (typeof characteristic.writeValue === "function") {
      await characteristic.writeValue(frame);
    } else {
      throw new Error("The browser cannot write to this BLE characteristic");
    }
    // The native client advances fragments from Android's characteristic-write callback. A
    // browser promise may resolve as soon as a no-response packet is queued, so leave a small
    // interval for this low-power firmware instead of flooding all fragments at once.
    await new Promise((resolve) => setTimeout(resolve, 35));
  }
}

async function optionalCharacteristic(service, uuid) {
  try { return await service.getCharacteristic(uuid); }
  catch (_err) { return null; }
}

export async function connectProvisioningCamera(deviceId, onNotification = () => {}) {
  if (!supportsWebBluetooth()) throw new Error("Web Bluetooth is not available in this browser");
  const expectedName = provisioningBleName(deviceId);
  // A camera normally stops advertising GW_BLE_* as soon as it accepts the Wi-Fi settings. The
  // browser, however, keeps the BluetoothDevice permission granted during the first pass. Reuse
  // that handle before opening the picker so an interrupted bind/finalize stage can reconnect to
  // an already provisioned camera without forcing a factory reset.
  let device = null;
  if (typeof navigator.bluetooth.getDevices === "function") {
    const granted = await navigator.bluetooth.getDevices();
    device = granted.find((candidate) => candidate.name === expectedName) || null;
  }
  if (!device) {
    device = await navigator.bluetooth.requestDevice({
      filters: [{ name: expectedName }],
      optionalServices: [BLE_SERVICE_UUID],
    });
  }
  if (!device.gatt) throw new Error("The selected camera does not expose Bluetooth GATT");

  let server = null;
  let phase = "connecting to GATT";
  try {
    server = await device.gatt.connect();
    phase = "opening the provisioning service";
    const service = await server.getPrimaryService(BLE_SERVICE_UUID);
    phase = "discovering characteristics";
    const [writeWithoutResponse, write, notify, indicate] = await Promise.all([
      optionalCharacteristic(service, BLE_UUIDS.writeWithoutResponse),
      optionalCharacteristic(service, BLE_UUIDS.write),
      optionalCharacteristic(service, BLE_UUIDS.notify),
      optionalCharacteristic(service, BLE_UUIDS.indicate),
    ]);
    if (!writeWithoutResponse && !write) throw new Error("The camera exposes no writable BLE channel");
    const assembler = createBleMessageAssembler();
    const listeners = [];
    phase = "enabling a response channel";
    // The vendor manager subscribes to its configured notify characteristic *and* to the
    // write-without-response characteristic (BleConfigManager fields p and n). This is unusual,
    // but intentional: some firmware revisions return packets over FED7 even though its logical
    // role is writing. FED6 is retained as a third compatibility fallback.
    const responseCharacteristics = [notify, writeWithoutResponse, indicate].filter(Boolean);
    for (const characteristic of responseCharacteristics) {
      try {
        await characteristic.startNotifications();
      } catch (error) {
        // Android's vendor client enables FED8 and FED6 opportunistically and ignores a rejected
        // CCCD. Chrome surfaces that rejection as NotSupportedError; keep the other response
        // channel instead of tearing down an otherwise valid GATT connection.
        console.warn("[CCG BLE] response channel rejected", characteristic.uuid, error);
        continue;
      }
      const listener = (event) => {
        const view = event.target?.value;
        if (!view) return;
        const frame = new Uint8Array(view.buffer, view.byteOffset, view.byteLength).slice();
        try {
          onNotification(frame, assembler.add(frame), null);
        } catch (error) {
          onNotification(frame, null, error);
        }
      };
      characteristic.addEventListener("characteristicvaluechanged", listener);
      listeners.push([characteristic, listener]);
    }
    if (!listeners.length) {
      const advertised = responseCharacteristics.map((characteristic) => {
        const properties = characteristic.properties || {};
        const enabled = ["read", "write", "writeWithoutResponse", "notify", "indicate"]
          .filter((name) => properties[name]);
        return `${characteristic.uuid.slice(4, 8)}[${enabled.join(",") || "none"}]`;
      }).join(" ");
      throw new Error(`The camera exposes no usable BLE response channel (${advertised})`);
    }
    return {
      device,
      server,
      service,
      writeWithoutResponse,
      write,
      writeFrames(frames) { return writeBleFrames({ writeWithoutResponse, write }, frames); },
      disconnect() {
        listeners.forEach(([characteristic, listener]) => {
          characteristic.removeEventListener("characteristicvaluechanged", listener);
        });
        if (device.gatt?.connected) device.gatt.disconnect();
      },
    };
  } catch (err) {
    if (device.gatt.connected) device.gatt.disconnect();
    const detail = err instanceof Error ? err.message : String(err);
    const wrapped = new Error(`Bluetooth ${phase}: ${detail}`);
    wrapped.name = err?.name || "Error";
    throw wrapped;
  }
}

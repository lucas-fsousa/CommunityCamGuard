"""Bounded public projections for optional Yoosee BLE network metadata."""


def is_signed_integer(value: object) -> bool:
    return type(value) is int and -(2**31) <= value < 2**31


def network_metadata(command: int, payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    if command == 0x73:
        link_type = payload.get("linkType")
        if not is_signed_integer(link_type):
            return None
        # Only the recovered WIFI name is known; never reflect an arbitrary name.
        result = {"linkType": link_type}
        if link_type == 1:
            result["linkTypeName"] = "WIFI"
        return result
    if command != 0x81 or not isinstance(payload.get("wifiList"), list):
        return None
    networks: list[dict[str, object]] = []
    for item in payload["wifiList"][:100]:
        if not isinstance(item, dict) or not isinstance(item.get("ssid"), str):
            continue
        ssid = item["ssid"]
        try:
            valid_ssid = 1 <= len(ssid.encode("utf-8")) <= 32
        except UnicodeEncodeError:
            valid_ssid = False
        if not valid_ssid:
            continue
        network: dict[str, object] = {"ssid": ssid}
        if is_signed_integer(item.get("level")):
            network["level"] = item["level"]
        networks.append(network)
    return {"wifiList": networks}
